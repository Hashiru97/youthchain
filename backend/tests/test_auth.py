from conftest import register_user


def test_register_issues_token_and_creates_user(client):
    data = register_user(client)
    assert "access_token" in data
    assert data["user"]["email"] == "alice@test.com"


def test_register_rejects_short_password(client):
    resp = client.post(
        "/register",
        json={"name": "X", "phone": "999", "email": "x@test.com", "password": "short"},
    )
    assert resp.status_code == 400


def test_register_duplicate_email_rejected_generically(client):
    register_user(client)
    resp = client.post(
        "/register",
        json={"name": "Alice2", "phone": "222", "email": "alice@test.com", "password": "password123"},
    )
    assert resp.status_code == 400
    # S-07: message must not distinguish "already exists" reconnaissance-style
    assert "already" not in resp.get_json()["error"].lower()


def test_login_success_issues_token(client):
    register_user(client)
    resp = client.post("/login", json={"email": "alice@test.com", "password": "password123"})
    assert resp.status_code == 200
    assert "access_token" in resp.get_json()


def test_login_wrong_password_and_unknown_user_return_identical_error(client):
    register_user(client)
    wrong_pw = client.post("/login", json={"email": "alice@test.com", "password": "nope12345"})
    unknown_user = client.post("/login", json={"email": "nobody@test.com", "password": "nope12345"})
    assert wrong_pw.status_code == 401
    assert unknown_user.status_code == 401
    # S-07: identical error message so a caller can't enumerate accounts
    assert wrong_pw.get_json()["error"] == unknown_user.get_json()["error"]


def test_protected_route_requires_token(client):
    resp = client.get("/passport/1")
    assert resp.status_code == 401


def test_protected_route_rejects_garbage_token(client):
    resp = client.get("/passport/1", headers={"Authorization": "Bearer not-a-real-token"})
    assert resp.status_code == 401


def test_otp_verify_rate_limited_after_five_bad_attempts(client):
    register_user(client, email="bob@test.com", phone="222", name="Bob")
    for _ in range(5):
        r = client.post("/auth/otp/verify", json={"email": "bob@test.com", "code": "000000"})
        assert r.status_code == 400
    limited = client.post("/auth/otp/verify", json={"email": "bob@test.com", "code": "000000"})
    assert limited.status_code == 429
