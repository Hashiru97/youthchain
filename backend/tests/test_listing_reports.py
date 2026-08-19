"""
Coverage for POST /api/report_listing (the Discover-feed counterpart to
POST /api/report_employer) and its admin review page — a scraped
listing has no real Employer account to report against, so this is a
deliberately separate path (see ScrapedListingReport's own docstring).
"""
import re

from conftest import register_user, auth_headers


def _make_scraped_job(app_module, title="Scraped Job", **kwargs):
    with app_module.app.app_context():
        source = app_module.JobSource(name="Careers.sl", base_url="https://careers.sl")
        app_module.db.session.add(source)
        app_module.db.session.commit()
        job = app_module.Job(
            title=title, location="Freetown", duration="Full-time",
            source="scraped", source_id=source.id, **kwargs,
        )
        app_module.db.session.add(job)
        app_module.db.session.commit()
        return job.id


def _make_employer_job(app_module, title="Employer Job"):
    with app_module.app.app_context():
        job = app_module.Job(title=title, location="Freetown", duration="Full-time", source="employer")
        app_module.db.session.add(job)
        app_module.db.session.commit()
        return job.id


def _create_admin(app_module, email="ops@youthchain.test", password="adminpass123"):
    with app_module.app.app_context():
        admin = app_module.Admin(
            name="Ops", email=email, role="admin", active=True,
            password_hash=app_module.generate_password_hash(password),
        )
        app_module.db.session.add(admin)
        app_module.db.session.commit()
    return email, password


def _csrf_token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "csrf token not found in page"
    return match.group(1)


def _login_admin(client, email, password):
    page = client.get("/admin/login")
    token = _csrf_token(page.get_data(as_text=True))
    return client.post("/admin/login", data={"csrf_token": token, "email": email, "password": password})


def test_report_listing_succeeds_for_a_scraped_job(client):
    import app as app_module

    job_id = _make_scraped_job(app_module)
    user = register_user(client)
    resp = client.post(
        "/api/report_listing",
        json={"job_id": job_id, "category": "scam", "details": "Asked for a registration fee upfront"},
        headers=auth_headers(user["access_token"]),
    )
    assert resp.status_code == 201

    with app_module.app.app_context():
        report = app_module.ScrapedListingReport.query.first()
        assert report is not None
        assert report.job_id == job_id
        assert report.category == "scam"
        assert report.status == "open"


def test_report_listing_rejects_an_employer_job(client):
    """An employer job already has its own report path
    (/api/report_employer) -- this one must not become a second,
    less-informative way to report the same thing."""
    import app as app_module

    job_id = _make_employer_job(app_module)
    user = register_user(client)
    resp = client.post(
        "/api/report_listing",
        json={"job_id": job_id, "category": "scam"},
        headers=auth_headers(user["access_token"]),
    )
    assert resp.status_code == 400
    assert "report_employer" in resp.get_json()["error"]


def test_report_listing_404s_for_a_nonexistent_job(client):
    user = register_user(client)
    resp = client.post(
        "/api/report_listing",
        json={"job_id": 999999, "category": "scam"},
        headers=auth_headers(user["access_token"]),
    )
    assert resp.status_code == 404


def test_report_listing_requires_a_valid_category(client):
    import app as app_module

    job_id = _make_scraped_job(app_module)
    user = register_user(client)
    resp = client.post(
        "/api/report_listing",
        json={"job_id": job_id, "category": "not_a_real_category"},
        headers=auth_headers(user["access_token"]),
    )
    assert resp.status_code == 400


def test_report_listing_requires_auth(client):
    import app as app_module

    job_id = _make_scraped_job(app_module)
    resp = client.post("/api/report_listing", json={"job_id": job_id, "category": "scam"})
    assert resp.status_code == 401


def test_report_listing_is_rate_limited(client):
    """Shares the same report: rate-limit bucket as /api/report_employer
    (both call _report_rate_limited(f"user:{user_id}")) -- deliberate,
    not a separate/looser budget per report type."""
    import app as app_module

    job_id = _make_scraped_job(app_module)
    user = register_user(client)
    headers = auth_headers(user["access_token"])

    for _ in range(5):
        resp = client.post("/api/report_listing", json={"job_id": job_id, "category": "scam"}, headers=headers)
        assert resp.status_code == 201

    resp = client.post("/api/report_listing", json={"job_id": job_id, "category": "scam"}, headers=headers)
    assert resp.status_code == 429


