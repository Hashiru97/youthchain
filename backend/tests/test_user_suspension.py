"""
Regression coverage for User.active -- the real gap found via a full
admin-capability review: Employer and Admin both had a suspend/reinstate
lever with a per-request re-check; User (the actual youth account) had
neither. A compromised or abusive youth account previously had zero
admin-side remedy. Mirrors test_appeals.py's employer-suspension test
shape (_create_admin/_login_admin helpers, monkeypatched _send_email).
"""
import re

import app as app_module
from conftest import register_user, auth_headers
from test_portal import register_portal_user, _csrf_token


def _create_admin(email, password="adminpass123", role="admin"):
    with app_module.app.app_context():
        admin = app_module.Admin(
            name="Ops", email=email, role=role, active=True,
            password_hash=app_module.generate_password_hash(password),
        )
        app_module.db.session.add(admin)
        app_module.db.session.commit()
    return email, password


def _login_admin(client, email, password):
    page = client.get("/admin/login")
    token = _csrf_token(page.get_data(as_text=True))
    return client.post("/admin/login", data={"csrf_token": token, "email": email, "password": password})


def _suspend_user(client, user_id, admin_email="admin_suspend@youthchain.test", role="admin"):
    admin_email, admin_password = _create_admin(admin_email, role=role)
    _login_admin(client, admin_email, admin_password)
    # /admin/users itself is @admin_role_required("admin") -- a verifier
    # can't even load it to get a CSRF token, so pull one from
    # /admin/analytics instead (viewable by both roles) when testing the
    # RBAC-denial path; the token itself isn't role-specific.
    page = client.get("/admin/analytics" if role != "admin" else "/admin/users")
    token = _csrf_token(page.get_data(as_text=True))
    resp = client.post(f"/admin/users/{user_id}/suspend", data={"csrf_token": token})
    client.post("/admin/logout", data={"csrf_token": token})
    return resp


def _reinstate_user(client, user_id, admin_email="admin_reinstate@youthchain.test"):
    admin_email, admin_password = _create_admin(admin_email)
    _login_admin(client, admin_email, admin_password)
    page = client.get("/admin/users")
    token = _csrf_token(page.get_data(as_text=True))
    resp = client.post(f"/admin/users/{user_id}/reinstate", data={"csrf_token": token})
    client.post("/admin/logout", data={"csrf_token": token})
    return resp


def test_admin_users_page_lists_accounts_with_status_and_session_count(client):
    register_user(client, email="listme@test.com")
    admin_email, admin_password = _create_admin("lister@youthchain.test")
    _login_admin(client, admin_email, admin_password)

    page = client.get("/admin/users").get_data(as_text=True)
    assert "listme@test.com" in page
    assert "Active" in page
    assert ">1<" in page  # one active session from registration


def test_admin_users_search_filters_by_name_email_phone(client):
    register_user(client, email="findme@test.com", phone="700111", name="Findable Person")
    register_user(client, email="other@test.com", phone="700222", name="Someone Else")
    admin_email, admin_password = _create_admin("searcher@youthchain.test")
    _login_admin(client, admin_email, admin_password)

    page = client.get("/admin/users?q=findme").get_data(as_text=True)
    assert "Findable Person" in page
    assert "Someone Else" not in page


def test_admin_can_suspend_a_user(client):
    a = register_user(client, email="suspendme@test.com")
    with app_module.app.app_context():
        user_id = app_module.User.query.filter_by(email="suspendme@test.com").first().id

    resp = _suspend_user(client, user_id)
    assert resp.status_code == 302
    with app_module.app.app_context():
        assert app_module.db.session.get(app_module.User, user_id).active is False

    # Immediate, not just "next login": the already-issued JWT is now dead.
    still_works = client.get("/api/candidate/me", headers=auth_headers(a["access_token"]))
    assert still_works.status_code == 401


