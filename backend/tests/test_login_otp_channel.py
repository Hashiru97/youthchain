"""
Regression coverage for the login OTP flow's channel choice (email vs
SMS) -- /auth/otp/request and /auth/otp/verify were email-only despite
password login already accepting phone OR email as the identifier, and
despite registration/password-reset already offering both channels (see
test_mobile_registration_otp.py, which this file deliberately mirrors).
Real gap: a phone-only-remembering user (an Orange/Africell number with
no email habitually used for this account) had no passwordless login
path at all.

Backward compatibility is the other half of what these tests prove: the
currently-installed mobile app never sends "channel" or "identifier" at
all (only "email"), and must keep working completely unchanged.
"""
import app as app_module
from conftest import register_user


def test_login_otp_request_backward_compatible_with_old_email_only_client(client, monkeypatch):
    """The exact request shape the currently-shipped app sends -- no
    "channel", no "identifier", just "email" -- must still work."""
    sent = []
    monkeypatch.setattr(app_module, "_send_email", lambda *a, **kw: sent.append(a) or True)
    register_user(client, email="oldclient-login@test.com", phone="23279000001")

    resp = client.post("/auth/otp/request", json={"email": "oldclient-login@test.com"})
    assert resp.status_code == 200
    assert len(sent) == 1
    assert sent[0][0] == "oldclient-login@test.com"

    with app_module.app.app_context():
        row = app_module.OTPCode.query.filter_by(
            identifier="oldclient-login@test.com", purpose="login", used=False,
        ).first()
        assert row is not None
        assert row.channel == "email"


def test_login_otp_request_sms_channel_uses_send_sms_for_an_existing_user(client, monkeypatch):
    sent_sms = []
    sent_email = []
    monkeypatch.setattr(app_module, "send_sms", lambda phone, body: sent_sms.append((phone, body)) or True)
    monkeypatch.setattr(app_module, "_send_email", lambda *a, **kw: sent_email.append(a) or True)
    register_user(client, email="smslogin@test.com", phone="23279001199")

    resp = client.post("/auth/otp/request", json={"channel": "sms", "identifier": "23279001199"})
    assert resp.status_code == 200
    assert len(sent_sms) == 1
    assert sent_sms[0][0] == "23279001199"
    assert sent_email == []

    with app_module.app.app_context():
        row = app_module.OTPCode.query.filter_by(
            identifier="23279001199", purpose="login", used=False,
        ).first()
        assert row is not None
        assert row.channel == "sms"


def test_login_otp_request_sms_channel_is_silent_for_an_unknown_phone(client, monkeypatch):
    """Anti-enumeration (S-07), same property the email channel already
    has: an unregistered phone gets the identical success-shaped response
    and no SMS is actually sent."""
    sent_sms = []
    monkeypatch.setattr(app_module, "send_sms", lambda phone, body: sent_sms.append((phone, body)) or True)

    resp = client.post("/auth/otp/request", json={"channel": "sms", "identifier": "23279999999"})
    assert resp.status_code == 200
    assert sent_sms == []


def test_login_otp_request_sms_channel_requires_a_non_empty_identifier(client):
    resp = client.post("/auth/otp/request", json={"channel": "sms", "identifier": ""})
    assert resp.status_code == 400


def test_login_otp_verify_sms_channel_returns_a_working_access_token(client, monkeypatch):
    monkeypatch.setattr(app_module, "send_sms", lambda *a, **kw: True)
    user = register_user(client, email="smsverify@test.com", phone="23279002233")
    client.post("/auth/otp/request", json={"channel": "sms", "identifier": "23279002233"})
    with app_module.app.app_context():
        code = app_module.OTPCode.query.filter_by(
            identifier="23279002233", purpose="login", used=False,
        ).first().code

    resp = client.post("/auth/otp/verify", json={"channel": "sms", "identifier": "23279002233", "code": code})
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["access_token"]
    assert body["user"]["id"] == user["user"]["id"]


def test_login_otp_verify_rejects_a_code_issued_for_the_other_channel(client, monkeypatch):
    """The channel actually gates which OTPCode row counts -- a code sent
    to this phone's email counterpart must not satisfy an sms-channel
    login, same channel-isolation guarantee registration already has."""
    monkeypatch.setattr(app_module, "send_sms", lambda *a, **kw: True)
    register_user(client, email="channelmismatch@test.com", phone="23279003344")
    client.post("/auth/otp/request", json={"channel": "email", "identifier": "channelmismatch@test.com"})
    with app_module.app.app_context():
        email_code = app_module.OTPCode.query.filter_by(
            identifier="channelmismatch@test.com", purpose="login", used=False,
        ).first().code

    resp = client.post("/auth/otp/verify", json={
        "channel": "sms", "identifier": "23279003344", "code": email_code,
    })
    assert resp.status_code == 400
    assert resp.get_json()["success"] is False


def test_login_via_sms_otp_for_a_suspended_user_is_blocked(client, monkeypatch):
    monkeypatch.setattr(app_module, "send_sms", lambda *a, **kw: True)
    user = register_user(client, email="suspendedsms@test.com", phone="23279004455")
    with app_module.app.app_context():
        u = app_module.db.session.get(app_module.User, user["user"]["id"])
        u.active = False
        app_module.db.session.commit()

    client.post("/auth/otp/request", json={"channel": "sms", "identifier": "23279004455"})
    with app_module.app.app_context():
        code = app_module.OTPCode.query.filter_by(
            identifier="23279004455", purpose="login", used=False,
        ).first().code

    resp = client.post("/auth/otp/verify", json={"channel": "sms", "identifier": "23279004455", "code": code})
    assert resp.status_code == 403
    assert resp.get_json()["suspended"] is True
