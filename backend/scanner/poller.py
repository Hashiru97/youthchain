"""
The Python side of "pg_cron owns when, Python owns how" (see the
enable_pg_cron_scan_scheduling migration's docstring for the full
design). pg_cron queues `scan_run` rows; this module claims one at a
time and actually runs it via scanner.pipeline.

claim_next_due_scan_run() uses Postgres's FOR UPDATE SKIP LOCKED — the
standard safe multi-consumer queue claim — so every gunicorn worker in
every backend replica can run the identical loop with zero risk of two
workers claiming the same scan_run, and no extra lock service. No-ops on
SQLite (local dev), since pg_cron itself never queues anything there
either.
"""
import os
import socket
import time

from sqlalchemy import text as sa_text

from scanner.pipeline import run_scan_for_source
from scanner.reaper import reap_expired_jobs


def claim_next_due_scan_run(db):
    """
    Atomically claims the oldest queued scan_run, if any, marking it
    'running' and stamping who claimed it. Returns (scan_run_id,
    source_id) or None if nothing is due right now.
    """
    if db.engine.dialect.name != "postgresql":
        return None

    row = db.session.execute(
        sa_text(
            "UPDATE scan_run SET status='running', started_at=now(), claimed_by=:claimed_by "
            "WHERE id = (SELECT id FROM scan_run WHERE status='queued' "
            "ORDER BY id LIMIT 1 FOR UPDATE SKIP LOCKED) "
            "RETURNING id, source_id"
        ),
        {"claimed_by": f"{socket.gethostname()}:{os.getpid()}"},
    ).fetchone()
    db.session.commit()
    return (row[0], row[1]) if row is not None else None


def run_forever(
    db,
    Job,
    JobSource,
    ScanRun,
    ScrapedCompany,
    SavedJob,
    ScrapedListingReport,
    firecrawl_key_fn,
    anthropic_key_fn,
    logger,
    interval_seconds: int = 30,
    reap_interval_seconds: int = 3600,
):
    """
    Runs until the process exits. Sleeps interval_seconds between claim
    attempts whether or not one was found — deliberately simple fixed
    polling rather than e.g. LISTEN/NOTIFY, since pg_cron only queues new
    work every 5 minutes anyway (see the migration), so sub-second
    latency has no real value here.

    Also reaps expired scraped listings (see scanner.reaper's own
    docstring for why that doesn't need its own pg_cron job the way scan
    scheduling does) — checked every iteration, but only actually run
    once every reap_interval_seconds (default 1 hour), tracked via
    last_reap_at. A plain DELETE query doesn't need pg_cron's exclusivity
    guarantees, but it also doesn't need running every 30 seconds — this
    keeps it off the hot path without adding a second scheduling
    mechanism.

    Any exception from a single iteration (a bug in our own code, not a
    scan failure — those are already caught inside run_scan_for_source)
    is logged and the loop continues, rather than the whole poller
    silently dying and every future scan piling up unclaimed.
    """
    last_reap_at = 0.0
    while True:
        try:
            claimed = claim_next_due_scan_run(db)
            if claimed is not None:
                scan_run_id, source_id = claimed
                run_scan_for_source(
                    source_id=source_id,
                    db=db,
                    Job=Job,
                    JobSource=JobSource,
                    ScanRun=ScanRun,
                    ScrapedCompany=ScrapedCompany,
                    firecrawl_key=firecrawl_key_fn(),
                    anthropic_key=anthropic_key_fn(),
                    logger=logger,
                    scan_run_id=scan_run_id,
                )

            now = time.monotonic()
            if now - last_reap_at >= reap_interval_seconds:
                deleted = reap_expired_jobs(db, Job, SavedJob, ScrapedListingReport)
                if deleted:
                    logger.info("scanner.poller: reaped %d expired scraped job(s)", deleted)
                last_reap_at = now
        except Exception:
            logger.exception("scanner.poller: unexpected error in poll loop, continuing")
            db.session.rollback()
        time.sleep(interval_seconds)
