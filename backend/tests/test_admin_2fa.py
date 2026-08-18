"""
Regression coverage for TOTP 2FA on Admin accounts (RFC 6238). Uses real
pyotp.TOTP() to generate valid codes — not mocked — so these tests prove
the actual enrollment/verification math works, not just that the routes
exist.
"""
import re

import pyotp


def _csrf_token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "csrf token not found in page"
    return match.group(1)


def _create_admin(email, password="adminpass123", role="admin", name="Ops"):
    import app as app_module

    with app_module.app.app_context():
        admin = app_module.Admin(
            name=name,
            email=email,
            role=role,
            password_hash=app_module.generate_password_hash(password),
        )
        app_module.db.session.add(admin)
        app_module.db.session.commit()
        admin_id = admin.id
    return admin_id


def _login(client, email, password):
    login_page = client.get("/admin/login")
    token = _csrf_token(login_page.get_data(as_text=True))
    return client.post(
        "/admin/login",
        data={"csrf_token": token, "email": email, "password": password},
        follow_redirects=False,
    )


def _extract_secret(html: str) -> str:
    match = re.search(r'<code class="mono">([A-Z0-9]+)</code>', html)
    assert match, "TOTP secret not found in setup page"
    return match.group(1)


def test_enroll_2fa_end_to_end_with_real_totp_codes(client):
    email = "totp-enroll@youthchain.test"
    _create_admin(email)
    _login(client, email, "adminpass123")

    setup_page = client.get("/admin/2fa/setup")
    assert setup_page.status_code == 200
    html = setup_page.get_data(as_text=True)
    secret = _extract_secret(html)
    token = _csrf_token(html)

    real_code = pyotp.TOTP(secret).now()
    resp = client.post(
        "/admin/2fa/setup",
        data={"csrf_token": token, "code": real_code},
    )
    assert resp.status_code == 200
    assert "now enabled" in resp.get_data(as_text=True).lower()

    import app as app_module
    with app_module.app.app_context():
        admin = app_module.Admin.query.filter_by(email=email).first()
        assert admin.totp_enabled is True
        assert admin.totp_secret == secret


def test_enrollment_rejects_wrong_code(client):
    email = "totp-wrong@youthchain.test"
    _create_admin(email)
    _login(client, email, "adminpass123")

    setup_page = client.get("/admin/2fa/setup")
    html = setup_page.get_data(as_text=True)
    token = _csrf_token(html)

    resp = client.post("/admin/2fa/setup", data={"csrf_token": token, "code": "000000"})
    assert "Incorrect code" in resp.get_data(as_text=True)

    import app as app_module
    with app_module.app.app_context():
        admin = app_module.Admin.query.filter_by(email=email).first()
        assert admin.totp_enabled is False


def test_login_with_2fa_enabled_requires_totp_step(client):
    import app as app_module

    email = "totp-login@youthchain.test"
    secret = pyotp.random_base32()
    with app_module.app.app_context():
        admin = app_module.Admin(
            name="2FA User",
            email=email,
            role="admin",
            password_hash=app_module.generate_password_hash("adminpass123"),
            totp_secret=secret,
            totp_enabled=True,
        )
        app_module.db.session.add(admin)
        app_module.db.session.commit()

    # Password alone does NOT grant access.
    login_resp = _login(client, email, "adminpass123")
    assert login_resp.status_code == 302
    assert "/admin/2fa/verify" in login_resp.headers["Location"]

    still_locked_out = client.get("/admin/analytics")
    assert still_locked_out.status_code == 404  # not authenticated yet

    # Now complete the TOTP step with a real, correctly-generated code.
    verify_page = client.get("/admin/2fa/verify")
    token = _csrf_token(verify_page.get_data(as_text=True))
    real_code = pyotp.TOTP(secret).now()
    verify_resp = client.post(
        "/admin/2fa/verify",
        data={"csrf_token": token, "code": real_code},
        follow_redirects=False,
    )
    assert verify_resp.status_code == 302

    now_authenticated = client.get("/admin/analytics")
    assert now_authenticated.status_code == 200


def test_2fa_verify_rejects_wrong_code_and_rate_limits(client):
    import app as app_module

    email = "totp-bruteforce@youthchain.test"
    secret = pyotp.random_base32()
    with app_module.app.app_context():
        admin = app_module.Admin(
            name="Target",
            email=email,
            role="verifier",
            password_hash=app_module.generate_password_hash("adminpass123"),
            totp_secret=secret,
            totp_enabled=True,
        )
        app_module.db.session.add(admin)
        app_module.db.session.commit()

    _login(client, email, "adminpass123")
    verify_page = client.get("/admin/2fa/verify")
    token = _csrf_token(verify_page.get_data(as_text=True))

    # 5 wrong attempts allowed (shares the same rate limiter as email OTP,
    # BL-09's 5-attempts-per-10-min budget), 6th is rate-limited.
    for _ in range(5):
        resp = client.post("/admin/2fa/verify", data={"csrf_token": token, "code": "000000"})
        assert "Incorrect code" in resp.get_data(as_text=True)

    sixth = client.post("/admin/2fa/verify", data={"csrf_token": token, "code": "000000"})
    assert "Too many attempts" in sixth.get_data(as_text=True)


def test_disable_2fa(client):
    import app as app_module

    email = "totp-disable@youthchain.test"
    secret = pyotp.random_base32()
    with app_module.app.app_context():
        admin = app_module.Admin(
            name="Disabler",
            email=email,
            role="admin",
            password_hash=app_module.generate_password_hash("adminpass123"),
            totp_secret=secret,
            totp_enabled=True,
        )
        app_module.db.session.add(admin)
        app_module.db.session.commit()

    # Log in through the full 2FA flow first.
    _login(client, email, "adminpass123")
    verify_page = client.get("/admin/2fa/verify")
    token = _csrf_token(verify_page.get_data(as_text=True))
    client.post("/admin/2fa/verify", data={"csrf_token": token, "code": pyotp.TOTP(secret).now()})

    settings_page = client.get("/admin/2fa/setup")
    disable_token = _csrf_token(settings_page.get_data(as_text=True))
    client.post("/admin/2fa/disable", data={"csrf_token": disable_token})

    with app_module.app.app_context():
        admin = app_module.Admin.query.filter_by(email=email).first()
        assert admin.totp_enabled is False
        assert admin.totp_secret is None
