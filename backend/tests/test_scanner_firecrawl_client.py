"""
Coverage for scanner/firecrawl_client.py's retry-on-5xx behavior — added
after a real scan run against careers.sl showed a handful of per-job
detail-page scrapes fail with a bare 500 and succeed immediately on a
second attempt (see scanner/pipeline.py's module docstring).
"""
import scanner.firecrawl_client as firecrawl_client
from scanner.firecrawl_client import ScanSourceError, fetch_url_plain, scrape_url


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


class _FakeGetResponse:
    def __init__(self, status_code=200, text="<html>real page</html>"):
        self.status_code = status_code
        self.text = text

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(f"{self.status_code} error")


def test_fetch_url_plain_returns_raw_html_on_success(monkeypatch):
    monkeypatch.setattr(
        firecrawl_client.requests, "get",
        lambda url, timeout, headers: _FakeGetResponse(200, "<html>the real page content</html>"),
    )
    html = fetch_url_plain("https://careers.sl/job/x/")
    assert html == "<html>the real page content</html>"


def test_fetch_url_plain_sends_a_browser_user_agent(monkeypatch):
    captured = {}

    def _fake_get(url, timeout, headers):
        captured["headers"] = headers
        return _FakeGetResponse(200, "<html>ok</html>")

    monkeypatch.setattr(firecrawl_client.requests, "get", _fake_get)
    fetch_url_plain("https://careers.sl/job/x/")
    assert "Mozilla" in captured["headers"]["User-Agent"]


def test_fetch_url_plain_raises_on_http_error(monkeypatch):
    monkeypatch.setattr(
        firecrawl_client.requests, "get",
        lambda url, timeout, headers: _FakeGetResponse(404, ""),
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
