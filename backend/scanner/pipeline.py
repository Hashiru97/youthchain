"""
Orchestrates one scan attempt: Firecrawl scrape -> Claude extraction ->
upsert into Job -> upsert ScrapedCompany -> record the outcome on
ScanRun. Every dependency (db session, model classes, API keys, logger)
is passed in explicitly rather than imported from app.py, so this module
has no circular import and can be unit-tested with fakes (see
backend/tests/test_scanner_pipeline.py).

Never lets a scan failure propagate as an exception to its caller (the
poller loop, the CLI script, or a request handler) — every failure mode
(missing API key, unreachable source, malformed AI output) is caught and
recorded on the owning scan_run.error_message instead, so one bad source
can never take down the poller loop or leave a scan_run stuck without an
explanation.

Two Firecrawl+Claude passes, not one: the first pass scrapes a source's
listing/index page (JobSource.base_url) and extracts every job summary
it can find there — but a listing index generally never carries a job's
full body text or salary, only its own detail page does (confirmed
against real Careers.sl/JobSearch SL data: every real scraped Job had
description="" and salary=None until this backfill pass was added). So
a second pass scrapes each job's own Job.apply_url and re-extracts just
the fields a detail page actually has (see extract_job_detail). It's
per-job try/except'd — one unreachable or malformed detail page must
never fail the whole scan, it just leaves that job with whatever the
listing pass already gave it (usually nothing) and gets retried on the
next scan, same as any other partial failure in this module.

When Firecrawl itself fails on a detail-page scrape, the backfill loop
falls back to fetch_url_plain — a direct HTTP GET from this backend,
bypassing Firecrawl entirely. Confirmed for real, not assumed: 3 of the
Careers.sl detail pages Firecrawl reported total failure on
(SCRAPE_ALL_ENGINES_FAILED, all its own engines tried) loaded in full on
the first try via a plain requests.get from this same machine — whatever
was blocking Firecrawl specifically (its infrastructure's IP
range/reputation, most likely) didn't apply to a direct request. The
fallback has no JS rendering and returns raw HTML instead of clean
markdown, which extract_job_detail's prompt is written to tolerate.

The backfill pass turns one scan from 2 HTTP round trips into roughly
2*N (N = jobs missing a description), which against real Careers.sl
data took ~3 minutes for 10 jobs, including two that hit a transient
Firecrawl 500. Two things follow from that, both deliberate: results are
committed incrementally (after the listing-pass upserts, and again after
each successful backfill) rather than in one commit at the very end, so
an interruption partway through a long backfill pass can't lose work
that already succeeded; and MAX_JOBS_BACKFILLED_PER_SCAN caps how many
detail pages get scraped in a single run, so a source that suddenly
lists hundreds of jobs can't turn one scheduled scan into hundreds of
paid API calls and a run long enough to trip the stale-scan reaper (see
the enable_pg_cron_scan_scheduling migration) — anything past the cap is
simply left for the next scheduled scan, same as any other not-yet-
backfilled job.

"Needs backfill" is a length check, not a plain null check: confirmed
for real that JobSearch SL's own listing page gives every job a short
non-null stub description ("Closing Date: 12 August 2026", 28 chars) --
truthy, so a plain `not description` check silently treated every one
of that source's jobs as already having a description and never
attempted a single backfill for it. Measured against every real
description this pass has actually backfilled so far: stubs top out
around 60 characters, genuine descriptions start at 173+ -- so anything
shorter than MIN_USEFUL_DESCRIPTION_LENGTH is treated the same as no
description at all.
"""
import hashlib
from dataclasses import dataclass, field
from datetime import datetime

from scanner.claude_extractor import ScanExtractionError, extract_job_detail, extract_jobs
from scanner.firecrawl_client import ScanSourceError, fetch_url_plain, scrape_url
from scanner.scam_signals import detect_scam_signals

# See the module docstring's "Needs backfill is a length check" note.
MIN_USEFUL_DESCRIPTION_LENGTH = 100

# See the module docstring's "Two things follow from that" paragraph.
MAX_JOBS_BACKFILLED_PER_SCAN = 40


@dataclass
class ScanOutcome:
    success: bool
    jobs_found: int
    jobs_created: int
    jobs_updated: int
    error_message: str | None = None
    # How many jobs got a second, per-job scrape of their own apply_url to
    # backfill description/salary (see the module docstring's "why a
    # second pass" note) — 0 whenever nothing needed it, never counted as
    # a scan failure on its own since a listing-page-only result is still
    # a real, usable Job row.
    jobs_backfilled: int = 0
    # IDs of Job rows genuinely created this scan (not just updated) —
    # lets a caller (see app._dispatch_job_alerts_for_scan) fire Saved
    # Search / profile-skill job alerts against only brand-new listings.
    # Deliberately not "every job touched this scan": a Job's external_id
    # already makes the upsert loop below create each real listing only
    # once, so alerting on creation alone is what keeps alert dispatch
    # naturally idempotent across rescans without a separate seen/
    # notified tracking table.
    created_job_ids: list[int] = field(default_factory=list)


