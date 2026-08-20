"""
Regression coverage for the compliance consent-capture flow: registration
requires an explicit consent=true, and a real timestamp is recorded (not
just a boolean flag) -- see User.consent_accepted_at in app.py.
"""
from conftest import register_user


def test_registration_without_consent_is_rejected(client):
    resp = client.post(
        "/register",
        json={"name": "X", "phone": "999", "email": "x@test.com", "password": "password123"},
    )
    assert resp.status_code == 400
    body = resp.get_json()
    assert body["success"] is False
    assert "privacy policy" in body["error"].lower()


def test_registration_with_consent_false_is_rejected(client):
    resp = client.post(
        "/register",
        json={
            "name": "X",
            "phone": "999",
            "email": "x@test.com",
            "password": "password123",
            "consent": False,
        },
    )
    assert resp.status_code == 400


def test_registration_with_consent_true_records_timestamp(client):
    import app as app_module

    data = register_user(client, consent=True)
    assert "access_token" in data

    with app_module.app.app_context():
        user = app_module.User.query.filter_by(email="alice@test.com").first()
        assert user.consent_accepted_at is not None


def test_privacy_policy_page_is_publicly_reachable(client):
    resp = client.get("/privacy-policy")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "pending legal review" in body.lower()


def test_terms_of_service_page_is_publicly_reachable(client):
    resp = client.get("/terms-of-service")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "pending legal review" in body.lower()
