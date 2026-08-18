"""
Coverage for scanner/pipeline.py — the core scrape -> extract -> upsert
orchestration. No real Firecrawl/Anthropic account is used: the client
functions are monkeypatched at the scanner.pipeline module (where
pipeline.py imported them into its own namespace, not where they're
defined — the standard Python monkeypatch gotcha), same convention
test_push_sms_providers.py already uses for Firebase/Twilio. The
db/model/upsert logic itself is exercised for real against the test
database.
"""
from datetime import date

import scanner.pipeline as pipeline
from scanner.claude_extractor import ScanExtractionError
from scanner.firecrawl_client import ScanSourceError


def _make_source(app_module, name="Careers.sl", url="https://careers.sl/jobs"):
    source = app_module.JobSource(name=name, base_url=url)
    app_module.db.session.add(source)
    app_module.db.session.commit()
    return source


def _run(app_module, source_id, **kwargs):
    return pipeline.run_scan_for_source(
        source_id=source_id,
        db=app_module.db,
        Job=app_module.Job,
        JobSource=app_module.JobSource,
        ScanRun=app_module.ScanRun,
        ScrapedCompany=app_module.ScrapedCompany,
        firecrawl_key="fake-firecrawl-key",
        anthropic_key="fake-anthropic-key",
        logger=app_module.logger,
        **kwargs,
    )


def test_happy_path_creates_jobs_and_records_success(client, monkeypatch):
    import app as app_module

    with app_module.app.app_context():
        source = _make_source(app_module)

        monkeypatch.setattr(pipeline, "scrape_url", lambda url, key: "# fake markdown")
        monkeypatch.setattr(
            pipeline,
            "extract_jobs",
            lambda markdown, key, logger=None: [
                {
                    "title": "Junior Developer",
                    "company_name": "Acme SL",
                    "location": "Freetown",
                    "salary": "Le 2,000,000/month",
                    "description": "Build things.",
                    "apply_url": "https://careers.sl/jobs/123",
                    "external_id": "https://careers.sl/jobs/123",
                    "employment_type": "Full-time",
                },
            ],
        )

        outcome = _run(app_module, source.id)

        assert outcome.success is True
        assert outcome.jobs_found == 1
        assert outcome.jobs_created == 1
        assert outcome.jobs_updated == 0

        job = app_module.Job.query.filter_by(source="scraped").first()
        assert job is not None
        assert job.title == "Junior Developer"
        assert job.company_name == "Acme SL"
        assert job.salary == "Le 2,000,000/month"
        assert job.apply_url == "https://careers.sl/jobs/123"
        assert job.employment_type == "Full-time"
        assert job.source_id == source.id

        company = app_module.ScrapedCompany.query.filter_by(name="Acme SL").first()
        assert company is not None
        assert company.verified is False

        scan_run = app_module.ScanRun.query.filter_by(source_id=source.id).first()
        assert scan_run.status == "success"
        assert scan_run.jobs_created == 1


def test_listing_without_a_description_gets_backfilled_from_its_own_page(client, monkeypatch):
    """The real bug found against real Careers.sl/JobSearch SL data: a
    listing-index page essentially never carries a job's full body text
    or salary, only its own detail page (Job.apply_url) does. A second
    scrape+extract pass must fill those in."""
    import app as app_module

    with app_module.app.app_context():
        source = _make_source(app_module)

        monkeypatch.setattr(pipeline, "scrape_url", lambda url, key: f"# markdown for {url}")
        monkeypatch.setattr(
            pipeline,
            "extract_jobs",
            lambda markdown, key, logger=None: [{
                "title": "Junior Developer", "company_name": "Acme SL", "location": "Freetown",
                "salary": None, "description": None,
                "apply_url": "https://careers.sl/job/junior-developer/",
                "external_id": "https://careers.sl/job/junior-developer/",
                "employment_type": None,
            }],
        )
        monkeypatch.setattr(
            pipeline,
            "extract_job_detail",
            lambda markdown, key, logger=None: {
                "description": "Full responsibilities: build and maintain internal tools.",
                "salary": "Le 3,000,000/month",
                "employment_type": "Full-time",
                "location": "Freetown, Sierra Leone",
            },
        )

        outcome = _run(app_module, source.id)

        assert outcome.success is True
        assert outcome.jobs_backfilled == 1

        job = app_module.Job.query.filter_by(source="scraped").first()
        assert job.description == "Full responsibilities: build and maintain internal tools."

        # Confirmed real gap: the admin scan-history panel showed
        # found/created/updated looking perfectly healthy with no way to
        # tell an admin whether the jobs underneath actually got real
        # bodies -- scan_run.jobs_backfilled is what the panel reads.
        scan_run = app_module.ScanRun.query.filter_by(source_id=source.id).first()
        assert scan_run.jobs_backfilled == 1
        assert job.salary == "Le 3,000,000/month"
        assert job.employment_type == "Full-time"


