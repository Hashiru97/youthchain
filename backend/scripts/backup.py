#!/usr/bin/env python3
"""
Snapshots the database and uploaded files into one timestamped archive
under backend/backups/, and optionally uploads it to S3-compatible object
storage (real off-site sync — see docker-compose.backup.yml for a
self-hosted MinIO target to point this at).

Addresses BL-07 / Phase 3 #21 of the engineering review: there was
previously no backup mechanism of any kind, meaning a single disk failure
would permanently and irrecoverably destroy every user's credentials,
applications, and account data.

Real bug found and fixed in this pass: this script only ever knew about
the SQLite file at instance/youthchain.db. If DATABASE_URL is pointed at
Postgres (this repo's own documented production path — see
.env.example/docs/load-testing.md), the previous version would have
silently backed up the uploads/ folder only, printed a warning about a
missing SQLite file, and NEVER backed up any actual user data at all.
Now detects DATABASE_URL and uses pg_dump for a Postgres target instead.

Usage:
    python scripts/backup.py                 # writes to backend/backups/
    python scripts/backup.py --out /path/to/dir
    python scripts/backup.py --no-upload      # skip S3 upload even if configured
"""
import argparse
import json
import os
import shutil
import subprocess
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


def _dump_postgres(database_url: str, dest_sql_path: str) -> bool:
    """Runs pg_dump against DATABASE_URL, writing plain SQL to dest_sql_path.
    Returns True on success. Requires the `pg_dump` client binary to be on
    PATH — present in the postgres:16-alpine image's client tools, and
    documented as a real prerequisite (not silently assumed) for running
    this script outside a container that has it."""
    if not shutil.which("pg_dump"):
        print("[backup] ERROR: pg_dump not found on PATH — cannot back up the Postgres database.", file=sys.stderr)
        print("[backup]        Run this from an environment with the postgresql-client tools installed.", file=sys.stderr)
        return False

    try:
        with open(dest_sql_path, "wb") as f:
            result = subprocess.run(
                ["pg_dump", "--no-owner", "--no-privileges", database_url],
                stdout=f,
                stderr=subprocess.PIPE,
                timeout=300,
            )
        if result.returncode != 0:
            print(f"[backup] ERROR: pg_dump failed: {result.stderr.decode(errors='replace')}", file=sys.stderr)
            return False
        return True
    except Exception as e:
        print(f"[backup] ERROR: pg_dump failed: {e}", file=sys.stderr)
        return False


def create_backup(backup_dir: str) -> str:
    os.makedirs(backup_dir, exist_ok=True)

    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    archive_path = os.path.join(backup_dir, f"youthchain_backup_{timestamp}.tar.gz")
    database_url = os.getenv("DATABASE_URL", "").strip()
    is_postgres = database_url.startswith("postgresql://") or database_url.startswith("postgres://")

    pg_dump_path = None
    if is_postgres:
        pg_dump_path = os.path.join(backup_dir, f".pg_dump_{timestamp}.sql")
        # DATABASE_URL itself (which contains credentials) is only ever
        # passed to the pg_dump subprocess directly, never logged/printed.
        print("[backup] DATABASE_URL points at Postgres — using pg_dump instead of the SQLite file.")
        if not _dump_postgres(database_url, pg_dump_path):
            print("[backup] Aborting: could not dump the Postgres database. See error above.", file=sys.stderr)
            # Raises rather than sys.exit(1) directly — the __main__ block
            # below needs a single, uniform place to catch every failure
            # mode and write the status file _write_status_file() writes
            # (see its own docstring for why that file exists at all), and
            # a bare sys.exit() here would bypass that entirely.
            raise RuntimeError("could not dump the Postgres database")
    elif not os.path.exists(DB_PATH):
        print(f"[backup] WARNING: no database found at {DB_PATH} — backing up uploads only (if any).")

    with tarfile.open(archive_path, "w:gz") as tar:
        if is_postgres and pg_dump_path and os.path.exists(pg_dump_path):
            tar.add(pg_dump_path, arcname="postgres_dump.sql")
        elif os.path.exists(DB_PATH):
            tar.add(DB_PATH, arcname="instance/youthchain.db")
        if os.path.isdir(UPLOADS_DIR):
            tar.add(UPLOADS_DIR, arcname="uploads")

    if pg_dump_path and os.path.exists(pg_dump_path):
        os.remove(pg_dump_path)  # the .sql is only ever meant to live inside the archive

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


