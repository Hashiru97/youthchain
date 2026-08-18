from conftest import register_user, auth_headers

import app as app_module

# Captured at module-load time, before conftest's autouse
# _no_real_pwned_passwords_lookup fixture (which stubs
# app_module._password_is_breached to a hardcoded False for every test)
# ever runs -- same reasoning as _real_write_onchain_tx_for_credential in
# test_credentials.py. Needed by the tests below that are specifically
# about this function's own real logic; calling through
# app_module._password_is_breached there would just call the autouse stub
# instead and trivially pass for the wrong reason.
_real_password_is_breached = app_module._password_is_breached


class _FakeResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"{self.status_code}")


def test_password_is_breached_computes_the_real_k_anonymity_split(monkeypatch):
    """
    SHA-1("password") = 5BAA61E4C9B93F3F0682250B6CF8331B7EE68FD8 -- a
    well-known, deterministic test vector (this is literally HIBP's own
    documentation example), used here to prove the prefix/suffix split is
    computed correctly without depending on the real API being reachable.
    Also proves the actual password is never sent -- only a 5-character
    hash prefix appears in the request URL.
    """
    import app as app_module

    captured = {}

    def fake_get(url, timeout=None, headers=None):
        captured["url"] = url
        # A real API response is "SUFFIX:count" lines for every hash
        # sharing this prefix -- includes the real suffix for "password"
        # itself among plausible decoys.
        return _FakeResponse(
            "1E4C9B93F3F0682250B6CF8331B7EE68FD8:9545824\n"
            "0000000000000000000000000000000000:1\n"
        )

    monkeypatch.setattr(app_module.requests, "get", fake_get)

    # The real function, not app_module._password_is_breached -- see the
    # module-level comment on _real_password_is_breached for why (the
    # autouse fixture stubs that attribute for every test).
    result = _real_password_is_breached("password")

    assert result is True
    # Exact match, not just a substring check -- proves ONLY the 5-char
    # hash prefix went out and nothing else (the domain name itself
    # happens to contain the substring "password", which would make a
    # naive `"password" not in url` check meaningless for this specific
    # test password -- an unrelated coincidence, not a real signal).
    assert captured["url"] == "https://api.pwnedpasswords.com/range/5BAA6"


def test_password_is_breached_returns_false_for_no_match(monkeypatch):
    import app as app_module

    monkeypatch.setattr(
        app_module.requests, "get",
        lambda url, timeout=None, headers=None: _FakeResponse("0000000000000000000000000000000000:1\n"),
    )

    assert _real_password_is_breached("a-genuinely-unique-passphrase-9x7q") is False


def test_password_is_breached_fails_open_when_api_is_unreachable(monkeypatch):
    """
    Deliberately the OPPOSITE default from file_is_malware_free()'s
    fail-closed: a transient outage of a third-party breach-check API
    blocking every new registration would be a worse outcome than
    occasionally skipping the check.
    """
    import app as app_module
    import requests as requests_module

    def fake_get(url, timeout=None, headers=None):
        raise requests_module.ConnectionError("simulated network failure")

    monkeypatch.setattr(app_module.requests, "get", fake_get)

    assert _real_password_is_breached("whatever-password-123") is None


def test_register_rejects_a_breached_password(client, monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, "_password_is_breached", lambda password: True)

    resp = client.post(
        "/register",
        json={
            "name": "X", "phone": "999", "email": "breached@test.com",
            "password": "password12345", "consent": True,
        },
    )
    assert resp.status_code == 400
    assert "breach" in resp.get_json()["error"].lower()

    with app_module.app.app_context():
        assert app_module.User.query.filter_by(email="breached@test.com").first() is None


def test_register_succeeds_when_breach_check_api_is_unreachable(client, monkeypatch):
    """Fail-open confirmed end-to-end, not just at the function level."""
    import app as app_module

    monkeypatch.setattr(app_module, "_password_is_breached", lambda password: None)

    resp = client.post(
        "/register",
        json={
            "name": "X", "phone": "998", "email": "failopen@test.com",
            "password": "password12345", "consent": True,
        },
    )
    assert resp.status_code == 201


def test_failed_login_is_logged_as_a_security_event(client):
    """
    Real gap found via a full OWASP Top 10 (A09: Security Logging and
    Monitoring Failures) review: every successful login already gets a
    real AnalyticsEvent (user_login) -- a FAILED one never did, anywhere
    in this codebase, despite rate limiting existing specifically because
    failed logins can mean an active attack. Throttling an attack and
    having an audit trail of it having happened are two different things.
    """
    import app as app_module

    register_user(client, email="alice-audit@test.com", phone="111")
    client.post("/login", json={"email": "alice-audit@test.com", "password": "wrong-password"})

    with app_module.app.app_context():
        event = app_module.AnalyticsEvent.query.filter_by(event_type="user_login_failed").first()
        assert event is not None
        assert event.user_id is not None  # the account really does exist


