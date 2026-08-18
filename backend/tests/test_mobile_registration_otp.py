"""
Regression coverage for the mobile registration OTP flow's channel choice
(email vs SMS) -- real gap found via user feedback: registration was
email-only despite password login already accepting phone OR email as
the identifier. Also covers the new /auth/otp/register/verify pre-check
endpoint, added specifically because the mobile app is a stateless JWT
client with no server-side session to remember "this identifier was
verified" between screens the way portal_register() does.

Backward compatibility is the other half of what these tests prove: the
currently-installed mobile app never sends "channel" or "identifier" at
all (only "email"), and must keep working completely unchanged.
"""
import app as app_module
from conftest import register_user


def test_otp_request_backward_compatible_with_old_email_only_client(client, monkeypatch):
    """The exact request shape the currently-shipped app sends -- no
    "channel", no "identifier", just "email" -- must still work."""
    sent = []
    monkeypatch.setattr(app_module, "_send_email", lambda *a, **kw: sent.append(a) or True)

    resp = client.post("/auth/otp/register/request", json={"email": "oldclient@test.com"})
    assert resp.status_code == 200
    assert len(sent) == 1
    assert sent[0][0] == "oldclient@test.com"

    with app_module.app.app_context():
        row = app_module.OTPCode.query.filter_by(identifier="oldclient@test.com", used=False).first()
        assert row is not None
        assert row.channel == "email"


def test_otp_request_sms_channel_uses_send_sms(client, monkeypatch):
    sent_sms = []
    sent_email = []
    monkeypatch.setattr(app_module, "send_sms", lambda phone, body: sent_sms.append((phone, body)) or True)
    monkeypatch.setattr(app_module, "_send_email", lambda *a, **kw: sent_email.append(a) or True)

    resp = client.post("/auth/otp/register/request", json={"channel": "sms", "identifier": "23279001122"})
    assert resp.status_code == 200
    assert len(sent_sms) == 1
    assert sent_sms[0][0] == "23279001122"
    assert sent_email == []

    with app_module.app.app_context():
        row = app_module.OTPCode.query.filter_by(identifier="23279001122", used=False).first()
        assert row is not None
        assert row.channel == "sms"


def test_otp_request_sms_channel_requires_a_non_empty_identifier(client):
    resp = client.post("/auth/otp/register/request", json={"channel": "sms", "identifier": ""})
    assert resp.status_code == 400


def test_register_backward_compatible_with_old_client_no_channel_key(client, monkeypatch):
    """An old client's final /register call (no "channel" key at all) must
    still verify against the email-issued code, exactly as before."""
    monkeypatch.setattr(app_module, "ENFORCE_EMAIL_OTP_REG", True)
    client.post("/auth/otp/register/request", json={"email": "oldflow@test.com"})
    with app_module.app.app_context():
        code = app_module.OTPCode.query.filter_by(identifier="oldflow@test.com", used=False).first().code

    resp = client.post("/register", json={
        "name": "Old Flow", "phone": "23279445566", "email": "oldflow@test.com",
        "password": "password123", "consent": True, "otp_code": code,
    })
    assert resp.status_code == 201
    with app_module.app.app_context():
        assert app_module.User.query.filter_by(email="oldflow@test.com").first() is not None


def test_register_with_first_and_last_name_concatenates_into_a_single_name(client):
    """
    BL-47: the registration form split "Full Name" into two boxes -- the
    backend still stores one `name` string (every other part of this
    codebase reads User.name as a single display value), so this proves
    the concatenation, not a schema split.
    """
    resp = client.post("/register", json={
        "first_name": "Fatmata", "last_name": "Koroma", "phone": "23279556600",
        "email": "fatmata@test.com", "password": "password123", "consent": True,
    })
    assert resp.status_code == 201
    with app_module.app.app_context():
        user = app_module.User.query.filter_by(email="fatmata@test.com").first()
        assert user.name == "Fatmata Koroma"


def test_register_accepts_an_optional_ncra_id_as_a_field_distinct_from_phone(client):
    resp = client.post("/register", json={
        "first_name": "Santigie", "last_name": "Bangura", "phone": "23279556601",
        "email": "santigie@test.com", "password": "password123", "consent": True,
        "ncra_id": "SL-NCRA-00219944",
    })
    assert resp.status_code == 201
    with app_module.app.app_context():
        user = app_module.User.query.filter_by(email="santigie@test.com").first()
        assert user.ncra_id == "SL-NCRA-00219944"
        assert user.phone == "23279556601"  # unaffected -- no conflation with ncra_id


