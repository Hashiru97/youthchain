"""
Regression coverage for credential revocation (real gap found via a full
program-wide review): before this, a fraudulent or erroneously-issued
credential had no way to ever be invalidated -- YouthChainRegistry.sol's
registerCredential() was write-once with no counterpart, and /verify
treated "was ever registered" as permanently equivalent to "currently
valid." See YouthChainRegistry.sol's revokeCredential()/isValid() for the
on-chain half, and _revoke_credential_onchain[_async]/_check_onchain_valid
in app.py for the backend half.
"""
import io
import re

import app as app_module
from conftest import register_user, auth_headers, FAKE_PDF_BYTES

# Captured at module-load time, before conftest's autouse
# _no_real_blockchain_subprocess fixture stubs this attribute for every
# test -- see test_credentials.py's identical pattern/comment. Needed by
# the write-avoidance-check test below, which is specifically about this
# function's own real internal logic.
_real_write_onchain_tx_for_credential = app_module._write_onchain_tx_for_credential
_real_check_onchain_valid = app_module._check_onchain_valid


def _csrf_token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "csrf token not found in page"
    return match.group(1)


def _create_admin(email, password="adminpass123", role="admin"):
    with app_module.app.app_context():
        admin = app_module.Admin(
            name="Ops",
            email=email,
            role=role,
            active=True,
            password_hash=app_module.generate_password_hash(password),
        )
        app_module.db.session.add(admin)
        app_module.db.session.commit()
    return email, password


def _login_admin(client, email, password):
    page = client.get("/admin/login")
    token = _csrf_token(page.get_data(as_text=True))
    return client.post(
        "/admin/login",
        data={"csrf_token": token, "email": email, "password": password},
    )


def _issue_credential(client, token):
    resp = client.post(
        "/issue_credential",
        data={"title": "Welding Cert", "issuer": "Bo Technical Institute", "file": (io.BytesIO(FAKE_PDF_BYTES), "c.pdf")},
        headers=auth_headers(token),
        content_type="multipart/form-data",
    )
    assert resp.status_code == 201
    return resp.get_json()["credential_id"]


def test_admin_can_revoke_a_credential(client):
    user = register_user(client)
    cred_id = _issue_credential(client, user["access_token"])

    admin_email, admin_password = _create_admin("admin@youthchain.test")
    _login_admin(client, admin_email, admin_password)

    page = client.get("/admin/credentials")
    assert "Welding Cert" in page.get_data(as_text=True)
    token = _csrf_token(page.get_data(as_text=True))

    resp = client.post(
        f"/admin/credentials/{cred_id}/revoke",
        data={"csrf_token": token},
        follow_redirects=False,
    )
    assert resp.status_code == 302

    with app_module.app.app_context():
        cred = app_module.db.session.get(app_module.Credential, cred_id)
        assert cred.revoked_at is not None
        admin_id = app_module.Admin.query.filter_by(email=admin_email).first().id
        assert cred.revoked_by_admin_id == admin_id

        notif = app_module.Notification.query.filter_by(
            user_id=user["user"]["id"], type="credential_revoked"
        ).first()
        assert notif is not None
        assert "revoked" in notif.title.lower()


def test_revoking_an_already_revoked_credential_is_rejected(client):
    user = register_user(client)
    cred_id = _issue_credential(client, user["access_token"])

    admin_email, admin_password = _create_admin("admin2@youthchain.test")
    _login_admin(client, admin_email, admin_password)

    page = client.get("/admin/credentials")
    token = _csrf_token(page.get_data(as_text=True))
    client.post(f"/admin/credentials/{cred_id}/revoke", data={"csrf_token": token})

    # Second attempt, fresh token (same form would be gone from a real
    # reload since the button no longer renders, but the route itself
    # must refuse regardless of what the client sends).
    page2 = client.get("/admin/credentials")
    token2 = _csrf_token(page2.get_data(as_text=True))
    resp = client.post(f"/admin/credentials/{cred_id}/revoke", data={"csrf_token": token2})
    assert resp.status_code == 403


def test_verifier_role_admin_cannot_revoke_a_credential(client):
    user = register_user(client)
    cred_id = _issue_credential(client, user["access_token"])

    verifier_email, verifier_password = _create_admin("verifier@youthchain.test", role="verifier")
    _login_admin(client, verifier_email, verifier_password)

    resp = client.post(f"/admin/credentials/{cred_id}/revoke", data={"csrf_token": "irrelevant"})
    assert resp.status_code == 403

    with app_module.app.app_context():
        cred = app_module.db.session.get(app_module.Credential, cred_id)
        assert cred.revoked_at is None


