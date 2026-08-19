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


# --- Encryption (BACKUP_ENCRYPTION_RECIPIENT / age) ----------------------
#
# encrypt_backup() itself was also live-verified for real, not just here:
# downloaded the real `age`/`age-keygen` v1.3.1 Windows binaries, generated
# a real keypair, ran create_backup() -> encrypt_backup() -> (restore.py's)
# _decrypt_archive() end to end against a throwaway tmp tree, and confirmed
# the decrypted instance/youthchain.db content matched the original
# byte-for-byte and that the plaintext .tar.gz no longer existed on disk
# after encryption. These tests below mock the `age` subprocess call
# instead (same reasoning as the pg_dump-missing-from-path test above --
# CI's plain `pytest tests/` leg runs on a bare ubuntu-latest runner with
# no `age` installed; only the built backend Docker image has it, via
# backend/Dockerfile's apt-get) so this suite doesn't depend on a real
# `age` binary being on PATH to pass.

def test_prune_old_backups_also_prunes_encrypted_age_archives(backup_module, tmp_path):
    """.tar.gz.age (age-encrypted) archives must age out under --keep the
    same as plain .tar.gz ones -- a host can accumulate a mix of both if
    BACKUP_ENCRYPTION_RECIPIENT was only added partway through its
    history."""
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    for i in range(3):
        (backup_dir / f"youthchain_backup_2026080{i}T000000Z.tar.gz.age").write_text("x")
    for i in range(3, 5):
        (backup_dir / f"youthchain_backup_2026080{i}T000000Z.tar.gz").write_text("x")

    backup_module.prune_old_backups(str(backup_dir), keep=2)

    remaining = sorted(os.listdir(backup_dir))
    assert remaining == [
        "youthchain_backup_20260803T000000Z.tar.gz",
        "youthchain_backup_20260804T000000Z.tar.gz",
    ]


def test_encrypt_backup_fails_loudly_when_recipient_unset(backup_module, monkeypatch, tmp_path):
    monkeypatch.delenv("BACKUP_ENCRYPTION_RECIPIENT", raising=False)
    fake_archive = tmp_path / "youthchain_backup_x.tar.gz"
    fake_archive.write_text("plaintext archive content")

    with pytest.raises(RuntimeError, match="BACKUP_ENCRYPTION_RECIPIENT is not set"):
        backup_module.encrypt_backup(str(fake_archive))

    # Must not have touched the plaintext archive on a config error.
    assert fake_archive.exists()


def test_encrypt_backup_fails_loudly_when_age_binary_missing(backup_module, monkeypatch, tmp_path):
    monkeypatch.setenv("BACKUP_ENCRYPTION_RECIPIENT", "age1fakerecipientforthistest0000000000000000000000000000")
    monkeypatch.setattr(backup_module.shutil, "which", lambda name: None)
    fake_archive = tmp_path / "youthchain_backup_x.tar.gz"
    fake_archive.write_text("plaintext archive content")

    with pytest.raises(RuntimeError, match="age is not installed"):
        backup_module.encrypt_backup(str(fake_archive))

    assert fake_archive.exists()


def test_encrypt_backup_removes_plaintext_and_returns_age_path_on_success(backup_module, monkeypatch, tmp_path):
    """Fakes a successful `age` invocation (real subprocess behavior is
    covered by the live-verified round trip described above) to confirm
    encrypt_backup()'s own bookkeeping: the plaintext archive must be
    gone, and the returned path must be the new `.age` file."""
    monkeypatch.setenv("BACKUP_ENCRYPTION_RECIPIENT", "age1fakerecipientforthistest0000000000000000000000000000")
    monkeypatch.setattr(backup_module.shutil, "which", lambda name: "/usr/bin/age")

    fake_archive = tmp_path / "youthchain_backup_x.tar.gz"
    fake_archive.write_text("plaintext archive content")

    class _FakeCompletedProcess:
        returncode = 0
        stderr = b""

    def _fake_run(cmd, stdout=None, stderr=None, timeout=None):
        # Real `age` writes ciphertext to the --output path; this fake
        # just writes placeholder bytes there so the file exists, the way
        # the real binary's side effect would.
        out_path = cmd[cmd.index("--output") + 1]
        with open(out_path, "wb") as f:
            f.write(b"fake ciphertext")
        return _FakeCompletedProcess()

    monkeypatch.setattr(backup_module.subprocess, "run", _fake_run)

    result_path = backup_module.encrypt_backup(str(fake_archive))

    assert result_path == str(fake_archive) + ".age"
    assert os.path.exists(result_path)
    assert not fake_archive.exists()  # plaintext must not survive encryption


