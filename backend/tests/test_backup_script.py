"""
Regression coverage for the pure-Python parts of scripts/backup.py
(create_backup, prune_old_backups) — the Postgres pg_dump and S3 upload
paths need real external processes/services and are covered by live
Docker verification instead (see docs/disaster-recovery.md), not here.
"""
import os
import sys
import tarfile

import pytest

SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
sys.path.insert(0, SCRIPTS_DIR)


@pytest.fixture
def backup_module(tmp_path, monkeypatch):
    import backup as backup_module  # noqa: E402

    # Point the module's file-location constants at a throwaway tmp_path
    # tree instead of the real backend/instance and backend/uploads.
    db_path = tmp_path / "instance" / "youthchain.db"
    db_path.parent.mkdir(parents=True)
    db_path.write_text("fake sqlite db content")

    uploads_dir = tmp_path / "uploads"
    uploads_dir.mkdir()
    (uploads_dir / "cv.pdf").write_text("fake cv")

    monkeypatch.setattr(backup_module, "DB_PATH", str(db_path))
    monkeypatch.setattr(backup_module, "UPLOADS_DIR", str(uploads_dir))
    monkeypatch.delenv("DATABASE_URL", raising=False)
    return backup_module


def test_create_backup_produces_a_real_tar_with_db_and_uploads(backup_module, tmp_path):
    backup_dir = tmp_path / "backups"
    archive_path = backup_module.create_backup(str(backup_dir))

    assert os.path.exists(archive_path)
    with tarfile.open(archive_path, "r:gz") as tar:
        names = tar.getnames()
    assert "instance/youthchain.db" in names
    assert any(n.startswith("uploads/") for n in names)


def test_prune_old_backups_keeps_only_the_n_most_recent(backup_module, tmp_path):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    # Create 5 fake backup files with distinguishable, sortable names.
    for i in range(5):
        (backup_dir / f"youthchain_backup_2026080{i}T000000Z.tar.gz").write_text("x")

    backup_module.prune_old_backups(str(backup_dir), keep=2)

    remaining = sorted(os.listdir(backup_dir))
    assert len(remaining) == 2
    # The two most recent (highest timestamp) should survive.
    assert remaining == [
        "youthchain_backup_20260803T000000Z.tar.gz",
        "youthchain_backup_20260804T000000Z.tar.gz",
    ]


def test_prune_old_backups_keeps_all_when_keep_is_zero(backup_module, tmp_path):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    for i in range(3):
        (backup_dir / f"youthchain_backup_2026080{i}T000000Z.tar.gz").write_text("x")

    backup_module.prune_old_backups(str(backup_dir), keep=0)

    assert len(os.listdir(backup_dir)) == 3


def test_upload_to_s3_is_a_noop_when_unconfigured(backup_module, monkeypatch, tmp_path):
    for var in ("BACKUP_S3_ENDPOINT", "BACKUP_S3_BUCKET", "BACKUP_S3_ACCESS_KEY", "BACKUP_S3_SECRET_KEY"):
        monkeypatch.delenv(var, raising=False)

    fake_archive = tmp_path / "fake.tar.gz"
    fake_archive.write_text("x")

    # Should return True (success — "no off-site sync configured" is a
    # valid outcome, not a failure) without trying to import boto3 or make
    # any network call.
    assert backup_module.upload_to_s3(str(fake_archive)) is True


def test_dump_postgres_fails_cleanly_when_pg_dump_missing_from_path(backup_module, monkeypatch, tmp_path):
    """Pure-Python coverage of the one real failure mode that's actually
    plausible in a misconfigured deployment (postgresql-client not
    installed) — doesn't need a real Postgres server, just shutil.which
    to report the binary absent, exactly as it would on a bare image."""
    monkeypatch.setattr(backup_module.shutil, "which", lambda name: None)

    dest = tmp_path / "dump.sql"
    ok = backup_module._dump_postgres("postgresql://user:pass@host:5432/db", str(dest))

    assert ok is False
    assert not dest.exists()


def test_create_backup_aborts_when_postgres_configured_but_pg_dump_missing(backup_module, monkeypatch, tmp_path):
    """
    create_backup() now raises RuntimeError instead of calling sys.exit(1)
    directly -- changed alongside adding _write_status_file()/the
    youthchain_backup_* metrics gauges: the __main__ block needs a single,
    uniform place to catch every failure mode and record it to the status
    file real observability now reads, and a bare sys.exit() inside
    create_backup() bypassed that entirely (see the comment at its call
    site). This test used to assert SystemExit here; updated to match.
    """
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@host:5432/db")
    monkeypatch.setattr(backup_module.shutil, "which", lambda name: None)

    backup_dir = tmp_path / "backups"
    with pytest.raises(RuntimeError, match="could not dump the Postgres database"):
        backup_module.create_backup(str(backup_dir))

    # No partial/corrupt archive should be left behind on a failed dump.
    assert not backup_dir.exists() or not any(backup_dir.iterdir())


def test_write_status_file_records_success(backup_module, tmp_path):
    import json

    out_dir = tmp_path / "backups"
    backup_module._write_status_file(str(out_dir), success=True)

    with open(out_dir / ".backup_status.json") as f:
        payload = json.load(f)
    assert payload["success"] is True
    assert payload["error"] is None
    assert payload["timestamp"]  # a real ISO timestamp was written


def test_write_status_file_records_failure_with_error_message(backup_module, tmp_path):
    import json

    out_dir = tmp_path / "backups"
    backup_module._write_status_file(str(out_dir), success=False, error="pg_dump failed: connection refused")

    with open(out_dir / ".backup_status.json") as f:
        payload = json.load(f)
    assert payload["success"] is False
    assert payload["error"] == "pg_dump failed: connection refused"


def test_write_status_file_creates_the_output_directory_if_missing(backup_module, tmp_path):
    """The status file must land even if create_backup() itself never got
    far enough to create the output directory (e.g. it failed before any
    archive was written)."""
    out_dir = tmp_path / "backups_never_created"
    assert not out_dir.exists()

    backup_module._write_status_file(str(out_dir), success=False, error="boom")

    assert (out_dir / ".backup_status.json").exists()


def test_write_status_file_never_raises_even_if_the_directory_is_unwritable(backup_module, tmp_path, monkeypatch):
    """Best-effort, same discipline as log_event() in app.py -- a failure
    writing observability bookkeeping must never break the backup run's
    own exit code."""

    def _boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(backup_module.os, "makedirs", _boom)

    # Must not raise.
    backup_module._write_status_file(str(tmp_path / "unwritable"), success=True)
