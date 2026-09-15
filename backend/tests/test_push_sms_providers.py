"""
Regression coverage for the BL-38 follow-up: real Firebase push / Twilio
SMS SDK integration in send_push_notification()/send_sms(), gated on
env-configured credentials, with a verified fallback to the original
log-only stub behavior when unconfigured. No real Firebase/Twilio account
is used in tests -- the SDK calls themselves are monkeypatched, but the
gating logic (configured vs not, has-token vs not) is exercised for real.
"""
from unittest.mock import MagicMock

from conftest import register_user, auth_headers


def test_push_token_can_be_registered_and_cleared(client):
    import app as app_module

    user = register_user(client)
    resp = client.put(
        "/api/push_token",
        json={"push_token": "fcm-device-token-abc"},
        headers=auth_headers(user["access_token"]),
    )
    assert resp.status_code == 200

    with app_module.app.app_context():
        stored = app_module.db.session.get(app_module.User, user["user"]["id"])
        assert stored.push_token == "fcm-device-token-abc"

    # registering with no token (e.g. on logout) clears it
    resp = client.put(
        "/api/push_token",
        json={},
        headers=auth_headers(user["access_token"]),
    )
    assert resp.status_code == 200
    with app_module.app.app_context():
        stored = app_module.db.session.get(app_module.User, user["user"]["id"])
        assert stored.push_token is None


def test_push_token_registration_requires_auth(client):
    resp = client.put("/api/push_token", json={"push_token": "x"})
    assert resp.status_code == 401


def test_send_push_notification_is_a_noop_stub_when_unconfigured(client, monkeypatch):
    """When _firebase_app is None (no FIREBASE_CREDENTIALS_JSON configured),
    this must behave exactly like the original stub (log, return False,
    never raise). Explicitly monkeypatched rather than relying on the
    ambient environment having no credentials set -- a real Firebase
    project got wired up for local dev/testing (see backend/.env), so
    that assumption is no longer safely true, the same reasoning the
    neighboring test below already applies for the *configured* case."""
    import app as app_module

    monkeypatch.setattr(app_module, "_firebase_app", None)

    user = register_user(client)
    result = app_module.send_push_notification(user["user"]["id"], "Title", "Body")
    assert result is False


def test_send_push_notification_skips_when_user_has_no_token(client, monkeypatch):
    import app as app_module

    user = register_user(client)
    # Simulate a configured Firebase app without actually contacting Firebase.
    monkeypatch.setattr(app_module, "_firebase_app", MagicMock())

    with app_module.app.app_context():
        result = app_module.send_push_notification(user["user"]["id"], "Title", "Body")
    assert result is False


def test_send_push_notification_calls_fcm_when_configured_and_token_present(client, monkeypatch):
    import app as app_module
    import firebase_admin.messaging as fcm_messaging

    user = register_user(client)
    with app_module.app.app_context():
        u = app_module.db.session.get(app_module.User, user["user"]["id"])
        u.push_token = "fcm-device-token-xyz"
        app_module.db.session.commit()

    monkeypatch.setattr(app_module, "_firebase_app", MagicMock())
    fake_send = MagicMock(return_value="projects/x/messages/1")
    monkeypatch.setattr(fcm_messaging, "send", fake_send)

    with app_module.app.app_context():
        result = app_module.send_push_notification(user["user"]["id"], "You're hired!", "Congrats")
    assert result is True
    assert fake_send.call_count == 1
    sent_message = fake_send.call_args[0][0]
    assert sent_message.token == "fcm-device-token-xyz"
    assert sent_message.notification.title == "You're hired!"


def test_send_push_notification_returns_false_on_fcm_error(client, monkeypatch):
    import app as app_module
    import firebase_admin.messaging as fcm_messaging

    user = register_user(client)
    with app_module.app.app_context():
        u = app_module.db.session.get(app_module.User, user["user"]["id"])
        u.push_token = "fcm-device-token-xyz"
        app_module.db.session.commit()

    monkeypatch.setattr(app_module, "_firebase_app", MagicMock())
    monkeypatch.setattr(fcm_messaging, "send", MagicMock(side_effect=RuntimeError("FCM unavailable")))

    with app_module.app.app_context():
        result = app_module.send_push_notification(user["user"]["id"], "Title", "Body")
    assert result is False