def test_encrypt_backup_raises_and_leaves_plaintext_when_age_itself_fails(backup_module, monkeypatch, tmp_path):
    monkeypatch.setenv("BACKUP_ENCRYPTION_RECIPIENT", "age1fakerecipientforthistest0000000000000000000000000000")
    monkeypatch.setattr(backup_module.shutil, "which", lambda name: "/usr/bin/age")

    fake_archive = tmp_path / "youthchain_backup_x.tar.gz"
    fake_archive.write_text("plaintext archive content")

    class _FakeFailedProcess:
        returncode = 1
        stderr = b"age: error: no identity matched any of the recipients"

    monkeypatch.setattr(backup_module.subprocess, "run", lambda *a, **k: _FakeFailedProcess())

    with pytest.raises(RuntimeError, match="age encryption failed"):
        backup_module.encrypt_backup(str(fake_archive))

    # A failed encryption must not silently destroy the only copy of the backup.
    assert fake_archive.exists()


# --- resolve_backup_encryption() (the fail-loud-if-uploading gate) -------

def test_resolve_backup_encryption_encrypts_when_recipient_configured(backup_module, monkeypatch):
    monkeypatch.setenv("BACKUP_ENCRYPTION_RECIPIENT", "age1fakerecipient")
    monkeypatch.setattr(backup_module, "encrypt_backup", lambda path: path + ".age")

    result = backup_module.resolve_backup_encryption("/tmp/x.tar.gz", no_encrypt=False, will_upload=False)

    assert result == "/tmp/x.tar.gz.age"


def test_resolve_backup_encryption_fails_loudly_when_uploading_unencrypted(backup_module, monkeypatch):
    """The core of this finding: BACKUP_S3_* configured (will_upload=True)
    but no BACKUP_ENCRYPTION_RECIPIENT must refuse to proceed, not
    silently upload plaintext PII."""
    monkeypatch.delenv("BACKUP_ENCRYPTION_RECIPIENT", raising=False)

    with pytest.raises(RuntimeError, match="BACKUP_S3_\\* is configured"):
        backup_module.resolve_backup_encryption("/tmp/x.tar.gz", no_encrypt=False, will_upload=True)


def test_resolve_backup_encryption_warns_but_proceeds_for_local_only_run(backup_module, monkeypatch, capsys):
    """No off-site upload this run (will_upload=False) -- an unencrypted
    local-only archive is still a valid, zero-config outcome, same as
    upload_to_s3() itself being a no-op when BACKUP_S3_* is unset."""
    monkeypatch.delenv("BACKUP_ENCRYPTION_RECIPIENT", raising=False)

    result = backup_module.resolve_backup_encryption("/tmp/x.tar.gz", no_encrypt=False, will_upload=False)

    assert result == "/tmp/x.tar.gz"
    assert "UNENCRYPTED" in capsys.readouterr().out


def test_resolve_backup_encryption_refuses_no_encrypt_combined_with_upload(backup_module):
    """--no-encrypt is for local debugging only -- combining it with an
    actual off-site upload must be refused outright, not silently honored."""
    with pytest.raises(RuntimeError, match="--no-encrypt cannot be combined with an off-site upload"):
        backup_module.resolve_backup_encryption("/tmp/x.tar.gz", no_encrypt=True, will_upload=True)


def test_resolve_backup_encryption_no_encrypt_is_a_noop_for_local_only_run(backup_module):
    result = backup_module.resolve_backup_encryption("/tmp/x.tar.gz", no_encrypt=True, will_upload=False)
    assert result == "/tmp/x.tar.gz"


# --- Remote retention pruning (prune_remote_backups) ----------------------

def test_prune_remote_backups_is_a_noop_when_s3_unconfigured(backup_module, monkeypatch):
    for var in ("BACKUP_S3_ENDPOINT", "BACKUP_S3_BUCKET", "BACKUP_S3_ACCESS_KEY", "BACKUP_S3_SECRET_KEY"):
        monkeypatch.delenv(var, raising=False)

    assert backup_module.prune_remote_backups(keep=14) is True


