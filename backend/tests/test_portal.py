"""
Regression coverage for the youth web portal (Phase 3 #9 — "what's on the
app, on the web too"): session-cookie registration/login mirroring the
employer/admin web portals, public job browsing, applying, tracking
applications, and the self-issued digital passport. All session-based
(not JWT) — see _current_portal_user_id's docstring in app.py for why the
web portal needed its own auth path alongside the mobile app's JWT one.
"""
import html
import io
import re

import app as app_module
from conftest import FAKE_PDF_BYTES


def _csrf_token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "csrf token not found in page"
    return match.group(1)


def register_portal_user(client, email="youth@test.com", name="Kadiatu", phone="23276111222", password="Str0ng!Passw0rd9", channel="email"):
    """
    Drives the real 3-step wizard (contact -> code -> details) exactly as
    a browser would, rather than reaching into the session directly —
    this is the actual code path being tested. ENFORCE_EMAIL_OTP_REG=0 in
    the test env (see conftest.py) means the code itself isn't checked,
    matching how /register's own tests work -- step="code" still has to
    be POSTed for real, though, since that's what flips the server-side
    portal_reg_verified flag the "details" step requires (can't be
    skipped straight to, even with enforcement off).

    channel="sms" drives the same wizard with phone as the verified
    identifier instead of email, for the tests that specifically cover
    that path.
    """
    identifier = email if channel == "email" else phone
    step1_page = client.get("/portal/register")
    token = _csrf_token(step1_page.get_data(as_text=True))
    step2 = client.post(
        "/portal/register",
        data={"csrf_token": token, "step": "contact", "channel": channel, "identifier": identifier},
    )
    token2 = _csrf_token(step2.get_data(as_text=True))
    step3 = client.post(
        "/portal/register",
        data={"csrf_token": token2, "step": "code", "otp_code": ""},
    )
    token3 = _csrf_token(step3.get_data(as_text=True))
    other = phone if channel == "email" else email
    # first_name/last_name replaced the old single "name" box (BL-47) --
    # split on the first space so existing callers passing one word (the
    # "Kadiatu" default) still produce a real first+last name, and
    # "Kadiatu" is still a substring of the stored full name for the
    # existing assertions that check for it.
    first_name, _, last_name = name.partition(" ")
    return client.post(
        "/portal/register",
        data={
            "csrf_token": token3, "step": "details",
            "first_name": first_name, "last_name": last_name or "Sesay", "other": other,
            "password": password, "consent": "on",
        },
    )


def _post_job_direct(title="Junior Dev", location="Freetown", duration="3 months", employer_id=None):
    with app_module.app.app_context():
        job = app_module.Job(title=title, location=location, duration=duration, employer_id=employer_id)
        app_module.db.session.add(job)
        app_module.db.session.commit()
        return job.id


def test_portal_registration_creates_account_and_logs_in(client):
    resp = register_portal_user(client)
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/portal")

    with app_module.app.app_context():
        user = app_module.User.query.filter_by(email="youth@test.com").first()
        assert user is not None
        assert user.consent_accepted_at is not None
        assert user.name == "Kadiatu Sesay"  # first_name/last_name concatenated

    dash = client.get("/portal")
    assert "Kadiatu" in dash.get_data(as_text=True)


def test_portal_registration_accepts_an_optional_ncra_id(client):
    step1_page = client.get("/portal/register")
    token = _csrf_token(step1_page.get_data(as_text=True))
    step2 = client.post("/portal/register", data={"csrf_token": token, "step": "contact", "channel": "email", "identifier": "ncrauser@test.com"})
    token2 = _csrf_token(step2.get_data(as_text=True))
    step3 = client.post("/portal/register", data={"csrf_token": token2, "step": "code", "otp_code": ""})
    token3 = _csrf_token(step3.get_data(as_text=True))
    resp = client.post("/portal/register", data={
        "csrf_token": token3, "step": "details", "first_name": "Ncra", "last_name": "Person",
        "other": "23277001122", "ncra_id": "SL-000112233", "password": "Str0ng!Passw0rd9", "consent": "on",
    })
    assert resp.status_code == 302
    with app_module.app.app_context():
        user = app_module.User.query.filter_by(email="ncrauser@test.com").first()
        assert user.ncra_id == "SL-000112233"
        assert user.phone == "23277001122"


