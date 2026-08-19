"""
Regression coverage for the employer-report feature: the youth-facing half
of the employer-suspension lever (see test_employer.py's suspend/reinstate
tests for the admin-facing half) -- without this, an admin has no real way
to learn a specific employer is behaving badly.
"""
import io
import re

from conftest import register_user, auth_headers, FAKE_PDF_BYTES


def _csrf_token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "csrf token not found in page"
    return match.group(1)


def _register_employer(client, email="acme@test.com", name="Acme", password="password123"):
    page = client.get("/employer/register")
    token = _csrf_token(page.get_data(as_text=True))
    return client.post(
        "/employer/register",
        data={"csrf_token": token, "name": name, "email": email, "password": password},
    )


def _create_admin(email, password="adminpass123", role="admin"):
    import app as app_module

    with app_module.app.app_context():
        admin = app_module.Admin(
            name="Ops",
            email=email,
            role=role,
            active=True,
            password_hash=app_module.generate_password_hash(password),
        )
        app_module.db.session.add(admin)
        app_module.db.session.commit()
    return email, password


def _login_admin(client, email, password):
    page = client.get("/admin/login")
    token = _csrf_token(page.get_data(as_text=True))
    return client.post(
        "/admin/login",
        data={"csrf_token": token, "email": email, "password": password},
    )


def _setup_application(client):
    """Employer posts a job, youth applies. Returns (employer_id, job_id, app_id, youth_token)."""
    import app as app_module

    _register_employer(client)
    page = client.get("/employer/post")
    token = _csrf_token(page.get_data(as_text=True))
    client.post(
        "/employer/post",
        data={"csrf_token": token, "title": "Welder", "location": "Bo", "duration": "6mo"},
    )
    with app_module.app.app_context():
        job = app_module.Job.query.filter_by(title="Welder").first()
        job_id = job.id
        employer_id = job.employer_id

    logout_page = client.get("/employer/post")
    client.post("/employer/logout", data={"csrf_token": _csrf_token(logout_page.get_data(as_text=True))})

    youth = register_user(client, email="youth@test.com", phone="333")
    client.post(
        "/apply",
        data={"job_id": str(job_id), "cv": (io.BytesIO(FAKE_PDF_BYTES), "cv.pdf")},
        headers=auth_headers(youth["access_token"]),
        content_type="multipart/form-data",
    )
    with app_module.app.app_context():
        application = app_module.Application.query.filter_by(user_id=youth["user"]["id"]).first()
        app_id = application.id

    return employer_id, job_id, app_id, youth["access_token"]


def test_youth_can_report_an_employer(client):
    import app as app_module

    employer_id, job_id, _app_id, token = _setup_application(client)

    resp = client.post(
        "/api/report_employer",
        json={"employer_id": employer_id, "job_id": job_id, "category": "fake_job", "details": "This looks scammy"},
        headers=auth_headers(token),
    )
    assert resp.status_code == 201
    report_id = resp.get_json()["report_id"]

    with app_module.app.app_context():
        report = app_module.EmployerReport.query.get(report_id)
        assert report.employer_id == employer_id
        assert report.job_id == job_id
        assert report.category == "fake_job"
        assert report.status == "open"


def test_report_requires_a_valid_category(client):
    employer_id, _job_id, _app_id, token = _setup_application(client)
    resp = client.post(
        "/api/report_employer",
        json={"employer_id": employer_id, "category": "not_a_real_category"},
        headers=auth_headers(token),
    )
    assert resp.status_code == 400


def test_report_rejects_job_id_belonging_to_a_different_employer(client):
    import app as app_module

    employer_id, _job_id, _app_id, token = _setup_application(client)
    with app_module.app.app_context():
        other_job = app_module.Job(title="Unrelated", location="Bo", duration="1mo")
        app_module.db.session.add(other_job)
        app_module.db.session.commit()
        other_job_id = other_job.id

    resp = client.post(
        "/api/report_employer",
        json={"employer_id": employer_id, "job_id": other_job_id, "category": "other"},
        headers=auth_headers(token),
    )
    assert resp.status_code == 400