def test_admin_listing_reports_page_shows_an_open_report(client):
    import app as app_module

    job_id = _make_scraped_job(app_module, title="Suspicious Listing")
    user = register_user(client)
    client.post(
        "/api/report_listing",
        json={"job_id": job_id, "category": "scam", "details": "Suspicious"},
        headers=auth_headers(user["access_token"]),
    )

    email, password = _create_admin(app_module)
    _login_admin(client, email, password)
    resp = client.get("/admin/listing_reports")
    html = resp.get_data(as_text=True)
    assert "Suspicious Listing" in html
    assert "Suspicious" in html


def test_admin_can_dismiss_a_listing_report(client):
    import app as app_module

    job_id = _make_scraped_job(app_module)
    user = register_user(client)
    client.post("/api/report_listing", json={"job_id": job_id, "category": "scam"}, headers=auth_headers(user["access_token"]))

    with app_module.app.app_context():
        report_id = app_module.ScrapedListingReport.query.first().id

    email, password = _create_admin(app_module)
    _login_admin(client, email, password)
    page = client.get("/admin/listing_reports")
    token = _csrf_token(page.get_data(as_text=True))
    resp = client.post(
        f"/admin/listing_reports/{report_id}/resolve",
        data={"csrf_token": token, "decision": "dismiss"},
    )
    assert resp.status_code == 302

    with app_module.app.app_context():
        report = app_module.ScrapedListingReport.query.get(report_id)
        assert report.status == "dismissed"
        # The job itself must still exist -- dismissing is not removal.
        assert app_module.Job.query.get(job_id) is not None


def test_reviewed_listing_report_shows_which_admin_resolved_it(client):
    import app as app_module

    job_id = _make_scraped_job(app_module)
    user = register_user(client)
    client.post("/api/report_listing", json={"job_id": job_id, "category": "scam"}, headers=auth_headers(user["access_token"]))

    with app_module.app.app_context():
        report_id = app_module.ScrapedListingReport.query.first().id

    email, password = _create_admin(app_module)
    _login_admin(client, email, password)
    page = client.get("/admin/listing_reports")
    token = _csrf_token(page.get_data(as_text=True))
    client.post(
        f"/admin/listing_reports/{report_id}/resolve",
        data={"csrf_token": token, "decision": "dismiss"},
    )

    page = client.get("/admin/listing_reports")
    assert "Ops" in page.get_data(as_text=True)  # _create_admin's fixed admin name


def test_admin_listing_reports_open_queue_is_paginated(client):
    import app as app_module

    email, password = _create_admin(app_module)
    _login_admin(client, email, password)

    with app_module.app.app_context():
        reporter = app_module.User(name="Reporter", email="reporter@test.com", phone="000", password_hash="x")
        app_module.db.session.add(reporter)
        app_module.db.session.commit()
        for i in range(30):
            app_module.db.session.add(app_module.ScrapedListingReport(
                reporter_user_id=reporter.id, job_id=None, category="other",
                status="open", created_at=app_module.datetime.utcnow(),
            ))
        app_module.db.session.commit()

    page1 = client.get("/admin/listing_reports")
    body1 = page1.get_data(as_text=True)
    assert "Open (30)" in body1
    assert "Next" in body1

    page2 = client.get("/admin/listing_reports?offset=25")
    body2 = page2.get_data(as_text=True)
    assert "Open (30)" in body2
    assert "Previous" in body2
    assert "Next" not in body2