def test_portal_registration_via_sms_channel(client):
    """
    Real gap found via user feedback: registration was email-only despite
    password login already accepting phone OR email as the identifier.
    channel="sms" drives the identical 3-step wizard with phone as the
    verified identifier instead.
    """
    resp = register_portal_user(client, email="smsreg@test.com", phone="23279112233", channel="sms")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/portal")
    with app_module.app.app_context():
        user = app_module.User.query.filter_by(phone="23279112233").first()
        assert user is not None
        assert user.email == "smsreg@test.com"


def test_portal_registration_sms_channel_sends_via_send_sms_not_email(client, monkeypatch):
    monkeypatch.setattr(app_module, "ENFORCE_EMAIL_OTP_REG", True)
    sent_sms = []
    sent_email = []
    monkeypatch.setattr(app_module, "send_sms", lambda phone, body: sent_sms.append((phone, body)) or True)
    monkeypatch.setattr(app_module, "_send_email", lambda *a, **kw: sent_email.append(a) or True)

    page = client.get("/portal/register")
    token = _csrf_token(page.get_data(as_text=True))
    client.post("/portal/register", data={
        "csrf_token": token, "step": "contact", "channel": "sms", "identifier": "23279998877",
    })
    assert len(sent_sms) == 1
    assert sent_sms[0][0] == "23279998877"
    assert sent_email == []


def test_portal_registration_cannot_skip_straight_to_details(client):
    """
    portal_reg_verified is a server-side session flag, not client-supplied
    -- posting step="details" without ever completing step="contact"/"code"
    first must be refused, not silently accepted.
    """
    page = client.get("/portal/register")
    token = _csrf_token(page.get_data(as_text=True))
    resp = client.post("/portal/register", data={
        "csrf_token": token, "step": "details", "name": "Skipper", "other": "23270000000",
        "password": "Str0ng!Passw0rd9", "consent": "on",
    })
    assert "verify your contact details first" in resp.get_data(as_text=True).lower()
    with app_module.app.app_context():
        assert app_module.User.query.filter_by(name="Skipper").first() is None


def test_portal_registration_rejects_wrong_otp_code(client, monkeypatch):
    monkeypatch.setattr(app_module, "ENFORCE_EMAIL_OTP_REG", True)
    page = client.get("/portal/register")
    token = _csrf_token(page.get_data(as_text=True))
    step2 = client.post("/portal/register", data={
        "csrf_token": token, "step": "contact", "channel": "email", "identifier": "wrongcode@test.com",
    })
    token2 = _csrf_token(step2.get_data(as_text=True))
    resp = client.post("/portal/register", data={"csrf_token": token2, "step": "code", "otp_code": "000000"})
    assert "invalid or expired code" in resp.get_data(as_text=True).lower()

    with app_module.app.app_context():
        assert app_module.User.query.filter_by(email="wrongcode@test.com").first() is None


def test_portal_registration_accepts_correct_otp_code(client, monkeypatch):
    monkeypatch.setattr(app_module, "ENFORCE_EMAIL_OTP_REG", True)
    page = client.get("/portal/register")
    token = _csrf_token(page.get_data(as_text=True))
    step2 = client.post("/portal/register", data={
        "csrf_token": token, "step": "contact", "channel": "email", "identifier": "rightcode@test.com",
    })
    token2 = _csrf_token(step2.get_data(as_text=True))
    with app_module.app.app_context():
        real_code = app_module.OTPCode.query.filter_by(identifier="rightcode@test.com", used=False).first().code
    resp = client.post("/portal/register", data={"csrf_token": token2, "step": "code", "otp_code": real_code})
    assert "verified" in resp.get_data(as_text=True).lower()
    with client.session_transaction() as sess:
        assert sess.get("portal_reg_verified") is True


