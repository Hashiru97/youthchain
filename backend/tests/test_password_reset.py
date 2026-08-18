"""
Regression coverage for BL-46 (forgot password), across all four
account-recovery surfaces: the mobile JSON API (youth), the youth web
portal, the employer web portal, and the admin web portal.

Real gap this closes: there was previously no account-recovery path at
all anywhere in this codebase -- a user who forgot their password had no
way back into their account. Reuses the exact channel-aware OTPCode
machinery already built and tested for registration (see OTPCode's
purpose column) rather than a second, parallel email-link/token scheme.

None of these routes are gated behind ENFORCE_EMAIL_OTP_REG (unlike
registration) -- password reset always requires a real code, even in
this test environment where that flag defaults to "0" for registration's
convenience. So every test here fetches the actual code from OTPCode
rather than relying on that bypass.
"""
import re

import pyotp

import app as app_module
from conftest import register_user


def _csrf_token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "csrf token not found in page"
    return match.group(1)


def _latest_reset_code(identifier: str) -> str:
    with app_module.app.app_context():
        row = (
            app_module.OTPCode.query.filter_by(identifier=identifier, purpose="reset", used=False)
            .order_by(app_module.OTPCode.id.desc())
            .first()
        )
        assert row is not None, f"no unused reset OTPCode for {identifier}"
        return row.code


def register_employer(client, email="acme@test.com", name="Acme", password="password123"):
    page = client.get("/employer/register")
    token = _csrf_token(page.get_data(as_text=True))
    return client.post(
        "/employer/register",
        data={"csrf_token": token, "name": name, "email": email, "password": password},
    )


def _create_admin(email, password="adminpass123", role="admin", name="Ops", totp_secret=None, totp_enabled=False):
    with app_module.app.app_context():
        admin = app_module.Admin(
            name=name,
            email=email,
            role=role,
            password_hash=app_module.generate_password_hash(password),
            totp_secret=totp_secret,
            totp_enabled=totp_enabled,
        )
        app_module.db.session.add(admin)
        app_module.db.session.commit()
        return admin.id


# ----------------------------- Mobile (youth, JWT) -----------------------------

def test_mobile_reset_request_is_generic_whether_or_not_account_exists(client):
    real = client.post("/auth/otp/reset/request", json={"channel": "email", "identifier": "nobody@test.com"})
    register_user(client, email="hasaccount@test.com", phone="23279000001")
    exists = client.post("/auth/otp/reset/request", json={"channel": "email", "identifier": "hasaccount@test.com"})
    assert real.status_code == exists.status_code == 200
    assert real.get_json()["message"] == exists.get_json()["message"]


def test_mobile_reset_request_does_not_issue_a_code_for_a_nonexistent_account(client):
    client.post("/auth/otp/reset/request", json={"channel": "email", "identifier": "ghost@test.com"})
    with app_module.app.app_context():
        row = app_module.OTPCode.query.filter_by(identifier="ghost@test.com", purpose="reset").first()
        assert row is None


