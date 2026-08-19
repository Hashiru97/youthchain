"""
Coverage for scanner/firecrawl_client.py's retry-on-5xx behavior — added
after a real scan run against careers.sl showed a handful of per-job
detail-page scrapes fail with a bare 500 and succeed immediately on a
second attempt (see scanner/pipeline.py's module docstring) — and for the
SSRF guard (_assert_safe_fetch_target) added after the engineering
review flagged that Job.apply_url is entirely scraped-content-controlled
and fetch_url_plain() had no scheme/IP validation before issuing a direct
request from this backend's own network.

DNS resolution for every test hostname below is monkeypatched rather than
hitting real DNS — the guard's own correctness (does it reject a
resolved-private IP, does it accept a resolved-public one) shouldn't
depend on what careers.sl or example-internal.test actually resolve to on
whatever network runs these tests.
"""
import socket

import pytest

import scanner.firecrawl_client as firecrawl_client
from scanner.firecrawl_client import ScanSourceError, fetch_url_plain, scrape_url

# A real, publicly-routable IP — used as the "safe" resolution target
# throughout. Not actually dialed (every test also monkeypatches
# requests.get/post), just needs to read as public to ipaddress's
# is_private/is_loopback/etc checks. Deliberately not one of the RFC 5737
# documentation ranges (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24) —
# Python's ipaddress module classifies those as is_private=True too.
_PUBLIC_IP = "8.8.8.8"
_PRIVATE_IP = "10.0.0.5"
_LOOPBACK_IP = "127.0.0.1"
_LINK_LOCAL_IP = "169.254.169.254"


def _fake_getaddrinfo(target_ip):
    def _resolve(host, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (target_ip, 0))]
    return _resolve


@pytest.fixture(autouse=True)
def _resolve_to_public_ip(monkeypatch):
    """Default for every test: hostnames resolve to a public IP. Individual
    tests override this to exercise the guard's rejection paths."""
    monkeypatch.setattr(firecrawl_client.socket, "getaddrinfo", _fake_getaddrinfo(_PUBLIC_IP))


class _FakeResponse:
    def __init__(self, status_code=200, json_data=None, text=""):
        self.status_code = status_code
        self._json_data = json_data
        self.text = text

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"{self.status_code} error")


def test_retries_once_on_a_bare_500_and_succeeds(monkeypatch):
    calls = []

    def _fake_post(*a, **k):
        calls.append(1)
        if len(calls) == 1:
            return _FakeResponse(500, text="server error")
        return _FakeResponse(200, {"success": True, "data": {"markdown": "# Real content"}})

    monkeypatch.setattr(firecrawl_client.requests, "post", _fake_post)

    markdown = scrape_url("https://careers.sl/job/x/", api_key="fake-key", _retry_delay=0)
    assert markdown == "# Real content"
    assert len(calls) == 2


def test_gives_up_after_a_second_500(monkeypatch):
    monkeypatch.setattr(
        firecrawl_client.requests, "post",
        lambda *a, **k: _FakeResponse(500, text="server error"),
    )
    try:
        scrape_url("https://careers.sl/job/x/", api_key="fake-key", _retry_delay=0)
        assert False, "expected ScanSourceError"
    except ScanSourceError as e:
        assert "500" in str(e)


def test_does_not_retry_a_401(monkeypatch):
    calls = []

    def _fake_post(*a, **k):
        calls.append(1)
        return _FakeResponse(401)

    monkeypatch.setattr(firecrawl_client.requests, "post", _fake_post)
    try:
        scrape_url("https://careers.sl/job/x/", api_key="bad-key", _retry_delay=0)
        assert False, "expected ScanSourceError"
    except ScanSourceError as e:
        assert "401" in str(e)
    assert len(calls) == 1


def test_does_not_retry_a_429(monkeypatch):
    calls = []

    def _fake_post(*a, **k):
        calls.append(1)
        return _FakeResponse(429)

    monkeypatch.setattr(firecrawl_client.requests, "post", _fake_post)
    try:
        scrape_url("https://careers.sl/job/x/", api_key="fake-key", _retry_delay=0)
        assert False, "expected ScanSourceError"
    except ScanSourceError as e:
        assert "429" in str(e)
    assert len(calls) == 1


def test_succeeds_first_try_makes_only_one_call(monkeypatch):
    calls = []

    def _fake_post(*a, **k):
        calls.append(1)
        return _FakeResponse(200, {"success": True, "data": {"markdown": "# Content"}})

    monkeypatch.setattr(firecrawl_client.requests, "post", _fake_post)
    markdown = scrape_url("https://careers.sl/job/x/", api_key="fake-key", _retry_delay=0)
    assert markdown == "# Content"
    assert len(calls) == 1


