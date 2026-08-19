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

Real gap found in the infra/DevOps audit and closed in this pass: the
archive this script produces contains real PII (CVs, employer
verification documents, the full user/application database) and was
uploaded to S3-compatible storage completely unencrypted — anyone with
read access to that bucket (a misconfigured bucket policy, a compromised
BACKUP_S3_* credential, a cloud provider employee/subpoena/breach) could
read every applicant's real name, contact details, and documents
directly. Encrypted now with age (see encrypt_backup() below,
BACKUP_ENCRYPTION_RECIPIENT in .env.example) whenever an off-site upload
is actually going to happen — this script refuses to upload plaintext PII
off this host, matching the same "correct by default, not
correct-if-configured-right" bar BACKUP_S3_* itself already set (all four
vars or none, never a half-configured silent partial state). A second,
related gap closed alongside it: prune_old_backups() below only ever
pruned the *local* backups/ directory -- every successful run still
uploaded unconditionally with nothing on this side ever deleting an old
remote object, so off-site storage grew forever unless the bucket had its
own lifecycle rule (see prune_remote_backups()).

Usage:
    python scripts/backup.py                 # writes to backend/backups/
    python scripts/backup.py --out /path/to/dir
    python scripts/backup.py --no-upload      # skip S3 upload even if configured
    python scripts/backup.py --no-encrypt     # skip age encryption (local-only; refused if --no-upload isn't also passed)
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
    # .tar.gz.age (age-encrypted) alongside plain .tar.gz — a host can
    # accumulate both if BACKUP_ENCRYPTION_RECIPIENT was only added partway
    # through its history, and this must keep pruning correctly either way.
    # Lexicographic sort still orders chronologically: the filename's
    # youthchain_backup_<timestamp> prefix is identical length in both
    # forms, so the differing suffix (.tar.gz vs .tar.gz.age) only ever
    # breaks a tie between two files sharing the exact same timestamp,
    # which encrypt_backup() below prevents by deleting the plaintext the
    # moment its encrypted twin exists.
    backups = sorted(
        f for f in os.listdir(backup_dir)
        if f.startswith("youthchain_backup_") and (f.endswith(".tar.gz") or f.endswith(".tar.gz.age"))
    )
    stale = backups[:-keep] if len(backups) > keep else []
    for name in stale:
        path = os.path.join(backup_dir, name)
        os.remove(path)
        print(f"[backup] Pruned old backup {path}")


