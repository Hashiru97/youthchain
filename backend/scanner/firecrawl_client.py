"""
Thin wrapper around Firecrawl's /v1/scrape endpoint — fetches a URL and
returns clean markdown content, letting Firecrawl's own infrastructure
handle JS rendering/bot-detection/etc. rather than this backend fetching
pages directly. Plain `requests.post`, same convention as
app._password_is_breached()'s HIBP call — no SDK for a single endpoint.
"""
import time

import requests


class ScanSourceError(Exception):
    """
    Raised when a source URL can't be scraped — missing/invalid API key,
    network failure, timeout, or Firecrawl itself reporting a failure
    (e.g. the target blocked the request). Always caught by
    scanner.pipeline and recorded on the owning scan_run.error_message —
    never allowed to propagate into the poller loop and kill it.
    """


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
    try:
        resp = requests.get(url, timeout=timeout, headers={"User-Agent": _PLAIN_FETCH_USER_AGENT})
    except requests.RequestException as e:
        raise ScanSourceError(f"Network error fetching {url} directly: {e}") from e

    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        raise ScanSourceError(f"Direct fetch of {url} returned {resp.status_code}: {e}") from e

    if not resp.text:
        raise ScanSourceError(f"Direct fetch of {url} returned an empty body")
    return resp.text