def test_a_short_listing_page_stub_still_gets_selected_for_backfill(client, monkeypatch):
    """The real bug found on JobSearch SL: its listing page gives every
    job a short but non-null description ("Closing Date: 12 August
    2026", 28 chars) -- a plain `not description` check treats that as
    "already has one" and never attempts backfill at all. A length
    check is required, not a null check."""
    import app as app_module

    with app_module.app.app_context():
        source = _make_source(app_module, name="JobSearch SL", url="https://www.jobsearchsl.com/vacancies")
        monkeypatch.setattr(pipeline, "scrape_url", lambda url, key: "# fake markdown")
        monkeypatch.setattr(
            pipeline,
            "extract_jobs",
            lambda markdown, key, logger=None: [{
                "title": "Accountant", "company_name": None, "location": None,
                "salary": None, "description": "Closing Date: 12 August 2026",
                "apply_url": "https://www.jobsearchsl.com/accountant",
                "external_id": "https://www.jobsearchsl.com/accountant", "employment_type": None,
            }],
        )
        monkeypatch.setattr(
            pipeline,
            "extract_job_detail",
            lambda content, key, logger=None: {
                "description": "A real, full job description with actual responsibilities, requirements, and application instructions.",
                "salary": "Le 4,000,000/month", "employment_type": "Full-time", "location": "Freetown",
            },
        )

        outcome = _run(app_module, source.id)

        assert outcome.jobs_backfilled == 1
        job = app_module.Job.query.filter_by(source="scraped").first()
        assert job.description.startswith("A real, full job description")
        assert job.salary == "Le 4,000,000/month"


def test_rescan_stub_from_listing_page_never_clobbers_a_real_backfilled_description(client, monkeypatch):
    """Companion regression test to the fix above: once a job has a real
    backfilled description, a later rescan's listing pass handing back
    the same short, truthy stub must not win a plain `or` merge and
    overwrite the real text back down to a stub."""
    import app as app_module

    with app_module.app.app_context():
        source = _make_source(app_module, name="JobSearch SL", url="https://www.jobsearchsl.com/vacancies")
        monkeypatch.setattr(pipeline, "scrape_url", lambda url, key: "# fake markdown")

        listing_stub = [{
            "title": "Accountant", "company_name": None, "location": None,
            "salary": None, "description": "Closing Date: 12 August 2026",
            "apply_url": "https://www.jobsearchsl.com/accountant",
            "external_id": "https://www.jobsearchsl.com/accountant", "employment_type": None,
        }]
        monkeypatch.setattr(pipeline, "extract_jobs", lambda markdown, key, logger=None: listing_stub)
        monkeypatch.setattr(
            pipeline,
            "extract_job_detail",
            lambda content, key, logger=None: {
                "description": "A real, full job description with actual responsibilities, requirements, and application instructions.",
                "salary": None, "employment_type": None, "location": None,
            },
        )
        _run(app_module, source.id)
        job = app_module.Job.query.filter_by(source="scraped").first()
        assert job.description.startswith("A real, full job description")

        # Rescan: listing pass still only ever returns the short stub.
        # Must not be re-selected for backfill (already has a useful
        # description) and must not clobber it on the plain field merge.
        def _fail_if_called(*a, **k):
            raise AssertionError("should not re-backfill a job that already has a useful description")

        monkeypatch.setattr(pipeline, "extract_job_detail", _fail_if_called)
        outcome = _run(app_module, source.id)

        assert outcome.jobs_backfilled == 0
        job = app_module.Job.query.filter_by(source="scraped").first()
        assert job.description.startswith("A real, full job description")


