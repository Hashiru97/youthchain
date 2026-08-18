"""
Regression coverage for BL-40 (usage-event logging) and BL-44 (the
/admin/analytics dashboard that aggregates those events).
"""
import json
import re

from conftest import register_user


def _csrf_token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "csrf token not found in page"
    return match.group(1)


def _create_admin(email="ops@youthchain.test", password="adminpass123"):
    import app as app_module

    with app_module.app.app_context():
        admin = app_module.Admin(
            name="Ops",
            email=email,
            password_hash=app_module.generate_password_hash(password),
        )
        app_module.db.session.add(admin)
        app_module.db.session.commit()
    return email, password


def test_registration_logs_an_event(client):
    import app as app_module

    register_user(client)
    with app_module.app.app_context():
        events = [e.event_type for e in app_module.AnalyticsEvent.query.all()]
    assert "user_registered" in events


def test_admin_dashboard_404s_when_key_unset(client, monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, "ADMIN_API_KEY", None)
    resp = client.get("/admin/analytics")
    assert resp.status_code == 404


def test_admin_dashboard_404s_with_wrong_key(client, monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, "ADMIN_API_KEY", "correct-key")
    resp = client.get("/admin/analytics?key=wrong-key")
    assert resp.status_code == 404


def test_admin_dashboard_shows_aggregate_counts_with_correct_key(client, monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, "ADMIN_API_KEY", "correct-key")
    register_user(client, email="dashboard@test.com", phone="909")

    resp = client.get("/admin/analytics?key=correct-key")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Registered youth" in body
    assert "user_registered" in body


def test_admin_dashboard_shows_gig_job_and_rating_stats(client, monkeypatch):
    """
    Real gap found via re-checking this page rather than assuming it was
    done: the gig/informal-work lifecycle (Job.job_type, Rating) added a
    whole new part of the platform with zero visibility on the one page an
    admin/funder/government stakeholder actually looks at for "what's
    happening here" numbers.
    """
    import app as app_module

    monkeypatch.setattr(app_module, "ADMIN_API_KEY", "correct-key")
    with app_module.app.app_context():
        formal = app_module.Job(title="Formal Role", location="X", duration="D", job_type="formal")
        gig = app_module.Job(title="Gig Role", location="X", duration="D", job_type="gig", category="Cleaning")
        app_module.db.session.add_all([formal, gig])
        app_module.db.session.commit()

    # The API-key path (unlike a real admin session login) never sets
    # is_admin_role, so the "Needs your attention" panel (which the
    # Rating Disputes card lives in) doesn't render here -- only the
    # always-visible stat cards below it are in scope for this test.
    resp = client.get("/admin/analytics?key=correct-key")
    body = resp.get_data(as_text=True)
    assert "Gig / hire-based jobs" in body
    assert "1 / 2" in body  # 1 gig job out of 2 total
    assert "Gigs completed" in body