def test_report_rejects_message_id_from_someone_elses_application(client):
    import app as app_module

    employer_id, job_id, _app_id, _token = _setup_application(client)

    # A second, unrelated youth applies to the same job and gets messaged.
    stranger = register_user(client, email="stranger@test.com", phone="444")
    client.post(
        "/apply",
        data={"job_id": str(job_id), "cv": (io.BytesIO(FAKE_PDF_BYTES), "cv.pdf")},
        headers=auth_headers(stranger["access_token"]),
        content_type="multipart/form-data",
    )
    with app_module.app.app_context():
        stranger_app = app_module.Application.query.filter_by(user_id=stranger["user"]["id"]).first()
        msg = app_module.Message(
            application_id=stranger_app.id,
            sender_type="employer",
            sender_employer_id=employer_id,
            body="hello",
        )
        app_module.db.session.add(msg)
        app_module.db.session.commit()
        msg_id = msg.id

    # A third youth (not the one that message thread belongs to) tries to report it.
    outsider = register_user(client, email="outsider@test.com", phone="555")
    resp = client.post(
        "/api/report_employer",
        json={"employer_id": employer_id, "message_id": msg_id, "category": "harassment"},
        headers=auth_headers(outsider["access_token"]),
    )
    assert resp.status_code == 403


def test_report_rate_limited(client, monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, "_REPORT_MAX_ATTEMPTS", 2)

    employer_id, _job_id, _app_id, token = _setup_application(client)
    for i in range(2):
        resp = client.post(
            "/api/report_employer",
            json={"employer_id": employer_id, "category": "other", "details": f"report {i}"},
            headers=auth_headers(token),
        )
        assert resp.status_code == 201

    third = client.post(
        "/api/report_employer",
        json={"employer_id": employer_id, "category": "other"},
        headers=auth_headers(token),
    )
    assert third.status_code == 429