def test_send_sms_is_a_noop_stub_when_unconfigured():
    """Default test environment has no TWILIO_* vars set, so _twilio_client
    is None -- must behave exactly like the original stub."""
    import app as app_module

    result = app_module.send_sms("+23276000000", "Your application was accepted")
    assert result is False


def test_send_sms_calls_twilio_when_configured(monkeypatch):
    import app as app_module

    fake_client = MagicMock()
    monkeypatch.setattr(app_module, "_twilio_client", fake_client)
    monkeypatch.setattr(app_module, "_TWILIO_FROM_NUMBER", "+15005550006")

    result = app_module.send_sms("+23276000000", "Your application was accepted")
    assert result is True
    fake_client.messages.create.assert_called_once_with(
        to="+23276000000", from_="+15005550006", body="Your application was accepted"
    )


# ----------------- _to_e164_sierra_leone() -----------------
# Real gap found while reviewing OTP-login SMS readiness: registration/
# profile screens never ask for a country code, so User.phone is a mix of
# local ("076123456"/"76123456") and international ("+23276123456")
# formats -- but Twilio's `to=` requires E.164 or it rejects the call
# outright (silently, from the caller's perspective, since send_sms/
# send_whatsapp swallow the exception). These pin the normalization that
# closes that gap.

def test_to_e164_sierra_leone_normalizes_local_format_with_leading_zero():
    import app as app_module

    assert app_module._to_e164_sierra_leone("076123456") == "+23276123456"


def test_to_e164_sierra_leone_normalizes_bare_eight_digit_subscriber_number():
    import app as app_module

    assert app_module._to_e164_sierra_leone("76123456") == "+23276123456"


def test_to_e164_sierra_leone_normalizes_local_country_code_with_no_plus():
    import app as app_module

    assert app_module._to_e164_sierra_leone("23276123456") == "+23276123456"


def test_to_e164_sierra_leone_leaves_already_e164_number_unchanged():
    import app as app_module

    assert app_module._to_e164_sierra_leone("+23276123456") == "+23276123456"


def test_to_e164_sierra_leone_leaves_unrecognized_shape_unchanged():
    """A number that doesn't match any recognized Sierra Leone shape (e.g.
    already has a different country's country code but no leading '+') is
    returned as-is rather than guessed at -- Twilio's own API is still
    the final validator for anything this doesn't recognize."""
    import app as app_module

    assert app_module._to_e164_sierra_leone("1234") == "1234"


def test_send_sms_normalizes_a_locally_formatted_phone_before_calling_twilio(monkeypatch):
    """The exact bug this closes: a user whose account has User.phone
    stored as "076123456" (typed that way at registration, never asked
    for a country code) must still actually receive the SMS once Twilio
    is configured, not have the call silently rejected."""
    import app as app_module

    fake_client = MagicMock()
    monkeypatch.setattr(app_module, "_twilio_client", fake_client)
    monkeypatch.setattr(app_module, "_TWILIO_FROM_NUMBER", "+15005550006")

    result = app_module.send_sms("076123456", "Your login code is 123456")
    assert result is True
    fake_client.messages.create.assert_called_once_with(
        to="+23276123456", from_="+15005550006", body="Your login code is 123456"
    )


def test_send_sms_returns_false_on_twilio_error(monkeypatch):
    import app as app_module

    fake_client = MagicMock()
    fake_client.messages.create.side_effect = RuntimeError("Twilio unavailable")
    monkeypatch.setattr(app_module, "_twilio_client", fake_client)
    monkeypatch.setattr(app_module, "_TWILIO_FROM_NUMBER", "+15005550006")

    result = app_module.send_sms("+23276000000", "Body")
    assert result is False


# ----------------- User.sms_alerts_enabled / GET /api/me / PUT /api/sms_alerts -----------------

def test_get_me_returns_the_authenticated_users_own_fields(client):
    user = register_user(client)
    resp = client.get("/api/me", headers=auth_headers(user["access_token"]))
    assert resp.status_code == 200
    body = resp.get_json()["user"]
    assert body["id"] == user["user"]["id"]
    assert body["sms_alerts_enabled"] is False