def test_prune_remote_backups_is_a_noop_when_keep_is_zero(backup_module, monkeypatch):
    # Even with S3 fully configured, keep=0 means "keep everything" --
    # same convention as prune_old_backups()'s local retention.
    monkeypatch.setenv("BACKUP_S3_ENDPOINT", "http://minio:9000")
    monkeypatch.setenv("BACKUP_S3_BUCKET", "youthchain-backups")
    monkeypatch.setenv("BACKUP_S3_ACCESS_KEY", "k")
    monkeypatch.setenv("BACKUP_S3_SECRET_KEY", "s")

    assert backup_module.prune_remote_backups(keep=0) is True


class _FakeS3Client:
    """Minimal stand-in for boto3's S3 client -- just enough of
    list_objects_v2's paginator + delete_object for prune_remote_backups()
    to exercise its own real logic (which keys count as stale) without a
    real MinIO/S3 endpoint. Real off-site upload/list/delete against a
    live MinIO container is covered by the Docker verification in
    docs/disaster-recovery.md, not here."""

    def __init__(self, objects):
        self._objects = objects
        self.deleted = []

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        client = self

        class _Paginator:
            def paginate(self, Bucket):
                yield {"Contents": [{"Key": k} for k in client._objects]}

        return _Paginator()

    def delete_object(self, Bucket, Key):
        self.deleted.append(Key)


def test_prune_remote_backups_deletes_only_the_oldest_beyond_keep(backup_module, monkeypatch):
    monkeypatch.setenv("BACKUP_S3_ENDPOINT", "http://minio:9000")
    monkeypatch.setenv("BACKUP_S3_BUCKET", "youthchain-backups")
    monkeypatch.setenv("BACKUP_S3_ACCESS_KEY", "k")
    monkeypatch.setenv("BACKUP_S3_SECRET_KEY", "s")

    objects = [f"youthchain_backup_2026080{i}T000000Z.tar.gz" for i in range(5)]
    fake_client = _FakeS3Client(objects)

    fake_boto3 = type(sys)("boto3")
    fake_boto3.client = lambda *a, **k: fake_client
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)

    ok = backup_module.prune_remote_backups(keep=2)

    assert ok is True
    assert fake_client.deleted == [
        "youthchain_backup_20260800T000000Z.tar.gz",
        "youthchain_backup_20260801T000000Z.tar.gz",
        "youthchain_backup_20260802T000000Z.tar.gz",
    ]


def test_prune_remote_backups_ignores_objects_outside_the_backup_naming_pattern(backup_module, monkeypatch):
    """The bucket may hold other objects (a manual upload, a different
    tool's data) -- pruning must only ever touch this script's own
    youthchain_backup_*.tar.gz[.age] objects, never delete something it
    didn't create."""
    monkeypatch.setenv("BACKUP_S3_ENDPOINT", "http://minio:9000")
    monkeypatch.setenv("BACKUP_S3_BUCKET", "youthchain-backups")
    monkeypatch.setenv("BACKUP_S3_ACCESS_KEY", "k")
    monkeypatch.setenv("BACKUP_S3_SECRET_KEY", "s")

    objects = [
        "youthchain_backup_20260801T000000Z.tar.gz",
        "youthchain_backup_20260802T000000Z.tar.gz.age",
        "someone_elses_object.txt",
        "README.md",
    ]
    fake_client = _FakeS3Client(objects)
    fake_boto3 = type(sys)("boto3")
    fake_boto3.client = lambda *a, **k: fake_client
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)

    ok = backup_module.prune_remote_backups(keep=1)

    assert ok is True
    assert fake_client.deleted == ["youthchain_backup_20260801T000000Z.tar.gz"]


def test_prune_remote_backups_reports_failure_on_a_real_s3_error(backup_module, monkeypatch):
    monkeypatch.setenv("BACKUP_S3_ENDPOINT", "http://minio:9000")
    monkeypatch.setenv("BACKUP_S3_BUCKET", "youthchain-backups")
    monkeypatch.setenv("BACKUP_S3_ACCESS_KEY", "k")
    monkeypatch.setenv("BACKUP_S3_SECRET_KEY", "s")

    class _BoomClient:
        def get_paginator(self, name):
            raise RuntimeError("connection refused")

    fake_boto3 = type(sys)("boto3")
    fake_boto3.client = lambda *a, **k: _BoomClient()
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)

    assert backup_module.prune_remote_backups(keep=2) is False