def test_rescan_never_wipes_a_previously_backfilled_description(client, monkeypatch):
    """Regression test for the bug the backfill feature would otherwise
    introduce: the listing pass alone never has a description, so a
    plain unconditional overwrite on rescan would erase a prior scan's
    successful backfill every single time the source is rescanned."""
    import app as app_module

    with app_module.app.app_context():
        source = _make_source(app_module)
        monkeypatch.setattr(pipeline, "scrape_url", lambda url, key: "# fake markdown")

        listing_only = [{
            "title": "Junior Developer", "company_name": "Acme SL", "location": "Freetown",
            "salary": None, "description": None,
            "apply_url": "https://careers.sl/job/junior-developer/",
            "external_id": "https://careers.sl/job/junior-developer/",
            "employment_type": None,
        }]
        monkeypatch.setattr(pipeline, "extract_jobs", lambda markdown, key, logger=None: listing_only)
        monkeypatch.setattr(
            pipeline,
            "extract_job_detail",
            lambda markdown, key, logger=None: {
                "description": "Full description from the detail page, including the role's responsibilities, requirements, and how to apply.",
                "salary": "Negotiable",
                "employment_type": "Full-time",
                "location": None,
            },
        )
        _run(app_module, source.id)

        job = app_module.Job.query.filter_by(source="scraped").first()
        assert job.description == "Full description from the detail page, including the role's responsibilities, requirements, and how to apply."

        # Rescan: listing pass still returns nothing for description/
        # salary (as it always does in practice) -- the update branch
        # must preserve what backfill already filled in, and since the
        # job already has a description, it must NOT be selected for a
        # second backfill scrape.
        def _fail_if_called(*a, **k):
            raise AssertionError("should not re-backfill a job that already has a description")

        monkeypatch.setattr(pipeline, "extract_job_detail", _fail_if_called)
        outcome = _run(app_module, source.id)

        assert outcome.jobs_backfilled == 0
        job = app_module.Job.query.filter_by(source="scraped").first()
        assert job.description == "Full description from the detail page, including the role's responsibilities, requirements, and how to apply."
        assert job.salary == "Negotiable"


def test_detail_page_backfill_failure_for_one_job_does_not_fail_the_scan(client, monkeypatch):
    """One job's detail page being unreachable/malformed must not fail
    the whole scan_run -- the listing-page data it already has is still
    a real, usable Job row, same "partial failure isn't total failure"
    contract as the rest of this module."""
    import app as app_module

    with app_module.app.app_context():
        source = _make_source(app_module)
        monkeypatch.setattr(pipeline, "scrape_url", lambda url, key: "# fake markdown")
        monkeypatch.setattr(
            pipeline,
            "extract_jobs",
            lambda markdown, key, logger=None: [{
                "title": "Junior Developer", "company_name": "Acme SL", "location": "Freetown",
                "salary": None, "description": None,
                "apply_url": "https://careers.sl/job/junior-developer/",
                "external_id": "https://careers.sl/job/junior-developer/",
                "employment_type": None,
            }],
        )

        def _raise_detail_error(markdown, key, logger=None):
            raise ScanExtractionError("Claude did not return valid JSON")

        monkeypatch.setattr(pipeline, "extract_job_detail", _raise_detail_error)

        outcome = _run(app_module, source.id)

        assert outcome.success is True
        assert outcome.jobs_created == 1
        assert outcome.jobs_backfilled == 0

        job = app_module.Job.query.filter_by(source="scraped").first()
        assert job is not None
        assert job.description is None

        scan_run = app_module.ScanRun.query.filter_by(source_id=source.id).first()
        assert scan_run.status == "success"