def test_portal_registration_duplicate_email_rejected_at_contact_step(client):
    register_portal_user(client, email="taken@test.com")
    client.post("/portal/logout", data={"csrf_token": _csrf_token(client.get("/portal").get_data(as_text=True))})

    page = client.get("/portal/register")
    token = _csrf_token(page.get_data(as_text=True))
    resp = client.post("/portal/register", data={
        "csrf_token": token, "step": "contact", "channel": "email", "identifier": "taken@test.com",
    })
    assert "unable to register" in resp.get_data(as_text=True).lower()


def test_portal_registration_duplicate_phone_rejected_at_contact_step_via_sms(client):
    register_portal_user(client, phone="23271112222")
    client.post("/portal/logout", data={"csrf_token": _csrf_token(client.get("/portal").get_data(as_text=True))})

    page = client.get("/portal/register")
    token = _csrf_token(page.get_data(as_text=True))
    resp = client.post("/portal/register", data={
        "csrf_token": token, "step": "contact", "channel": "sms", "identifier": "23271112222",
    })
    assert "unable to register" in resp.get_data(as_text=True).lower()


def test_portal_registration_rejects_a_breached_password(client, monkeypatch):
    monkeypatch.setattr(app_module, "_password_is_breached", lambda password: True)
    resp = register_portal_user(client, email="breached@test.com")
    assert "breach" in resp.get_data(as_text=True).lower()
    with app_module.app.app_context():
        assert app_module.User.query.filter_by(email="breached@test.com").first() is None


def test_portal_registration_sets_a_session_cookie_with_a_real_expiry(client):
    resp = register_portal_user(client, email="expiry@test.com")
    set_cookie_headers = resp.headers.getlist("Set-Cookie")
    session_cookie = next(h for h in set_cookie_headers if h.startswith("session="))
    assert "Expires=" in session_cookie


def test_portal_dashboard_requires_no_login_to_browse(client):
    _post_job_direct(title="Public Job")
    resp = client.get("/portal")
    assert resp.status_code == 200
    assert "Public Job" in resp.get_data(as_text=True)


def test_portal_dashboard_search_filters_by_skill(client):
    _post_job_direct(title="Wiring Job")
    with app_module.app.app_context():
        job = app_module.Job.query.filter_by(title="Wiring Job").first()
        job.required_skills = "wiring,electrical"
        app_module.db.session.commit()
    _post_job_direct(title="Unrelated Job")

    resp = client.get("/portal?skill=electrical")
    body = resp.get_data(as_text=True)
    assert "Wiring Job" in body
    assert "Unrelated Job" not in body


def test_portal_dashboard_pagination_next_and_prev_links(client):
    """
    Real gap found by re-checking this feature: the job feed had no way
    to reach a second page at all, regardless of how many jobs existed.
    Uses ?limit=2 to make the page boundary reachable with only 3 seeded
    jobs rather than needing to seed past the real default page size.
    """
    for i in range(3):
        _post_job_direct(title=f"Job {i}")

    page1 = client.get("/portal?limit=2")
    body1 = page1.get_data(as_text=True)
    assert "Next" in body1
    assert "Previous" not in body1

    match = re.search(r'href="(/portal\?[^"]*offset=2[^"]*)"', body1)
    assert match, body1
    # href attributes are HTML-escaped (&amp;) by Jinja's autoescaping —
    # a real browser decodes that back to & before requesting the URL;
    # the test client doesn't, so this has to be done explicitly or the
    # query string silently corrupts into ...&amp;offset=2 (a param
    # literally named "amp;offset", not "offset").
    page2 = client.get(html.unescape(match.group(1)))
    body2 = page2.get_data(as_text=True)
    assert "Previous" in body2
    assert "Next" not in body2


def test_portal_job_detail_shows_login_prompt_for_anonymous_visitor(client):
    job_id = _post_job_direct()
    resp = client.get(f"/portal/jobs/{job_id}")
    body = resp.get_data(as_text=True)
    assert "Log in to apply" in body


