"""
Auto-deletes scraped (Discover) Job rows once they're REAP_GRACE_DAYS
past their own stated application_deadline -- the "wiped off after
about a week" half of the expired-listings feature. See
_search_jobs()'s expired_only param (app.py) for the other half: a job
moves into GET /api/discover_jobs/old the moment its deadline passes,
and stays visible there for exactly this grace window before this
module deletes it outright.

No pg_cron migration for this one, unlike scan scheduling (see
enable_pg_cron_scan_scheduling) -- that split exists because pg_cron
can only run SQL, and claiming a scan_run needs FOR UPDATE SKIP LOCKED
exclusivity across every worker/replica. A DELETE ... WHERE deadline <
cutoff has neither requirement: it's naturally idempotent (running it
twice, or from every worker at once, does the same safe thing either
way), so scanner.poller's run_forever loop just runs it directly on a
plain timer, identically on SQLite dev and Postgres prod -- no new
scheduling infrastructure needed for something this simple.
"""
from datetime import datetime, timedelta

REAP_GRACE_DAYS = 7


def reap_expired_jobs(db, Job, SavedJob, ScrapedListingReport, grace_days: int = REAP_GRACE_DAYS, now=None) -> int:
    """
    Hard-deletes every scraped Job whose application_deadline is more
    than grace_days in the past. Deletes any SavedJob rows pointing at
    them first, and nulls job_id on any ScrapedListingReport rows
    pointing at them -- both are real FKs (see the saved_job migration
    and ScrapedListingReport's own docstring), which a real Postgres
    deployment enforces even though SQLite dev doesn't, so this must
    never assume the delete would otherwise cascade -- or, for reports,
    that leaving a NOT NULL-violating dangling reference is even an
    option. Reports are nulled rather than deleted so a report's
    moderation history survives the listing it was filed against being
    reaped (admin_listing_reports.html already renders "Listing removed"
    for a report whose job_id is None).

    Returns how many jobs were deleted (0 is the common case — most
    runs, most days, nothing has aged that far past its own deadline
    yet).
    """
    now = now or datetime.utcnow()
    cutoff = (now - timedelta(days=grace_days)).date()

    expired = Job.query.filter(
        Job.source == "scraped",
        Job.application_deadline.isnot(None),
        Job.application_deadline < cutoff,
    ).all()
    if not expired:
        return 0

    job_ids = [j.id for j in expired]
    SavedJob.query.filter(SavedJob.job_id.in_(job_ids)).delete(synchronize_session=False)
    ScrapedListingReport.query.filter(ScrapedListingReport.job_id.in_(job_ids)).update(
        {"job_id": None}, synchronize_session=False
    )
    for job in expired:
        db.session.delete(job)
    db.session.commit()
    return len(expired)
