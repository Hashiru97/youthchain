"""
Regression coverage for real RBAC granularity within the Admin role
(admin vs verifier — see the Admin model docstring in app.py) and
immediate session revocation on deactivation.
"""
import re


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
    return admin_id, email, password


def _login(client, email, password):
    login_page = client.get("/admin/login")
    token = _csrf_token(login_page.get_data(as_text=True))
    return client.post(
        "/admin/login",
        data={"csrf_token": token, "email": email, "password": password},
    )


def test_verifier_cannot_reach_admin_accounts(client):
    _, email, password = _create_admin("verifier@youthchain.test", role="verifier")
    _login(client, email, password)

    resp = client.get("/admin/accounts")
    assert resp.status_code == 403


def test_verifier_can_still_reach_analytics(client):
    _, email, password = _create_admin("verifier2@youthchain.test", role="verifier")
    _login(client, email, password)

    resp = client.get("/admin/analytics")
    assert resp.status_code == 200
    # The "Manage Admins" link is admin-role-only, even though the page
    # itself is reachable.
    assert "Manage Admins" not in resp.get_data(as_text=True)


def test_admin_can_reach_accounts_and_sees_manage_link_on_analytics(client):
    _, email, password = _create_admin("admin1@youthchain.test", role="admin")
    _login(client, email, password)

    accounts_resp = client.get("/admin/accounts")
    assert accounts_resp.status_code == 200

    analytics_resp = client.get("/admin/analytics")
    assert "Manage Admins" in analytics_resp.get_data(as_text=True)


def test_admin_can_deactivate_another_admin(client):
    admin_id, admin_email, admin_password = _create_admin("boss@youthchain.test", role="admin")
    target_id, target_email, target_password = _create_admin("staff@youthchain.test", role="verifier")

    _login(client, admin_email, admin_password)
    page = client.get("/admin/accounts")
    token = _csrf_token(page.get_data(as_text=True))

    resp = client.post(
        f"/admin/accounts/{target_id}/deactivate",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 302

    import app as app_module
    with app_module.app.app_context():
        target = app_module.Admin.query.get(target_id)
        assert target.active is False


def test_admin_cannot_deactivate_own_account(client):
    admin_id, email, password = _create_admin("selfadmin@youthchain.test", role="admin")
    _login(client, email, password)
    # /admin/accounts deliberately renders no deactivate form for your own
    # row (correct UX — you shouldn't be offered a button that always
    # 403s), so it carries no CSRF token for this specific action. Source
    # one from a different page in the same session instead — Flask-WTF's
    # CSRF token is session-bound, not form-specific.
    page = client.get("/admin/analytics")
    token = _csrf_token(page.get_data(as_text=True))

    resp = client.post(
        f"/admin/accounts/{admin_id}/deactivate",
        data={"csrf_token": token},
    )
    assert resp.status_code == 403

    import app as app_module
    with app_module.app.app_context():
        me = app_module.Admin.query.get(admin_id)
        assert me.active is True


def test_deactivated_admin_session_is_revoked_immediately_not_just_blocked_at_next_login(client):
    """
    The core correctness property: deactivation must take effect on the
    NEXT request from an already-logged-in session, not merely block a
    future login attempt.
    """
    boss_id, boss_email, boss_password = _create_admin("boss2@youthchain.test", role="admin")
    target_id, target_email, target_password = _create_admin("victim@youthchain.test", role="verifier")

    # `target` logs in and confirms they can reach the dashboard.
    _login(client, target_email, target_password)
    assert client.get("/admin/analytics").status_code == 200

    # A second client acting as the admin deactivates `target` out from
    # under them.
    import app as app_module
    with app_module.app.app_context():
        target = app_module.Admin.query.get(target_id)
        target.active = False
        app_module.db.session.commit()

    # The original (still browser-side-authenticated-looking) session
    # should now be rejected, not silently still work until next login.
    resp = client.get("/admin/analytics")
    assert resp.status_code == 404  # admin_access_required 404s when no valid session/key


def test_deactivated_admin_cannot_log_in(client):
    admin_id, email, password = _create_admin("deactivated@youthchain.test", role="verifier")
    import app as app_module
    with app_module.app.app_context():
        admin = app_module.Admin.query.get(admin_id)
        admin.active = False
        app_module.db.session.commit()

    resp = _login(client, email, password)
    assert resp.status_code == 200
    assert "deactivated" in resp.get_data(as_text=True).lower()