def encrypt_backup(archive_path: str) -> str:
    """
    Encrypts archive_path with age (https://age-encryption.org) using the
    recipient public key in BACKUP_ENCRYPTION_RECIPIENT, writing
    <archive_path>.age and then deleting the plaintext archive — the
    plaintext must never survive on disk once its encrypted twin exists,
    same discipline create_backup() already applies to pg_dump's own
    intermediate .sql file just above.

    Shells out to the `age` CLI (backend/Dockerfile installs it via apt,
    same subprocess-to-a-real-binary pattern already used for pg_dump/psql
    in this file) rather than a Python age library — age deliberately has
    no complex config/API surface to wrap, and this keeps the dependency
    footprint identical to the pg_dump/psql precedent instead of adding a
    new PyPI package for one CLI call.

    Raises RuntimeError (not sys.exit — same reason create_backup() raises
    for a failed pg_dump: __main__ needs one uniform place to catch every
    failure mode and record it to .backup_status.json) if
    BACKUP_ENCRYPTION_RECIPIENT is unset, the `age` binary is missing, or
    encryption itself fails. Callers decide whether that's fatal for a
    given run — see __main__ below: mandatory the instant an off-site
    upload is about to happen (these archives contain real PII), optional
    for a purely local backup that never leaves this host.
    """
    recipient = os.getenv("BACKUP_ENCRYPTION_RECIPIENT", "").strip()
    if not recipient:
        raise RuntimeError(
            "BACKUP_ENCRYPTION_RECIPIENT is not set — refusing to encrypt. "
            "See backend/.env.example for how to generate an age keypair."
        )
    if not shutil.which("age"):
        raise RuntimeError(
            "age is not installed — cannot encrypt the backup archive. "
            "backend/Dockerfile already installs it (apt package `age`); "
            "run this from an environment with the `age` binary on PATH, "
            "e.g. inside the backend/backup-cron container."
        )

    encrypted_path = archive_path + ".age"
    try:
        result = subprocess.run(
            ["age", "--recipient", recipient, "--output", encrypted_path, archive_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=300,
        )
    except Exception as e:
        raise RuntimeError(f"age encryption failed: {e}") from e
    if result.returncode != 0:
        raise RuntimeError(f"age encryption failed: {result.stderr.decode(errors='replace')}")

    os.remove(archive_path)  # the plaintext is only ever meant to exist transiently
    size_kb = os.path.getsize(encrypted_path) / 1024
    print(f"[backup] Encrypted archive -> {encrypted_path} ({size_kb:.1f} KB)")
    return encrypted_path


def _s3_config():
    """
    Returns (endpoint, bucket, access_key, secret_key) if BACKUP_S3_* is
    fully configured (all four, same all-or-nothing rule this file has
    always used — see upload_to_s3()'s own comment history), else None.
    Factored out of upload_to_s3() so __main__ can ask the same question
    ("is an off-site upload actually about to happen this run?") to decide
    whether encryption is mandatory, without duplicating — and risking
    drifting from — upload_to_s3()'s own definition of "configured."
    """
    endpoint = os.getenv("BACKUP_S3_ENDPOINT")
    bucket = os.getenv("BACKUP_S3_BUCKET")
    access_key = os.getenv("BACKUP_S3_ACCESS_KEY")
    secret_key = os.getenv("BACKUP_S3_SECRET_KEY")
    if endpoint and bucket and access_key and secret_key:
        return endpoint, bucket, access_key, secret_key
    return None


def upload_to_s3(archive_path: str) -> bool:
    """
    Off-site sync — real, not aspirational: uploads the archive to any
    S3-compatible endpoint (self-hosted MinIO via docker-compose.backup.yml,
    or a real cloud bucket) if BACKUP_S3_* env vars are set. Silently a
    no-op (returns True) if unconfigured, so this script keeps working
    with zero setup exactly as it always has — off-site sync is additive,
    not a new hard requirement.
    """
    cfg = _s3_config()
    if cfg is None:
        return True  # not configured — a local-only backup is still a valid outcome
    endpoint, bucket, access_key, secret_key = cfg

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


def prune_remote_backups(keep: int) -> bool:
    """
    Deletes remote objects in the S3-compatible bucket beyond the most
    recent `keep` — real gap found in the infra audit: prune_old_backups()
    above only ever pruned the *local* backups/ directory. Every
    successful run still uploaded unconditionally to BACKUP_S3_* with
    nothing on this side ever deleting an old object there, so off-site
    storage grew unbounded unless the bucket itself had a lifecycle rule —
    something an operator had to remember to configure separately, and
    easy to never do (see docs/disaster-recovery.md's previous "retention
    is only half-implemented" note). This closes it the same way this
    codebase closes every other "works if configured correctly" gap:
    enforced by the script itself, not left to an operator's bucket
    config. `keep` is the exact same --keep value prune_old_backups() uses
    for local retention, so local and remote history stay the same depth
    by default (no separate remote-retention env var to forget to set).

    No-op (returns True) if BACKUP_S3_* isn't configured or keep <= 0
    ("keep everything"), same conventions as upload_to_s3()/
    prune_old_backups(). A failure here (network blip, a bucket policy
    that allows PutObject but not DeleteObject) is reported like any other
    real failure in this script — surfaced to __main__'s single uniform
    error path — rather than swallowed, since a retention policy that
    silently never actually enforces itself is the exact bug this
    function exists to fix.
    """
    if keep <= 0:
        return True
    cfg = _s3_config()
    if cfg is None:
        return True
    endpoint, bucket, access_key, secret_key = cfg

    try:
        import boto3
    except ImportError:
        print("[backup] WARNING: BACKUP_S3_* is configured but boto3 isn't installed — skipping remote retention pruning.", file=sys.stderr)
        return False

    try:
        client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
        )
        paginator = client.get_paginator("list_objects_v2")
        keys = []
        for page in paginator.paginate(Bucket=bucket):
            for obj in page.get("Contents", []):
                name = obj["Key"]
                if name.startswith("youthchain_backup_") and (name.endswith(".tar.gz") or name.endswith(".tar.gz.age")):
                    keys.append(name)
        keys.sort()  # same chronological-by-filename ordering as prune_old_backups()
        stale = keys[:-keep] if len(keys) > keep else []
        for key in stale:
            client.delete_object(Bucket=bucket, Key=key)
            print(f"[backup] Pruned old remote backup s3://{bucket}/{key}")
        return True
    except Exception as e:
        print(f"[backup] ERROR: remote retention pruning failed: {e}", file=sys.stderr)
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