def test_suspending_revokes_every_active_session_across_both_channels(client):
    register_user(client, email="multidev@test.com")
    tok1 = client.post("/login", json={"email": "multidev@test.com", "password": "password123"}).get_json()["access_token"]
    tok2 = client.post("/login", json={"email": "multidev@test.com", "password": "password123"}).get_json()["access_token"]

    with app_module.app.app_context():
        user_id = app_module.User.query.filter_by(email="multidev@test.com").first().id

    _suspend_user(client, user_id)

    for tok in (tok1, tok2):
        resp = client.get("/api/candidate/me", headers=auth_headers(tok))
        assert resp.status_code == 401


def test_suspended_user_cannot_log_in_again_via_jwt(client):
    register_user(client, email="relogin@test.com")
    with app_module.app.app_context():
        user_id = app_module.User.query.filter_by(email="relogin@test.com").first().id
    _suspend_user(client, user_id)

    resp = client.post("/login", json={"email": "relogin@test.com", "password": "password123"})
    assert resp.status_code == 403
    assert resp.get_json()["suspended"] is True


def test_suspended_user_cannot_log_in_via_otp(client):
    register_user(client, email="otpsuspended@test.com")
    with app_module.app.app_context():
        user_id = app_module.User.query.filter_by(email="otpsuspended@test.com").first().id
    _suspend_user(client, user_id)

    client.post("/auth/otp/request", json={"email": "otpsuspended@test.com"})
    with app_module.app.app_context():
        code = app_module.OTPCode.query.filter_by(identifier="otpsuspended@test.com", used=False).first().code
    resp = client.post("/auth/otp/verify", json={"email": "otpsuspended@test.com", "code": code})
    assert resp.status_code == 403
    assert resp.get_json()["suspended"] is True


def test_suspended_user_cannot_log_in_via_portal_and_existing_session_dies(client):
    register_portal_user(client, email="portalsuspended@test.com")
    with app_module.app.app_context():
        user_id = app_module.User.query.filter_by(email="portalsuspended@test.com").first().id

    admin_client = app_module.app.test_client()
    _suspend_user(admin_client, user_id)

    # The already-logged-in browser is kicked out on its very next request.
    redirected = client.get("/portal/devices")
    assert redirected.status_code == 302
    assert "/portal/login" in redirected.headers["Location"]

    login_page = client.get("/portal/login")
    resp = client.post("/portal/login", data={
        "csrf_token": _csrf_token(login_page.get_data(as_text=True)),
        "identifier": "portalsuspended@test.com", "password": "Str0ng!Passw0rd9",
    })
    assert "suspended" in resp.get_data(as_text=True).lower()


def test_admin_can_reinstate_a_suspended_user(client):
    register_user(client, email="comeback@test.com")
    with app_module.app.app_context():
        user_id = app_module.User.query.filter_by(email="comeback@test.com").first().id
    _suspend_user(client, user_id)
    _reinstate_user(client, user_id)

    with app_module.app.app_context():
        assert app_module.db.session.get(app_module.User, user_id).active is True
    resp = client.post("/login", json={"email": "comeback@test.com", "password": "password123"})
    assert resp.status_code == 200


def test_verifier_role_cannot_suspend_a_user(client):
    """RBAC: matches @admin_role_required("admin") -- verifier is read-only for exactly this reason elsewhere."""
    register_user(client, email="rbactest@test.com")
    with app_module.app.app_context():
        user_id = app_module.User.query.filter_by(email="rbactest@test.com").first().id

    resp = _suspend_user(client, user_id, admin_email="verifier@youthchain.test", role="verifier")
    assert resp.status_code == 403
    with app_module.app.app_context():
        assert app_module.db.session.get(app_module.User, user_id).active is True


def test_suspending_a_user_notifies_them_by_email(client, monkeypatch):
    sent = []
    monkeypatch.setattr(
        app_module, "_send_email",
        lambda to_email, subject, body: sent.append((to_email, subject, body)) or True,
    )
    register_user(client, email="notifyme@test.com")
    with app_module.app.app_context():
        user_id = app_module.User.query.filter_by(email="notifyme@test.com").first().id
    _suspend_user(client, user_id)

    assert len(sent) == 1
    to_email, subject, body = sent[0]
    assert to_email == "notifyme@test.com"
    assert "suspended" in subject.lower()
