"""
Regression coverage for the gig/informal-work application lifecycle: a gig
job (Job.job_type == "gig") doesn't require a CV to apply to, but a formal
job must keep requiring one exactly as before -- that regression guard
(test_formal_job_apply_without_cv_is_still_rejected) is the single most
important test in this file, since Application.cv_file being relaxed to
nullable at the DB level could otherwise silently weaken the formal-job
path too if the route-level check ever drifted.
"""
import io
import re

from conftest import register_user, auth_headers, FAKE_PDF_BYTES


def _csrf_token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "csrf token not found in page"
    return match.group(1)


def _create_job(job_type="formal", category=None, title="Test Job"):
    import app as app_module

    with app_module.app.app_context():
        job = app_module.Job(title=title, location="Freetown", duration="1 week", job_type=job_type, category=category)
        app_module.db.session.add(job)
        app_module.db.session.commit()
        return job.id


def _create_scraped_job(title="Scraped Job"):
    import app as app_module

    with app_module.app.app_context():
        source = app_module.JobSource(name="Careers.sl", base_url="https://careers.sl")
        app_module.db.session.add(source)
        app_module.db.session.commit()
        job = app_module.Job(
            title=title, location="Freetown", duration="Full-time",
            source="scraped", source_id=source.id,
        )
        app_module.db.session.add(job)
        app_module.db.session.commit()
        return job.id


def test_gig_job_apply_without_cv_succeeds(client):
    import app as app_module

    job_id = _create_job(job_type="gig", category="Cleaning", title="Cleaner Needed")
    a = register_user(client)

    resp = client.post(
        "/apply",
        data={"job_id": str(job_id)},
        headers=auth_headers(a["access_token"]),
        content_type="multipart/form-data",
    )
    assert resp.status_code == 201

    with app_module.app.app_context():
        application = app_module.Application.query.filter_by(user_id=a["user"]["id"], job_id=job_id).first()
        assert application is not None
        assert application.cv_file is None


