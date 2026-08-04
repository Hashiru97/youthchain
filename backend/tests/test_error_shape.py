"""
Regression coverage for BL-29 / TD-10: two incompatible JSON error shapes
used to coexist ({"error": ...} vs {"success": false, "error": ...}).
Every error response across the API now includes "success": false —
additive only, so no existing success-path parsing (mobile app, tests)
had to change.
"""
from conftest import register_user


def test_validation_error_includes_success_false(client):
    resp = client.post(
        "/register",
        json={"name": "X", "phone": "999", "email": "x@test.com", "password": "short"},
    )
    body = resp.get_json()
    assert body["success"] is False
    assert "error" in body


def test_auth_error_includes_success_false(client):
    resp = client.get("/passport/1")
    body = resp.get_json()
    assert body["success"] is False


def test_forbidden_error_includes_success_false(client):
    a = register_user(client, email="alice@test.com", phone="111")
    b = register_user(client, email="bob@test.com", phone="222")
    resp = client.get(
        f"/passport/{b['user']['id']}",
        headers={"Authorization": f"Bearer {a['access_token']}"},
    )
    assert resp.status_code == 403
    assert resp.get_json()["success"] is False


def test_404_json_error_includes_success_false(client):
    # /api/ is one of the API_PREFIXES routed to the JSON 404 handler
    # rather than Flask's default HTML error page.
    resp = client.get("/api/this-route-does-not-exist")
    assert resp.status_code == 404
    assert resp.get_json()["success"] is False
