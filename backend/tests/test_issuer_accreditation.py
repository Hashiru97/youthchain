"""
Regression coverage for on-chain issuer accreditation (real gap found via
a full-codebase review): YouthChainRegistry.sol's accreditIssuer()/
revokeIssuer() (only the deploying wallet is auto-accredited in the
constructor) had no backend route and no admin UI at all -- there was no
operational path in this codebase to accredit a second credential-issuer
wallet once deployed. See _accredit_issuer_onchain/_revoke_issuer_onchain/
_list_issuers_onchain in app.py and blockchain/scripts/accreditIssuer.js/
revokeIssuer.js/listIssuers.js for the two halves.

Mirrors test_credential_revocation.py's structure/helpers.
"""
import re

import app as app_module

VALID_ADDRESS = "0x70997970c51812dc3a010c7d01b50e0d17dc79c8"


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


def test_admin_can_accredit_a_new_issuer(client, monkeypatch):
    monkeypatch.setattr(app_module, "_accredit_issuer_onchain", lambda address: "0xaccredittxhash")

    admin_email, admin_password = _create_admin("admin@youthchain.test")
    _login_admin(client, admin_email, admin_password)

    page = client.get("/admin/issuers")
    token = _csrf_token(page.get_data(as_text=True))

    resp = client.post(
        "/admin/issuers/accredit",
        data={"csrf_token": token, "address": VALID_ADDRESS},
        follow_redirects=False,
    )
    assert resp.status_code == 302
    assert resp.headers["Location"] == "/admin/issuers"

    with app_module.app.app_context():
        event = app_module.AnalyticsEvent.query.filter_by(event_type="issuer_accredited").first()
        assert event is not None
        assert VALID_ADDRESS in event.meta


def test_admin_can_revoke_an_issuer(client, monkeypatch):
    monkeypatch.setattr(app_module, "_revoke_issuer_onchain", lambda address: "0xrevoketxhash")

    admin_email, admin_password = _create_admin("admin2@youthchain.test")
    _login_admin(client, admin_email, admin_password)

    page = client.get("/admin/issuers")
    token = _csrf_token(page.get_data(as_text=True))

    resp = client.post(
        "/admin/issuers/revoke",
        data={"csrf_token": token, "address": VALID_ADDRESS},
        follow_redirects=False,
    )
    assert resp.status_code == 302

    with app_module.app.app_context():
        event = app_module.AnalyticsEvent.query.filter_by(event_type="issuer_revoked").first()
        assert event is not None
        assert VALID_ADDRESS in event.meta


def test_accrediting_an_invalid_address_is_rejected_before_touching_the_chain(client, monkeypatch):
    called = {"hit": False}
    monkeypatch.setattr(
        app_module, "_accredit_issuer_onchain",
        lambda address: called.__setitem__("hit", True) or "0xshouldnothappen",
    )

    admin_email, admin_password = _create_admin("admin3@youthchain.test")
    _login_admin(client, admin_email, admin_password)

    page = client.get("/admin/issuers")
    token = _csrf_token(page.get_data(as_text=True))

    resp = client.post(
        "/admin/issuers/accredit",
        data={"csrf_token": token, "address": "not-an-address"},
    )
    assert resp.status_code == 200
    assert "isn&#39;t a valid Ethereum address" in resp.get_data(as_text=True)
    assert called["hit"] is False


def test_accreditation_failure_shows_an_inline_error_not_a_redirect(client, monkeypatch):
    monkeypatch.setattr(app_module, "_accredit_issuer_onchain", lambda address: None)

    admin_email, admin_password = _create_admin("admin4@youthchain.test")
    _login_admin(client, admin_email, admin_password)

    page = client.get("/admin/issuers")
    token = _csrf_token(page.get_data(as_text=True))

    resp = client.post(
        "/admin/issuers/accredit",
        data={"csrf_token": token, "address": VALID_ADDRESS},
    )
    assert resp.status_code == 200
    assert "failed" in resp.get_data(as_text=True).lower()

    with app_module.app.app_context():
        event = app_module.AnalyticsEvent.query.filter_by(event_type="issuer_accredited").first()
        assert event is None


def test_verifier_role_cannot_accredit_or_revoke_issuers(client):
    verifier_email, verifier_password = _create_admin("verifier@youthchain.test", role="verifier")
    _login_admin(client, verifier_email, verifier_password)

    resp = client.post("/admin/issuers/accredit", data={"csrf_token": "irrelevant", "address": VALID_ADDRESS})
    assert resp.status_code == 403

    resp2 = client.post("/admin/issuers/revoke", data={"csrf_token": "irrelevant", "address": VALID_ADDRESS})
    assert resp2.status_code == 403


def test_issuers_page_requires_admin_login(client):
    resp = client.get("/admin/issuers", follow_redirects=False)
    assert resp.status_code == 302
    assert "/admin/login" in resp.headers["Location"]


def test_admin_issuers_page_lists_current_accreditation_state(client, monkeypatch):
    monkeypatch.setattr(
        app_module, "_list_issuers_onchain",
        lambda: [
            {"address": "0xf39fd6e51aad88f6f4ce6ab8827279cfffb9226", "accredited": True},
            {"address": VALID_ADDRESS, "accredited": False},
        ],
    )

    admin_email, admin_password = _create_admin("admin5@youthchain.test")
    _login_admin(client, admin_email, admin_password)

    body = client.get("/admin/issuers").get_data(as_text=True)
    assert "0xf39fd6e51aad88f6f4ce6ab8827279cfffb9226" in body
    assert VALID_ADDRESS in body
    assert "Accredited" in body
    assert "Revoked" in body


def test_admin_issuers_page_shows_error_when_chain_unreachable(client, monkeypatch):
    monkeypatch.setattr(app_module, "_list_issuers_onchain", lambda: None)

    admin_email, admin_password = _create_admin("admin6@youthchain.test")
    _login_admin(client, admin_email, admin_password)

    body = client.get("/admin/issuers").get_data(as_text=True)
    assert "Couldn" in body and "reach the blockchain node" in body