def _content_hash(job_data: dict) -> str:
    """
    Fallback de-dup key when Claude doesn't identify a stable per-listing
    URL for a job (external_id). Not cryptographically meaningful — just
    stable across re-scans of the same unchanged listing so it doesn't
    get re-created as a duplicate every scan interval.
    """
    basis = f"{job_data.get('title', '')}|{job_data.get('company_name', '')}|{job_data.get('location', '')}"
    return "hash:" + hashlib.sha256(basis.encode("utf-8")).hexdigest()


def _prefer_fuller_text(new_value: str | None, existing_value: str | None) -> str | None:
    """
    Prefers whichever of the two is longer. Used for the description
    merge on rescan so a listing-page stub (JobSearch SL gives every job
    a short non-null "Closing Date: X" line — see the module docstring)
    can never clobber a previously backfilled real description, while a
    genuinely better/longer value replacing a shorter one still wins.
    """
    new_value = new_value or ""
    existing_value = existing_value or ""
    return new_value if len(new_value) > len(existing_value) else (existing_value or None)


def _parse_deadline(raw: str | None):
    """
    Parses the "YYYY-MM-DD" string extract_jobs/extract_job_detail's own
    prompts ask Claude for into a real date for Job.application_deadline.
    Never raises — an unparseable or missing value (a response that
    ignores the requested format, or a listing that genuinely states no
    deadline) just means "don't know", the same as any other optional
    field a source didn't state, not a scan failure worth recording an
    error over.
    """
    if not raw:
        return None
    try:
        return datetime.strptime(raw.strip(), "%Y-%m-%d").date()
    except (ValueError, AttributeError):
        return None


def _apply_scam_signals(job_row) -> None:
    """
    Recomputes Job.scam_signals from whatever's currently on job_row --
    called after the listing pass sets/updates title/description/salary/
    apply_url, and again after the backfill loop below changes any of
    those same fields, so the stored signals always reflect the fullest
    text available rather than freezing whatever the listing pass alone
    saw. See scanner.scam_signals.detect_scam_signals's own docstring
    for what it actually checks.
    """
    signals = detect_scam_signals(job_row.title, job_row.description, job_row.salary, job_row.apply_url)
    job_row.scam_signals = ",".join(signals) if signals else None


def _upsert_scraped_company(company_name: str, db, ScrapedCompany) -> None:
    name = company_name.strip()
    if not name:
        return
    existing = ScrapedCompany.query.filter(db.func.lower(ScrapedCompany.name) == name.lower()).first()
    now = datetime.utcnow()
    if existing:
        existing.last_seen_at = now
    else:
        db.session.add(ScrapedCompany(name=name, verified=False, first_seen_at=now, last_seen_at=now))