def test_job_with_no_apply_url_is_never_selected_for_backfill(client, monkeypatch):
    """No apply_url means there's nothing to scrape a second time --
    must not crash trying to fetch None as a URL."""
    import app as app_module

    with app_module.app.app_context():
        source = _make_source(app_module)
        monkeypatch.setattr(pipeline, "scrape_url", lambda url, key: "# fake markdown")
        monkeypatch.setattr(
            pipeline,
            "extract_jobs",
            lambda markdown, key, logger=None: [{
                "title": "Junior Developer", "company_name": "Acme SL", "location": "Freetown",
                "salary": None, "description": None, "apply_url": None,
                "external_id": "no-apply-url-job", "employment_type": None,
            }],
        )

        def _fail_if_called(*a, **k):
            raise AssertionError("should not attempt backfill with no apply_url")

        monkeypatch.setattr(pipeline, "extract_job_detail", _fail_if_called)

        outcome = _run(app_module, source.id)
        assert outcome.success is True
        assert outcome.jobs_backfilled == 0


def test_falls_back_to_direct_fetch_when_firecrawl_fails(client, monkeypatch):
    """Confirmed for real against 3 live Careers.sl detail pages (see
    the module docstring): Firecrawl reported total failure on URLs a
    plain direct GET loaded fine. scrape_url failing must not be the
    final word on a job -- fetch_url_plain gets one shot before giving
    up."""
    import app as app_module

    with app_module.app.app_context():
        source = _make_source(app_module)
        monkeypatch.setattr(pipeline, "scrape_url", lambda url, key: "# fake markdown for the index page")
        monkeypatch.setattr(
            pipeline,
            "extract_jobs",
            lambda markdown, key, logger=None: [{
                "title": "Junior Developer", "company_name": None, "location": None,
                "salary": None, "description": None,
                "apply_url": "https://careers.sl/job/junior-developer/",
                "external_id": "https://careers.sl/job/junior-developer/", "employment_type": None,
            }],
        )

        def _scrape_url(url, key):
            if "junior-developer" in url:
                raise ScanSourceError("SCRAPE_ALL_ENGINES_FAILED")
            return "# fake markdown for the index page"

        monkeypatch.setattr(pipeline, "scrape_url", _scrape_url)
        monkeypatch.setattr(pipeline, "fetch_url_plain", lambda url: "<html>raw html for the detail page</html>")
        monkeypatch.setattr(
            pipeline,
            "extract_job_detail",
            lambda content, key, logger=None: {
                "description": "Extracted from the raw-HTML fallback." if "<html>" in content else None,
                "salary": None, "employment_type": None, "location": None,
            },
        )

        outcome = _run(app_module, source.id)

        assert outcome.jobs_backfilled == 1
        job = app_module.Job.query.filter_by(source="scraped").first()
        assert job.description == "Extracted from the raw-HTML fallback."


def test_gives_up_gracefully_when_both_firecrawl_and_direct_fetch_fail(client, monkeypatch):
    import app as app_module

    with app_module.app.app_context():
        source = _make_source(app_module)
        monkeypatch.setattr(pipeline, "scrape_url", lambda url, key: "# fake markdown for the index page")
        monkeypatch.setattr(
            pipeline,
            "extract_jobs",
            lambda markdown, key, logger=None: [{
                "title": "Junior Developer", "company_name": None, "location": None,
                "salary": None, "description": None,
                "apply_url": "https://careers.sl/job/junior-developer/",
                "external_id": "https://careers.sl/job/junior-developer/", "employment_type": None,
            }],
        )

        def _scrape_url(url, key):
            if "junior-developer" in url:
                raise ScanSourceError("SCRAPE_ALL_ENGINES_FAILED")
            return "# fake markdown for the index page"

        monkeypatch.setattr(pipeline, "scrape_url", _scrape_url)

        def _fetch_fail(url):
            raise ScanSourceError("Direct fetch also failed: connection refused")

        monkeypatch.setattr(pipeline, "fetch_url_plain", _fetch_fail)

        outcome = _run(app_module, source.id)

        assert outcome.success is True
        assert outcome.jobs_backfilled == 0
        job = app_module.Job.query.filter_by(source="scraped").first()
        assert job is not None
        assert job.description is None