def test_failed_login_against_an_unknown_account_is_still_logged(client):
    """
    Logged with user_id=None when the account doesn't exist at all --
    this is an internal audit log, not the HTTP response (which
    deliberately gives an identical error either way, per S-07), so it's
    fine and useful for the log to know more than the caller does.
    """
    import app as app_module

    client.post("/login", json={"email": "nobody-audit@test.com", "password": "whatever12345"})

    with app_module.app.app_context():
        event = app_module.AnalyticsEvent.query.filter_by(event_type="user_login_failed").first()
        assert event is not None
        assert event.user_id is None


def test_logout_blocklists_the_token_so_it_no_longer_authenticates(client):
    """
    Real gap found via a full OWASP Top 10 (A07: Identification and
    Authentication Failures) review: there was no /logout route for the
    mobile app at all, and no server-side way to invalidate an
    already-issued JWT before its natural 12h expiry -- the employer/admin
    session-cookie flows already correctly revoke immediately on
    Employer.active/Admin.active going False; JWTs had no equivalent.
    Proves the actual end-to-end effect: the token genuinely stops
    authenticating after logout, not just that the route returns 200.
    """
    data = register_user(client)
    token = data["access_token"]

    still_valid = client.get(f"/passport/{data['user']['id']}", headers=auth_headers(token))
    assert still_valid.status_code == 200

    logout_resp = client.post("/logout", headers=auth_headers(token))
    assert logout_resp.status_code == 200

    after_logout = client.get(f"/passport/{data['user']['id']}", headers=auth_headers(token))
    assert after_logout.status_code == 401


def test_logout_does_not_affect_a_different_users_token(client):
    alice = register_user(client, email="alice-logout@test.com", phone="111")
    bob = register_user(client, email="bob-logout@test.com", phone="222")

    client.post("/logout", headers=auth_headers(alice["access_token"]))

    bob_still_works = client.get(f"/passport/{bob['user']['id']}", headers=auth_headers(bob["access_token"]))
    assert bob_still_works.status_code == 200


def test_logout_without_a_token_is_rejected(client):
    resp = client.post("/logout")
    assert resp.status_code == 401


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


def test_otp_request_is_rate_limited_to_stop_email_bombing(client):
    """
    Real gap found via a full security review: /auth/otp/request (the
    login-OTP sender) had no throttle at all, unlike its sibling
    /auth/otp/register/request and /auth/otp/verify — an unauthenticated
    caller could spam it with any registered user's email and cause
    YouthChain to send that person unlimited real OTP emails (verified
    live: 30/30 rapid requests sent 30 emails before this fix).
    """
    register_user(client, email="eve@test.com", phone="555", name="Eve")
    for _ in range(5):
        r = client.post("/auth/otp/request", json={"email": "eve@test.com"})
        assert r.status_code == 200
    limited = client.post("/auth/otp/request", json={"email": "eve@test.com"})
    assert limited.status_code == 429


def test_otp_request_rate_limit_does_not_leak_account_existence(client):
    """
    The rate-limit check must run before (and regardless of) the
    account-existence lookup, or the 429-vs-200 pattern itself becomes a
    user-enumeration side channel — exactly what the identical
    success-shaped 200 response (S-07) was already designed to prevent.
    """
    register_user(client, email="frank@test.com", phone="666", name="Frank")

    real_codes = [client.post("/auth/otp/request", json={"email": "frank@test.com"}).status_code for _ in range(6)]
    fake_codes = [client.post("/auth/otp/request", json={"email": "nobody@test.com"}).status_code for _ in range(6)]

    assert real_codes == fake_codes == [200, 200, 200, 200, 200, 429]


def test_login_rate_limited_after_five_bad_password_attempts(client):
    """
    Real gap found via a full security review, not anticipated in
    advance: only OTP verification (test above) had any throttle — the
    password-based /login route itself had none at all, meaning an
    attacker could guess a real user's password an unbounded number of
    times. Same generic rate limiter as OTP/uploads, keyed by the
    submitted identifier rather than IP, so spreading guesses across many
    source IPs doesn't help an attacker target one specific account.
    """
    register_user(client, email="carol@test.com", phone="333", name="Carol")
    for _ in range(5):
        r = client.post("/login", json={"email": "carol@test.com", "password": "wrong-password"})
        assert r.status_code == 401
    limited = client.post("/login", json={"email": "carol@test.com", "password": "wrong-password"})
    assert limited.status_code == 429

    # A DIFFERENT account's login attempts are unaffected -- the limit is
    # per-identifier, not a global lockout of the whole route.
    register_user(client, email="dave@test.com", phone="444", name="Dave")
    other = client.post("/login", json={"email": "dave@test.com", "password": "password123"})
    assert other.status_code == 200