def run_scan_for_source(
    source_id: int,
    db,
    Job,
    JobSource,
    ScanRun,
    ScrapedCompany,
    firecrawl_key: str | None,
    anthropic_key: str | None,
    logger,
    scan_run_id: int | None = None,
) -> ScanOutcome:
    """
    Runs (or resumes, if scan_run_id points at an already-claimed row) one
    scan attempt for job_source `source_id`. When scan_run_id is omitted
    (the CLI script's direct-run path), creates its own ScanRun row so
    every scan attempt — scheduled, manually triggered, or CLI-run — has
    a real record.
    """
    source = JobSource.query.get(source_id)

    if scan_run_id is not None:
        scan_run = ScanRun.query.get(scan_run_id)
        if scan_run is None:
            # Not reachable via the poller today (it claims a row and
            # passes that same, now-committed id straight through), but
            # this function is a public entry point other callers could
            # misuse with a stale/invalid id -- fail loudly and return,
            # rather than crash on `scan_run.status = ...` below with
            # nothing to attach the error to.
            message = f"scan_run {scan_run_id} not found"
            logger.error("scanner.pipeline: %s", message)
            return ScanOutcome(success=False, jobs_found=0, jobs_created=0, jobs_updated=0, error_message=message)
        if source is None:
            message = f"job_source {source_id} not found"
            scan_run.status = "failed"
            scan_run.finished_at = datetime.utcnow()
            scan_run.error_message = message
            db.session.commit()
            logger.error("scanner.pipeline: %s", message)
            return ScanOutcome(success=False, jobs_found=0, jobs_created=0, jobs_updated=0, error_message=message)
    else:
        # No existing scan_run row to attach a failure to yet -- and
        # scan_run.source_id is a real NOT NULL foreign key (enforced for
        # real on Postgres, the actual production database; SQLite does
        # NOT enforce it by default, which is exactly how this bug
        # shipped past every SQLite-backed test before it was caught
        # against a real Postgres container: INSERTing a scan_run row
        # for a source_id that doesn't exist raises IntegrityError, not
        # a graceful failure). So: validate the source exists BEFORE
        # ever creating a row that references it, not after.
        if source is None:
            message = f"job_source {source_id} not found"
            logger.error("scanner.pipeline: %s", message)
            return ScanOutcome(success=False, jobs_found=0, jobs_created=0, jobs_updated=0, error_message=message)
        scan_run = ScanRun(source_id=source_id, status="running", trigger="manual", started_at=datetime.utcnow())
        db.session.add(scan_run)
        db.session.commit()

    scan_run.status = "running"
    if scan_run.started_at is None:
        scan_run.started_at = datetime.utcnow()
    source.last_scan_started_at = datetime.utcnow()
    db.session.commit()

    try:
        markdown = scrape_url(source.base_url, firecrawl_key)
        jobs_data = extract_jobs(markdown, anthropic_key, logger=logger)
    except (ScanSourceError, ScanExtractionError) as e:
        scan_run.status = "failed"
        scan_run.finished_at = datetime.utcnow()
        scan_run.error_message = str(e)
        db.session.commit()
        logger.warning("scanner.pipeline: scan of job_source %s (%s) failed: %s", source.id, source.name, e)
        return ScanOutcome(success=False, jobs_found=0, jobs_created=0, jobs_updated=0, error_message=str(e))

    created = 0
    updated = 0
    now = datetime.utcnow()
    # Jobs whose apply_url is worth a second, per-job scrape to backfill
    # description/salary — anything without a *useful* description yet
    # (see MIN_USEFUL_DESCRIPTION_LENGTH), whether brand new or an
    # existing row a previous scan's backfill attempt never
    # reached/succeeded for, or only ever got a listing-page stub.
    to_backfill: list = []
    # Populated below only in the "not existing" branch -- see
    # ScanOutcome.created_job_ids' own docstring for why creation-only.
    new_job_rows: list = []

    for job_data in jobs_data:
        external_id = job_data.get("external_id") or _content_hash(job_data)
        existing = Job.query.filter_by(source_id=source.id, external_id=external_id).first()

        if existing:
            existing.title = job_data["title"]
            existing.location = job_data.get("location") or existing.location
            existing.company_name = job_data.get("company_name")
            # Prefer whatever's already there over the listing pass's
            # (usually empty) value -- a plain unconditional overwrite
            # here would wipe out a previous scan's successful backfill
            # every time the source is rescanned, since the listing page
            # itself essentially never carries these two fields.
            existing.salary = job_data.get("salary") or existing.salary
            # Longer-wins, not just truthy-wins: a plain `or` here would
            # let JobSearch SL's own short "Closing Date: X" stub
            # (truthy!) clobber a real, already-backfilled description
            # right back down to a stub on every single rescan.
            existing.description = _prefer_fuller_text(job_data.get("description"), existing.description)
            existing.apply_url = job_data.get("apply_url") or existing.apply_url
            existing.employment_type = job_data.get("employment_type") or existing.employment_type
            existing.application_deadline = _parse_deadline(job_data.get("deadline")) or existing.application_deadline
            existing.required_skills = job_data.get("required_skills") or existing.required_skills
            existing.scraped_at = now
            updated += 1
            job_row = existing
        else:
            job_row = Job(
                title=job_data["title"],
                # location/duration are NOT NULL on Job (pre-dating this
                # feature) — scraped listings don't always state either,
                # so a plain placeholder keeps the insert valid without
                # inventing data the source didn't actually provide.
                location=job_data.get("location") or "Not specified",
                duration="Not specified",
                required_skills=job_data.get("required_skills"),
                job_type="formal",
                category=None,
                employer_id=None,
                source="scraped",
                source_id=source.id,
                company_name=job_data.get("company_name"),
                description=job_data.get("description"),
                salary=job_data.get("salary"),
                apply_url=job_data.get("apply_url"),
                employment_type=job_data.get("employment_type"),
                application_deadline=_parse_deadline(job_data.get("deadline")),
                external_id=external_id,
                scraped_at=now,
            )
            db.session.add(job_row)
            created += 1
            new_job_rows.append(job_row)

        _apply_scam_signals(job_row)

        if job_data.get("company_name"):
            _upsert_scraped_company(job_data["company_name"], db, ScrapedCompany)

        if job_row.apply_url and len(job_row.description or "") < MIN_USEFUL_DESCRIPTION_LENGTH:
            to_backfill.append(job_row)

    # Persist the listing-pass results now, before starting the
    # potentially minutes-long, network-bound backfill loop below --
    # see the module docstring. Doesn't touch scan_run.status (still
    # "running" until the very end) so a mid-backfill interruption still
    # leaves it recoverable by the stale-scan reaper rather than stuck
    # looking "success" with counts that don't match what actually ran.
    db.session.commit()
    # IDs only exist post-commit (autoincrement PKs aren't assigned until
    # flush) -- read them back now, before the backfill loop below can
    # touch these same rows further.
    created_job_ids = [j.id for j in new_job_rows]

    if len(to_backfill) > MAX_JOBS_BACKFILLED_PER_SCAN:
        logger.warning(
            "scanner.pipeline: job_source %s (%s) has %d jobs needing backfill, capping this scan to %d — "
            "the rest will be picked up on a later scan",
            source.id, source.name, len(to_backfill), MAX_JOBS_BACKFILLED_PER_SCAN,
        )
        to_backfill = to_backfill[:MAX_JOBS_BACKFILLED_PER_SCAN]

    backfilled = 0
    for job_row in to_backfill:
        try:
            detail_content = scrape_url(job_row.apply_url, firecrawl_key)
        except ScanSourceError as firecrawl_error:
            # Confirmed for real against 3 Careers.sl detail pages that
            # Firecrawl reported total failure (SCRAPE_ALL_ENGINES_FAILED)
            # on: a plain direct GET from this backend loaded every one
            # of them in full on the first try. Whatever's blocking
            # Firecrawl there doesn't apply to a direct request, so it's
            # worth one before giving up on the job entirely.
            try:
                detail_content = fetch_url_plain(job_row.apply_url)
                logger.info(
                    "scanner.pipeline: Firecrawl failed for %r (%s), direct fetch fallback succeeded",
                    job_row.title, job_row.apply_url,
                )
            except ScanSourceError:
                logger.warning(
                    "scanner.pipeline: detail-page backfill failed for %r (%s): %s",
                    job_row.title, job_row.apply_url, firecrawl_error,
                )
                continue

        try:
            detail = extract_job_detail(detail_content, anthropic_key, logger=logger)
        except ScanExtractionError as e:
            logger.warning(
                "scanner.pipeline: detail-page backfill failed for %r (%s): %s",
                job_row.title, job_row.apply_url, e,
            )
            continue
        # Confirmed for real: a technically-successful scrape+extract
        # doesn't guarantee any of these came back non-null -- some real
        # Careers.sl listings' own detail pages genuinely have no body
        # text beyond a title/contact line, and extract_job_detail's
        # prompt correctly returns null rather than inventing one. Only
        # count it as backfilled when a field actually gained real data,
        # so the metric means "got new information", not just "made an
        # HTTP request that didn't error".
        got_new_data = False
        if detail.get("description"):
            job_row.description = detail["description"]
            got_new_data = True
        if detail.get("salary"):
            job_row.salary = detail["salary"]
            got_new_data = True
        if detail.get("employment_type") and not job_row.employment_type:
            job_row.employment_type = detail["employment_type"]
            got_new_data = True
        if detail.get("location") and job_row.location == "Not specified":
            job_row.location = detail["location"]
            got_new_data = True
        if detail.get("deadline") and not job_row.application_deadline:
            parsed_deadline = _parse_deadline(detail["deadline"])
            if parsed_deadline:
                job_row.application_deadline = parsed_deadline
                got_new_data = True
        if detail.get("required_skills") and not job_row.required_skills:
            job_row.required_skills = detail["required_skills"]
            got_new_data = True
        if got_new_data:
            backfilled += 1
            # Re-run now, not just after the listing pass above -- the
            # detail page is exactly where a fee demand or vague pay is
            # most likely to actually show up (a listing index rarely
            # carries a listing's full body text at all, see this
            # module's own "Two Firecrawl+Claude passes" docstring).
            _apply_scam_signals(job_row)
        # Commit after every attempt, not just successful ones --
        # this loop is the slow, network-bound part of a scan (see the
        # module docstring); losing every already-completed backfill to
        # one later interruption would be a much worse outcome than a
        # few extra commits.
        db.session.commit()

    scan_run.status = "success"
    scan_run.finished_at = datetime.utcnow()
    scan_run.jobs_found = len(jobs_data)
    scan_run.jobs_created = created
    scan_run.jobs_updated = updated
    scan_run.jobs_backfilled = backfilled
    db.session.commit()

    logger.info(
        "scanner.pipeline: job_source %s (%s) scan complete — found=%d created=%d updated=%d backfilled=%d",
        source.id, source.name, len(jobs_data), created, updated, backfilled,
    )
    return ScanOutcome(
        success=True, jobs_found=len(jobs_data), jobs_created=created, jobs_updated=updated, jobs_backfilled=backfilled,
        created_job_ids=created_job_ids,
    )