def test_backfill_counter_does_not_count_a_successful_call_that_found_nothing(client, monkeypatch):
    """Confirmed for real against 2 live Careers.sl listings ('Office
    Admin', 'Office Administration, Finance & Grant Manager'): their own
    detail pages genuinely have no body text beyond a title/contact
    line, so extract_job_detail correctly returns description=None
    rather than inventing one -- that's not a failure, but it's not new
    data either, and jobs_backfilled must reflect that distinction."""
    import app as app_module

    with app_module.app.app_context():
        source = _make_source(app_module)
        monkeypatch.setattr(pipeline, "scrape_url", lambda url, key: "# fake markdown")
        monkeypatch.setattr(
            pipeline,
            "extract_jobs",
            lambda markdown, key, logger=None: [{
                "title": "Office Admin", "company_name": None, "location": None,
                "salary": None, "description": None,
                "apply_url": "https://careers.sl/job/office-admin/",
                "external_id": "https://careers.sl/job/office-admin/", "employment_type": None,
            }],
        )
        monkeypatch.setattr(
            pipeline,
            "extract_job_detail",
            lambda content, key, logger=None: {
                "description": None, "salary": None,
                "employment_type": "Full Time", "location": "Freetown, Sierra Leone",
            },
        )

        outcome = _run(app_module, source.id)

        # employment_type/location were null on the listing pass, so the
        # detail pass's real "Full Time"/"Freetown..." values still count
        # as new data even though description/salary came back null.
        assert outcome.jobs_backfilled == 1
        job = app_module.Job.query.filter_by(source="scraped").first()
        assert job.employment_type == "Full Time"
        assert job.description is None


def test_backfill_counter_excludes_a_call_that_found_absolutely_nothing_new(client, monkeypatch):
    import app as app_module

    with app_module.app.app_context():
        source = _make_source(app_module)
        monkeypatch.setattr(pipeline, "scrape_url", lambda url, key: "# fake markdown")
        monkeypatch.setattr(
            pipeline,
            "extract_jobs",
            lambda markdown, key, logger=None: [{
                "title": "Office Admin", "company_name": None, "location": None,
                "salary": None, "description": None,
                "apply_url": "https://careers.sl/job/office-admin/",
                "external_id": "https://careers.sl/job/office-admin/", "employment_type": "Full Time",
            }],
        )
        monkeypatch.setattr(
            pipeline,
            "extract_job_detail",
            lambda content, key, logger=None: {
                "description": None, "salary": None, "employment_type": None, "location": None,
            },
        )

        outcome = _run(app_module, source.id)

        assert outcome.jobs_backfilled == 0
        job = app_module.Job.query.filter_by(source="scraped").first()
        assert job.description is None
        assert job.employment_type == "Full Time"


def test_backfill_is_capped_per_scan(client, monkeypatch):
    """A source that suddenly lists far more jobs than usual must not
    turn one scheduled scan into an unbounded number of paid Firecrawl/
    Claude calls -- see MAX_JOBS_BACKFILLED_PER_SCAN's docstring. The
    jobs past the cap are still created (listing-pass data is never
    capped, only the backfill pass), just left with no description for
    this run, same as any other not-yet-backfilled job."""
    import app as app_module

    with app_module.app.app_context():
        source = _make_source(app_module)
        monkeypatch.setattr(pipeline, "MAX_JOBS_BACKFILLED_PER_SCAN", 3)
        monkeypatch.setattr(pipeline, "scrape_url", lambda url, key: "# fake markdown")
        monkeypatch.setattr(
            pipeline,
            "extract_jobs",
            lambda markdown, key, logger=None: [
                {
                    "title": f"Job {i}", "company_name": None, "location": None,
                    "salary": None, "description": None,
                    "apply_url": f"https://careers.sl/job/{i}/",
                    "external_id": f"https://careers.sl/job/{i}/", "employment_type": None,
                }
                for i in range(5)
            ],
        )
        monkeypatch.setattr(
            pipeline,
            "extract_job_detail",
            lambda markdown, key, logger=None: {
                "description": "Backfilled.", "salary": None, "employment_type": None, "location": None,
            },
        )

        outcome = _run(app_module, source.id)

        assert outcome.success is True
        assert outcome.jobs_created == 5
        assert outcome.jobs_backfilled == 3

        jobs = app_module.Job.query.filter_by(source="scraped").all()
        assert len(jobs) == 5
        assert sum(1 for j in jobs if j.description) == 3


