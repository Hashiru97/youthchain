"""
Regression coverage for the CSRF error handler's surface routing (real
bug found via live end-to-end testing of the credential-upload flow, not
hypothetical -- see _csrf_error's docstring in app.py). It used to
unconditionally render employer_login.html for every surface's CSRF
failure: a portal user or admin whose token went stale mid-session was
shown the wrong login page (wrong branding, wrong form) instead of their
own surface's.
"""
import re

import app as app_module
from conftest import register_user


def _csrf_token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "csrf token not found in page"
    return match.group(1)


def _create_admin(email, password="adminpass123", role="admin"):
    with app_module.app.app_context():
        admin = app_module.Admin(
            name="Ops", email=email, role=role, active=True,
            password_hash=app_module.generate_password_hash(password),
        )
        app_module.db.session.add(admin)
        app_module.db.session.commit()
    return email, password


def _create_employer(email, password="employerpass123"):
    with app_module.app.app_context():
        employer = app_module.Employer(
            name="Ops Co", email=email, account_type="individual",
            password_hash=app_module.generate_password_hash(password),
        )
        app_module.db.session.add(employer)
        app_module.db.session.commit()
    return email, password


def test_portal_csrf_failure_shows_the_portal_login_page(client):
    user = register_user(client)
    # register_user (see conftest) logs the mobile/JWT account in, but the
    # portal is session-cookie auth -- log in on that surface too so this
    # request reaches the CSRF check instead of the "not logged in"
    # redirect branch.
    with app_module.app.app_context():
        u = app_module.db.session.get(app_module.User, user["user"]["id"])
        u_email = u.email
    portal_page = client.get("/portal/login")
    token = _csrf_token(portal_page.get_data(as_text=True))
    client.post("/portal/login", data={"csrf_token": token, "identifier": u_email, "password": "password123"})

    resp = client.post("/portal/passport", data={"csrf_token": "not-a-real-token", "title": "x", "issuer": "x"})
    assert resp.status_code == 400
    body = resp.get_data(as_text=True)
    assert "Log In" in body or "Welcome back" in body
    assert "Employer Login" not in body
    assert "Admin Login" not in body


def test_admin_csrf_failure_shows_the_admin_login_page(client):
    admin_email, admin_password = _create_admin("csrfadmin@youthchain.test")
    login_page = client.get("/admin/login")
    token = _csrf_token(login_page.get_data(as_text=True))
    client.post("/admin/login", data={"csrf_token": token, "email": admin_email, "password": admin_password})

    resp = client.post("/admin/credentials/1/revoke", data={"csrf_token": "not-a-real-token"})
    assert resp.status_code == 400
    body = resp.get_data(as_text=True)
    assert "Admin Login" in body
    assert "Employer Login" not in body


def test_employer_csrf_failure_shows_the_employer_login_page(client):
    employer_email, employer_password = _create_employer("csrfemployer@youthchain.test")
    login_page = client.get("/employer/login")
    token = _csrf_token(login_page.get_data(as_text=True))
    client.post("/employer/login", data={"csrf_token": token, "email": employer_email, "password": employer_password})

    resp = client.post("/employer/post", data={"csrf_token": "not-a-real-token"})
    assert resp.status_code == 400
    assert "Employer Login" in resp.get_data(as_text=True)


def test_api_csrf_failure_returns_json_not_html():
    """
    No currently-existing route both matches API_PREFIXES and enforces
    session-cookie CSRF (csrf.protect() is only ever called from the
    three *_login_required decorators, all session-cookie surfaces, none
    of them API-prefixed) -- JWT-authenticated API routes don't need CSRF
    protection at all, bearer tokens aren't ambient like cookies. This
    branch exists as a defensive guard for if that ever changes, so it's
    exercised directly rather than through a real request.
    """
    from flask_wtf.csrf import CSRFError

    with app_module.app.test_request_context("/login"):
        resp, status = app_module._csrf_error(CSRFError("test"))
        assert status == 400
        assert resp.is_json
        assert "csrf" in resp.get_json()["error"].lower()
