#!/usr/bin/env python3
"""
Restores the SQLite database and uploaded files from a backup archive
created by scripts/backup.py.

Safety: before overwriting anything, the current youthchain.db/uploads (if
present) are themselves moved aside into backend/backups/pre_restore_<ts>/
rather than deleted outright, so a bad restore is itself recoverable.

Usage:
    python scripts/restore.py backend/backups/youthchain_backup_20260804T120000Z.tar.gz
"""
import argparse
import os
import shutil
import sys
import tarfile
from datetime import datetime, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "instance", "youthchain.db")
UPLOADS_DIR = os.path.join(BASE_DIR, "uploads")
BACKUP_DIR = os.path.join(BASE_DIR, "backups")


def restore_from(archive_path: str) -> None:
    if not os.path.exists(archive_path):
        print(f"[restore] ERROR: archive not found: {archive_path}", file=sys.stderr)
        sys.exit(1)

    # Move current state aside instead of deleting it outright.
    if os.path.exists(DB_PATH) or os.path.isdir(UPLOADS_DIR):
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safety_dir = os.path.join(BACKUP_DIR, f"pre_restore_{ts}")
        os.makedirs(safety_dir, exist_ok=True)
        if os.path.exists(DB_PATH):
            shutil.move(DB_PATH, os.path.join(safety_dir, "youthchain.db"))
        if os.path.isdir(UPLOADS_DIR):
            shutil.move(UPLOADS_DIR, os.path.join(safety_dir, "uploads"))
        print(f"[restore] Existing state moved aside to {safety_dir}")

    with tarfile.open(archive_path, "r:gz") as tar:
        tar.extractall(BASE_DIR)

    print(f"[restore] Restored from {archive_path}")
    print(f"[restore] DB present: {os.path.exists(DB_PATH)}, uploads present: {os.path.isdir(UPLOADS_DIR)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("archive", help="Path to a youthchain_backup_*.tar.gz file")
    args = parser.parse_args()
    restore_from(args.archive)