def test_admin_removing_a_listing_deletes_the_job_and_any_saved_copies(client):
    """Real FK-safety concern: a Postgres FK on saved_job.job_id would
    reject deleting a Job that's still referenced by a SavedJob row --
    the resolve route must clear those first, same reasoning
    scanner.reaper's own purge has to apply."""
    import app as app_module

    job_id = _make_scraped_job(app_module, title="Scam Listing")
    user = register_user(client)
    headers = auth_headers(user["access_token"])
    client.post(f"/api/jobs/{job_id}/save", headers=headers)
    client.post("/api/report_listing", json={"job_id": job_id, "category": "scam"}, headers=headers)

    with app_module.app.app_context():
        report_id = app_module.ScrapedListingReport.query.first().id
        assert app_module.SavedJob.query.filter_by(job_id=job_id).count() == 1

    email, password = _create_admin(app_module)
    _login_admin(client, email, password)
    page = client.get("/admin/listing_reports")
    token = _csrf_token(page.get_data(as_text=True))
    resp = client.post(
        f"/admin/listing_reports/{report_id}/resolve",
        data={"csrf_token": token, "decision": "remove_listing"},
    )
    assert resp.status_code == 302

    with app_module.app.app_context():
        assert app_module.Job.query.get(job_id) is None
        assert app_module.SavedJob.query.filter_by(job_id=job_id).count() == 0
        # The report itself survives the job it was filed against being
        # deleted -- job_id is a real FK (nullable, unlike
        # saved_job.job_id, precisely so this row can outlive the job)
        # that a real Postgres deployment enforces, so it must be nulled
        # rather than left dangling.
        report = app_module.ScrapedListingReport.query.get(report_id)
        assert report.status == "actioned"
        assert report.job_id is None

    # And the page itself must still render -- this is the template
    # code path (jobs.get(report.job_id) with job_id now None) that a
    # dangling FK would never have let us reach at all.
    reviewed_page = client.get("/admin/listing_reports").get_data(as_text=True)
    assert "Listing removed" in reviewed_page


def test_admin_removing_a_listing_with_multiple_open_reports_nulls_all_of_them(client):
    """A job can be reported more than once (see the "repeatedly-
    reported" sort in admin_listing_reports()) -- resolving ONE of those
    reports as remove_listing must not leave the OTHER report(s) on the
    same job still pointing at a job_id that no longer exists."""
    import app as app_module

    job_id = _make_scraped_job(app_module, title="Repeatedly Reported")
    user_a = register_user(client, email="reporter-a@test.com", phone="222")
    user_b = register_user(client, email="reporter-b@test.com", phone="333")
    client.post(
        "/api/report_listing", json={"job_id": job_id, "category": "scam"},
        headers=auth_headers(user_a["access_token"]),
    )
    client.post(
        "/api/report_listing", json={"job_id": job_id, "category": "fake_job"},
        headers=auth_headers(user_b["access_token"]),
    )

    with app_module.app.app_context():
        reports = app_module.ScrapedListingReport.query.filter_by(job_id=job_id).all()
        assert len(reports) == 2
        first_report_id, second_report_id = reports[0].id, reports[1].id

    email, password = _create_admin(app_module)
    _login_admin(client, email, password)
    page = client.get("/admin/listing_reports")
    token = _csrf_token(page.get_data(as_text=True))
    resp = client.post(
        f"/admin/listing_reports/{first_report_id}/resolve",
        data={"csrf_token": token, "decision": "remove_listing"},
    )
    assert resp.status_code == 302

    with app_module.app.app_context():
        assert app_module.Job.query.get(job_id) is None
        first = app_module.ScrapedListingReport.query.get(first_report_id)
        second = app_module.ScrapedListingReport.query.get(second_report_id)
        assert first.status == "actioned" and first.job_id is None
        # Not resolved via this action, but still must not be left
        # dangling -- this is the row that would trip a Postgres FK
        # violation on the Job delete if it were missed.
        assert second.status == "open" and second.job_id is None


def test_admin_listing_reports_requires_admin_role_not_verifier(client):
    import app as app_module

    with app_module.app.app_context():
        admin = app_module.Admin(
            name="Verifier", email="verifier@youthchain.test", role="verifier", active=True,
            password_hash=app_module.generate_password_hash("adminpass123"),
        )
        app_module.db.session.add(admin)
        app_module.db.session.commit()

    _login_admin(client, "verifier@youthchain.test", "adminpass123")
    resp = client.get("/admin/listing_reports")
    assert resp.status_code == 403
