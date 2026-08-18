"""
Coverage for scanner/reaper.py's expired-listing purge — the "wiped off
after about a week" half of the deadline feature.
"""
from datetime import date, timedelta

import scanner.reaper as reaper
from conftest import register_user


def _make_source(app_module, name="Careers.sl", url="https://careers.sl/jobs"):
    source = app_module.JobSource(name=name, base_url=url)
    app_module.db.session.add(source)
    app_module.db.session.commit()
    return source


def _make_scraped_job(app_module, source, title="Job", **kwargs):
    job = app_module.Job(
        title=title, location="Freetown", duration="Full-time",
        source="scraped", source_id=source.id, **kwargs,
    )
    app_module.db.session.add(job)
    app_module.db.session.commit()
    return job


def test_deletes_jobs_past_the_grace_period(client):
    import app as app_module

    with app_module.app.app_context():
        source = _make_source(app_module)
        old_job = _make_scraped_job(
            app_module, source, title="Long Expired",
            application_deadline=date.today() - timedelta(days=10),
        )
        deleted = reaper.reap_expired_jobs(app_module.db, app_module.Job, app_module.SavedJob, app_module.ScrapedListingReport, grace_days=7)

        assert deleted == 1
        assert app_module.Job.query.get(old_job.id) is None


def test_does_not_delete_a_job_still_within_the_grace_period(client):
    import app as app_module

    with app_module.app.app_context():
        source = _make_source(app_module)
        recent_job = _make_scraped_job(
            app_module, source, title="Recently Expired",
            application_deadline=date.today() - timedelta(days=2),
        )
        deleted = reaper.reap_expired_jobs(app_module.db, app_module.Job, app_module.SavedJob, app_module.ScrapedListingReport, grace_days=7)

        assert deleted == 0
        assert app_module.Job.query.get(recent_job.id) is not None


def test_never_deletes_a_job_with_no_deadline(client):
    import app as app_module

    with app_module.app.app_context():
        source = _make_source(app_module)
        job = _make_scraped_job(app_module, source, title="No Deadline", application_deadline=None)
        deleted = reaper.reap_expired_jobs(app_module.db, app_module.Job, app_module.SavedJob, app_module.ScrapedListingReport, grace_days=7)

        assert deleted == 0
        assert app_module.Job.query.get(job.id) is not None


def test_never_touches_employer_jobs_even_with_a_deadline_column_populated(client):
    """Defensive: employer-posted jobs never set application_deadline in
    practice (post_job() has no such field), but the reaper's own query
    filters on source == "scraped" explicitly regardless -- this
    confirms that filter, not just the absence of the field."""
    import app as app_module

    with app_module.app.app_context():
        job = app_module.Job(
            title="Employer Job", location="Freetown", duration="Full-time", source="employer",
            application_deadline=date.today() - timedelta(days=100),
        )
        app_module.db.session.add(job)
        app_module.db.session.commit()
        job_id = job.id

        deleted = reaper.reap_expired_jobs(app_module.db, app_module.Job, app_module.SavedJob, app_module.ScrapedListingReport, grace_days=7)

        assert deleted == 0
        assert app_module.Job.query.get(job_id) is not None


def test_deletes_saved_job_rows_for_a_reaped_job_first(client):
    """The real FK-safety concern this function exists to handle: a
    Postgres FK on saved_job.job_id would reject deleting a Job still
    referenced by a SavedJob row."""
    import app as app_module

    user = register_user(client)
    with app_module.app.app_context():
        source = _make_source(app_module)
        job = _make_scraped_job(
            app_module, source, title="Saved And Expired",
            application_deadline=date.today() - timedelta(days=10),
        )
        app_module.db.session.add(app_module.SavedJob(user_id=user["user"]["id"], job_id=job.id))
        app_module.db.session.commit()

        deleted = reaper.reap_expired_jobs(app_module.db, app_module.Job, app_module.SavedJob, app_module.ScrapedListingReport, grace_days=7)

        assert deleted == 1
        assert app_module.Job.query.get(job.id) is None
        assert app_module.SavedJob.query.filter_by(job_id=job.id).count() == 0


def test_nulls_report_job_id_for_a_reaped_job_instead_of_leaving_a_dangling_fk(client):
    """The same FK-safety concern as SavedJob, but for
    ScrapedListingReport -- unlike SavedJob rows (which are simply
    deleted, disposable), a report is nulled rather than deleted so its
    moderation history (who reported what, when, and how it was
    resolved) survives the listing being reaped. job_id is a real FK a
    Postgres deployment enforces even though SQLite dev doesn't, so
    leaving it pointed at a row this function is about to delete would
    fail there even though it silently "works" here."""
    import app as app_module

    user = register_user(client)
    with app_module.app.app_context():
        source = _make_source(app_module)
        job = _make_scraped_job(
            app_module, source, title="Reported And Expired",
            application_deadline=date.today() - timedelta(days=10),
        )
        report = app_module.ScrapedListingReport(
            reporter_user_id=user["user"]["id"], job_id=job.id, category="scam",
        )
        app_module.db.session.add(report)
        app_module.db.session.commit()
        report_id = report.id

        deleted = reaper.reap_expired_jobs(
            app_module.db, app_module.Job, app_module.SavedJob, app_module.ScrapedListingReport, grace_days=7,
        )

        assert deleted == 1
        assert app_module.Job.query.get(job.id) is None
        survived = app_module.ScrapedListingReport.query.get(report_id)
        assert survived is not None
        assert survived.job_id is None


def test_a_job_exactly_at_the_grace_boundary_is_not_yet_deleted(client):
    """grace_days=7 means "more than 7 days past deadline", not "7 days
    or more" -- a job exactly 7 days past its deadline still gets one
    more day."""
    import app as app_module

    with app_module.app.app_context():
        source = _make_source(app_module)
        job = _make_scraped_job(
            app_module, source, title="Exactly At Boundary",
            application_deadline=date.today() - timedelta(days=7),
        )
        deleted = reaper.reap_expired_jobs(app_module.db, app_module.Job, app_module.SavedJob, app_module.ScrapedListingReport, grace_days=7)

        assert deleted == 0
        assert app_module.Job.query.get(job.id) is not None


def test_returns_zero_when_nothing_is_expired(client):
    import app as app_module

    with app_module.app.app_context():
        deleted = reaper.reap_expired_jobs(app_module.db, app_module.Job, app_module.SavedJob, app_module.ScrapedListingReport, grace_days=7)
        assert deleted == 0
