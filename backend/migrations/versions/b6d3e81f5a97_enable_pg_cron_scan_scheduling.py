"""enable pg_cron scan scheduling

Revision ID: b6d3e81f5a97
Revises: a1f92c7de034
Create Date: 2026-08-17 09:25:00.000000

Postgres-only — no-ops on SQLite via a dialect check, same pattern
_search_jobs() already uses for its FTS branch. pg_cron can only run SQL,
so it owns *when* a scan happens, not *how*: it queues a `scan_run` row
for every active, due `job_source`, and the Python-side claim loop in
scanner.poller (FOR UPDATE SKIP LOCKED, safe across every gunicorn
worker/replica) does the actual Firecrawl->Claude->upsert work. See
scanner/pipeline.py and scanner/poller.py for that half.

REQUIRES the pg_cron-enabled Postgres image (infra/postgres-cron/) to
already be running with shared_preload_libraries=pg_cron set at server
start -- CREATE EXTENSION fails loudly otherwise (correct fail-loud
behavior, but it means the compose image swap must land BEFORE this
migration ever runs against a real deployment; see docker-compose.yml's
postgres service and infra/postgres-cron/Dockerfile).

Two scheduled jobs:
  - youthchain-queue-due-scans (every 5 min): INSERTs a queued scan_run
    for each active source whose last_scan_started_at is null or older
    than its own scan_frequency_minutes, guarded against double-queuing
    by checking for an already queued/running run on that source.
  - youthchain-reap-stale-scan-runs (every 10 min): flips any scan_run
    stuck 'running' past 30 minutes to 'failed' (dead-worker recovery --
    a worker that crashed mid-scan would otherwise leave that source
    permanently blocked from ever being queued again).
"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'b6d3e81f5a97'
down_revision = 'a1f92c7de034'
branch_labels = None
depends_on = None


def upgrade():
    if op.get_bind().dialect.name != "postgresql":
        return

    op.execute("CREATE EXTENSION IF NOT EXISTS pg_cron;")

    op.execute("""
        SELECT cron.schedule('youthchain-queue-due-scans', '*/5 * * * *', $$
            INSERT INTO scan_run (source_id, status, trigger, queued_at)
            SELECT js.id, 'queued', 'schedule', now()
            FROM job_source js
            WHERE js.active = true
              AND (js.last_scan_started_at IS NULL
                   OR js.last_scan_started_at <= now() - (js.scan_frequency_minutes || ' minutes')::interval)
              AND NOT EXISTS (
                  SELECT 1 FROM scan_run sr
                  WHERE sr.source_id = js.id AND sr.status IN ('queued', 'running')
              );
        $$);
    """)

    op.execute("""
        SELECT cron.schedule('youthchain-reap-stale-scan-runs', '*/10 * * * *', $$
            UPDATE scan_run SET status = 'failed', finished_at = now(),
                error_message = 'Timed out - worker likely died mid-scan (auto-recovered by youthchain-reap-stale-scan-runs)'
            WHERE status = 'running' AND started_at <= now() - interval '30 minutes';
        $$);
    """)


def downgrade():
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("SELECT cron.unschedule('youthchain-queue-due-scans');")
    op.execute("SELECT cron.unschedule('youthchain-reap-stale-scan-runs');")
    # Deliberately NOT `DROP EXTENSION pg_cron` -- other scheduled jobs on
    # the same Postgres cluster may depend on it; unscheduling these two
    # named jobs is a complete, isolated rollback of what this migration
    # itself added.