def test_revoke_requires_admin_login(client):
    user = register_user(client)
    cred_id = _issue_credential(client, user["access_token"])

    resp = client.post(f"/admin/credentials/{cred_id}/revoke", data={"csrf_token": "irrelevant"}, follow_redirects=False)
    assert resp.status_code == 302
    assert "/admin/login" in resp.headers["Location"]


def test_verify_by_id_shows_revoked_status_for_a_revoked_credential(client):
    user = register_user(client)
    cred_id = _issue_credential(client, user["access_token"])

    admin_email, admin_password = _create_admin("admin3@youthchain.test")
    _login_admin(client, admin_email, admin_password)
    page = client.get("/admin/credentials")
    token = _csrf_token(page.get_data(as_text=True))
    client.post(f"/admin/credentials/{cred_id}/revoke", data={"csrf_token": token})

    # Log out of the admin session -- /verify/<id> is a public route.
    client.get("/admin/logout")

    verify_page = client.get(f"/verify/{cred_id}")
    body = verify_page.get_data(as_text=True)
    assert "REVOKED" in body
    assert "no longer be trusted" in body


def test_admin_credentials_page_shows_valid_and_revoked_status(client):
    user = register_user(client)
    valid_id = _issue_credential(client, user["access_token"])

    admin_email, admin_password = _create_admin("admin4@youthchain.test")
    _login_admin(client, admin_email, admin_password)

    page = client.get("/admin/credentials")
    body = page.get_data(as_text=True)
    assert "Valid" in body
    assert "Revoke" in body

    token = _csrf_token(body)
    client.post(f"/admin/credentials/{valid_id}/revoke", data={"csrf_token": token})

    page2 = client.get("/admin/credentials").get_data(as_text=True)
    assert "Revoked" in page2


def test_revoke_onchain_write_completion_updates_credential(client, monkeypatch):
    """
    Mirrors test_onchain_write_completion_updates_credential_and_notifies_owner
    for the revocation write path: conftest's autouse fixture already runs
    the background revoke inline -- this makes that inline write actually
    "succeed" instead of the default no-op stub, and confirms the tx hash
    lands on revoke_onchain_tx (not onchain_tx, which stays untouched).
    """
    monkeypatch.setattr(app_module, "_revoke_credential_onchain", lambda credential: "0xrevoketxhash")

    user = register_user(client)
    cred_id = _issue_credential(client, user["access_token"])

    admin_email, admin_password = _create_admin("admin5@youthchain.test")
    _login_admin(client, admin_email, admin_password)
    page = client.get("/admin/credentials")
    token = _csrf_token(page.get_data(as_text=True))
    client.post(f"/admin/credentials/{cred_id}/revoke", data={"csrf_token": token})

    with app_module.app.app_context():
        cred = app_module.db.session.get(app_module.Credential, cred_id)
        assert cred.revoke_onchain_tx == "0xrevoketxhash"
        assert cred.onchain_tx is None  # unrelated column, must stay untouched


def test_check_onchain_valid_parses_the_scripts_output(monkeypatch):
    """
    Direct unit coverage of _check_onchain_valid's stdout parsing --
    mirrors the existing pattern for _check_onchain_registered
    (test_hardhat_network_is_configurable), calling the real function
    since conftest's autouse fixture stubs app_module._check_onchain_valid
    for every other test.
    """
    monkeypatch.setattr(app_module.shutil, "which", lambda name: "npx")

    for stdout, expected in [("VALID:true", True), ("VALID:false", False)]:
        class FakeCompletedProcess:
            returncode = 0

        proc = FakeCompletedProcess()
        proc.stdout = stdout
        proc.stderr = ""
        monkeypatch.setattr(app_module.subprocess, "run", lambda *a, **k: proc)

        assert _real_check_onchain_valid("a" * 64) is expected


def test_write_onchain_tx_for_credential_uses_existence_check_not_validity_check(client, monkeypatch):
    """
    _write_onchain_tx_for_credential's write-avoidance check must stay on
    _check_onchain_registered (existence-only), not _check_onchain_valid
    -- a hash that's been registered-then-revoked still "exists" on-chain
    and registerCredential() would revert on a second attempt regardless
    of revocation status. If this ever got flipped to the validity check,
    a revoked hash would look "not valid" and this function would attempt
    a doomed re-registration on every single future issuance of the same
    content.
    """
    called = {"registered_checked": False, "valid_checked": False}
    monkeypatch.setattr(
        app_module, "_check_onchain_registered",
        lambda h: called.__setitem__("registered_checked", True) or True,
    )
    monkeypatch.setattr(
        app_module, "_check_onchain_valid",
        lambda h: called.__setitem__("valid_checked", True) or False,
    )

    fake_credential = app_module.Credential(id=999, user_id=1, title="x", issuer="y", hash="b" * 64)
    result = _real_write_onchain_tx_for_credential(fake_credential)

    assert result is None
    assert called["registered_checked"] is True
    assert called["valid_checked"] is False
