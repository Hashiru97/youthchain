"""
Regression coverage for S-03/BL-05 (employer session auth) and the CSRF
protection added alongside it.
"""
import re


def _csrf_token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "csrf token not found in page"
    return match.group(1)


def register_employer(client, email="acme@test.com", name="Acme", password="password123", account_type=None):
    page = client.get("/employer/register")
    token = _csrf_token(page.get_data(as_text=True))
    data = {"csrf_token": token, "name": name, "email": email, "password": password}
    if account_type is not None:
        data["account_type"] = account_type
    return client.post("/employer/register", data=data)


def test_employer_registration_sets_a_session_cookie_with_a_real_expiry(client):
    """
    Real gap found via a full security review: no session (employer or
    admin) previously ever expired on its own -- Flask's default is a
    non-permanent cookie with no Expires/Max-Age at all, so a browser tab
    left open indefinitely kept a valid session authenticated forever,
    with no automatic idle timeout. session.permanent = True (see
    PERMANENT_SESSION_LIFETIME's own comment in app.py) fixes that --
    this confirms the actual Set-Cookie header now carries a real expiry,
    not just that the config value exists.
    """
    resp = register_employer(client)
    set_cookie_headers = resp.headers.getlist("Set-Cookie")
    assert any("session=" in h for h in set_cookie_headers)
    session_cookie = next(h for h in set_cookie_headers if h.startswith("session="))
    assert "Expires=" in session_cookie


def test_employer_registration_rejects_a_breached_password(client, monkeypatch):
    """
    Real gap found via a full OWASP Top 10 (A07) review -- see the
    equivalent mobile /register test's docstring for the full reasoning.
    """
    import app as app_module

    monkeypatch.setattr(app_module, "_password_is_breached", lambda password: True)

    page = client.get("/employer/register")
    token = _csrf_token(page.get_data(as_text=True))
    resp = client.post(
        "/employer/register",
        data={"csrf_token": token, "name": "Acme", "email": "breached@test.com", "password": "password123"},
    )
    assert "breach" in resp.get_data(as_text=True).lower()

    with app_module.app.app_context():
        assert app_module.Employer.query.filter_by(email="breached@test.com").first() is None


def test_employer_dashboard_requires_login(client):
    resp = client.get("/employer", follow_redirects=False)
    assert resp.status_code == 302
    assert "/employer/login" in resp.headers["Location"]


def test_employer_register_then_dashboard_reachable(client):
    resp = register_employer(client)
    assert resp.status_code == 302
    dash = client.get("/employer")
    assert dash.status_code == 200


def test_employer_dashboard_shows_correct_per_job_applicant_counts(client):
    """
    Real gap found via a full-codebase review: employer_dashboard() ran one
    Application.count() query per job in a loop (N+1) -- replaced with a
    single grouped query. This test pins the actual behavior (right count
    per job, including a job with zero applicants) so that rewrite can't
    silently regress into "same count for every job" or "off by one from
    another employer's job" style bugs.
    """
    register_employer(client)
    page = client.get("/employer/post")
    token = _csrf_token(page.get_data(as_text=True))
    for title in ("Busy Job", "Quiet Job"):
        client.post(
            "/employer/post",
            data={"csrf_token": token, "title": title, "location": "L", "duration": "D"},
        )

    import app as app_module

    with app_module.app.app_context():
        busy_job = app_module.Job.query.filter_by(title="Busy Job").first()
        quiet_job = app_module.Job.query.filter_by(title="Quiet Job").first()
        applicants = [
            app_module.User(
                name=f"App {i}",
                phone=f"7700000{i}",
                email=f"app{i}@test.com",
                password_hash="x",
                consent_accepted_at=app_module.datetime.utcnow(),
            )
            for i in range(3)
        ]
        app_module.db.session.add_all(applicants)
        app_module.db.session.commit()
        # 3 applications for the busy job, 0 for the quiet one.
        for applicant in applicants:
            app_module.db.session.add(app_module.Application(
                user_id=applicant.id, job_id=busy_job.id, cv_file="cv.pdf",
            ))
        app_module.db.session.commit()

    dash = client.get("/employer").get_data(as_text=True)
    busy_section = dash.split("Busy Job")[1].split("</article>")[0]
    quiet_section = dash.split("Quiet Job")[1].split("</article>")[0]
    assert 'employer-applicant-badge-count">3</span>' in busy_section
    assert 'employer-applicant-badge-count">0</span>' in quiet_section


