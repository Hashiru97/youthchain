"""
Regression coverage for an open-redirect vulnerability found via a full
security review: portal_login, employer_login, admin_login, and
admin_2fa_verify all honored a `?next=` query parameter with zero
validation, redirecting straight to whatever value it held after a
successful login. An attacker-crafted link like
`/portal/login?next=https://evil.example/phish` showed the victim the
real, correctly-branded login page (building trust), then silently
redirected them to an attacker-controlled page immediately after they
authenticated with real credentials.

Fixed via _safe_next_path(), which only accepts a same-origin relative
path (starts with exactly one "/", not "//", and carries no scheme or
netloc of its own).
"""
import re

import app as app_module
from conftest import register_user


def _csrf_token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "csrf token not found in page"
    return match.group(1)


def test_safe_next_path_rejects_absolute_and_protocol_relative_urls():
    assert app_module._safe_next_path(None) is None
    assert app_module._safe_next_path("") is None
    assert app_module._safe_next_path("https://evil.example/phish") is None
    assert app_module._safe_next_path("http://evil.example") is None
    assert app_module._safe_next_path("//evil.example/phish") is None
    assert app_module._safe_next_path("javascript:alert(1)") is None
    assert app_module._safe_next_path("/employer") == "/employer"
    assert app_module._safe_next_path("/employer/applications/5?x=1") == "/employer/applications/5?x=1"


def test_portal_login_ignores_an_absolute_url_next_param(client):
    email = "openredirect@test.com"
    from werkzeug.security import generate_password_hash

    with app_module.app.app_context():
        user = app_module.User(name="Redirect Test", phone="7009", email=email,
                                password_hash=generate_password_hash("password123"))
        app_module.db.session.add(user)
        app_module.db.session.commit()

    page = client.get("/portal/login?next=https://evil.example/phish")
    token = _csrf_token(page.get_data(as_text=True))
    resp = client.post(
        "/portal/login?next=https://evil.example/phish",
        data={"csrf_token": token, "identifier": email, "password": "password123"},
    )
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/portal", (
        f"login must redirect to the safe default, not the attacker-supplied "
        f"absolute URL -- got {resp.headers['Location']!r}"
    )


def test_employer_login_ignores_an_absolute_url_next_param(client):
    from werkzeug.security import generate_password_hash

    with app_module.app.app_context():
        employer = app_module.Employer(name="Redirect Test Co", email="openredirectemp@test.com",
                                        account_type="individual",
                                        password_hash=generate_password_hash("password123"))
        app_module.db.session.add(employer)
        app_module.db.session.commit()

    page = client.get("/employer/login?next=https://evil.example/phish")
    token = _csrf_token(page.get_data(as_text=True))
    resp = client.post(
        "/employer/login?next=https://evil.example/phish",
        data={"csrf_token": token, "email": "openredirectemp@test.com", "password": "password123"},
    )
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/employer"


def test_portal_login_still_honors_a_real_same_origin_next_param(client):
    """Guards the fix against breaking the legitimate use of `?next=`."""
    from werkzeug.security import generate_password_hash

    email = "realredirect@test.com"
    with app_module.app.app_context():
        user = app_module.User(name="Real Redirect", phone="7010", email=email,
                                password_hash=generate_password_hash("password123"))
        app_module.db.session.add(user)
        app_module.db.session.commit()

    page = client.get("/portal/login?next=%2Fportal%2Fpassport")
    token = _csrf_token(page.get_data(as_text=True))
    resp = client.post(
        "/portal/login?next=%2Fportal%2Fpassport",
        data={"csrf_token": token, "identifier": email, "password": "password123"},
    )
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/portal/passport"
