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


# --- Decryption (BACKUP_ENCRYPTION_IDENTITY / age) ------------------------
#
# _decrypt_archive() itself was also live-verified for real, not just
# here: paired with scripts/backup.py's encrypt_backup() live-verification
# (see test_backup_script.py's own comment) -- a real archive encrypted
# with a real age keypair was decrypted back via this exact function using
# the raw-identity-content path (BACKUP_ENCRYPTION_IDENTITY set to the
# "AGE-SECRET-KEY-..." string itself, not a file path), and the resulting
# plaintext .tar.gz's instance/youthchain.db content matched the original
# byte-for-byte. These tests mock the `age` subprocess call instead, same
# reasoning as this file's existing pg_dump-snapshot tests -- CI's plain
# `pytest tests/` leg has no real `age` binary on PATH.

def test_decrypt_archive_fails_loudly_when_identity_unset(restore_module, monkeypatch):
    monkeypatch.delenv("BACKUP_ENCRYPTION_IDENTITY", raising=False)

    with pytest.raises(SystemExit):
        restore_module._decrypt_archive("/fake/archive.tar.gz.age", "/fake/dest")


def test_decrypt_archive_fails_loudly_when_age_binary_missing(restore_module, monkeypatch):
    monkeypatch.setenv("BACKUP_ENCRYPTION_IDENTITY", "AGE-SECRET-KEY-1FAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKE")
    monkeypatch.setattr(restore_module.shutil, "which", lambda name: None)

    with pytest.raises(SystemExit):
        restore_module._decrypt_archive("/fake/archive.tar.gz.age", "/fake/dest")


def test_decrypt_archive_writes_raw_identity_content_to_a_private_tempfile(restore_module, monkeypatch, tmp_path):
    """BACKUP_ENCRYPTION_IDENTITY given as raw key content (not a path) --
    same path-or-content convention FIREBASE_CREDENTIALS_JSON uses in
    app.py -- must be written to a 0600 file, since `age --identity` only
    accepts a file path."""
    identity_content = "AGE-SECRET-KEY-1FAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKE"
    monkeypatch.setenv("BACKUP_ENCRYPTION_IDENTITY", identity_content)
    monkeypatch.setattr(restore_module.shutil, "which", lambda name: "/usr/bin/age")

    captured_cmd = {}

    class _FakeCompletedProcess:
        returncode = 0
        stderr = b""

    def _fake_run(cmd, stdout=None, stderr=None, timeout=None):
        captured_cmd["cmd"] = cmd
        out_path = cmd[cmd.index("--output") + 1]
        with open(out_path, "wb") as f:
            f.write(b"fake plaintext tar.gz bytes")
        return _FakeCompletedProcess()

    monkeypatch.setattr(restore_module.subprocess, "run", _fake_run)

    dest_dir = str(tmp_path)
    result_path = restore_module._decrypt_archive("/fake/archive.tar.gz.age", dest_dir)

    assert os.path.exists(result_path)
    identity_file = captured_cmd["cmd"][captured_cmd["cmd"].index("--identity") + 1]
    assert os.path.exists(identity_file)
    with open(identity_file) as f:
        assert f.read().strip() == identity_content
    # Must be private -- age itself refuses a group/world-readable identity file.
    if os.name != "nt":  # POSIX file-mode bits aren't meaningful on Windows
        assert (os.stat(identity_file).st_mode & 0o777) == 0o600


def test_decrypt_archive_uses_identity_path_directly_when_given_a_path(restore_module, monkeypatch, tmp_path):
    identity_file_path = tmp_path / "my-identity.txt"
    identity_file_path.write_text("AGE-SECRET-KEY-1REALPATHNOTINLINECONTENT00000000000000000")
    monkeypatch.setenv("BACKUP_ENCRYPTION_IDENTITY", str(identity_file_path))
    monkeypatch.setattr(restore_module.shutil, "which", lambda name: "/usr/bin/age")

    captured_cmd = {}

    class _FakeCompletedProcess:
        returncode = 0
        stderr = b""

    def _fake_run(cmd, stdout=None, stderr=None, timeout=None):
        captured_cmd["cmd"] = cmd
        out_path = cmd[cmd.index("--output") + 1]
        with open(out_path, "wb") as f:
            f.write(b"fake plaintext tar.gz bytes")
        return _FakeCompletedProcess()

    monkeypatch.setattr(restore_module.subprocess, "run", _fake_run)

    dest_dir = tmp_path / "out"
    dest_dir.mkdir()
    restore_module._decrypt_archive("/fake/archive.tar.gz.age", str(dest_dir))

    used_identity = captured_cmd["cmd"][captured_cmd["cmd"].index("--identity") + 1]
    # No new file created from raw content -- the path given IS the identity file.
    assert used_identity == str(identity_file_path)


def test_decrypt_archive_raises_and_exits_when_age_itself_fails(restore_module, monkeypatch, tmp_path):
    monkeypatch.setenv("BACKUP_ENCRYPTION_IDENTITY", "AGE-SECRET-KEY-1FAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKEFAKE")
    monkeypatch.setattr(restore_module.shutil, "which", lambda name: "/usr/bin/age")

    class _FakeFailedProcess:
        returncode = 1
        stderr = b"age: error: no identity matched any of the recipients"

    monkeypatch.setattr(restore_module.subprocess, "run", lambda *a, **k: _FakeFailedProcess())

    with pytest.raises(SystemExit):
        restore_module._decrypt_archive("/fake/archive.tar.gz.age", str(tmp_path))