def test_admin_dashboard_shows_ratings_split_by_direction_and_account_type_mix(client, monkeypatch):
    """
    Real bug found and fixed while adding this: the average-rating stat
    used to blend employer_to_worker and worker_to_employer ratings into
    one number, which is statistically meaningless -- they're two
    different populations answering two different questions ("how good
    was this worker" vs "how good was this employer"). Also covers the
    Employer.account_type stat added alongside it.
    """
    import app as app_module

    monkeypatch.setattr(app_module, "ADMIN_API_KEY", "correct-key")
    with app_module.app.app_context():
        employer = app_module.Employer(
            name="Acme", email="acme-analytics@test.com", password_hash="x", account_type="individual",
        )
        user = app_module.User(name="Worker", email="worker-analytics@test.com", phone="900", password_hash="x")
        app_module.db.session.add_all([employer, user])
        app_module.db.session.commit()
        job = app_module.Job(title="Gig", location="X", duration="D", job_type="gig", employer_id=employer.id)
        app_module.db.session.add(job)
        app_module.db.session.commit()
        application = app_module.Application(user_id=user.id, job_id=job.id, status="Completed")
        app_module.db.session.add(application)
        app_module.db.session.commit()
        # Employer rates the worker 5/5; worker rates the employer 1/5 --
        # a blended average would show 3.0 for both, hiding that these are
        # very different signals about very different things.
        app_module.db.session.add_all([
            app_module.Rating(application_id=application.id, direction="employer_to_worker",
                               employer_id=employer.id, user_id=user.id, score=5),
            app_module.Rating(application_id=application.id, direction="worker_to_employer",
                               employer_id=employer.id, user_id=user.id, score=1),
        ])
        app_module.db.session.commit()

    resp = client.get("/admin/analytics?key=correct-key")
    body = resp.get_data(as_text=True)
    assert "Avg. worker rating (1 rating)" in body
    assert "Avg. employer rating (1 rating)" in body
    # Both directions' own average must show up distinctly, not blended.
    stat_values = re.findall(r'admin-stat-value">([^<]+)<', body)
    assert "5.0" in stat_values
    assert "1.0" in stat_values
    assert "3.0" not in stat_values
    assert "Individual employer accounts" in body
    assert "1 / 1" in body


def test_admin_dashboard_reachable_via_real_login_with_no_key_set(client, monkeypatch):
    """The real Admin account path (BL-44 follow-up) works even when
    ADMIN_API_KEY is entirely unset — it's a separate, primary auth path,
    not merely a fallback for when the key is missing."""
    import app as app_module

    monkeypatch.setattr(app_module, "ADMIN_API_KEY", None)
    email, password = _create_admin()

    login_page = client.get("/admin/login")
    token = _csrf_token(login_page.get_data(as_text=True))
    resp = client.post(
        "/admin/login",
        data={"csrf_token": token, "email": email, "password": password},
        follow_redirects=False,
    )
    assert resp.status_code == 302


def test_admin_login_sets_a_session_cookie_with_a_real_expiry(client, monkeypatch):
    """
    Real gap found via a full security review, same as the employer-side
    version of this test: an Admin session -- the most privileged identity
    in this system -- previously never expired on its own either. Checked
    specifically for admin (not just employer) since the two logins set
    session.permanent at two different call sites in app.py.
    """
    import app as app_module

    monkeypatch.setattr(app_module, "ADMIN_API_KEY", None)
    email, password = _create_admin(email="expiry-check@youthchain.test")

    login_page = client.get("/admin/login")
    token = _csrf_token(login_page.get_data(as_text=True))
    resp = client.post(
        "/admin/login",
        data={"csrf_token": token, "email": email, "password": password},
    )
    set_cookie_headers = resp.headers.getlist("Set-Cookie")
    session_cookie = next(h for h in set_cookie_headers if h.startswith("session="))
    assert "Expires=" in session_cookie

    dash = client.get("/admin/analytics")
    assert dash.status_code == 200
    assert "Registered youth" in dash.get_data(as_text=True)


def test_admin_login_rejects_wrong_password(client):
    email, _ = _create_admin()
    login_page = client.get("/admin/login")
    token = _csrf_token(login_page.get_data(as_text=True))
    resp = client.post(
        "/admin/login",
        data={"csrf_token": token, "email": email, "password": "wrong-password"},
    )
    assert resp.status_code == 200
    assert "Incorrect email or password" in resp.get_data(as_text=True)


def test_admin_login_rate_limited_after_five_bad_password_attempts(client):
    """
    Real gap found via a full security review: the admin login route --
    guarding the single most privileged account type in this system --
    had no throttle on password attempts at all before this. Same generic
    limiter as OTP/uploads, keyed per-email.
    """
    email, _ = _create_admin(email="bruteforce@youthchain.test")
    login_page = client.get("/admin/login")
    token = _csrf_token(login_page.get_data(as_text=True))

    for _ in range(5):
        resp = client.post(
            "/admin/login",
            data={"csrf_token": token, "email": email, "password": "wrong-password"},
        )
        assert resp.status_code == 200

    limited = client.post(
        "/admin/login",
        data={"csrf_token": token, "email": email, "password": "wrong-password"},
    )
    assert limited.status_code == 429