def test_listing_pass_upserts_survive_an_unexpected_crash_during_backfill(client, monkeypatch):
    """The core crash-safety guarantee this pass exists for: the
    backfill loop is minutes-long and network-bound (real scan against
    careers.sl took ~3 minutes for 10 jobs), so results must be
    committed incrementally rather than in one commit at the very end --
    otherwise an interruption partway through loses even the listing-
    pass upserts that had nothing to do with the failure."""
    import app as app_module

    with app_module.app.app_context():
        source = _make_source(app_module)
        monkeypatch.setattr(pipeline, "scrape_url", lambda url, key: "# fake markdown")
        monkeypatch.setattr(
            pipeline,
            "extract_jobs",
            lambda markdown, key, logger=None: [
                {
                    "title": "Job A", "company_name": None, "location": None,
                    "salary": None, "description": None,
                    "apply_url": "https://careers.sl/job/a/", "external_id": "https://careers.sl/job/a/",
                    "employment_type": None,
                },
                {
                    "title": "Job B", "company_name": None, "location": None,
                    "salary": None, "description": None,
                    "apply_url": "https://careers.sl/job/b/", "external_id": "https://careers.sl/job/b/",
                    "employment_type": None,
                },
            ],
        )

        calls = []

        def _detail(markdown, key, logger=None):
            calls.append(1)
            if len(calls) == 1:
                return {"description": "Backfilled A.", "salary": None, "employment_type": None, "location": None}
            raise RuntimeError("unexpected bug, not a ScanSourceError/ScanExtractionError")

        monkeypatch.setattr(pipeline, "extract_job_detail", _detail)

        try:
            _run(app_module, source.id)
            assert False, "expected the unhandled RuntimeError to propagate"
        except RuntimeError:
            pass

        # Both jobs' listing-pass upserts, AND job A's already-completed
        # backfill, must have survived even though the call as a whole
        # raised on job B.
        job_a = app_module.Job.query.filter_by(title="Job A").first()
        job_b = app_module.Job.query.filter_by(title="Job B").first()
        assert job_a is not None and job_a.description == "Backfilled A."
        assert job_b is not None


def test_rescan_updates_existing_job_instead_of_duplicating(client, monkeypatch):
    import app as app_module

    with app_module.app.app_context():
        source = _make_source(app_module)
        monkeypatch.setattr(pipeline, "scrape_url", lambda url, key: "# fake markdown")

        first_pass = [{
            "title": "Junior Developer", "company_name": "Acme SL", "location": "Freetown",
            "salary": None, "description": None, "apply_url": None,
            "external_id": "https://careers.sl/jobs/123",
        }]
        monkeypatch.setattr(pipeline, "extract_jobs", lambda markdown, key, logger=None: first_pass)
        _run(app_module, source.id)

        second_pass = [{
            "title": "Junior Developer (Updated)", "company_name": "Acme SL", "location": "Freetown",
            "salary": "Negotiable", "description": None, "apply_url": None,
            "external_id": "https://careers.sl/jobs/123",
        }]
        monkeypatch.setattr(pipeline, "extract_jobs", lambda markdown, key, logger=None: second_pass)
        outcome = _run(app_module, source.id)

        assert outcome.jobs_created == 0
        assert outcome.jobs_updated == 1
        all_scraped = app_module.Job.query.filter_by(source="scraped").all()
        assert len(all_scraped) == 1
        assert all_scraped[0].title == "Junior Developer (Updated)"
        assert all_scraped[0].salary == "Negotiable"


