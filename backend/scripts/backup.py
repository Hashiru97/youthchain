#!/usr/bin/env python3
"""
Snapshots the SQLite database and uploaded files into one timestamped
archive under backend/backups/.

Addresses BL-07 / Phase 3 #21 of the engineering review: there was
previously no backup mechanism of any kind, meaning a single disk failure
would permanently and irrecoverably destroy every user's credentials,
applications, and account data.

This is a local-filesystem implementation suitable for a single-instance
deployment. For a real production deployment, point BACKUP_DIR at (or sync
it to) off-machine storage (e.g. object storage) — a backup that lives on
the same disk as the data it protects does not protect against disk
failure, only against operator error.

Usage:
    python scripts/backup.py                 # writes to backend/backups/
    python scripts/backup.py --out /path/to/dir
"""
import argparse
import os
import shutil
import sys
import tarfile
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Flask-SQLAlchemy resolves a relative "sqlite:///youthchain.db" URI against
# app.instance_path, which defaults to <app root>/instance/ — NOT the app
# root itself. Confirmed by inspection of the running app, not assumed.
DB_PATH = os.path.join(BASE_DIR, "instance", "youthchain.db")
UPLOADS_DIR = os.path.join(BASE_DIR, "uploads")
DEFAULT_BACKUP_DIR = os.path.join(BASE_DIR, "backups")


def create_backup(backup_dir: str) -> str:
    os.makedirs(backup_dir, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive_path = os.path.join(backup_dir, f"youthchain_backup_{timestamp}.tar.gz")

    if not os.path.exists(DB_PATH):
        print(f"[backup] WARNING: no database found at {DB_PATH} — backing up uploads only (if any).")

    with tarfile.open(archive_path, "w:gz") as tar:
        if os.path.exists(DB_PATH):
            tar.add(DB_PATH, arcname="instance/youthchain.db")
        if os.path.isdir(UPLOADS_DIR):
            tar.add(UPLOADS_DIR, arcname="uploads")

    size_kb = os.path.getsize(archive_path) / 1024
    print(f"[backup] Wrote {archive_path} ({size_kb:.1f} KB)")
    return archive_path


def prune_old_backups(backup_dir: str, keep: int) -> None:
    if keep <= 0:
        return
    backups = sorted(
        (f for f in os.listdir(backup_dir) if f.startswith("youthchain_backup_") and f.endswith(".tar.gz")),
    )
    stale = backups[:-keep] if len(backups) > keep else []
    for name in stale:
        path = os.path.join(backup_dir, name)
        os.remove(path)
        print(f"[backup] Pruned old backup {path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=DEFAULT_BACKUP_DIR, help="Directory to write the backup archive into")
    parser.add_argument("--keep", type=int, default=14, help="Number of recent backups to retain locally (0 = keep all)")
    args = parser.parse_args()

    path = create_backup(args.out)
    prune_old_backups(args.out, args.keep)
    sys.exit(0)
