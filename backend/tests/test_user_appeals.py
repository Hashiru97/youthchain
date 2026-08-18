"""
Regression coverage for UserAppeal -- the youth (User) counterpart to
EmployerAppeal. Real gap found via a full admin-capability review: once
admin_suspend_user() could lock a youth account out entirely (see
User.active's docstring), the only way back was an admin noticing and
reinstating unprompted -- no appeal path existed for the population this
whole platform serves, even though the employer side already had one.
Mirrors test_appeals.py's structure and helpers (_create_admin/_login_admin,
_suspend/_submit_appeal shape) for the parallel feature.
"""
import re

import app as app_module
from conftest import register_user
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


def _suspend_user(client, user_id, admin_email="admin_suspend@youthchain.test"):
    admin_email, admin_password = _create_admin(admin_email)
    _login_admin(client, admin_email, admin_password)
    page = client.get("/admin/users")
    token = _csrf_token(page.get_data(as_text=True))
    client.post(f"/admin/users/{user_id}/suspend", data={"csrf_token": token})
    client.post("/admin/logout", data={"csrf_token": token})


def _submit_appeal(client, email, password, message="I believe this is a mistake."):
    page = client.get("/portal/appeal")
    token = _csrf_token(page.get_data(as_text=True))
    return client.post(
        "/portal/appeal",
        data={"csrf_token": token, "email": email, "password": password, "message": message},
    )


def test_suspended_user_can_submit_an_appeal(client):
    register_user(client, email="appealer@test.com")
    with app_module.app.app_context():
        user_id = app_module.User.query.filter_by(email="appealer@test.com").first().id
    _suspend_user(client, user_id)

    resp = _submit_appeal(client, "appealer@test.com", "password123")
    assert resp.status_code == 200
    assert "Your appeal has been submitted" in resp.get_data(as_text=True)

    with app_module.app.app_context():
        appeal = app_module.UserAppeal.query.filter_by(user_id=user_id).first()
        assert appeal is not None
        assert appeal.status == "open"
        assert appeal.message == "I believe this is a mistake."


def test_appeal_rejects_wrong_password(client):
    register_user(client, email="wrongpw@test.com")
    with app_module.app.app_context():
        user_id = app_module.User.query.filter_by(email="wrongpw@test.com").first().id
    _suspend_user(client, user_id)

    resp = _submit_appeal(client, "wrongpw@test.com", "totally-wrong-password")
    assert "Incorrect email or password" in resp.get_data(as_text=True)
    with app_module.app.app_context():
        assert app_module.UserAppeal.query.filter_by(user_id=user_id).count() == 0


def test_appeal_password_guessing_is_rate_limited_before_any_success(client, monkeypatch):
    """Same real gap and same fix as test_appeals.py's equivalent -- both
    surfaces (web form and mobile JSON) share _submit_user_appeal_internal,
    so this one test covers both; the JSON path is exercised separately
    below for its distinct status-code shape."""
    monkeypatch.setattr(app_module, "_LOGIN_MAX_ATTEMPTS", 2)

    register_user(client, email="guessme@test.com")
    with app_module.app.app_context():
        user_id = app_module.User.query.filter_by(email="guessme@test.com").first().id
    _suspend_user(client, user_id)

    for _ in range(2):
        resp = _submit_appeal(client, "guessme@test.com", "wrong-guess")
        assert "Incorrect email or password" in resp.get_data(as_text=True)

    resp = _submit_appeal(client, "guessme@test.com", "password123")
    assert resp.status_code == 429
    assert "Too many attempts" in resp.get_data(as_text=True)
    with app_module.app.app_context():
        assert app_module.UserAppeal.query.filter_by(user_id=user_id).count() == 0