def test_get_me_requires_auth(client):
    resp = client.get("/api/me")
    assert resp.status_code == 401


def test_put_sms_alerts_sets_and_reads_back(client):
    user = register_user(client)
    headers = auth_headers(user["access_token"])

    resp = client.put("/api/sms_alerts", json={"enabled": True}, headers=headers)
    assert resp.status_code == 200
    assert resp.get_json()["sms_alerts_enabled"] is True
    assert client.get("/api/me", headers=headers).get_json()["user"]["sms_alerts_enabled"] is True

    resp = client.put("/api/sms_alerts", json={"enabled": False}, headers=headers)
    assert resp.get_json()["sms_alerts_enabled"] is False


def test_put_sms_alerts_requires_auth(client):
    resp = client.put("/api/sms_alerts", json={"enabled": True})
    assert resp.status_code == 401


# ----------------- notify_user(..., sms=True) gating -----------------

def test_notify_user_sends_sms_when_caller_requests_it_and_user_opted_in(client, monkeypatch):
    import app as app_module

    user = register_user(client)
    fake_client = MagicMock()
    monkeypatch.setattr(app_module, "_twilio_client", fake_client)
    monkeypatch.setattr(app_module, "_TWILIO_FROM_NUMBER", "+15005550006")

    with app_module.app.app_context():
        u = app_module.db.session.get(app_module.User, user["user"]["id"])
        u.sms_alerts_enabled = True
        app_module.db.session.commit()

        app_module.notify_user(user["user"]["id"], "job_alert_match", "New job matches your skills", "Data Clerk", sms=True)

    fake_client.messages.create.assert_called_once()
    kwargs = fake_client.messages.create.call_args.kwargs
    assert kwargs["to"] == user["user"]["phone"]
    assert "New job matches your skills" in kwargs["body"]


def test_notify_user_skips_sms_when_caller_does_not_request_it(client, monkeypatch):
    """Every pre-existing notify_user() call site defaults to sms=False --
    this must stay silent for those even for a user who opted in, since
    sms=True is what marks a notification *type* as SMS-eligible in the
    first place (see notify_user's own docstring)."""
    import app as app_module

    user = register_user(client)
    fake_client = MagicMock()
    monkeypatch.setattr(app_module, "_twilio_client", fake_client)
    monkeypatch.setattr(app_module, "_TWILIO_FROM_NUMBER", "+15005550006")

    with app_module.app.app_context():
        u = app_module.db.session.get(app_module.User, user["user"]["id"])
        u.sms_alerts_enabled = True
        app_module.db.session.commit()

        app_module.notify_user(user["user"]["id"], "new_message", "New message", "Hi there")

    fake_client.messages.create.assert_not_called()


def test_notify_user_skips_sms_when_user_never_opted_in(client, monkeypatch):
    import app as app_module

    user = register_user(client)
    fake_client = MagicMock()
    monkeypatch.setattr(app_module, "_twilio_client", fake_client)
    monkeypatch.setattr(app_module, "_TWILIO_FROM_NUMBER", "+15005550006")

    with app_module.app.app_context():
        app_module.notify_user(user["user"]["id"], "job_alert_match", "New job matches your skills", "Data Clerk", sms=True)

    fake_client.messages.create.assert_not_called()


# ----------------- send_whatsapp() -----------------

def test_send_whatsapp_is_a_noop_stub_when_unconfigured():
    """Default test environment has no TWILIO_WHATSAPP_* vars set (even if
    TWILIO_ACCOUNT_SID/AUTH_TOKEN/FROM_NUMBER were somehow present, the
    from-number and template SID are independent gates) -- must behave
    exactly like send_sms's own stub."""
    import app as app_module

    result = app_module.send_whatsapp("+23276000000", "Data Clerk")
    assert result is False