def test_mobile_reset_full_happy_path_changes_password_and_revokes_sessions(client):
    register_user(client, email="resetme@test.com", phone="23279000002", password="OldPassw0rd!")
    login1 = client.post("/login", json={"email": "resetme@test.com", "password": "OldPassw0rd!"})
    login2 = client.post("/login", json={"email": "resetme@test.com", "password": "OldPassw0rd!"})
    assert login1.status_code == login2.status_code == 200

    with app_module.app.app_context():
        user_id = app_module.User.query.filter_by(email="resetme@test.com").first().id
        pre_reset_session_ids = [
            s.id for s in app_module.UserSession.query.filter_by(user_id=user_id).all()
        ]
        assert len(pre_reset_session_ids) == 3  # registration's own + both explicit logins

    client.post("/auth/otp/reset/request", json={"channel": "email", "identifier": "resetme@test.com"})
    code = _latest_reset_code("resetme@test.com")

    precheck = client.post("/auth/otp/reset/verify", json={"channel": "email", "identifier": "resetme@test.com", "code": code})
    assert precheck.status_code == 200
    assert precheck.get_json()["success"] is True

    confirm = client.post("/auth/otp/reset/confirm", json={
        "channel": "email", "identifier": "resetme@test.com", "code": code, "new_password": "BrandNewPassw0rd!",
    })
    assert confirm.status_code == 200

    old_login = client.post("/login", json={"email": "resetme@test.com", "password": "OldPassw0rd!"})
    assert old_login.status_code == 401
    new_login = client.post("/login", json={"email": "resetme@test.com", "password": "BrandNewPassw0rd!"})
    assert new_login.status_code == 200

    # Every pre-reset session is revoked -- the whole point of a password
    # reset often being "I think my account is compromised." The NEW
    # session from new_login just above must NOT be among them.
    with app_module.app.app_context():
        pre_reset_sessions = app_module.UserSession.query.filter(
            app_module.UserSession.id.in_(pre_reset_session_ids)
        ).all()
        assert len(pre_reset_sessions) == 3
        assert all(s.revoked_at is not None for s in pre_reset_sessions)

        post_reset_session = app_module.UserSession.query.filter(
            app_module.UserSession.user_id == user_id,
            ~app_module.UserSession.id.in_(pre_reset_session_ids),
        ).first()
        assert post_reset_session is not None
        assert post_reset_session.revoked_at is None


def test_mobile_reset_confirm_rejects_a_weak_password(client, monkeypatch):
    monkeypatch.setattr(app_module, "_password_is_breached", lambda p: False)
    register_user(client, email="weakpw@test.com", phone="23279000003")
    client.post("/auth/otp/reset/request", json={"channel": "email", "identifier": "weakpw@test.com"})
    code = _latest_reset_code("weakpw@test.com")
    resp = client.post("/auth/otp/reset/confirm", json={
        "channel": "email", "identifier": "weakpw@test.com", "code": code, "new_password": "short",
    })
    assert resp.status_code == 400


def test_mobile_reset_confirm_rejects_a_breached_password(client, monkeypatch):
    register_user(client, email="breached@test.com", phone="23279000004")
    client.post("/auth/otp/reset/request", json={"channel": "email", "identifier": "breached@test.com"})
    code = _latest_reset_code("breached@test.com")

    monkeypatch.setattr(app_module, "_password_is_breached", lambda p: True)
    resp = client.post("/auth/otp/reset/confirm", json={
        "channel": "email", "identifier": "breached@test.com", "code": code, "new_password": "Password123!",
    })
    assert resp.status_code == 400
    assert "data breach" in resp.get_json()["error"]


def test_mobile_reset_verify_precheck_does_not_consume_the_code(client):
    """The same non-consuming precheck contract as otp_verify_for_registration."""
    register_user(client, email="precheckreset@test.com", phone="23279000005")
    client.post("/auth/otp/reset/request", json={"channel": "email", "identifier": "precheckreset@test.com"})
    code = _latest_reset_code("precheckreset@test.com")

    client.post("/auth/otp/reset/verify", json={"channel": "email", "identifier": "precheckreset@test.com", "code": code})
    # Still valid afterward.
    confirm = client.post("/auth/otp/reset/confirm", json={
        "channel": "email", "identifier": "precheckreset@test.com", "code": code, "new_password": "SecondPassw0rd!",
    })
    assert confirm.status_code == 200


def test_mobile_reset_via_sms_channel(client, monkeypatch):
    sent = []
    monkeypatch.setattr(app_module, "send_sms", lambda phone, body: sent.append((phone, body)) or True)
    phone = "23279000006"
    register_user(client, email="smsreset@test.com", phone=phone, password="OldPassw0rd!")

    resp = client.post("/auth/otp/reset/request", json={"channel": "sms", "identifier": phone})
    assert resp.status_code == 200
    assert len(sent) == 1

    code = _latest_reset_code(phone)
    confirm = client.post("/auth/otp/reset/confirm", json={
        "channel": "sms", "identifier": phone, "code": code, "new_password": "NewViaSms123!",
    })
    assert confirm.status_code == 200

    new_login = client.post("/login", json={"phone": phone, "password": "NewViaSms123!"})
    assert new_login.status_code == 200


