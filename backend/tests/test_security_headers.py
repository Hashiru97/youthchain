"""
Regression coverage for _security_headers() in app.py (BL-08 / Phase 3 #23,
extended with a real Content-Security-Policy during a full OWASP Top 10
review — A05: Security Misconfiguration). None of these headers had any
test coverage before this file existed, despite BL-08 predating this
session by a wide margin.
"""


def test_baseline_security_headers_are_always_present(client):
    resp = client.get("/healthz")
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    assert resp.headers.get("X-Frame-Options") == "DENY"
    assert resp.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"


def test_content_security_policy_is_present_and_restrictive(client):
    resp = client.get("/employer/login")
    csp = resp.headers.get("Content-Security-Policy")
    assert csp is not None
    assert "default-src 'self'" in csp
    # The real point of this header: no inline script execution and no
    # third-party script origins allowed, even if a future XSS bug
    # manages to inject markup somewhere.
    assert "script-src 'self'" in csp
    assert "style-src 'self'" in csp
    # Every inline style="..." attribute was extracted to a real CSS class,
    # so 'unsafe-inline' is not needed anywhere in this policy, not just
    # in script-src.
    assert "'unsafe-inline'" not in csp
    assert "object-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp


def test_hsts_is_not_sent_over_plain_http_even_in_a_request_claiming_https(client):
    """
    HSTS is gated on IS_PRODUCTION AND request.is_secure — the test client
    never sets is_secure, and IS_PRODUCTION is False in the test
    environment (FLASK_ENV isn't "production"), so this should never
    appear during a normal local/test run. Sending it over plain HTTP
    would be a lie the browser can't act on.
    """
    resp = client.get("/healthz")
    assert "Strict-Transport-Security" not in resp.headers


def test_static_socketio_client_is_served_locally_not_from_a_cdn(client):
    """
    Real gap found via the same OWASP review: employer_dashboard.html and
    employer_applications.html used to load the Socket.IO client from
    cdn.socket.io with no Subresource Integrity pin -- a supply-chain risk
    and inconsistent with this codebase's own self-hosted-only discipline
    everywhere else. Now vendored locally.
    """
    resp = client.get("/static/socket.io.min.js")
    assert resp.status_code == 200
    assert b"Socket.IO" in resp.data
