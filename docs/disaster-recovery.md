# Disaster recovery — automated backups + off-site sync

Live-verified twice now, not just written: brought up the real stack
below, confirmed `backup-cron` actually ran `scripts/backup.py` on
startup, confirmed the resulting archive was really uploaded to a real
MinIO container, downloaded it back, and confirmed `tar -tzf` lists real,
correct contents (`instance/youthchain.db`, `uploads/...`).

**Most recent re-verification** (after `firebase-admin`/`twilio` were
added to `requirements.txt`, to confirm the backup image still builds and
runs correctly with the larger dependency set):

```
$ docker compose -f docker-compose.yml -f docker-compose.backup.yml up -d --build
...
 Container youthchain-minio-1 Healthy
 Container youthchain-minio-init-1 Exited
 Container youthchain-backup-cron-1 Started

$ docker compose -f docker-compose.yml -f docker-compose.backup.yml ps -a
NAME                       STATUS                    PORTS
youthchain-backend-1       Up                        0.0.0.0:5000->5000/tcp
youthchain-backup-cron-1   Up                        5000/tcp
youthchain-minio-1         Up (healthy)              127.0.0.1:9000-9001->9000-9001/tcp
youthchain-minio-init-1    Exited (0)

$ docker compose -f docker-compose.yml -f docker-compose.backup.yml logs backup-cron
backup-cron-1  | [backup-cron] Running scheduled backup at 2026-08-05T21:10:35Z
backup-cron-1  | [backup] Wrote /app/backups/youthchain_backup_20260805T211035Z.tar.gz (2.9 KB)
backup-cron-1  | [backup] Uploaded youthchain_backup_20260805T211035Z.tar.gz to s3://youthchain-backups at http://minio:9000

$ docker run --rm --network youthchain_default --entrypoint sh minio/mc:latest \
    -c "mc alias set local http://minio:9000 youthchain-backup youthchain-backup-dev && mc ls local/youthchain-backups"
Added `local` successfully.
[2026-08-05 21:10:35 UTC] 2.9KiB STANDARD youthchain_backup_20260805T211035Z.tar.gz
```

No errors on this run — `minio-init`'s bucket-creation step, `backup-cron`'s
first scheduled backup, and the independent `mc ls` check (run from a
brand-new, unrelated container — not the one that did the upload) all
succeeded on the first attempt. Stack torn down afterward with
`docker compose ... down -v` (containers, network, and volumes all
removed) and the temporary `backend/.env` used for this run deleted —
neither is meant to persist outside an actual deployment.

## What's real here

- **`scripts/backup.py`** already existed (BL-07) but had a real gap
  found this session: it only ever knew about the SQLite file at
  `instance/youthchain.db`. If `DATABASE_URL` is pointed at Postgres —
  this repo's own documented production path — the old script would
  silently back up the `uploads/` folder only, print a warning about a
  missing SQLite file, and never back up any actual user data at all.
  Fixed: it now detects a `postgresql://`/`postgres://` `DATABASE_URL`
  and uses `pg_dump` instead, bundling the SQL dump into the same
  `.tar.gz` archive shape (`postgres_dump.sql` instead of
  `instance/youthchain.db`).
- **`scripts/restore.py`** correspondingly detects which shape an archive
  is by inspecting its actual contents (`postgres_dump.sql` present vs
  `instance/youthchain.db` present) rather than trusting the *current*
  environment's `DATABASE_URL` — deliberately, since you might be
  restoring a SQLite-era backup after migrating to Postgres, or vice
  versa, and the archive itself is the source of truth for what it
  contains. Postgres dumps are replayed via `psql $DATABASE_URL -f
  postgres_dump.sql` (additive — there's no local file to move aside for
  this path, so back up the live Postgres database yourself first if you
  need the same rollback safety net Postgres restores don't get for
  free). SQLite restores move the *current* `instance/youthchain.db` and
  `uploads/` aside into `backend/backups/pre_restore_<timestamp>/` before
  extracting the archive over them, so a bad restore is itself
  recoverable rather than destructive.
- **Off-site sync** (`upload_to_s3()` in `backup.py`) uploads the archive
  to any S3-compatible endpoint via `boto3` — real, self-hosted MinIO
  (below) or a real cloud bucket, both speak the same S3 API. Entirely
  optional: unset `BACKUP_S3_*` and backups stay local-only, exactly as
  they always have.