def test_a_registration_code_cannot_be_used_to_reset_an_existing_accounts_password(client):
    """
    The exact scenario the new `purpose` column exists to prevent: a
    leftover, unused registration OTP for this identifier must not
    double as a valid reset code for an already-existing account there.
    """
    register_user(client, email="crosspurpose@test.com", phone="23279000007", password="OldPassw0rd!")

    # Issue a *registration*-purpose code at the same identifier (e.g. a
    # stale, abandoned re-registration attempt) -- this route never gates
    # on ENFORCE_EMAIL_OTP_REG, it always issues a real code.
    client.post("/auth/otp/register/request", json={"channel": "email", "identifier": "crosspurpose@test.com"})
    with app_module.app.app_context():
        reg_code = (
            app_module.OTPCode.query.filter_by(identifier="crosspurpose@test.com", purpose="register", used=False)
            .first().code
        )

    resp = client.post("/auth/otp/reset/confirm", json={
        "channel": "email", "identifier": "crosspurpose@test.com", "code": reg_code, "new_password": "ShouldNotWork1!",
    })
    assert resp.status_code == 400

    # Original password still works -- the reset did not go through.
    still_old = client.post("/login", json={"email": "crosspurpose@test.com", "password": "OldPassw0rd!"})
    assert still_old.status_code == 200


def test_a_login_otp_code_cannot_be_used_to_reset_password(client):
    """Same cross-purpose protection, against the passwordless-login OTP purpose."""
    register_user(client, email="loginpurpose@test.com", phone="23279000008", password="OldPassw0rd!")
    client.post("/auth/otp/request", json={"email": "loginpurpose@test.com"})
    with app_module.app.app_context():
        login_code = (
            app_module.OTPCode.query.filter_by(identifier="loginpurpose@test.com", purpose="login", used=False)
            .first().code
        )

    resp = client.post("/auth/otp/reset/confirm", json={
        "channel": "email", "identifier": "loginpurpose@test.com", "code": login_code, "new_password": "ShouldNotWork2!",
    })
    assert resp.status_code == 400


def test_mobile_reset_request_is_rate_limited(client):
    identifier = "resetratelimit@test.com"
    for _ in range(5):
        resp = client.post("/auth/otp/reset/request", json={"channel": "email", "identifier": identifier})
        assert resp.status_code == 200
    limited = client.post("/auth/otp/reset/request", json={"channel": "email", "identifier": identifier})
    assert limited.status_code == 429


# ----------------------------- Youth web portal -----------------------------

def test_portal_reset_full_happy_path(client):
    register_user(client, email="portalreset@test.com", phone="23279000009", password="OldPassw0rd!")

    page = client.get("/portal/forgot-password")
    token = _csrf_token(page.get_data(as_text=True))
    step2 = client.post("/portal/forgot-password", data={
        "csrf_token": token, "step": "contact", "channel": "email", "identifier": "portalreset@test.com",
    })
    assert step2.status_code == 200
    code = _latest_reset_code("portalreset@test.com")

    token2 = _csrf_token(step2.get_data(as_text=True))
    step3 = client.post("/portal/forgot-password", data={"csrf_token": token2, "step": "code", "otp_code": code})
    assert "is verified" in step3.get_data(as_text=True)

    token3 = _csrf_token(step3.get_data(as_text=True))
    final = client.post("/portal/forgot-password", data={
        "csrf_token": token3, "step": "confirm", "otp_code": code,
        "new_password": "BrandNewPortal1!", "confirm_password": "BrandNewPortal1!",
    })
    assert "Your password was changed" in final.get_data(as_text=True)

    old_login = client.post("/portal/login", data={
        "csrf_token": _csrf_token(client.get("/portal/login").get_data(as_text=True)),
        "identifier": "portalreset@test.com", "password": "OldPassw0rd!",
    })
    assert "Incorrect" in old_login.get_data(as_text=True)

    new_login = client.post("/portal/login", data={
        "csrf_token": _csrf_token(client.get("/portal/login").get_data(as_text=True)),
        "identifier": "portalreset@test.com", "password": "BrandNewPortal1!",
    })
    assert new_login.status_code == 302