def test_portal_apply_creates_application_and_redirects_to_my_applications(client):
    job_id = _post_job_direct(title="Solar Tech", location="Bo")
    register_portal_user(client, email="applicant@test.com")

    detail = client.get(f"/portal/jobs/{job_id}")
    token = _csrf_token(detail.get_data(as_text=True))
    resp = client.post(
        f"/portal/jobs/{job_id}/apply",
        data={"csrf_token": token, "cv": (io.BytesIO(FAKE_PDF_BYTES), "cv.pdf")},
        content_type="multipart/form-data",
    )
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/portal/applications")

    with app_module.app.app_context():
        user = app_module.User.query.filter_by(email="applicant@test.com").first()
        application = app_module.Application.query.filter_by(user_id=user.id, job_id=job_id).first()
        assert application is not None
        assert application.status == "Pending"


def test_portal_apply_blocks_duplicate_application(client):
    job_id = _post_job_direct()
    register_portal_user(client, email="dupe@test.com")

    def _apply():
        detail = client.get(f"/portal/jobs/{job_id}")
        token = _csrf_token(detail.get_data(as_text=True))
        return client.post(
            f"/portal/jobs/{job_id}/apply",
            data={"csrf_token": token, "cv": (io.BytesIO(FAKE_PDF_BYTES), "cv.pdf")},
            content_type="multipart/form-data",
        )

    first = _apply()
    assert first.status_code == 302
    second = _apply()
    assert "already applied" in second.get_data(as_text=True).lower()

    with app_module.app.app_context():
        user = app_module.User.query.filter_by(email="dupe@test.com").first()
        count = app_module.Application.query.filter_by(user_id=user.id, job_id=job_id).count()
        assert count == 1


