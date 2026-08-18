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
        stored = app_module.User.query.get(user["user"]["id"])
        assert stored.push_token == "fcm-device-token-abc"

    # registering with no token (e.g. on logout) clears it
    resp = client.put(
        "/api/push_token",
        json={},
        headers=auth_headers(user["access_token"]),
    )
    assert resp.status_code == 200
    with app_module.app.app_context():
        stored = app_module.User.query.get(user["user"]["id"])
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
        u = app_module.User.query.get(user["user"]["id"])
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
        u = app_module.User.query.get(user["user"]["id"])
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


def test_send_sms_returns_false_on_twilio_error(monkeypatch):
    import app as app_module

    fake_client = MagicMock()
    fake_client.messages.create.side_effect = RuntimeError("Twilio unavailable")
    monkeypatch.setattr(app_module, "_twilio_client", fake_client)
    monkeypatch.setattr(app_module, "_TWILIO_FROM_NUMBER", "+15005550006")

    result = app_module.send_sms("+23276000000", "Body")
    assert result is False
