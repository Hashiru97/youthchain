"""
Regression coverage for CORS scoping (see API_PREFIXES/_is_api_request in
app.py). Added alongside upgrading flask-cors 4.0.1 -> 6.0.0 (a full
dependency-vulnerability audit via pip-audit found three real path-matching
CVEs in 4.0.1 that could cause CORS headers to be applied to routes they
shouldn't be -- PYSEC-2026-1383/1384/1385). This app's own CORS
configuration is deliberately narrow: wildcard origins on the bearer-token
JSON API surface only (API_PREFIXES), and explicitly NOT on the
session-cookie-authenticated employer/admin web portals, since cookies are
an ambient credential and wildcard CORS + cookies would reopen a
CSRF-style hole. These tests prove that scoping actually holds with a real
cross-origin request, not just that the config line exists.
"""


def test_api_route_gets_cors_headers_for_a_cross_origin_request(client):
    """
    flask-cors 6.0.0 reflects the specific requesting Origin back rather
    than the literal string "*" when the configured policy allows all
    origins (a real, benign behavior change found while upgrading, not a
    security regression -- the effective policy is identical either way:
    a wildcard-configured resource allows any origin, so reflecting the
    caller's own origin back grants nothing a literal "*" wouldn't have).
    """
    resp = client.get("/jobs", headers={"Origin": "https://evil.example.com"})
    assert resp.status_code == 200
    assert resp.headers.get("Access-Control-Allow-Origin") in ("*", "https://evil.example.com")


def test_session_cookie_portal_route_gets_no_cors_headers(client):
    """
    The route this actually matters for: /employer/login sets a session
    cookie. If CORS headers were ever mistakenly applied here (exactly
    the class of bug flask-cors 4.0.1 had — see module docstring), a
    malicious cross-origin page could read authenticated responses from
    this portal using a victim's ambient cookie.
    """
    resp = client.get("/employer/login", headers={"Origin": "https://evil.example.com"})
    assert resp.status_code == 200
    assert "Access-Control-Allow-Origin" not in resp.headers


def test_admin_portal_route_gets_no_cors_headers(client):
    resp = client.get("/admin/login", headers={"Origin": "https://evil.example.com"})
    assert resp.status_code == 200
    assert "Access-Control-Allow-Origin" not in resp.headers