def test_portal_apply_requires_login(client):
    job_id = _post_job_direct()
    resp = client.post(
        f"/portal/jobs/{job_id}/apply",
        data={"cv": (io.BytesIO(FAKE_PDF_BYTES), "cv.pdf")},
        content_type="multipart/form-data",
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert "/portal/login" in resp.headers["Location"]


def test_portal_applications_page_shows_job_title_and_status(client):
    job_id = _post_job_direct(title="Wiring Job", location="Bo")
    register_portal_user(client, email="tracker@test.com")

    detail = client.get(f"/portal/jobs/{job_id}")
    token = _csrf_token(detail.get_data(as_text=True))
    client.post(
        f"/portal/jobs/{job_id}/apply",
        data={"csrf_token": token, "cv": (io.BytesIO(FAKE_PDF_BYTES), "cv.pdf")},
        content_type="multipart/form-data",
    )

    apps_page = client.get("/portal/applications")
    body = apps_page.get_data(as_text=True)
    assert "Wiring Job" in body
    assert "Pending" in body


def test_portal_passport_issues_credential_and_lists_it(client):
    register_portal_user(client, email="passport@test.com")

    passport_page = client.get("/portal/passport")
    token = _csrf_token(passport_page.get_data(as_text=True))
    resp = client.post(
        "/portal/passport",
        data={
            "csrf_token": token, "title": "Cert in Web Dev", "issuer": "Limkokwing",
            "file": (io.BytesIO(FAKE_PDF_BYTES), "cert.pdf"),
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    assert "Cert in Web Dev" in resp.get_data(as_text=True)

    with app_module.app.app_context():
        user = app_module.User.query.filter_by(email="passport@test.com").first()
        cred = app_module.Credential.query.filter_by(user_id=user.id).first()
        assert cred is not None
        assert cred.title == "Cert in Web Dev"


def test_portal_login_then_logout(client):
    register_portal_user(client, email="loginflow@test.com")
    client.post("/portal/logout", data={"csrf_token": _csrf_token(client.get("/portal").get_data(as_text=True))})

    login_page = client.get("/portal/login")
    token = _csrf_token(login_page.get_data(as_text=True))
    resp = client.post(
        "/portal/login",
        data={"csrf_token": token, "identifier": "loginflow@test.com", "password": "Str0ng!Passw0rd9"},
    )
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/portal")


def test_portal_login_rejects_wrong_password(client):
    register_portal_user(client, email="wrongpw@test.com")
    client.post("/portal/logout", data={"csrf_token": _csrf_token(client.get("/portal").get_data(as_text=True))})

    login_page = client.get("/portal/login")
    token = _csrf_token(login_page.get_data(as_text=True))
    resp = client.post(
        "/portal/login",
        data={"csrf_token": token, "identifier": "wrongpw@test.com", "password": "totally-wrong"},
    )
    assert "incorrect" in resp.get_data(as_text=True).lower()


def test_portal_applications_requires_login(client):
    resp = client.get("/portal/applications", follow_redirects=False)
    assert resp.status_code == 302
    assert "/portal/login" in resp.headers["Location"]


def test_portal_passport_requires_login(client):
    resp = client.get("/portal/passport", follow_redirects=False)
    assert resp.status_code == 302
    assert "/portal/login" in resp.headers["Location"]


def test_get_app_page_reachable_and_every_portal_cta_links_to_it_not_privacy_policy(client):
    """
    Real bug found by re-checking this feature rather than trusting the
    original summary of it: every "Get the app" CTA (nav bar on every
    portal page, plus the dashboard/applications banners) pointed at
    /privacy-policy instead of anywhere related to the app. Pins the fix:
    a real /get-app page exists, and the shell's nav CTA reaches it.
    """
    resp = client.get("/get-app")
    assert resp.status_code == 200
    assert "download links aren" in resp.get_data(as_text=True).lower()

    dash = client.get("/portal").get_data(as_text=True)
    assert '/get-app"' in dash
    assert dash.count('href="/privacy-policy"') <= 1  # only the footer's real privacy link, not the app CTA


def test_portal_register_details_error_preserves_name_and_other_field(client):
    """
    Real UX gap found by re-checking this feature: a validation error on
    the details step used to blank out name/phone too, forcing a full
    re-type of the form for one mistake. Password is deliberately never
    echoed back.
    """
    step1_page = client.get("/portal/register")
    token = _csrf_token(step1_page.get_data(as_text=True))
    step2 = client.post("/portal/register", data={"csrf_token": token, "step": "contact", "channel": "email", "identifier": "retry@test.com"})
    token2 = _csrf_token(step2.get_data(as_text=True))
    step3 = client.post("/portal/register", data={"csrf_token": token2, "step": "code", "otp_code": ""})
    token3 = _csrf_token(step3.get_data(as_text=True))

    # A too-short password fails validation before anything else, so this
    # exercises the field-preservation fix directly.
    resp = client.post(
        "/portal/register",
        data={
            "csrf_token": token3, "step": "details", "first_name": "Retry", "last_name": "Person", "other": "23277000111",
            "password": "short", "consent": "on",
        },
    )
    body = resp.get_data(as_text=True)
    assert "at least 8 characters" in body
    assert 'value="Retry"' in body
    assert 'value="Person"' in body
    assert 'value="23277000111"' in body


def test_downloaded_cv_reachable_via_portal_session_not_just_jwt(client):
    """
    download_application previously only recognized a JWT-authenticated
    applicant or a session-authenticated employer -- a youth using the
    new web portal (session, not JWT) to re-download their own CV would
    have been wrongly forbidden. Pins the fix.
    """
    job_id = _post_job_direct()
    register_portal_user(client, email="ownfile@test.com")

    detail = client.get(f"/portal/jobs/{job_id}")
    token = _csrf_token(detail.get_data(as_text=True))
    client.post(
        f"/portal/jobs/{job_id}/apply",
        data={"csrf_token": token, "cv": (io.BytesIO(FAKE_PDF_BYTES), "cv.pdf")},
        content_type="multipart/form-data",
    )

    with app_module.app.app_context():
        user = app_module.User.query.filter_by(email="ownfile@test.com").first()
        application = app_module.Application.query.filter_by(user_id=user.id, job_id=job_id).first()
        cv_filename = application.cv_file

    resp = client.get(f"/application_file/{cv_filename}")
    assert resp.status_code == 200