def test_employer_dashboard_shows_required_skills_pills(client):
    """
    Real gap found by re-checking the dashboard's own rendered output
    (not just the template code) after a UI redesign: employer_dashboard()
    built job_data as a hand-picked dict that never included
    required_skills at all, so the skills-pill section silently rendered
    nothing for every job that had skills set -- Jinja's default-undefined
    behavior hid the bug instead of erroring on it. Pins the fix.
    """
    register_employer(client)
    page = client.get("/employer/post")
    token = _csrf_token(page.get_data(as_text=True))
    client.post(
        "/employer/post",
        data={
            "csrf_token": token, "title": "Skilled Job", "location": "L", "duration": "D",
            "required_skills": "python, flutter",
        },
    )

    dash = client.get("/employer").get_data(as_text=True)
    assert '<span class="employer-skill-pill">python</span>' in dash
    assert '<span class="employer-skill-pill">flutter</span>' in dash


def test_employer_login_rate_limited_after_five_bad_password_attempts(client):
    """
    Real gap found via a full security review: /employer/login had no
    throttle on password attempts at all -- only OTP verification did
    anywhere in this codebase before this. Same generic limiter as
    OTP/uploads/admin login, keyed per-email.
    """
    register_employer(client, email="bruteforce@test.com")
    login_page = client.get("/employer/login")
    token = _csrf_token(login_page.get_data(as_text=True))

    for _ in range(5):
        resp = client.post(
            "/employer/login",
            data={"csrf_token": token, "email": "bruteforce@test.com", "password": "wrong-password"},
        )
        assert resp.status_code == 200

    limited = client.post(
        "/employer/login",
        data={"csrf_token": token, "email": "bruteforce@test.com", "password": "wrong-password"},
    )
    assert limited.status_code == 429


def test_employer_failed_login_is_logged_as_a_security_event(client):
    """
    Real gap found via a full OWASP Top 10 (A09) review -- see the
    equivalent mobile /login test's docstring for the full reasoning.
    """
    import app as app_module

    register_employer(client, email="audit-target@test.com")
    login_page = client.get("/employer/login")
    token = _csrf_token(login_page.get_data(as_text=True))
    client.post(
        "/employer/login",
        data={"csrf_token": token, "email": "audit-target@test.com", "password": "wrong-password"},
    )

    with app_module.app.app_context():
        event = app_module.AnalyticsEvent.query.filter_by(event_type="employer_login_failed").first()
        assert event is not None
        assert event.employer_id is not None


def test_post_job_without_csrf_token_rejected(client):
    register_employer(client)
    resp = client.post("/employer/post", data={"title": "T", "location": "L", "duration": "D"})
    assert resp.status_code == 400


def test_post_job_with_csrf_token_succeeds_and_sets_employer_id(client):
    register_employer(client)
    page = client.get("/employer/post")
    token = _csrf_token(page.get_data(as_text=True))
    resp = client.post(
        "/employer/post",
        data={"csrf_token": token, "title": "Welder", "location": "Bo", "duration": "6mo"},
        follow_redirects=False,
    )
    assert resp.status_code == 302

    import app as app_module

    with app_module.app.app_context():
        job = app_module.Job.query.filter_by(title="Welder").first()
        assert job is not None
        assert job.employer_id is not None


def test_second_employer_cannot_manage_first_employers_job(client):
    register_employer(client, email="acme@test.com")
    page = client.get("/employer/post")
    token = _csrf_token(page.get_data(as_text=True))
    client.post(
        "/employer/post",
        data={"csrf_token": token, "title": "Job1", "location": "L", "duration": "D"},
    )

    import app as app_module

    with app_module.app.app_context():
        job = app_module.Job.query.filter_by(title="Job1").first()
        job_id = job.id

    # Log out, register a second, unrelated employer
    logout_page = client.get("/employer/post")  # still authed as acme
    client.post(
        "/employer/logout",
        data={"csrf_token": _csrf_token(logout_page.get_data(as_text=True))},
    )
    register_employer(client, email="other@test.com")

    resp = client.get(f"/employer/applications/{job_id}")
    assert resp.status_code == 403