def test_mobile_api_appeal_password_guessing_is_rate_limited(client, monkeypatch):
    monkeypatch.setattr(app_module, "_LOGIN_MAX_ATTEMPTS", 2)

    register_user(client, email="mobileguess@test.com")
    with app_module.app.app_context():
        user_id = app_module.User.query.filter_by(email="mobileguess@test.com").first().id
    _suspend_user(client, user_id)

    for _ in range(2):
        resp = client.post("/api/appeal", json={"email": "mobileguess@test.com", "password": "nope", "message": "x"})
        assert resp.status_code == 400

    resp = client.post("/api/appeal", json={"email": "mobileguess@test.com", "password": "password123", "message": "x"})
    assert resp.status_code == 429
    with app_module.app.app_context():
        assert app_module.UserAppeal.query.filter_by(user_id=user_id).count() == 0


def test_active_user_cannot_appeal(client):
    register_user(client, email="notsuspended@test.com")
    resp = _submit_appeal(client, "notsuspended@test.com", "password123")
    assert "This account is not currently suspended" in resp.get_data(as_text=True)


def test_appeal_requires_a_message(client):
    register_user(client, email="nomessage@test.com")
    with app_module.app.app_context():
        user_id = app_module.User.query.filter_by(email="nomessage@test.com").first().id
    _suspend_user(client, user_id)

    resp = _submit_appeal(client, "nomessage@test.com", "password123", message="")
    assert "Please describe why" in resp.get_data(as_text=True)


def test_duplicate_open_appeal_is_rejected(client):
    register_user(client, email="dupe@test.com")
    with app_module.app.app_context():
        user_id = app_module.User.query.filter_by(email="dupe@test.com").first().id
    _suspend_user(client, user_id)

    first = _submit_appeal(client, "dupe@test.com", "password123")
    assert "Your appeal has been submitted" in first.get_data(as_text=True)

    second = _submit_appeal(client, "dupe@test.com", "password123", message="Trying again.")
    assert "already have an appeal under review" in second.get_data(as_text=True)

    with app_module.app.app_context():
        assert app_module.UserAppeal.query.filter_by(user_id=user_id).count() == 1


def test_appeal_rate_limited(client, monkeypatch):
    monkeypatch.setattr(app_module, "_APPEAL_MAX_ATTEMPTS", 1)

    register_user(client, email="ratelimited@test.com")
    with app_module.app.app_context():
        user_id = app_module.User.query.filter_by(email="ratelimited@test.com").first().id
    _suspend_user(client, user_id)

    first = _submit_appeal(client, "ratelimited@test.com", "password123")
    assert "Your appeal has been submitted" in first.get_data(as_text=True)

    with app_module.app.app_context():
        appeal = app_module.UserAppeal.query.filter_by(user_id=user_id).first()
        appeal.status = "denied"
        app_module.db.session.commit()

    second = _submit_appeal(client, "ratelimited@test.com", "password123", message="Second try.")
    assert "Too many appeal attempts" in second.get_data(as_text=True)