def test_send_whatsapp_is_a_noop_stub_when_twilio_client_configured_but_whatsapp_vars_are_not(monkeypatch):
    """A real Twilio client (SMS working) is not by itself enough --
    WhatsApp needs its own sender number and approved template SID."""
    import app as app_module

    monkeypatch.setattr(app_module, "_twilio_client", MagicMock())
    monkeypatch.setattr(app_module, "_TWILIO_WHATSAPP_FROM_NUMBER", None)
    monkeypatch.setattr(app_module, "_TWILIO_WHATSAPP_TEMPLATE_SID", None)

    result = app_module.send_whatsapp("+23276000000", "Data Clerk")
    assert result is False


def test_send_whatsapp_calls_twilio_content_api_when_fully_configured(monkeypatch):
    import app as app_module

    fake_client = MagicMock()
    monkeypatch.setattr(app_module, "_twilio_client", fake_client)
    monkeypatch.setattr(app_module, "_TWILIO_WHATSAPP_FROM_NUMBER", "+14155238886")
    monkeypatch.setattr(app_module, "_TWILIO_WHATSAPP_TEMPLATE_SID", "HXtestTemplateSid")

    result = app_module.send_whatsapp("+23276000000", "Data Clerk")
    assert result is True
    fake_client.messages.create.assert_called_once_with(
        to="whatsapp:+23276000000",
        from_="whatsapp:+14155238886",
        content_sid="HXtestTemplateSid",
        content_variables='{"1": "Data Clerk"}',
    )


def test_send_whatsapp_normalizes_a_locally_formatted_phone_before_calling_twilio(monkeypatch):
    """Same gap and fix as send_sms's own normalization test above --
    WhatsApp's `to=` requires E.164 just as strictly as SMS does."""
    import app as app_module

    fake_client = MagicMock()
    monkeypatch.setattr(app_module, "_twilio_client", fake_client)
    monkeypatch.setattr(app_module, "_TWILIO_WHATSAPP_FROM_NUMBER", "+14155238886")
    monkeypatch.setattr(app_module, "_TWILIO_WHATSAPP_TEMPLATE_SID", "HXtestTemplateSid")

    result = app_module.send_whatsapp("076123456", "Data Clerk")
    assert result is True
    fake_client.messages.create.assert_called_once_with(
        to="whatsapp:+23276123456",
        from_="whatsapp:+14155238886",
        content_sid="HXtestTemplateSid",
        content_variables='{"1": "Data Clerk"}',
    )


def test_send_whatsapp_returns_false_on_twilio_error(monkeypatch):
    import app as app_module

    fake_client = MagicMock()
    fake_client.messages.create.side_effect = RuntimeError("Twilio unavailable")
    monkeypatch.setattr(app_module, "_twilio_client", fake_client)
    monkeypatch.setattr(app_module, "_TWILIO_WHATSAPP_FROM_NUMBER", "+14155238886")
    monkeypatch.setattr(app_module, "_TWILIO_WHATSAPP_TEMPLATE_SID", "HXtestTemplateSid")

    result = app_module.send_whatsapp("+23276000000", "Data Clerk")
    assert result is False


# ----------------- User.whatsapp_alerts_enabled / GET /api/me / PUT /api/whatsapp_alerts -----------------

def test_get_me_includes_whatsapp_alerts_enabled(client):
    user = register_user(client)
    resp = client.get("/api/me", headers=auth_headers(user["access_token"]))
    assert resp.status_code == 200
    assert resp.get_json()["user"]["whatsapp_alerts_enabled"] is False


def test_put_whatsapp_alerts_sets_and_reads_back(client):
    user = register_user(client)
    headers = auth_headers(user["access_token"])

    resp = client.put("/api/whatsapp_alerts", json={"enabled": True}, headers=headers)
    assert resp.status_code == 200
    assert resp.get_json()["whatsapp_alerts_enabled"] is True
    assert client.get("/api/me", headers=headers).get_json()["user"]["whatsapp_alerts_enabled"] is True

    resp = client.put("/api/whatsapp_alerts", json={"enabled": False}, headers=headers)
    assert resp.get_json()["whatsapp_alerts_enabled"] is False


def test_put_whatsapp_alerts_requires_auth(client):
    resp = client.put("/api/whatsapp_alerts", json={"enabled": True})
    assert resp.status_code == 401


# ----------------- notify_user(..., whatsapp=True) gating -----------------