def test_employer_cannot_change_status_of_an_application_for_a_different_employers_job(client):
    """
    Real, severe IDOR found via a full OWASP Top 10 (A01: Broken Access
    Control) review and reproduced live before fixing -- this is a
    DIFFERENT bug from the test above, not a duplicate: that one covers
    an employer trying to reach someone else's job_id directly (already
    blocked); this one covers an employer POSTing to their OWN,
    legitimately-owned job_id with an app_id copied/guessed from a
    completely unrelated job belonging to a different employer.
    _employer_owns_job(job, eid) checked job_id, but the app_id used to
    look up which Application to actually mutate was never cross-checked
    against it at all -- any employer who owned at least one job could
    accept/reject any applicant on the entire platform. Reproduced live:
    a second employer successfully flipped the first employer's
    applicant to "Accepted" through their own, unrelated job's page, and
    it correctly triggered a real notification to the unrelated youth.
    """
    import app as app_module

    # Employer A posts Job A, a youth applies.
    register_employer(client, email="employer-a@test.com", name="Employer A")
    page = client.get("/employer/post")
    client.post(
        "/employer/post",
        data={"csrf_token": _csrf_token(page.get_data(as_text=True)), "title": "Job A", "location": "Bo", "duration": "6mo"},
    )
    with app_module.app.app_context():
        job_a = app_module.Job.query.filter_by(title="Job A").first()
        job_a_id = job_a.id

    logout_page = client.get("/employer/post")
    client.post("/employer/logout", data={"csrf_token": _csrf_token(logout_page.get_data(as_text=True))})

    import io

    from conftest import register_user, auth_headers, FAKE_PDF_BYTES

    youth = register_user(client, email="alice-idor@test.com", phone="555")
    client.post(
        "/apply",
        data={"job_id": str(job_a_id), "cv": (io.BytesIO(FAKE_PDF_BYTES), "cv.pdf")},
        headers=auth_headers(youth["access_token"]),
        content_type="multipart/form-data",
    )
    with app_module.app.app_context():
        application_a1 = app_module.Application.query.filter_by(user_id=youth["user"]["id"]).first()
        application_a1_id = application_a1.id
        assert application_a1.status == "Pending"

    # Employer B posts an unrelated Job B and is logged in as themselves.
    register_employer(client, email="employer-b@test.com", name="Employer B")
    page = client.get("/employer/post")
    token = _csrf_token(page.get_data(as_text=True))
    client.post(
        "/employer/post",
        data={"csrf_token": token, "title": "Job B", "location": "Kenema", "duration": "3mo"},
    )
    with app_module.app.app_context():
        job_b = app_module.Job.query.filter_by(title="Job B").first()
        job_b_id = job_b.id

    # Attack: POST to their OWN job_id (passes _employer_owns_job), but
    # with app_id forged to point at Employer A's applicant.
    resp = client.post(
        f"/employer/applications/{job_b_id}",
        data={"csrf_token": token, "app_id": str(application_a1_id), "action": "accept"},
    )
    assert resp.status_code == 403

    with app_module.app.app_context():
        application_a1_after = app_module.db.session.get(app_module.Application, application_a1_id)
        assert application_a1_after.status == "Pending"


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