def test_admin_can_deny_an_appeal_leaving_user_suspended(client):
    register_user(client, email="denyme@test.com", name="Deny Me")
    with app_module.app.app_context():
        user_id = app_module.User.query.filter_by(email="denyme@test.com").first().id
    _suspend_user(client, user_id)
    _submit_appeal(client, "denyme@test.com", "password123")

    admin_email, admin_password = _create_admin("user_appeal_admin1@youthchain.test")
    _login_admin(client, admin_email, admin_password)

    page = client.get("/admin/user_appeals")
    assert page.status_code == 200
    assert "Deny Me" in page.get_data(as_text=True)

    with app_module.app.app_context():
        appeal_id = app_module.UserAppeal.query.filter_by(user_id=user_id).first().id

    token = _csrf_token(page.get_data(as_text=True))
    resp = client.post(
        f"/admin/user_appeals/{appeal_id}/resolve",
        data={"csrf_token": token, "decision": "deny"},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app_module.app.app_context():
        appeal = app_module.UserAppeal.query.get(appeal_id)
        assert appeal.status == "denied"
        assert appeal.reviewed_by_admin_id is not None
        assert app_module.User.query.get(user_id).active is False


def test_admin_can_reinstate_user_from_an_appeal(client):
    register_user(client, email="reinstateme@test.com")
    with app_module.app.app_context():
        user_id = app_module.User.query.filter_by(email="reinstateme@test.com").first().id
    _suspend_user(client, user_id)
    _submit_appeal(client, "reinstateme@test.com", "password123")

    admin_email, admin_password = _create_admin("user_appeal_admin2@youthchain.test")
    _login_admin(client, admin_email, admin_password)

    with app_module.app.app_context():
        appeal_id = app_module.UserAppeal.query.filter_by(user_id=user_id).first().id

    page = client.get("/admin/user_appeals")
    token = _csrf_token(page.get_data(as_text=True))
    client.post(f"/admin/user_appeals/{appeal_id}/resolve", data={"csrf_token": token, "decision": "reinstate"})

    with app_module.app.app_context():
        appeal = app_module.UserAppeal.query.get(appeal_id)
        assert appeal.status == "reinstated"
        assert app_module.User.query.get(user_id).active is True

    # Can actually log back in -- not just a flag flip the login path ignores.
    login_resp = client.post("/login", json={"email": "reinstateme@test.com", "password": "password123"})
    assert login_resp.status_code == 200


def test_verifier_role_admin_cannot_reach_user_appeals(client):
    verifier_email, verifier_password = _create_admin("user_appeal_verifier@youthchain.test", role="verifier")
    _login_admin(client, verifier_email, verifier_password)
    resp = client.get("/admin/user_appeals")
    assert resp.status_code == 403


def test_suspending_a_user_notifies_them_by_email_with_an_appeal_link(client, monkeypatch):
    sent = []
    monkeypatch.setattr(
        app_module, "_send_email",
        lambda to_email, subject, body: sent.append((to_email, subject, body)) or True,
    )

    register_user(client, email="linktest@test.com")
    with app_module.app.app_context():
        user_id = app_module.User.query.filter_by(email="linktest@test.com").first().id
    _suspend_user(client, user_id)

    assert len(sent) == 1
    to_email, subject, body = sent[0]
    assert to_email == "linktest@test.com"
    assert "suspended" in subject.lower()
    assert "/portal/appeal" in body


def test_suspended_portal_login_page_links_to_the_appeal_form(client):
    register_portal_user(client, email="portalappeal@test.com")
    with app_module.app.app_context():
        user_id = app_module.User.query.filter_by(email="portalappeal@test.com").first().id
    _suspend_user(client, user_id)

    login_page = client.get("/portal/login")
    token = _csrf_token(login_page.get_data(as_text=True))
    resp = client.post("/portal/login", data={
        "csrf_token": token, "identifier": "portalappeal@test.com", "password": "Str0ng!Passw0rd9",
    })
    body = resp.get_data(as_text=True)
    assert "This account has been suspended" in body
    assert 'href="/portal/appeal"' in body


def test_mobile_api_appeal_endpoint_mirrors_the_web_form(client):
    """
    Mobile has no login-gated way to reach anything once suspended (JWT
    blocklisted at suspension time) -- /api/appeal is the unauthenticated
    JSON counterpart to /portal/appeal so the app doesn't have to send a
    suspended user to a browser just to contest it.
    """
    register_user(client, email="mobileappeal@test.com")
    with app_module.app.app_context():
        user_id = app_module.User.query.filter_by(email="mobileappeal@test.com").first().id
    _suspend_user(client, user_id)

    resp = client.post("/api/appeal", json={
        "email": "mobileappeal@test.com", "password": "password123",
        "message": "This was a mistake, please review.",
    })
    assert resp.status_code == 201
    body = resp.get_json()
    assert body["success"] is True
    assert "appeal_id" in body

    with app_module.app.app_context():
        appeal = app_module.UserAppeal.query.filter_by(user_id=user_id).first()
        assert appeal is not None
        assert appeal.message == "This was a mistake, please review."


def test_mobile_api_appeal_rejects_wrong_password(client):
    register_user(client, email="mobilewrong@test.com")
    with app_module.app.app_context():
        user_id = app_module.User.query.filter_by(email="mobilewrong@test.com").first().id
    _suspend_user(client, user_id)

    resp = client.post("/api/appeal", json={
        "email": "mobilewrong@test.com", "password": "nope", "message": "x",
    })
    assert resp.status_code == 400
    assert resp.get_json()["success"] is False
