"""
Regression coverage for scripts/restore.py's Postgres safety net (real gap
found via a full-codebase review): the Postgres restore path used to run
psql immediately with no confirmation and no snapshot of the target
database, unlike the SQLite path, which already moved the current state
aside before overwriting it. These tests cover the pure-Python confirm/
snapshot logic with a fake `pg_dump`/`psql` on PATH -- a real Postgres
server isn't needed to prove the gating behavior itself.
"""
import os
import sys

import pytest

SCRIPTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
sys.path.insert(0, SCRIPTS_DIR)


@pytest.fixture
def restore_module(tmp_path, monkeypatch):
    import restore as restore_module  # noqa: E402

    monkeypatch.setattr(restore_module, "BACKUP_DIR", str(tmp_path / "backups"))
    return restore_module


def test_confirm_postgres_restore_proceeds_silently_when_assume_yes(restore_module):
    # Must not raise/prompt at all -- there is no stdin to read from in a
    # non-interactive test run, so this would hang or raise if it tried.
    restore_module._confirm_postgres_restore("postgresql://user:pass@host/db", assume_yes=True)


def test_confirm_postgres_restore_aborts_when_user_declines(restore_module, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "no")
    with pytest.raises(SystemExit):
        restore_module._confirm_postgres_restore("postgresql://user:pass@host/db", assume_yes=False)


def test_confirm_postgres_restore_proceeds_when_user_types_yes(restore_module, monkeypatch):
    monkeypatch.setattr("builtins.input", lambda prompt="": "yes")
    # Must not raise.
    restore_module._confirm_postgres_restore("postgresql://user:pass@host/db", assume_yes=False)


def test_snapshot_postgres_before_restore_aborts_cleanly_when_pg_dump_missing(restore_module, monkeypatch):
    """Same failure mode/pattern as backup.py's own pg_dump-missing test --
    refusing to restore without a snapshot is the whole point of this
    function, so a missing pg_dump must abort loudly, not skip silently."""
    monkeypatch.setattr(restore_module.shutil, "which", lambda name: None)

    with pytest.raises(SystemExit):
        restore_module._snapshot_postgres_before_restore("postgresql://user:pass@host/db")


def test_snapshot_postgres_before_restore_writes_a_real_snapshot_file(restore_module, monkeypatch, tmp_path):
    monkeypatch.setattr(restore_module.shutil, "which", lambda name: "/usr/bin/pg_dump")

    class _FakeCompletedProcess:
        returncode = 0
        stderr = b""

    def _fake_run(cmd, stdout=None, stderr=None, timeout=None):
        stdout.write(b"-- fake pg_dump output\n")
        return _FakeCompletedProcess()

    monkeypatch.setattr(restore_module.subprocess, "run", _fake_run)

    restore_module._snapshot_postgres_before_restore("postgresql://user:pass@host/db")

    snapshot_dirs = list((tmp_path / "backups").glob("pre_restore_*"))
    assert len(snapshot_dirs) == 1
    snapshot_file = snapshot_dirs[0] / "postgres_pre_restore.sql"
    assert snapshot_file.exists()
    assert snapshot_file.read_bytes() == b"-- fake pg_dump output\n"


def test_snapshot_postgres_before_restore_aborts_when_pg_dump_itself_fails(restore_module, monkeypatch):
    monkeypatch.setattr(restore_module.shutil, "which", lambda name: "/usr/bin/pg_dump")

    class _FakeFailedProcess:
        returncode = 1
        stderr = b"connection refused"

    monkeypatch.setattr(restore_module.subprocess, "run", lambda *a, **k: _FakeFailedProcess())

    with pytest.raises(SystemExit):
        restore_module._snapshot_postgres_before_restore("postgresql://user:pass@host/db")