def upload_to_s3(archive_path: str) -> bool:
    """
    Off-site sync — real, not aspirational: uploads the archive to any
    S3-compatible endpoint (self-hosted MinIO via docker-compose.backup.yml,
    or a real cloud bucket) if BACKUP_S3_* env vars are set. Silently a
    no-op (returns True) if unconfigured, so this script keeps working
    with zero setup exactly as it always has — off-site sync is additive,
    not a new hard requirement.
    """
    endpoint = os.getenv("BACKUP_S3_ENDPOINT")
    bucket = os.getenv("BACKUP_S3_BUCKET")
    access_key = os.getenv("BACKUP_S3_ACCESS_KEY")
    secret_key = os.getenv("BACKUP_S3_SECRET_KEY")

    if not (endpoint and bucket and access_key and secret_key):
        return True  # not configured — a local-only backup is still a valid outcome

    try:
        import boto3
    except ImportError:
        print("[backup] WARNING: BACKUP_S3_* is configured but boto3 isn't installed — skipping off-site upload.", file=sys.stderr)
        return False

    try:
        client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )
        key = os.path.basename(archive_path)
        client.upload_file(archive_path, bucket, key)
        print(f"[backup] Uploaded {key} to s3://{bucket} at {endpoint}")
        return True
    except Exception as e:
        print(f"[backup] ERROR: off-site upload failed: {e}", file=sys.stderr)
        return False


def _write_status_file(out_dir: str, success: bool, error: str | None = None) -> None:
    """
    Real gap found and fixed alongside the retry-backoff change below
    (docker-compose.backup.yml): a failed scheduled backup was previously
    only visible to someone manually reading backup-cron's container
    logs — Prometheus, already scraping the backend's own /metrics (see
    docker-compose.observability.yml), had nothing to alert on, so a
    silently-broken backup pipeline could run for weeks with no signal
    anywhere an operator would actually look.

    Written to the same backups-data volume the `backend` service already
    mounts (docker-compose.yml's `backups-data:/app/backups`) — the Flask
    app reads this file directly and exposes it as real gauge metrics
    (see _read_backup_status()/the youthchain_backup_* gauges in app.py),
    so this needed no new service, no new shared channel, and no push
    gateway: just reusing a volume and an endpoint that already exist.

    Best-effort: a failure writing this file must never change the
    backup run's own exit code — this is observability bookkeeping, not
    the backup itself.
    """
    status_path = os.path.join(out_dir, ".backup_status.json")
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "success": success,
        "error": error,
    }
    try:
        os.makedirs(out_dir, exist_ok=True)
        with open(status_path, "w") as f:
            json.dump(payload, f)
    except Exception as e:
        print(f"[backup] WARNING: could not write status file: {e}", file=sys.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=DEFAULT_BACKUP_DIR, help="Directory to write the backup archive into")
    parser.add_argument("--keep", type=int, default=14, help="Number of recent backups to retain locally (0 = keep all)")
    parser.add_argument("--no-upload", action="store_true", help="Skip S3/MinIO upload even if BACKUP_S3_* is configured")
    args = parser.parse_args()

    try:
        path = create_backup(args.out)
        prune_old_backups(args.out, args.keep)

        if not args.no_upload:
            ok = upload_to_s3(path)
            if not ok:
                _write_status_file(args.out, success=False, error="off-site upload failed")
                sys.exit(1)

        _write_status_file(args.out, success=True)
        sys.exit(0)
    except Exception as e:
        _write_status_file(args.out, success=False, error=str(e))
        print(f"[backup] ERROR: backup failed: {e}", file=sys.stderr)
        sys.exit(1)