def test_scrape_url_refuses_a_private_target_without_ever_calling_firecrawl(monkeypatch):
    """Defense-in-depth: scrape_url itself never reaches Firecrawl for a
    target that resolves privately, even though Firecrawl is the actual
    fetcher in the success path."""
    monkeypatch.setattr(firecrawl_client.socket, "getaddrinfo", _fake_getaddrinfo(_PRIVATE_IP))

    def _fail_if_called(*a, **k):
        raise AssertionError("requests.post must not be called for a private-resolving target")

    monkeypatch.setattr(firecrawl_client.requests, "post", _fail_if_called)
    try:
        scrape_url("https://internal.example/job/x/", api_key="fake-key", _retry_delay=0)
        assert False, "expected ScanSourceError"
    except ScanSourceError as e:
        assert "non-public" in str(e)


class _FakeGetResponse:
    """Stands in for requests.Response under stream=True — supports the
    context-manager protocol (`with resp:`), .iter_content(), .is_redirect,
    and .encoding, all of which fetch_url_plain now relies on."""

    def __init__(self, status_code=200, text="<html>real page</html>", is_redirect=False, headers=None):
        self.status_code = status_code
        self.text = text
        self.is_redirect = is_redirect
        self.headers = headers or {}
        self.encoding = "utf-8"

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"{self.status_code} error")

    def iter_content(self, chunk_size=65536):
        data = self.text.encode("utf-8")
        for i in range(0, len(data), chunk_size):
            yield data[i:i + chunk_size]

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_fetch_url_plain_returns_raw_html_on_success(monkeypatch):
    monkeypatch.setattr(
        firecrawl_client.requests, "get",
        lambda url, timeout, headers, allow_redirects, stream: _FakeGetResponse(
            200, "<html>the real page content</html>"
        ),
    )
    html = fetch_url_plain("https://careers.sl/job/x/")
    assert html == "<html>the real page content</html>"


def test_fetch_url_plain_sends_a_browser_user_agent(monkeypatch):
    captured = {}

    def _fake_get(url, timeout, headers, allow_redirects, stream):
        captured["headers"] = headers
        captured["allow_redirects"] = allow_redirects
        return _FakeGetResponse(200, "<html>ok</html>")

    monkeypatch.setattr(firecrawl_client.requests, "get", _fake_get)
    fetch_url_plain("https://careers.sl/job/x/")
    assert "Mozilla" in captured["headers"]["User-Agent"]
    # Following a redirect could land on an internal address the initial
    # SSRF check never saw — must never be left to requests to auto-follow.
    assert captured["allow_redirects"] is False


def test_fetch_url_plain_raises_on_http_error(monkeypatch):
    monkeypatch.setattr(
        firecrawl_client.requests, "get",
        lambda url, timeout, headers, allow_redirects, stream: _FakeGetResponse(404, ""),
    )
    try:
        fetch_url_plain("https://careers.sl/job/gone/")
        assert False, "expected ScanSourceError"
    except ScanSourceError as e:
        assert "404" in str(e)


def test_fetch_url_plain_raises_on_network_error(monkeypatch):
    def _raise(*a, **k):
        import requests
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(firecrawl_client.requests, "get", _raise)
    try:
        fetch_url_plain("https://careers.sl/job/x/")
        assert False, "expected ScanSourceError"
    except ScanSourceError:
        pass


@pytest.mark.parametrize("bad_ip", [_PRIVATE_IP, _LOOPBACK_IP, _LINK_LOCAL_IP])
def test_fetch_url_plain_refuses_a_target_resolving_to_a_non_public_address(monkeypatch, bad_ip):
    monkeypatch.setattr(firecrawl_client.socket, "getaddrinfo", _fake_getaddrinfo(bad_ip))

    def _fail_if_called(*a, **k):
        raise AssertionError("requests.get must not be called for a non-public-resolving target")

    monkeypatch.setattr(firecrawl_client.requests, "get", _fail_if_called)
    try:
        fetch_url_plain("http://looks-public.example/apply")
        assert False, "expected ScanSourceError"
    except ScanSourceError as e:
        assert "non-public" in str(e)


def test_fetch_url_plain_refuses_a_non_http_scheme():
    try:
        fetch_url_plain("file:///etc/passwd")
        assert False, "expected ScanSourceError"
    except ScanSourceError as e:
        assert "scheme" in str(e)


def test_fetch_url_plain_refuses_to_follow_a_redirect(monkeypatch):
    monkeypatch.setattr(
        firecrawl_client.requests, "get",
        lambda url, timeout, headers, allow_redirects, stream: _FakeGetResponse(
            302, "", is_redirect=True, headers={"Location": "http://169.254.169.254/latest/meta-data/"}
        ),
    )
    try:
        fetch_url_plain("https://careers.sl/job/x/")
        assert False, "expected ScanSourceError"
    except ScanSourceError as e:
        assert "redirect" in str(e)


def test_fetch_url_plain_aborts_a_response_over_the_size_cap(monkeypatch):
    oversized = "x" * (firecrawl_client._MAX_FETCH_BYTES + 1)
    monkeypatch.setattr(
        firecrawl_client.requests, "get",
        lambda url, timeout, headers, allow_redirects, stream: _FakeGetResponse(200, oversized),
    )
    try:
        fetch_url_plain("https://careers.sl/job/x/")
        assert False, "expected ScanSourceError"
    except ScanSourceError as e:
        assert "exceeded" in str(e)