def test_admin_can_view_and_dismiss_a_report(client):
    import app as app_module

    employer_id, job_id, _app_id, token = _setup_application(client)
    client.post(
        "/api/report_employer",
        json={"employer_id": employer_id, "job_id": job_id, "category": "scam"},
        headers=auth_headers(token),
    )

    admin_email, admin_password = _create_admin("admin@youthchain.test")
    _login_admin(client, admin_email, admin_password)

    page = client.get("/admin/reports")
    assert page.status_code == 200
    assert "Acme" in page.get_data(as_text=True)

    with app_module.app.app_context():
        report = app_module.EmployerReport.query.filter_by(employer_id=employer_id).first()
        report_id = report.id

    dismiss_token = _csrf_token(page.get_data(as_text=True))
    resp = client.post(
        f"/admin/reports/{report_id}/resolve",
        data={"csrf_token": dismiss_token, "decision": "dismiss"},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app_module.app.app_context():
        report = app_module.EmployerReport.query.get(report_id)
        assert report.status == "dismissed"
        assert report.reviewed_by_admin_id is not None
        # Dismissing a report must not suspend the employer.
        employer = app_module.Employer.query.get(employer_id)
        assert employer.active is True


def test_admin_can_suspend_employer_directly_from_a_report(client):
    import app as app_module

    employer_id, job_id, _app_id, token = _setup_application(client)
    client.post(
        "/api/report_employer",
        json={"employer_id": employer_id, "job_id": job_id, "category": "scam"},
        headers=auth_headers(token),
    )

    admin_email, admin_password = _create_admin("admin2@youthchain.test")
    _login_admin(client, admin_email, admin_password)

    page = client.get("/admin/reports")
    with app_module.app.app_context():
        report = app_module.EmployerReport.query.filter_by(employer_id=employer_id).first()
        report_id = report.id

    suspend_token = _csrf_token(page.get_data(as_text=True))
    client.post(
        f"/admin/reports/{report_id}/resolve",
        data={"csrf_token": suspend_token, "decision": "suspend"},
    )

    with app_module.app.app_context():
        report = app_module.EmployerReport.query.get(report_id)
        assert report.status == "actioned"
        employer = app_module.Employer.query.get(employer_id)
        assert employer.active is False


def test_reviewed_report_shows_which_admin_resolved_it(client):
    """Real gap found reviewing this queue: reviewed_by_admin_id was
    always captured on resolve, but no template ever rendered it -- there
    was no way to see, from anywhere in the product, which admin actually
    took a given action."""
    import app as app_module

    employer_id, job_id, _app_id, token = _setup_application(client)
    client.post(
        "/api/report_employer",
        json={"employer_id": employer_id, "job_id": job_id, "category": "scam"},
        headers=auth_headers(token),
    )

    admin_email, admin_password = _create_admin("reviewer@youthchain.test")
    _login_admin(client, admin_email, admin_password)

    page = client.get("/admin/reports")
    with app_module.app.app_context():
        report_id = app_module.EmployerReport.query.filter_by(employer_id=employer_id).first().id

    dismiss_token = _csrf_token(page.get_data(as_text=True))
    client.post(
        f"/admin/reports/{report_id}/resolve",
        data={"csrf_token": dismiss_token, "decision": "dismiss"},
    )

    page = client.get("/admin/reports")
    assert "Ops" in page.get_data(as_text=True)  # _create_admin's fixed admin name


def test_admin_reports_open_queue_is_paginated(client):
    """Real gap found reviewing this queue: the open list rendered every
    row on one page with no cap at all -- fine at a handful of reports,
    a real problem at scale."""
    import app as app_module

    admin_email, admin_password = _create_admin("pageadmin@youthchain.test")
    _login_admin(client, admin_email, admin_password)

    with app_module.app.app_context():
        employer = app_module.Employer(name="Bulk Co", email="bulk@test.com", password_hash="x", active=True)
        app_module.db.session.add(employer)
        app_module.db.session.commit()
        reporter = app_module.User(name="Reporter", email="reporter@test.com", phone="000", password_hash="x")
        app_module.db.session.add(reporter)
        app_module.db.session.commit()
        for i in range(30):
            app_module.db.session.add(app_module.EmployerReport(
                employer_id=employer.id, reporter_user_id=reporter.id, category="other",
                status="open", created_at=app_module.datetime.utcnow(),
            ))
        app_module.db.session.commit()

    page1 = client.get("/admin/reports")
    body1 = page1.get_data(as_text=True)
    assert "Open (30)" in body1
    assert "Next" in body1
    assert "Previous" not in body1

    page2 = client.get("/admin/reports?offset=25")
    body2 = page2.get_data(as_text=True)
    assert "Open (30)" in body2
    assert "Previous" in body2
    assert "Next" not in body2


def test_verifier_role_admin_cannot_reach_reports(client):
    verifier_email, verifier_password = _create_admin("verifier@youthchain.test", role="verifier")
    _login_admin(client, verifier_email, verifier_password)

    resp = client.get("/admin/reports")
    assert resp.status_code == 403


def test_employer_with_multiple_open_reports_is_surfaced_and_sorted_first(client):
    """
    Real gap found and closed after this feature first shipped: reports
    listed in plain chronological order made an employer reported 5 times
    by 5 different youths look like 5 unrelated rows -- the pattern was in
    the data but nothing surfaced it. Now the open queue is sorted by
    report count (most-reported employer first) and each repeat offender's
    row carries a visible count badge.
    """
    import app as app_module

    # ZetaCorp gets reported once, first (so a naive chronological sort
    # would put it above everything reported later). BetaCorp then gets
    # reported twice, by two different youths -- the fix must still put
    # BetaCorp's row first despite being reported later, because it has
    # more open reports against it.
    first_employer_id, first_job_id, _app_id, token_a = _setup_application(client)
    client.post(
        "/api/report_employer",
        json={"employer_id": first_employer_id, "job_id": first_job_id, "category": "other"},
        headers=auth_headers(token_a),
    )
    with app_module.app.app_context():
        first_employer_name = app_module.Employer.query.get(first_employer_id).name

    _register_employer(client, email="betacorp@test.com", name="BetaCorp")
    post_page = client.get("/employer/post")
    client.post("/employer/post", data={
        "csrf_token": _csrf_token(post_page.get_data(as_text=True)),
        "title": "Sales Rep", "location": "Bo", "duration": "3mo",
    })
    with app_module.app.app_context():
        beta_job = app_module.Job.query.filter_by(title="Sales Rep").first()
        beta_employer_id = beta_job.employer_id
        beta_job_id = beta_job.id
    client.post("/employer/logout", data={"csrf_token": _csrf_token(post_page.get_data(as_text=True))})

    youth_b = register_user(client, email="youthb@test.com", phone="777")
    youth_c = register_user(client, email="youthc@test.com", phone="888")
    client.post(
        "/api/report_employer",
        json={"employer_id": beta_employer_id, "job_id": beta_job_id, "category": "scam"},
        headers=auth_headers(youth_b["access_token"]),
    )
    client.post(
        "/api/report_employer",
        json={"employer_id": beta_employer_id, "job_id": beta_job_id, "category": "fake_job"},
        headers=auth_headers(youth_c["access_token"]),
    )

    admin_email, admin_password = _create_admin("admin3@youthchain.test")
    _login_admin(client, admin_email, admin_password)

    page = client.get("/admin/reports")
    body = page.get_data(as_text=True)
    assert "⚠ 2 open reports" in body
    # BetaCorp (2 open reports, reported later) must appear before
    # ZetaCorp (1 open report, reported first).
    assert body.index("BetaCorp") < body.index(first_employer_name)