def test_portal_reset_cannot_skip_to_confirm_step(client):
    """Same un-skippable-step guard as portal_register()'s details step."""
    register_user(client, email="skipreset@test.com", phone="23279000010")
    resp = client.post("/portal/forgot-password", data={
        "csrf_token": _csrf_token(client.get("/portal/forgot-password").get_data(as_text=True)),
        "step": "confirm", "otp_code": "000000", "new_password": "Whatever123!", "confirm_password": "Whatever123!",
    })
    assert "verify your contact details" in resp.get_data(as_text=True)


def test_portal_reset_rejects_mismatched_passwords(client):
    register_user(client, email="mismatchreset@test.com", phone="23279000011")
    page = client.get("/portal/forgot-password")
    token = _csrf_token(page.get_data(as_text=True))
    step2 = client.post("/portal/forgot-password", data={
        "csrf_token": token, "step": "contact", "channel": "email", "identifier": "mismatchreset@test.com",
    })
    code = _latest_reset_code("mismatchreset@test.com")
    token2 = _csrf_token(step2.get_data(as_text=True))
    step3 = client.post("/portal/forgot-password", data={"csrf_token": token2, "step": "code", "otp_code": code})
    token3 = _csrf_token(step3.get_data(as_text=True))
    final = client.post("/portal/forgot-password", data={
        "csrf_token": token3, "step": "confirm", "otp_code": code,
        "new_password": "OnePassword1!", "confirm_password": "DifferentPassword1!",
    })
    assert "do not match" in final.get_data(as_text=True)


# ----------------------------- Employer web portal -----------------------------

def test_employer_reset_full_happy_path(client):
    register_employer(client, email="employerreset@test.com", password="OldEmployerPw1!")
    client.post("/employer/logout")

    page = client.get("/employer/forgot-password")
    token = _csrf_token(page.get_data(as_text=True))
    step2 = client.post("/employer/forgot-password", data={"csrf_token": token, "step": "contact", "email": "employerreset@test.com"})
    code = _latest_reset_code("employer:employerreset@test.com")

    token2 = _csrf_token(step2.get_data(as_text=True))
    step3 = client.post("/employer/forgot-password", data={"csrf_token": token2, "step": "code", "otp_code": code})
    assert "is verified" in step3.get_data(as_text=True)

    token3 = _csrf_token(step3.get_data(as_text=True))
    final = client.post("/employer/forgot-password", data={
        "csrf_token": token3, "step": "confirm", "otp_code": code,
        "new_password": "BrandNewEmployer1!", "confirm_password": "BrandNewEmployer1!",
    })
    assert "Your password was changed" in final.get_data(as_text=True)

    old_login = client.post("/employer/login", data={
        "csrf_token": _csrf_token(client.get("/employer/login").get_data(as_text=True)),
        "email": "employerreset@test.com", "password": "OldEmployerPw1!",
    })
    assert "Incorrect" in old_login.get_data(as_text=True)

    new_login = client.post("/employer/login", data={
        "csrf_token": _csrf_token(client.get("/employer/login").get_data(as_text=True)),
        "email": "employerreset@test.com", "password": "BrandNewEmployer1!",
    })
    assert new_login.status_code == 302


def test_employer_reset_request_is_generic_whether_or_not_account_exists(client):
    real = client.post("/employer/forgot-password", data={
        "csrf_token": _csrf_token(client.get("/employer/forgot-password").get_data(as_text=True)),
        "step": "contact", "email": "noemployer@test.com",
    })
    register_employer(client, email="hasemployeraccount@test.com")
    client.post("/employer/logout")
    exists = client.post("/employer/forgot-password", data={
        "csrf_token": _csrf_token(client.get("/employer/forgot-password").get_data(as_text=True)),
        "step": "contact", "email": "hasemployeraccount@test.com",
    })
    # Both advance to the same "code" step regardless of account existence.
    assert "6-digit code" in real.get_data(as_text=True)
    assert "6-digit code" in exists.get_data(as_text=True)


# ----------------------------- Admin web portal -----------------------------