def test_notify_user_sends_whatsapp_when_caller_requests_it_and_user_opted_in(client, monkeypatch):
    import app as app_module

    user = register_user(client)
    fake_client = MagicMock()
    monkeypatch.setattr(app_module, "_twilio_client", fake_client)
    monkeypatch.setattr(app_module, "_TWILIO_WHATSAPP_FROM_NUMBER", "+14155238886")
    monkeypatch.setattr(app_module, "_TWILIO_WHATSAPP_TEMPLATE_SID", "HXtestTemplateSid")

    with app_module.app.app_context():
        u = app_module.db.session.get(app_module.User, user["user"]["id"])
        u.whatsapp_alerts_enabled = True
        app_module.db.session.commit()

        app_module.notify_user(user["user"]["id"], "job_alert_match", "New job matches your skills", "Data Clerk", whatsapp=True)

    fake_client.messages.create.assert_called_once()
    kwargs = fake_client.messages.create.call_args.kwargs
    assert kwargs["to"] == f"whatsapp:{user['user']['phone']}"
    assert kwargs["content_variables"] == '{"1": "Data Clerk"}'


def test_notify_user_skips_whatsapp_when_caller_does_not_request_it(client, monkeypatch):
    import app as app_module

    user = register_user(client)
    fake_client = MagicMock()
    monkeypatch.setattr(app_module, "_twilio_client", fake_client)
    monkeypatch.setattr(app_module, "_TWILIO_WHATSAPP_FROM_NUMBER", "+14155238886")
    monkeypatch.setattr(app_module, "_TWILIO_WHATSAPP_TEMPLATE_SID", "HXtestTemplateSid")

    with app_module.app.app_context():
        u = app_module.db.session.get(app_module.User, user["user"]["id"])
        u.whatsapp_alerts_enabled = True
        app_module.db.session.commit()

        app_module.notify_user(user["user"]["id"], "new_message", "New message", "Hi there")

    fake_client.messages.create.assert_not_called()


def test_notify_user_skips_whatsapp_when_user_never_opted_in(client, monkeypatch):
    import app as app_module

    user = register_user(client)
    fake_client = MagicMock()
    monkeypatch.setattr(app_module, "_twilio_client", fake_client)
    monkeypatch.setattr(app_module, "_TWILIO_WHATSAPP_FROM_NUMBER", "+14155238886")
    monkeypatch.setattr(app_module, "_TWILIO_WHATSAPP_TEMPLATE_SID", "HXtestTemplateSid")

    with app_module.app.app_context():
        app_module.notify_user(user["user"]["id"], "job_alert_match", "New job matches your skills", "Data Clerk", whatsapp=True)

    fake_client.messages.create.assert_not_called()


def test_notify_user_sms_and_whatsapp_opt_ins_are_independent(client, monkeypatch):
    """A user opted into SMS but not WhatsApp (or vice versa) should only
    ever receive the channel they actually opted into, even when a call
    site requests both -- same one-flag-per-real-choice reasoning as
    User.whatsapp_alerts_enabled's own docstring."""
    import app as app_module

    user = register_user(client)
    fake_client = MagicMock()
    monkeypatch.setattr(app_module, "_twilio_client", fake_client)
    monkeypatch.setattr(app_module, "_TWILIO_FROM_NUMBER", "+15005550006")
    monkeypatch.setattr(app_module, "_TWILIO_WHATSAPP_FROM_NUMBER", "+14155238886")
    monkeypatch.setattr(app_module, "_TWILIO_WHATSAPP_TEMPLATE_SID", "HXtestTemplateSid")

    with app_module.app.app_context():
        u = app_module.db.session.get(app_module.User, user["user"]["id"])
        u.sms_alerts_enabled = True
        u.whatsapp_alerts_enabled = False
        app_module.db.session.commit()

        app_module.notify_user(
            user["user"]["id"], "job_alert_match", "New job matches your skills", "Data Clerk",
            sms=True, whatsapp=True,
        )

    assert fake_client.messages.create.call_count == 1
    kwargs = fake_client.messages.create.call_args.kwargs
    # The one call made was the SMS (plain `to=`), never whatsap: — proves
    # the whatsapp_alerts_enabled=False gate actually suppressed that leg.
    assert kwargs["to"] == user["user"]["phone"]