def test_scrape_failure_is_recorded_not_raised(client, monkeypatch):
    import app as app_module

    with app_module.app.app_context():
        source = _make_source(app_module)

        def _raise(url, key):
            raise ScanSourceError("Firecrawl rejected the API key (401)")

        monkeypatch.setattr(pipeline, "scrape_url", _raise)

        outcome = _run(app_module, source.id)

        assert outcome.success is False
        assert "401" in outcome.error_message
        scan_run = app_module.ScanRun.query.filter_by(source_id=source.id).first()
        assert scan_run.status == "failed"
        assert "401" in scan_run.error_message
        assert app_module.Job.query.filter_by(source="scraped").count() == 0


def test_extraction_failure_is_recorded_not_raised(client, monkeypatch):
    import app as app_module

    with app_module.app.app_context():
        source = _make_source(app_module)
        monkeypatch.setattr(pipeline, "scrape_url", lambda url, key: "# fake markdown")

        def _raise(markdown, key, logger=None):
            raise ScanExtractionError("Claude did not return valid JSON")

        monkeypatch.setattr(pipeline, "extract_jobs", _raise)

        outcome = _run(app_module, source.id)

        assert outcome.success is False
        scan_run = app_module.ScanRun.query.filter_by(source_id=source.id).first()
        assert scan_run.status == "failed"
        assert "valid JSON" in scan_run.error_message


def test_missing_api_keys_fail_loudly_not_silently(client):
    """No monkeypatch at all -- scrape_url itself raises when api_key is
    falsy (see firecrawl_client.scrape_url), which is exactly the
    "no real key configured yet" case this feature is built to handle
    from day one, not something bolted on later."""
    import app as app_module

    with app_module.app.app_context():
        source = _make_source(app_module)
        outcome = pipeline.run_scan_for_source(
            source_id=source.id,
            db=app_module.db,
            Job=app_module.Job,
            JobSource=app_module.JobSource,
            ScanRun=app_module.ScanRun,
            ScrapedCompany=app_module.ScrapedCompany,
            firecrawl_key=None,
            anthropic_key=None,
            logger=app_module.logger,
        )
        assert outcome.success is False
        assert "FIRECRAWL_API_KEY" in outcome.error_message
        scan_run = app_module.ScanRun.query.filter_by(source_id=source.id).first()
        assert scan_run.status == "failed"


def test_scan_of_unknown_source_id_is_recorded_as_failed(client):
    import app as app_module

    with app_module.app.app_context():
        outcome = pipeline.run_scan_for_source(
            source_id=999999,
            db=app_module.db,
            Job=app_module.Job,
            JobSource=app_module.JobSource,
            ScanRun=app_module.ScanRun,
            ScrapedCompany=app_module.ScrapedCompany,
            firecrawl_key="k",
            anthropic_key="k",
            logger=app_module.logger,
        )
        assert outcome.success is False
        assert "999999" in outcome.error_message


def test_scan_with_stale_scan_run_id_fails_gracefully_not_crash(client):
    """Real bug found on review: when scan_run_id is given but doesn't
    resolve to a row, the function used to fall through to
    `scan_run.status = "running"` on a None and crash with AttributeError.
    Not reachable via the real poller path (it claims a row and passes
    that same, already-committed id straight through) -- but this is a
    public function other callers could hit with a stale id, and it must
    fail loudly, not crash."""
    import app as app_module

    with app_module.app.app_context():
        source = _make_source(app_module)
        outcome = pipeline.run_scan_for_source(
            source_id=source.id,
            db=app_module.db,
            Job=app_module.Job,
            JobSource=app_module.JobSource,
            ScanRun=app_module.ScanRun,
            ScrapedCompany=app_module.ScrapedCompany,
            firecrawl_key="k",
            anthropic_key="k",
            logger=app_module.logger,
            scan_run_id=999999,
        )
        assert outcome.success is False
        assert "999999" in outcome.error_message


def test_a_well_formed_deadline_is_parsed_and_stored(client, monkeypatch):
    import app as app_module

    with app_module.app.app_context():
        source = _make_source(app_module)
        monkeypatch.setattr(pipeline, "scrape_url", lambda url, key: "# fake markdown")
        monkeypatch.setattr(
            pipeline,
            "extract_jobs",
            lambda markdown, key, logger=None: [{
                "title": "Enumerator", "company_name": None, "location": None,
                "salary": None, "description": "A real, full job description well over the hundred character floor.",
                "apply_url": None, "external_id": "job-with-deadline",
                "employment_type": None, "deadline": "2026-08-23",
            }],
        )
        _run(app_module, source.id)

        job = app_module.Job.query.filter_by(source="scraped").first()
        assert job.application_deadline == date(2026, 8, 23)