def test_admin_reset_without_totp_full_happy_path(client):
    _create_admin("adminreset@test.com", password="OldAdminPw1!")

    page = client.get("/admin/forgot-password")
    token = _csrf_token(page.get_data(as_text=True))
    step2 = client.post("/admin/forgot-password", data={"csrf_token": token, "step": "contact", "email": "adminreset@test.com"})
    code = _latest_reset_code("admin:adminreset@test.com")

    token2 = _csrf_token(step2.get_data(as_text=True))
    step3 = client.post("/admin/forgot-password", data={"csrf_token": token2, "step": "code", "otp_code": code})
    # No TOTP enabled -- goes straight to "confirm", not "totp".
    assert "Choose a new password" in step3.get_data(as_text=True)

    token3 = _csrf_token(step3.get_data(as_text=True))
    final = client.post("/admin/forgot-password", data={
        "csrf_token": token3, "step": "confirm", "otp_code": code,
        "new_password": "BrandNewAdmin1!", "confirm_password": "BrandNewAdmin1!",
    })
    assert "Your password was changed" in final.get_data(as_text=True)

    new_login = client.post("/admin/login", data={
        "csrf_token": _csrf_token(client.get("/admin/login").get_data(as_text=True)),
        "email": "adminreset@test.com", "password": "BrandNewAdmin1!",
    })
    assert new_login.status_code == 302


def test_admin_reset_with_totp_enabled_requires_the_totp_step(client):
    secret = pyotp.random_base32()
    _create_admin("totpadminreset@test.com", password="OldAdminPw1!", totp_secret=secret, totp_enabled=True)

    page = client.get("/admin/forgot-password")
    token = _csrf_token(page.get_data(as_text=True))
    step2 = client.post("/admin/forgot-password", data={"csrf_token": token, "step": "contact", "email": "totpadminreset@test.com"})
    code = _latest_reset_code("admin:totpadminreset@test.com")

    token2 = _csrf_token(step2.get_data(as_text=True))
    step3 = client.post("/admin/forgot-password", data={"csrf_token": token2, "step": "code", "otp_code": code})
    # TOTP IS enabled -- routed to the "totp" step, not straight to "confirm".
    body3 = step3.get_data(as_text=True)
    assert "two-factor authentication" in body3
    assert "Choose a new password" not in body3

    # Attempting to jump straight to "confirm" without the totp step is refused.
    skip = client.post("/admin/forgot-password", data={
        "csrf_token": _csrf_token(body3), "step": "confirm", "otp_code": code,
        "new_password": "ShouldNotWork1!", "confirm_password": "ShouldNotWork1!",
    })
    assert "authenticator step" in skip.get_data(as_text=True)

    # Wrong TOTP code is rejected.
    token3 = _csrf_token(body3)
    wrong = client.post("/admin/forgot-password", data={"csrf_token": token3, "step": "totp", "otp_code": code, "totp_code": "000000"})
    assert "Incorrect authenticator code" in wrong.get_data(as_text=True)

    # Real TOTP code advances to confirm.
    token4 = _csrf_token(wrong.get_data(as_text=True))
    real_totp = pyotp.TOTP(secret).now()
    step4 = client.post("/admin/forgot-password", data={"csrf_token": token4, "step": "totp", "otp_code": code, "totp_code": real_totp})
    assert "Choose a new password" in step4.get_data(as_text=True)

    token5 = _csrf_token(step4.get_data(as_text=True))
    final = client.post("/admin/forgot-password", data={
        "csrf_token": token5, "step": "confirm", "otp_code": code,
        "new_password": "BrandNewTotpAdmin1!", "confirm_password": "BrandNewTotpAdmin1!",
    })
    assert "Your password was changed" in final.get_data(as_text=True)

    with app_module.app.app_context():
        admin = app_module.Admin.query.filter_by(email="totpadminreset@test.com").first()
        assert app_module.check_password_hash(admin.password_hash, "BrandNewTotpAdmin1!")
        # 2FA enrollment itself survives a password reset -- resetting the
        # password is not a way to silently drop an attacker-unfriendly factor.
        assert admin.totp_enabled is True