def test_admin_failed_login_is_logged_as_a_security_event(client):
    """
    Real gap found via a full OWASP Top 10 (A09) review -- arguably the
    single highest-value place for this in the whole app: a failed login
    against the most privileged account type in this system previously
    had zero audit trail anywhere.
    """
    import app as app_module

    email, _ = _create_admin(email="audit-target@youthchain.test")
    login_page = client.get("/admin/login")
    token = _csrf_token(login_page.get_data(as_text=True))
    client.post(
        "/admin/login",
        data={"csrf_token": token, "email": email, "password": "wrong-password"},
    )

    with app_module.app.app_context():
        event = app_module.AnalyticsEvent.query.filter_by(event_type="admin_login_failed").first()
        assert event is not None
        assert json.loads(event.meta)["target_admin_id"] is not None


def test_admin_logout_clears_session(client, monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, "ADMIN_API_KEY", None)
    email, password = _create_admin(email="logout@youthchain.test")

    login_page = client.get("/admin/login")
    token = _csrf_token(login_page.get_data(as_text=True))
    client.post(
        "/admin/login",
        data={"csrf_token": token, "email": email, "password": password},
    )

    # The dashboard page itself carries a logout form (with its own CSRF
    # token) once a real admin session is active — reuse it rather than
    # hand-rolling a second token source.
    dash_page = client.get("/admin/analytics")
    logout_token = _csrf_token(dash_page.get_data(as_text=True))
    client.post("/admin/logout", data={"csrf_token": logout_token})

    after = client.get("/admin/analytics")
    assert after.status_code == 404


def test_analytics_dashboard_renders_real_svg_charts(client, monkeypatch):
    """
    Regression coverage for the inline-SVG funnel/time-series charts —
    confirms real chart markup is rendered from real data (an actual
    <rect> bar with the correct value), not just that the page loads.
    """
    import app as app_module

    monkeypatch.setattr(app_module, "ADMIN_API_KEY", "chart-test-key")

    # Registration already logs a real AnalyticsEvent (user_registered),
    # which feeds the 14-day time-series chart.
    register_user(client, email="chartuser@test.com", phone="777")

    resp = client.get("/admin/analytics?key=chart-test-key")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)

    # The time-series chart renders even with a single day of data.
    assert "<svg" in body
    assert 'role="img"' in body
    # Today's event count (1, from the registration above) should appear
    # as a real value label somewhere in the rendered SVG.
    assert ">1<" in body


def test_svg_bar_chart_html_escapes_labels_and_values():
    """
    Defense-in-depth found via a full security review, not a currently
    exploitable bug: _svg_bar_chart() is rendered with `| safe` in
    admin_analytics.html (deliberately, since it needs to emit real SVG
    markup) -- both of today's call sites only ever pass server-generated
    labels (fixed status literals, strftime dates), genuinely safe as-is.
    But that safety was previously just a docstring convention on a
    general-purpose shared helper, not anything the function itself
    enforced -- a future caller passing real user-controlled data (an
    employer name, a job title -- both plausible next additions to this
    exact dashboard) would have introduced a real stored-XSS in the admin
    console with nothing to catch it. This proves the function now
    escapes regardless of what a caller passes in.
    """
    import app as app_module

    malicious_label = '</text><script>alert(document.cookie)</script><text>'
    svg = app_module._svg_bar_chart([(malicious_label, 5)])

    assert "<script>" not in svg
    assert "&lt;script&gt;" in svg


def test_svg_bar_chart_escapes_a_malicious_value_formatter():
    import app as app_module

    svg = app_module._svg_bar_chart(
        [("Pending", 1)],
        value_fmt=lambda v: '"><script>alert(1)</script>',
    )

    assert "<script>" not in svg
    assert "&lt;script&gt;" in svg