def test_a_malformed_deadline_is_dropped_not_crashed_on(client, monkeypatch):
    """Claude ignoring the requested YYYY-MM-DD format (e.g. "ASAP",
    "Ongoing", or some other free-text) must not raise -- it just means
    no usable deadline, same as if the source hadn't stated one at
    all."""
    import app as app_module

    with app_module.app.app_context():
        source = _make_source(app_module)
        monkeypatch.setattr(pipeline, "scrape_url", lambda url, key: "# fake markdown")
        monkeypatch.setattr(
            pipeline,
            "extract_jobs",
            lambda markdown, key, logger=None: [{
                "title": "Enumerator", "company_name": None, "location": None,
                "salary": None, "description": "A real, full job description well over the hundred character floor.",
                "apply_url": None, "external_id": "job-with-bad-deadline",
                "employment_type": None, "deadline": "Ongoing",
            }],
        )
        outcome = _run(app_module, source.id)

        assert outcome.success is True
        job = app_module.Job.query.filter_by(source="scraped").first()
        assert job.application_deadline is None


def test_rescan_does_not_clobber_a_real_deadline_with_a_missing_one(client, monkeypatch):
    """Same "prefer real data over a rescan's empty value" reasoning
    already established for description/salary -- a source page that
    stops mentioning a deadline it stated before (or a flaky extraction)
    must not silently erase the one already recorded."""
    import app as app_module

    with app_module.app.app_context():
        source = _make_source(app_module)
        monkeypatch.setattr(pipeline, "scrape_url", lambda url, key: "# fake markdown")

        with_deadline = [{
            "title": "Enumerator", "company_name": None, "location": None,
            "salary": None, "description": "A real, full job description well over the hundred character floor.",
            "apply_url": None, "external_id": "job-rescan-deadline",
            "employment_type": None, "deadline": "2026-08-23",
        }]
        monkeypatch.setattr(pipeline, "extract_jobs", lambda markdown, key, logger=None: with_deadline)
        _run(app_module, source.id)

        without_deadline = [{
            "title": "Enumerator", "company_name": None, "location": None,
            "salary": None, "description": "A real, full job description well over the hundred character floor.",
            "apply_url": None, "external_id": "job-rescan-deadline",
            "employment_type": None, "deadline": None,
        }]
        monkeypatch.setattr(pipeline, "extract_jobs", lambda markdown, key, logger=None: without_deadline)
        _run(app_module, source.id)

        job = app_module.Job.query.filter_by(source="scraped").first()
        assert job.application_deadline == date(2026, 8, 23)


def test_backfill_pass_can_fill_a_deadline_the_listing_pass_missed(client, monkeypatch):
    import app as app_module

    with app_module.app.app_context():
        source = _make_source(app_module)
        monkeypatch.setattr(pipeline, "scrape_url", lambda url, key: "# fake markdown")
        monkeypatch.setattr(
            pipeline,
            "extract_jobs",
            lambda markdown, key, logger=None: [{
                "title": "Enumerator", "company_name": None, "location": None,
                "salary": None, "description": None,
                "apply_url": "https://careers.sl/job/enumerator/",
                "external_id": "https://careers.sl/job/enumerator/",
                "employment_type": None, "deadline": None,
            }],
        )
        monkeypatch.setattr(
            pipeline,
            "extract_job_detail",
            lambda content, key, logger=None: {
                "description": "A real, full job description well over the hundred character floor.",
                "salary": None, "employment_type": None, "location": None, "deadline": "2026-09-01",
            },
        )

        outcome = _run(app_module, source.id)

        assert outcome.jobs_backfilled == 1
        job = app_module.Job.query.filter_by(source="scraped").first()
        assert job.application_deadline == date(2026, 9, 1)