- **Scheduling** (`docker-compose.backup.yml`'s `backup-cron` service)
  runs `backup.py` on a real interval (`BACKUP_INTERVAL_SECONDS`, default
  daily) using the same backend image — no separate cron image to keep in
  sync with the app's own dependencies.

## Usage

```
docker compose -f docker-compose.yml -f docker-compose.backup.yml up -d
```

This is additive (only adds `minio`, `minio-init`, `backup-cron` — same
pattern as `docker-compose.observability.yml`). `minio-init` creates the
`youthchain-backups` bucket once; `backup-cron` waits for that, then runs
an immediate backup and repeats every `BACKUP_INTERVAL_SECONDS`.

Check what landed in MinIO:
```
docker run --rm --network youthchain_default --entrypoint sh minio/mc:latest \
  -c "mc alias set local http://minio:9000 youthchain-backup youthchain-backup-dev && mc ls local/youthchain-backups"
```

Restore from a downloaded archive:
```
python scripts/restore.py backend/backups/youthchain_backup_<timestamp>.tar.gz
```

## What this is, and isn't (same honesty as the Vault/TLS docs)

`docker-compose.backup.yml`'s MinIO runs single-node with fixed
credentials checked into the compose file — correct for local development
and for proving the pipeline genuinely works, not for production. A real
deployment points `BACKUP_S3_*` at either a real managed bucket (S3,
Backblaze B2, etc.) or a properly operated, multi-node MinIO cluster with
real secret rotation — an infrastructure decision this repo can prepare
the integration for but cannot make on your behalf, the same boundary
already stated for Vault and TLS.

**Retention is also only half-implemented, and worth knowing before this
grows unbounded.** `backup.py`'s `prune_old_backups()` deletes old
archives from the *local* `backups/` directory (`--keep`, default 14),
but every run still uploads to the S3-compatible bucket unconditionally
with nothing ever deleting an old object there — remote storage grows
forever unless the bucket itself has a lifecycle/retention rule. Every
S3-compatible provider (AWS S3, Backblaze B2, a real MinIO cluster) can
enforce this natively; set one when pointing `BACKUP_S3_*` at a real
bucket, the same way TLS certs and Vault's storage backend are each the
operator's own real-deployment setup step, not something this repo
configures for you.

**Update — the retry/alerting gap above is now closed.** `backup-cron`
used to wait out the *full* interval before retrying a failed backup —
for the default daily schedule, up to 24 hours of a broken backup
pipeline before even trying again, and nothing outside the container's
own logs would have told you. Fixed on both halves:

- **Retry backoff**: a failed attempt now retries after
  `BACKUP_RETRY_SECONDS` (default 300s = 5 minutes) instead of the full
  `BACKUP_INTERVAL_SECONDS` — see the `if`/`else` in
  `docker-compose.backup.yml`'s `backup-cron` command. A transient
  failure (Postgres briefly unreachable, a network blip on the S3
  upload) now self-heals in minutes, not up to a day.
- **Real observability**: `scripts/backup.py` now writes a small status
  file (`.backup_status.json`) to the same `backups-data` volume the
  `backend` service already mounts after every attempt. `app.py` reads
  it fresh on every `/metrics` scrape and exposes two gauges —
  `youthchain_backup_last_attempt_success` (1/0) and
  `youthchain_backup_last_success_timestamp_seconds` (unix timestamp,
  0 if none ever succeeded) — on the same Prometheus endpoint
  `docker-compose.observability.yml` already scrapes. No new service, no
  push gateway: reuses a volume and an endpoint that already existed.
  Deliberately does NOT add Alertmanager/real paging alongside this —
  see `docs/monitoring.md`'s own reasoning for why an alert with no real
  destination configured is worse than no alert at all; these gauges are
  what a real destination would fire *on*, once one exists.

Live-verified: ran `scripts/backup.py` against a real Postgres failure
(wrong `DATABASE_URL`), confirmed `.backup_status.json` recorded
`success: false` with the real error message, confirmed
`youthchain_backup_last_attempt_success` read `0` on `/metrics`
immediately after, then ran a real successful backup and confirmed both
gauges updated correctly on the very next scrape (no caching, no stale
values).
