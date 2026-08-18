"""
Regression coverage for the identity duplicate-detection heuristic
(_check_duplicate_signals / DuplicateFlag in app.py): fuzzy name/phone
matching flags likely duplicate youth accounts for admin review WITHOUT
ever blocking registration or auto-merging anything.
"""
import re

from conftest import register_user


def _csrf_token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "csrf token not found in page"
    return match.group(1)


def _create_admin(email, password="adminpass123", role="admin"):
    import app as app_module

    with app_module.app.app_context():
        admin = app_module.Admin(
            name="Ops",
            email=email,
            role=role,
            password_hash=app_module.generate_password_hash(password),
        )
        app_module.db.session.add(admin)
        app_module.db.session.commit()
    return email, password


def login_admin(client, email, password):
    login_page = client.get("/admin/login")
    token = _csrf_token(login_page.get_data(as_text=True))
    return client.post(
        "/admin/login",
        data={"csrf_token": token, "email": email, "password": password},
    )


def test_registration_never_blocked_by_duplicate_signals(client):
    """Two accounts with an identical name must both register successfully
    -- flagging is advisory only, never a gate."""
    a = register_user(client, name="Mohamed Kamara", email="a@test.com", phone="111")
    b = register_user(client, name="Mohamed Kamara", email="b@test.com", phone="222")
    assert "access_token" in a
    assert "access_token" in b


def test_near_identical_name_creates_a_flag(client):
    import app as app_module

    register_user(client, name="Mohamed Kamara", email="a@test.com", phone="111")
    register_user(client, name="Mohammed Kamara", email="b@test.com", phone="222")

    with app_module.app.app_context():
        flags = app_module.DuplicateFlag.query.all()
        assert len(flags) >= 1
        assert any("similar" in f.reason for f in flags)


def test_unrelated_accounts_do_not_create_a_flag(client):
    import app as app_module

    register_user(client, name="Alice Johnson", email="alice2@test.com", phone="777")
    register_user(client, name="Bob Zephyr", email="bob2@test.com", phone="888")

    with app_module.app.app_context():
        assert app_module.DuplicateFlag.query.count() == 0


def test_phone_suffix_collision_across_country_code_formatting_creates_a_flag(client):
    """076123456 (local format: leading 0 + 8-digit subscriber number) and
    +23276123456 (international format: 232 + the same 8-digit subscriber
    number) are the same real phone number written two ways."""
    import app as app_module

    register_user(client, name="Fatmata Sesay", email="c@test.com", phone="076123456")
    register_user(client, name="Someone Else", email="d@test.com", phone="+23276123456")

    with app_module.app.app_context():
        flags = app_module.DuplicateFlag.query.all()
        assert any("phone" in f.reason for f in flags)


def test_admin_can_view_and_dismiss_a_flag(client):
    import app as app_module

    register_user(client, name="Ibrahim Conteh", email="e@test.com", phone="333")
    register_user(client, name="Ibrahim Conteh", email="f@test.com", phone="444")

    with app_module.app.app_context():
        flag = app_module.DuplicateFlag.query.first()
        flag_id = flag.id

    email, password = _create_admin("dupreviewer@youthchain.test")
    login_admin(client, email, password)

    queue = client.get("/admin/duplicate_flags")
    assert queue.status_code == 200
    assert "Ibrahim Conteh" in queue.get_data(as_text=True)

    dismiss_token = _csrf_token(queue.get_data(as_text=True))
    resp = client.post(
        f"/admin/duplicate_flags/{flag_id}/resolve",
        data={"csrf_token": dismiss_token},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app_module.app.app_context():
        flag = app_module.DuplicateFlag.query.get(flag_id)
        assert flag.resolved is True
        assert flag.resolved_at is not None
        assert flag.resolved_by_admin_id is not None

    # dismissed flags drop out of the unresolved queue
    queue_after = client.get("/admin/duplicate_flags")
    assert "Ibrahim Conteh" not in queue_after.get_data(as_text=True)


def test_verifier_role_admin_cannot_reach_duplicate_flags(client):
    email, password = _create_admin("dupverifier@youthchain.test", role="verifier")
    login_admin(client, email, password)

    resp = client.get("/admin/duplicate_flags")
    assert resp.status_code == 403