def test_formal_job_apply_without_cv_is_still_rejected(client):
    job_id = _create_job(job_type="formal", title="Accountant")
    a = register_user(client)

    resp = client.post(
        "/apply",
        data={"job_id": str(job_id)},
        headers=auth_headers(a["access_token"]),
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400


def test_apply_to_gig_job_with_optional_cv_still_validates_file_type(client):
    job_id = _create_job(job_type="gig", title="Tailor Needed")
    a = register_user(client)

    resp = client.post(
        "/apply",
        data={"job_id": str(job_id), "cv": (io.BytesIO(b"not a real pdf"), "cv.pdf")},
        headers=auth_headers(a["access_token"]),
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400


def test_apply_to_gig_job_with_valid_optional_cv_saves_it(client):
    import app as app_module

    job_id = _create_job(job_type="gig", title="Driver Needed")
    a = register_user(client)

    resp = client.post(
        "/apply",
        data={"job_id": str(job_id), "cv": (io.BytesIO(FAKE_PDF_BYTES), "proof.pdf")},
        headers=auth_headers(a["access_token"]),
        content_type="multipart/form-data",
    )
    assert resp.status_code == 201

    with app_module.app.app_context():
        application = app_module.Application.query.filter_by(user_id=a["user"]["id"], job_id=job_id).first()
        assert application.cv_file is not None


def test_apply_to_nonexistent_job_404s(client):
    a = register_user(client)
    resp = client.post(
        "/apply",
        data={"job_id": "999999"},
        headers=auth_headers(a["access_token"]),
        content_type="multipart/form-data",
    )
    assert resp.status_code == 404


def test_apply_to_a_scraped_discover_listing_is_rejected(client):
    """A scraped (Discover) listing has no employer account behind it to
    receive an Application, and its own "apply" action opens apply_url
    externally (see DiscoverJobDetailScreen) -- it should never be
    possible to create a real Application row against one through this
    route. This also guards a real FK-safety concern: Application.job_id
    is a NOT NULL FK a real Postgres deployment enforces, and unlike
    ScrapedListingReport.job_id, it can never safely be nulled out later
    (you cannot silently drop someone's actual job application), so the
    only safe fix is preventing the row from ever being created."""
    import app as app_module

    job_id = _create_scraped_job()
    a = register_user(client)

    resp = client.post(
        "/apply",
        data={"job_id": str(job_id)},
        headers=auth_headers(a["access_token"]),
        content_type="multipart/form-data",
    )
    assert resp.status_code == 400

    with app_module.app.app_context():
        assert app_module.Application.query.filter_by(job_id=job_id).first() is None


def test_portal_apply_to_gig_job_without_cv_succeeds(client):
    """Same relaxation on the youth web portal's session-authenticated apply route."""
    import app as app_module

    job_id = _create_job(job_type="gig", title="Market Vendor Needed")
    register_user(client, email="portaluser@test.com", phone="222")
    with client.session_transaction() as sess:
        with app_module.app.app_context():
            user = app_module.User.query.filter_by(email="portaluser@test.com").first()
        sess["portal_user_id"] = user.id

    page = client.get(f"/portal/jobs/{job_id}")
    resp = client.post(
        f"/portal/jobs/{job_id}/apply",
        data={"csrf_token": _csrf_token(page.get_data(as_text=True))},
        follow_redirects=False,
    )
    assert resp.status_code == 302

    with app_module.app.app_context():
        application = app_module.Application.query.filter_by(job_id=job_id).first()
        assert application is not None
        assert application.cv_file is None


def test_portal_apply_to_formal_job_without_cv_is_still_rejected(client):
    import app as app_module

    job_id = _create_job(job_type="formal", title="Clerk")
    register_user(client, email="portaluser2@test.com", phone="223")
    with client.session_transaction() as sess:
        with app_module.app.app_context():
            user = app_module.User.query.filter_by(email="portaluser2@test.com").first()
        sess["portal_user_id"] = user.id

    page = client.get(f"/portal/jobs/{job_id}")
    resp = client.post(
        f"/portal/jobs/{job_id}/apply",
        data={"csrf_token": _csrf_token(page.get_data(as_text=True))},
    )
    assert resp.status_code == 200  # re-renders the job detail page with an error, not a redirect

    with app_module.app.app_context():
        assert app_module.Application.query.filter_by(job_id=job_id).first() is None


def test_portal_apply_to_a_scraped_discover_listing_is_rejected(client):
    """Same rejection on the web portal's own apply route -- job IDs are
    shared across employer and scraped rows, so a scraped listing is
    just as directly POSTable to here as an employer one."""
    import app as app_module

    job_id = _create_scraped_job()
    register_user(client, email="portaluser4@test.com", phone="225")
    with client.session_transaction() as sess:
        with app_module.app.app_context():
            user = app_module.User.query.filter_by(email="portaluser4@test.com").first()
        sess["portal_user_id"] = user.id

    page = client.get(f"/portal/jobs/{job_id}")
    resp = client.post(
        f"/portal/jobs/{job_id}/apply",
        data={"csrf_token": _csrf_token(page.get_data(as_text=True))},
    )
    assert resp.status_code == 200  # re-renders with an error, not a redirect

    with app_module.app.app_context():
        assert app_module.Application.query.filter_by(job_id=job_id).first() is None


def test_portal_job_detail_does_not_mark_cv_input_required_for_gig_jobs(client):
    """
    Real bug found and fixed via manual review, not caught by any
    route-level test above: the CV <input> on portal_job_detail.html had a
    hardcoded HTML `required` attribute regardless of job_type. A real
    browser enforces that client-side before the request is ever sent, so
    a gig application without a CV was silently unsubmittable through the
    web portal even though the backend (see the tests above) always
    accepted it fine -- Flask's test client POSTs directly and never
    exercises HTML5 form validation, which is exactly why this needed a
    template-content assertion, not just a route-behavior one.
    """
    import app as app_module

    gig_job_id = _create_job(job_type="gig", title="Gig Job")
    formal_job_id = _create_job(job_type="formal", title="Formal Job")
    register_user(client, email="portaluser3@test.com", phone="224")
    with client.session_transaction() as sess:
        with app_module.app.app_context():
            user = app_module.User.query.filter_by(email="portaluser3@test.com").first()
        sess["portal_user_id"] = user.id

    gig_html = client.get(f"/portal/jobs/{gig_job_id}").get_data(as_text=True)
    cv_input_line = next(line for line in gig_html.splitlines() if 'id="cv"' in line)
    assert "required" not in cv_input_line

    formal_html = client.get(f"/portal/jobs/{formal_job_id}").get_data(as_text=True)
    cv_input_line = next(line for line in formal_html.splitlines() if 'id="cv"' in line)
    assert "required" in cv_input_line