def test_register_without_an_ncra_id_leaves_it_null(client):
    resp = client.post("/register", json={
        "first_name": "Adama", "last_name": "Sesay", "phone": "23279556602",
        "email": "adama@test.com", "password": "password123", "consent": True,
    })
    assert resp.status_code == 201
    with app_module.app.app_context():
        user = app_module.User.query.filter_by(email="adama@test.com").first()
        assert user.ncra_id is None


def test_register_with_sms_channel_verifies_against_phone_issued_code(client, monkeypatch):
    monkeypatch.setattr(app_module, "ENFORCE_EMAIL_OTP_REG", True)
    phone = "23279556677"
    client.post("/auth/otp/register/request", json={"channel": "sms", "identifier": phone})
    with app_module.app.app_context():
        code = app_module.OTPCode.query.filter_by(identifier=phone, used=False).first().code

    resp = client.post("/register", json={
        "name": "SMS Flow", "phone": phone, "email": "smsflow@test.com",
        "password": "password123", "consent": True, "otp_code": code, "channel": "sms",
    })
    assert resp.status_code == 201
    with app_module.app.app_context():
        user = app_module.User.query.filter_by(phone=phone).first()
        assert user is not None
        assert user.email == "smsflow@test.com"


def test_register_with_sms_channel_rejects_a_code_issued_for_email(client, monkeypatch):
    """The channel actually gates which OTPCode row counts -- a code sent
    to this same phone number's email counterpart must not satisfy an
    sms-channel registration."""
    monkeypatch.setattr(app_module, "ENFORCE_EMAIL_OTP_REG", True)
    phone = "23279667788"
    client.post("/auth/otp/register/request", json={"channel": "email", "identifier": "notphone@test.com"})
    with app_module.app.app_context():
        wrong_code = app_module.OTPCode.query.filter_by(identifier="notphone@test.com", used=False).first().code

    resp = client.post("/register", json={
        "name": "Mismatch", "phone": phone, "email": "mismatch@test.com",
        "password": "password123", "consent": True, "otp_code": wrong_code, "channel": "sms",
    })
    assert resp.status_code == 400
    with app_module.app.app_context():
        assert app_module.User.query.filter_by(phone=phone).first() is None


def test_otp_verify_for_registration_precheck_succeeds_without_consuming(client, monkeypatch):
    """
    The whole point of this endpoint: a correct code reports success here
    AND is still valid for the real /register call afterward -- it must
    not mark the code used, unlike register()'s own verification.
    """
    monkeypatch.setattr(app_module, "ENFORCE_EMAIL_OTP_REG", True)
    client.post("/auth/otp/register/request", json={"channel": "email", "identifier": "precheck@test.com"})
    with app_module.app.app_context():
        code = app_module.OTPCode.query.filter_by(identifier="precheck@test.com", used=False).first().code

    precheck = client.post("/auth/otp/register/verify", json={
        "channel": "email", "identifier": "precheck@test.com", "code": code,
    })
    assert precheck.status_code == 200
    assert precheck.get_json()["success"] is True

    # Still valid -- the real registration call consumes it for real.
    resp = client.post("/register", json={
        "name": "Precheck Person", "phone": "23279778899", "email": "precheck@test.com",
        "password": "password123", "consent": True, "otp_code": code,
    })
    assert resp.status_code == 201


def test_otp_verify_for_registration_precheck_rejects_wrong_code(client, monkeypatch):
    monkeypatch.setattr(app_module, "ENFORCE_EMAIL_OTP_REG", True)
    client.post("/auth/otp/register/request", json={"channel": "email", "identifier": "wrongprecheck@test.com"})

    resp = client.post("/auth/otp/register/verify", json={
        "channel": "email", "identifier": "wrongprecheck@test.com", "code": "000000",
    })
    assert resp.status_code == 400
    assert resp.get_json()["success"] is False


def test_otp_verify_for_registration_precheck_is_rate_limited(client, monkeypatch):
    monkeypatch.setattr(app_module, "ENFORCE_EMAIL_OTP_REG", True)
    identifier = "precheckratelimit@test.com"
    # _OTP_MAX_ATTEMPTS is 5, shared across every _otp_rate_limited call
    # keyed on this identifier -- the request call below already consumes
    # one of the five, so only 4 more verify attempts fit before the 5th
    # (here, the 4th verify call) trips the limit.
    client.post("/auth/otp/register/request", json={"channel": "email", "identifier": identifier})

    for _ in range(4):
        resp = client.post("/auth/otp/register/verify", json={"channel": "email", "identifier": identifier, "code": "000000"})
        assert resp.status_code == 400

    limited = client.post("/auth/otp/register/verify", json={"channel": "email", "identifier": identifier, "code": "000000"})
    assert limited.status_code == 429