def resolve_backup_encryption(path: str, no_encrypt: bool, will_upload: bool) -> str:
    """
    Applies this finding's actual decision logic — encrypt when
    BACKUP_ENCRYPTION_RECIPIENT is set, refuse to proceed when it's
    missing AND an off-site upload is about to happen, warn-and-proceed
    for a purely local run otherwise — and returns the path the rest of
    this run should use from here on (the new .age path if encryption
    ran, `path` unchanged otherwise).

    Factored out of __main__ into its own function, like create_backup()/
    prune_old_backups()/upload_to_s3() already are, specifically so this
    logic — the actual point of the encryption finding this closes — is
    unit-testable directly (see tests/test_backup_script.py) rather than
    only exercisable by shelling out to this whole script as a
    subprocess.

    Raises RuntimeError (never sys.exit) for both refusal cases, same
    uniform-error-path reasoning as encrypt_backup()/create_backup().
    """
    if no_encrypt and will_upload:
        raise RuntimeError(
            "--no-encrypt cannot be combined with an off-site upload: "
            "BACKUP_S3_* is configured, so this run would upload real PII "
            "(CVs, employer verification documents, the full user "
            "database) to S3-compatible storage in plaintext. Pass "
            "--no-upload too if you really want an unencrypted, "
            "local-only backup, or drop --no-encrypt to encrypt as usual."
        )
    if no_encrypt:
        return path

    recipient_configured = bool(os.getenv("BACKUP_ENCRYPTION_RECIPIENT", "").strip())
    if recipient_configured:
        return encrypt_backup(path)

    if will_upload:
        # Fail loud, not silent-skip: this is the one path where an
        # unencrypted archive is about to leave this host, and
        # BACKUP_S3_* being configured with no encryption recipient is
        # exactly the misconfiguration this finding exists to catch, not
        # a valid "encryption is optional" state.
        raise RuntimeError(
            "BACKUP_ENCRYPTION_RECIPIENT is not set, but BACKUP_S3_* "
            "is configured — refusing to upload an unencrypted "
            "archive containing real PII off this host. Set "
            "BACKUP_ENCRYPTION_RECIPIENT (see backend/.env.example "
            "for how to generate one with `age-keygen`), or pass "
            "--no-upload for a local-only, unencrypted backup."
        )

    # No off-site upload this run (BACKUP_S3_* unset, or --no-upload
    # passed) — a plaintext local-only archive is a valid outcome, same
    # posture .env.example's own header already states for at-rest
    # encryption in general ("a real deployment needs full-disk
    # encryption on the host... this codebase can't turn that on by
    # itself"), but still loud about it rather than silent, since this is
    # the one place that guarantee could quietly stop being true.
    print(
        "[backup] WARNING: BACKUP_ENCRYPTION_RECIPIENT is not set — "
        "this backup archive is UNENCRYPTED on disk. Fine for a "
        "purely local, already disk-encrypted host; NOT fine the "
        "moment this archive leaves this host by any means "
        "(a copied file, a misconfigured future BACKUP_S3_*, etc)."
    )
    return path


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default=DEFAULT_BACKUP_DIR, help="Directory to write the backup archive into")
    parser.add_argument("--keep", type=int, default=14, help="Number of recent backups to retain locally AND remotely (0 = keep all)")
    parser.add_argument("--no-upload", action="store_true", help="Skip S3/MinIO upload (and remote retention pruning) even if BACKUP_S3_* is configured")
    parser.add_argument(
        "--no-encrypt",
        action="store_true",
        help=(
            "Skip age encryption even if BACKUP_ENCRYPTION_RECIPIENT is set. "
            "Refused if an off-site upload is also about to happen this run "
            "(BACKUP_S3_* configured and --no-upload not also passed) — these "
            "archives contain real PII and must not leave this host in "
            "plaintext; combine with --no-upload for a local-only debug backup."
        ),
    )
    args = parser.parse_args()

    try:
        path = create_backup(args.out)

        # Whether an off-site upload is ACTUALLY going to happen this run —
        # not just "was --no-upload omitted", but also "is BACKUP_S3_*
        # actually fully configured" — is what decides whether encryption
        # is mandatory below. Computed once, from the same _s3_config()
        # upload_to_s3()/prune_remote_backups() themselves use, so this
        # can't drift from what those functions will actually do.
        will_upload = not args.no_upload and _s3_config() is not None

        path = resolve_backup_encryption(path, args.no_encrypt, will_upload)

        prune_old_backups(args.out, args.keep)

        if will_upload:
            ok = upload_to_s3(path)
            if not ok:
                _write_status_file(args.out, success=False, error="off-site upload failed")
                sys.exit(1)

            if not prune_remote_backups(args.keep):
                _write_status_file(args.out, success=False, error="remote retention pruning failed")
                sys.exit(1)

        _write_status_file(args.out, success=True)
        sys.exit(0)
    except Exception as e:
        _write_status_file(args.out, success=False, error=str(e))
        print(f"[backup] ERROR: backup failed: {e}", file=sys.stderr)
        sys.exit(1)