def test_admin_can_suspend_and_reinstate_an_employer(client):
    """
    Real gap found via audit, not assumed: Employer.verification_status is
    a display badge only -- nothing checked it as an access-control gate
    anywhere, so there was previously no way to stop ANY employer
    (verified or not) short of editing the database directly. This
    exercises the actual fix end to end: suspend blocks login with a
    real, honest error message; reinstate restores it.
    """
    import app as app_module

    register_employer(client, email="rogue@corp.com", name="RogueCorp", password="password123")
    with app_module.app.app_context():
        employer_id = app_module.Employer.query.filter_by(email="rogue@corp.com").first().id

    logout_page = client.get("/employer/post")
    client.post("/employer/logout", data={"csrf_token": _csrf_token(logout_page.get_data(as_text=True))})

    admin_email, admin_password = _create_admin("admin@youthchain.test")
    _login_admin(client, admin_email, admin_password)

    suspend_page = client.get("/admin/employer_verifications")
    assert "RogueCorp" in suspend_page.get_data(as_text=True)
    suspend_token = _csrf_token(suspend_page.get_data(as_text=True))
    resp = client.post(
        f"/admin/employers/{employer_id}/suspend",
        data={"csrf_token": suspend_token},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app_module.app.app_context():
        assert app_module.db.session.get(app_module.Employer, employer_id).active is False

    # Suspended employer can no longer log in, with an honest reason.
    logout_page = client.get("/admin/employer_verifications")
    client.post("/admin/logout", data={"csrf_token": _csrf_token(logout_page.get_data(as_text=True))})

    login_page = client.get("/employer/login")
    login_token = _csrf_token(login_page.get_data(as_text=True))
    login_resp = client.post(
        "/employer/login",
        data={"csrf_token": login_token, "email": "rogue@corp.com", "password": "password123"},
    )
    assert "suspended" in login_resp.get_data(as_text=True).lower()

    # Reinstate restores access.
    _login_admin(client, admin_email, admin_password)
    roster_page = client.get("/admin/employer_verifications")
    reinstate_token = _csrf_token(roster_page.get_data(as_text=True))
    client.post(
        f"/admin/employers/{employer_id}/reinstate",
        data={"csrf_token": reinstate_token},
    )
    with app_module.app.app_context():
        assert app_module.db.session.get(app_module.Employer, employer_id).active is True

    logout_page = client.get("/admin/employer_verifications")
    client.post("/admin/logout", data={"csrf_token": _csrf_token(logout_page.get_data(as_text=True))})

    login_page = client.get("/employer/login")
    login_token = _csrf_token(login_page.get_data(as_text=True))
    login_resp = client.post(
        "/employer/login",
        data={"csrf_token": login_token, "email": "rogue@corp.com", "password": "password123"},
        follow_redirects=False,
    )
    assert login_resp.status_code == 302  # real login success again


def test_suspending_an_employer_revokes_an_already_open_session_immediately(client):
    """
    The whole point of gating suspension on every request (see
    _current_employer_id()) rather than only at login: an employer who is
    already logged in when suspended must lose access on their very next
    request, not just their next login.
    """
    import app as app_module

    register_employer(client, email="rogue2@corp.com", name="RogueCorp2", password="password123")
    with app_module.app.app_context():
        employer_id = app_module.Employer.query.filter_by(email="rogue2@corp.com").first().id

    # Still logged in as the employer right now -- confirm the session works.
    assert client.get("/employer").status_code == 200

    with app_module.app.app_context():
        employer = app_module.db.session.get(app_module.Employer, employer_id)
        employer.active = False
        app_module.db.session.commit()

    # Same still-open session, no new login -- must be locked out immediately.
    resp = client.get("/employer", follow_redirects=False)
    assert resp.status_code == 302
    assert "/employer/login" in resp.headers["Location"]


def test_verifier_role_admin_cannot_suspend_an_employer(client):
    import app as app_module

    register_employer(client, email="target@corp.com", name="TargetCorp")
    with app_module.app.app_context():
        employer_id = app_module.Employer.query.filter_by(email="target@corp.com").first().id

    logout_page = client.get("/employer/post")
    client.post("/employer/logout", data={"csrf_token": _csrf_token(logout_page.get_data(as_text=True))})

    verifier_email, verifier_password = _create_admin("verifier@youthchain.test", role="verifier")
    _login_admin(client, verifier_email, verifier_password)

    resp = client.post(
        f"/admin/employers/{employer_id}/suspend",
        data={"csrf_token": "irrelevant"},
    )
    assert resp.status_code == 403

    with app_module.app.app_context():
        assert app_module.db.session.get(app_module.Employer, employer_id).active is True
