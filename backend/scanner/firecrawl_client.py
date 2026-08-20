"""
Thin wrapper around Firecrawl's /v1/scrape endpoint — fetches a URL and
returns clean markdown content, letting Firecrawl's own infrastructure
handle JS rendering/bot-detection/etc. rather than this backend fetching
pages directly. Plain `requests.post`, same convention as
app._password_is_breached()'s HIBP call — no SDK for a single endpoint.
"""
import contextlib
import ipaddress
import socket
import time
from urllib.parse import urlparse

import requests


class ScanSourceError(Exception):
    """
    Raised when a source URL can't be scraped — missing/invalid API key,
    network failure, timeout, or Firecrawl itself reporting a failure
    (e.g. the target blocked the request). Always caught by
    scanner.pipeline and recorded on the owning scan_run.error_message —
    never allowed to propagate into the poller loop and kill it.
    """


def _assert_safe_fetch_target(url: str) -> list:
    """
    SSRF guard for any URL this backend fetches directly (fetch_url_plain
    below, and scrape_url as defense-in-depth even though Firecrawl's own
    infra is the actual fetcher there). `Job.apply_url` is entirely
    scraped-page content extracted by Claude — never something an admin
    typed in — so nothing about it can be trusted before this check:
    a listing crafted so Firecrawl fails to fetch its apply_url (easy —
    point it at an address Firecrawl's infra can't reach) would otherwise
    make fetch_url_plain() issue a direct request from this backend's own
    network to wherever that URL points, e.g. 169.254.169.254 (cloud
    instance metadata) or an internal service, and feed the response back
    into extraction/storage as an oracle for internal responses.

    Rejects non-http(s) schemes outright, then resolves the hostname and
    rejects if ANY resolved address is loopback/link-local/private/
    reserved/multicast — resolving (rather than only pattern-matching the
    literal hostname) is what actually closes this, since a hostname
    attacker-controlled DNS could otherwise point anywhere on first
    request and somewhere internal on a later one.

    Returns the validated addrinfo list (see _pin_dns below) — this
    function's own resolution and whatever requests/urllib3 performs
    internally moments later when actually opening the connection are two
    SEPARATE DNS lookups. An attacker running their own DNS server can
    legitimately answer them differently (a public IP here, a private one
    then) — classic DNS-rebinding, and the entire reason this check
    existed was defeated by it otherwise. Callers MUST fetch inside
    _pin_dns(hostname, this-return-value) so the connection actually
    opened is guaranteed to be the same address just validated, not a
    fresh, unvalidated lookup.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ScanSourceError(f"Refusing to fetch {url!r}: scheme must be http or https")
    if not parsed.hostname:
        raise ScanSourceError(f"Refusing to fetch {url!r}: no hostname")

    try:
        addrinfo = socket.getaddrinfo(parsed.hostname, None)
    except socket.gaierror as e:
        raise ScanSourceError(f"Refusing to fetch {url!r}: DNS resolution failed: {e}") from e

    for family, _, _, _, sockaddr in addrinfo:
        ip = ipaddress.ip_address(sockaddr[0])
        if (
            ip.is_loopback
            or ip.is_link_local
            or ip.is_private
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ScanSourceError(
                f"Refusing to fetch {url!r}: resolves to non-public address {ip}"
            )
    return addrinfo


@contextlib.contextmanager
def _pin_dns(hostname: str, addrinfo: list):
    """
    Closes the DNS-rebinding TOCTOU window above: scopes a monkeypatch of
    socket.getaddrinfo so that ANY resolution of `hostname` during this
    block — specifically including whatever requests/urllib3 does
    internally to actually open the connection — returns exactly the
    already-validated `addrinfo`, never a fresh lookup an attacker's DNS
    server could answer differently. Only intercepts lookups for this
    exact hostname; everything else (Firecrawl's own domain, unrelated
    DNS this process needs) resolves normally, and the real resolver is
    always restored in `finally` even on exception.

    Deliberately does NOT touch how the TLS handshake itself validates
    the connection — SNI and certificate hostname verification still run
    against the real `hostname` string throughout; only the numeric
    address the socket actually connects to is pinned. That's what makes
    this safe to do at the DNS layer alone rather than needing to
    hand-roll connection-pool/SNI logic to pin an IP directly.
    """
    real_getaddrinfo = socket.getaddrinfo

    def _pinned(host, *args, **kwargs):
        if host == hostname:
            return addrinfo
        return real_getaddrinfo(host, *args, **kwargs)

    socket.getaddrinfo = _pinned
    try:
        yield
    finally:
        socket.getaddrinfo = real_getaddrinfo


FIRECRAWL_SCRAPE_URL = "https://api.firecrawl.dev/v1/scrape"


def scrape_url(url: str, api_key: str | None, timeout: int = 30, _retry_delay: float = 2.0) -> str:
    """
    Returns the scraped page's markdown content. Raises ScanSourceError
    (never returns None/empty on failure) so callers can't accidentally
    treat "nothing scraped" as "zero jobs found" — those are different
    outcomes and scan_run needs to record them differently.

    Retries once on a bare 5xx — observed for real running the
    description/salary backfill pass (see scanner.pipeline's docstring)
    against careers.sl's own per-job pages: a handful of detail-page
    scrapes failed with a plain 500 and succeeded immediately on a
    second attempt. Not retried for 401 (won't resolve by retrying) or
    429 (its own message already says to wait for the next scheduled
    scan, not hammer it again seconds later).
    """
    if not api_key:
        raise ScanSourceError("FIRECRAWL_API_KEY is not configured")
    _assert_safe_fetch_target(url)

    for attempt in (1, 2):
        try:
            resp = requests.post(
                FIRECRAWL_SCRAPE_URL,
                timeout=timeout,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={"url": url, "formats": ["markdown"]},
            )
        except requests.RequestException as e:
            raise ScanSourceError(f"Network error reaching Firecrawl: {e}") from e

        if resp.status_code == 401:
            raise ScanSourceError("Firecrawl rejected the API key (401)")
        if resp.status_code == 429:
            raise ScanSourceError("Firecrawl rate limit hit (429) — try again on the next scheduled scan")
        if 500 <= resp.status_code < 600 and attempt == 1:
            time.sleep(_retry_delay)
            continue
        break

    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        raise ScanSourceError(f"Firecrawl returned {resp.status_code}: {resp.text[:300]}") from e

    try:
        data = resp.json()
    except ValueError as e:
        raise ScanSourceError("Firecrawl response was not valid JSON") from e

    if not data.get("success", True):
        raise ScanSourceError(f"Firecrawl reported failure: {data.get('error', 'unknown error')}")

    markdown = (data.get("data") or {}).get("markdown")
    if not markdown:
        raise ScanSourceError("Firecrawl returned no markdown content for this URL")
    return markdown


# A plain browser User-Agent, not this process's default (python-requests/x.y)
# -- some sites that don't block a normal browser still block an obvious
# script's default UA on principle, independent of whatever's actually
# blocking Firecrawl below.
_PLAIN_FETCH_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# claude_extractor.py's prompt already truncates to 60,000 chars before
# sending anything to the model, but that truncation happens after the
# full body is fetched into memory — this bounds the fetch itself, so a
# large or slow-drip response from an apply_url can't hold an unbounded
# amount of memory (or wall-clock time inside `timeout`) before that
# later truncation ever kicks in.
_MAX_FETCH_BYTES = 5 * 1024 * 1024


def fetch_url_plain(url: str, timeout: int = 15) -> str:
    """
    Fetches a URL directly from this backend, bypassing Firecrawl
    entirely. Not a general-purpose replacement for scrape_url — no JS
    rendering, no bot-detection handling, returns raw HTML rather than
    clean markdown. Exists specifically as scanner.pipeline's backfill-
    pass fallback for the case confirmed against real Careers.sl detail
    pages: Firecrawl's own scraping engines reported total failure
    (SCRAPE_ALL_ENGINES_FAILED) on URLs that a plain HTTP GET from this
    same backend loaded in full on the first try — whatever was blocking
    Firecrawl specifically (its infrastructure's IP range/reputation,
    most likely) didn't apply to a direct request. Raises ScanSourceError
    on any failure, same contract as scrape_url, so pipeline.py's
    existing except handling covers both without change.
    """
    addrinfo = _assert_safe_fetch_target(url)
    hostname = urlparse(url).hostname

    try:
        with _pin_dns(hostname, addrinfo):
            resp = requests.get(
                url,
                timeout=timeout,
                headers={"User-Agent": _PLAIN_FETCH_USER_AGENT},
                allow_redirects=False,
                stream=True,
            )
    except requests.RequestException as e:
        raise ScanSourceError(f"Network error fetching {url} directly: {e}") from e

    with resp:
        # A redirect could point anywhere, including an internal address
        # that passed the pre-check above only because the *original* URL
        # was public — allow_redirects=False above stops requests from
        # silently following it, so a 3xx here is a definite (not just
        # possible) SSRF-via-redirect attempt, not a normal outcome to
        # retry around.
        if resp.is_redirect:
            raise ScanSourceError(f"Refusing to follow redirect from {url} to {resp.headers.get('Location')!r}")

        try:
            resp.raise_for_status()
        except requests.HTTPError as e:
            raise ScanSourceError(f"Direct fetch of {url} returned {resp.status_code}: {e}") from e

        body = bytearray()
        for chunk in resp.iter_content(chunk_size=65536):
            body += chunk
            if len(body) > _MAX_FETCH_BYTES:
                raise ScanSourceError(
                    f"Direct fetch of {url} exceeded {_MAX_FETCH_BYTES} bytes, aborting"
                )

    if not body:
        raise ScanSourceError(f"Direct fetch of {url} returned an empty body")
    return body.decode(resp.encoding or "utf-8", errors="replace")
