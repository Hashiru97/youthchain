"""
Regression coverage for the phone-lookup exact-match gap found alongside
the Twilio E.164 fix (see test_push_sms_providers.py's own normalization
tests): every phone-based account lookup in app.py used to do a plain
`User.phone == identifier` comparison, so a user who registered typing
their number in one Sierra Leone format (e.g. "076123456") could not log
in, request an OTP, or reset their password by typing an equivalent
format (e.g. "+23276123456") -- see _phone_lookup_candidates's own
docstring. These exercise the fix at both the unit level (the candidate
generator itself) and through the real HTTP endpoints it's wired into.
"""
from conftest import register_user, auth_headers


def test_phone_lookup_candidates_expands_every_sierra_leone_format():
    import app as app_module

    candidates = app_module._phone_lookup_candidates("076123456")
    assert candidates == {
        "076123456", "76123456", "076123456", "23276123456", "+23276123456",
    }


def test_phone_lookup_candidates_from_e164_input_still_includes_local_formats():
    import app as app_module

    candidates = app_module._phone_lookup_candidates("+23276123456")
    assert "76123456" in candidates
    assert "076123456" in candidates
    assert "23276123456" in candidates
    assert "+23276123456" in candidates


def test_phone_lookup_candidates_on_a_non_phone_identifier_is_just_itself():
    """An email (or anything else with no recognizable 8-digit Sierra
    Leone subscriber number inside it) must yield ONLY itself as a
    candidate -- this is what keeps User.phone.in_(...) exactly as safe
    as the plain `==` it replaces for every non-phone call site."""
    import app as app_module

    candidates = app_module._phone_lookup_candidates("alice@test.com")
    assert candidates == {"alice@test.com"}


# ----------------- Real endpoints: register in one format, reach the account via another -----------------

def test_login_finds_the_account_when_typed_phone_format_differs_from_registered(client):
    register_user(client, phone="076123456", email="fmt1@test.com", password="password123")

    resp = client.post(
        "/login", json={"phone": "+23276123456", "password": "password123"},
    )
    assert resp.status_code == 200
    assert resp.get_json()["user"]["phone"] == "076123456"


def test_login_still_rejects_wrong_password_after_format_normalized_match(client):
    register_user(client, phone="076123457", email="fmt2@test.com", password="password123")

    resp = client.post(
        "/login", json={"phone": "23276123457", "password": "wrong-password"},
    )
    assert resp.status_code == 401


def test_otp_login_request_and_verify_work_with_a_differently_formatted_phone(client, monkeypatch):
    import app as app_module

    register_user(client, phone="076123458", email="fmt3@test.com", password="password123")

    captured = {}
    monkeypatch.setattr(
        app_module, "send_sms",
        lambda phone, body: captured.update(phone=phone, body=body) or True,
    )

    resp = client.post(
        "/auth/otp/request", json={"channel": "sms", "identifier": "76123458"},
    )
    assert resp.status_code == 200
    assert captured, "send_sms should have been called for a real, existing account"

    code = captured["body"].split("Your login code is ")[1].split(".")[0]
    resp = client.post(
        "/auth/otp/verify",
        json={"channel": "sms", "identifier": "76123458", "code": code},
    )
    assert resp.status_code == 200
    assert resp.get_json()["user"]["phone"] == "076123458"


def test_registration_duplicate_check_catches_a_differently_formatted_existing_phone(client):
    register_user(client, phone="076123459", email="fmt4@test.com", password="password123")

    resp = client.post(
        "/register",
        json={
            "name": "Someone Else",
            "phone": "+23276123459",
            "email": "different-email@test.com",
            "password": "password123",
            "consent": True,
        },
    )
    assert resp.status_code == 400
    assert resp.get_json()["success"] is False
