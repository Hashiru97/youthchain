#!/usr/bin/env python3
"""
Restores the database and uploaded files from a backup archive created by
scripts/backup.py. Handles both archive shapes that script can produce:
instance/youthchain.db (SQLite) or postgres_dump.sql (Postgres, restored
via psql against DATABASE_URL) — whichever is actually present in the
archive, not assumed from the current environment's DATABASE_URL alone
(you might be restoring a SQLite-era backup after migrating to Postgres,
or vice versa).

Safety: before overwriting anything, the current youthchain.db/uploads (if
present) are themselves moved aside into backend/backups/pre_restore_<ts>/
rather than deleted outright, so a bad restore is itself recoverable.
Postgres restores are additive (psql replays INSERT/COPY statements into
whatever DATABASE_URL points at) — a real gap found via a full-codebase
review, closed below: this used to run psql immediately with no
confirmation and no snapshot of the target database, so a single mistyped
DATABASE_URL (e.g. pointed at production instead of staging) would
silently replay a stale dump into a live database with no way back. It now
takes its own pg_dump snapshot of whatever DATABASE_URL currently points
at into the same pre_restore_<ts>/ directory before touching anything, and
requires --yes (or an interactive "yes" confirmation naming the exact
DATABASE_URL about to be written to) before proceeding — the same
"move aside, don't delete" safety net the SQLite path already had.

Usage:
    python scripts/restore.py backend/backups/youthchain_backup_20260804T120000Z.tar.gz
    python scripts/restore.py --yes backend/backups/youthchain_backup_20260804T120000Z.tar.gz
"""
import argparse
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "instance", "youthchain.db")
UPLOADS_DIR = os.path.join(BASE_DIR, "uploads")
BACKUP_DIR = os.path.join(BASE_DIR, "backups")


def _confirm_postgres_restore(database_url: str, assume_yes: bool) -> None:
    if assume_yes:
        return
    print(f"[restore] About to REPLAY a dump into: {database_url}", file=sys.stderr)
    print("[restore] This is additive (INSERT/COPY), not a wipe, but can still duplicate or", file=sys.stderr)
    print("[restore] conflict with existing data. A pre-restore snapshot will be taken first.", file=sys.stderr)
    answer = input("[restore] Type 'yes' to continue: ").strip().lower()
    if answer != "yes":
        print("[restore] Aborted — no changes made.", file=sys.stderr)
        sys.exit(1)


def _snapshot_postgres_before_restore(database_url: str) -> None:
    """
    Same pg_dump this project already uses for real backups (see
    scripts/backup.py's _dump_postgres) — run once more here, immediately
    before a restore, so a botched or wrong-target restore is itself
    recoverable, matching the SQLite path's "move current state aside"
    behavior instead of leaving Postgres as the one restore path with no
    rollback net.
    """
    if not shutil.which("pg_dump"):
        print(
            "[restore] ERROR: pg_dump not found on PATH — refusing to restore without a "
            "pre-restore snapshot. Run this from an environment with the postgresql-client "
            "tools installed.",
            file=sys.stderr,
        )
        sys.exit(1)

    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safety_dir = os.path.join(BACKUP_DIR, f"pre_restore_{ts}")
    os.makedirs(safety_dir, exist_ok=True)
    snapshot_path = os.path.join(safety_dir, "postgres_pre_restore.sql")

    try:
        with open(snapshot_path, "wb") as f:
            result = subprocess.run(
                ["pg_dump", "--no-owner", "--no-privileges", database_url],
                stdout=f,
                stderr=subprocess.PIPE,
                timeout=300,
            )
        if result.returncode != 0:
            print(
                f"[restore] ERROR: pre-restore pg_dump snapshot failed, aborting restore "
                f"without touching anything: {result.stderr.decode(errors='replace')}",
                file=sys.stderr,
            )
            sys.exit(1)
    except Exception as e:
        print(f"[restore] ERROR: pre-restore pg_dump snapshot failed, aborting restore: {e}", file=sys.stderr)
        sys.exit(1)

    print(f"[restore] Pre-restore snapshot of the current database saved to {snapshot_path}")


def _restore_postgres_dump(dump_path: str, assume_yes: bool) -> None:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not (database_url.startswith("postgresql://") or database_url.startswith("postgres://")):
        print(
            "[restore] ERROR: this archive contains a Postgres dump (postgres_dump.sql), "
            "but DATABASE_URL is not set to a postgresql:// URL in this environment. "
            "Set DATABASE_URL to the target Postgres instance and re-run.",
            file=sys.stderr,
        )
        sys.exit(1)
    if not shutil.which("psql"):
        print("[restore] ERROR: psql not found on PATH — cannot restore the Postgres dump.", file=sys.stderr)
        sys.exit(1)

    _confirm_postgres_restore(database_url, assume_yes)
    _snapshot_postgres_before_restore(database_url)

    result = subprocess.run(["psql", database_url, "-f", dump_path], capture_output=True, timeout=300)
    if result.returncode != 0:
        print(f"[restore] ERROR: psql restore failed: {result.stderr.decode(errors='replace')}", file=sys.stderr)
        sys.exit(1)
    print("[restore] Postgres dump restored successfully.")


def restore_from(archive_path: str, assume_yes: bool = False) -> None:
    if not os.path.exists(archive_path):
        print(f"[restore] ERROR: archive not found: {archive_path}", file=sys.stderr)
        sys.exit(1)

    with tarfile.open(archive_path, "r:gz") as tar:
        names = tar.getnames()
        has_postgres_dump = "postgres_dump.sql" in names
        has_sqlite_db = "instance/youthchain.db" in names
        has_uploads = any(n.startswith("uploads/") for n in names)

        if has_postgres_dump:
            with tempfile.TemporaryDirectory() as tmp:
                tar.extract("postgres_dump.sql", tmp)
                _restore_postgres_dump(os.path.join(tmp, "postgres_dump.sql"), assume_yes)
            if has_uploads:
                tar.extractall(
                    BASE_DIR,
                    members=[m for m in tar.getmembers() if m.name.startswith("uploads/")],
                    filter="data",
                )
        else:
            # SQLite path — move current state aside instead of deleting
            # it outright, same safety-net behavior as before.
            if os.path.exists(DB_PATH) or os.path.isdir(UPLOADS_DIR):
                ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                safety_dir = os.path.join(BACKUP_DIR, f"pre_restore_{ts}")
                os.makedirs(safety_dir, exist_ok=True)
                if os.path.exists(DB_PATH):
                    shutil.move(DB_PATH, os.path.join(safety_dir, "youthchain.db"))
                if os.path.isdir(UPLOADS_DIR):
                    shutil.move(UPLOADS_DIR, os.path.join(safety_dir, "uploads"))
                print(f"[restore] Existing state moved aside to {safety_dir}")
            tar.extractall(BASE_DIR, filter="data")

    print(f"[restore] Restored from {archive_path}")
    if has_postgres_dump:
        print("[restore] Restored Postgres database via psql.")
    else:
        print(f"[restore] DB present: {os.path.exists(DB_PATH)}, uploads present: {os.path.isdir(UPLOADS_DIR)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", help="Path to a youthchain_backup_*.tar.gz file")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip the interactive confirmation prompt before restoring into a Postgres DATABASE_URL "
        "(for non-interactive/scripted use — a pre-restore snapshot is still always taken).",
    )
    args = parser.parse_args()
    restore_from(args.archive, assume_yes=args.yes)
