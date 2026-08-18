"""
Calls Claude's Messages API to extract structured job listings from a
page's scraped markdown content. Plain `requests.post` against the raw
API (same "no SDK for a small number of calls" reasoning as
firecrawl_client.py) — this backend has no existing Anthropic/OpenAI
integration to build on, so this is a first-time, deliberately minimal
integration.
"""
import json
import re

import requests

ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_VERSION = "2023-06-01"
# claude-sonnet-5: current-generation Sonnet, per this project's own
# "always default to the latest Claude models" guidance. Structured
# extraction from a single page's content doesn't need Opus-tier
# reasoning, and Haiku is a real cost lever worth revisiting once real
# scan volume exists — Sonnet is the right default to start with, not a
# permanent choice baked in by this comment.
CLAUDE_MODEL = "claude-sonnet-5"

REQUIRED_JOB_FIELDS = ("title",)
JOB_FIELDS = ("title", "company_name", "location", "salary", "description", "apply_url", "external_id", "employment_type", "deadline")
# Fields extract_job_detail backfills from a job's own page — deliberately
# a subset of JOB_FIELDS (no title/company_name/apply_url/external_id:
# those are already trustworthy from the listing-page pass and re-scraping
# a detail page isn't a more reliable source for identity fields, only for
# the body text a listing index never carries).
DETAIL_FIELDS = ("description", "salary", "employment_type", "location", "deadline")

EXTRACTION_PROMPT = """You are extracting job listings from a scraped job-board web page. \
Below is the page's content in markdown. Find every distinct job listing on the page and \
return them as a JSON array — nothing else, no explanation, no markdown code fence.

Each element must be an object with exactly these fields:
- "title": the job title (required — if you can't identify a real job title, skip that listing entirely)
- "company_name": the hiring company's name, or null if not stated
- "location": the job's location (city/region), or null if not stated
- "salary": the salary/pay as stated on the page (keep it as free text exactly as written, e.g. "Le 2,000,000/month", "Negotiable", "$500-800/month" — do not convert or normalize it), or null if not stated
- "description": the fullest available description of the role/responsibilities/requirements, or null if there's genuinely none beyond the title. Never write a sentence ABOUT the description being missing or incomplete (e.g. "No further details are provided") — that is not a description, use null instead.
- "apply_url": the URL to apply or view the full listing, or null if none is present in the content
- "external_id": a stable identifier for this specific listing — prefer its own detail-page URL if the content contains one, otherwise null
- "employment_type": the employment type/work arrangement as stated on the page (e.g. "Full-time", "Part-time", "Contract", "Internship", "Temporary" — use the source's own wording, do not invent or infer one it doesn't state), or null if not stated
- "deadline": the application deadline / closing date as stated on the page, formatted as "YYYY-MM-DD" (convert whatever format the page uses into this one — e.g. "23 August 2026" becomes "2026-08-23"), or null if no deadline is stated or you are not confident of the exact date. Never guess a year or day that isn't actually stated on the page.

If the page contains no real job listings at all, return an empty array: []

Page content:

{content}
"""

# Deliberately a separate prompt/shape from EXTRACTION_PROMPT, not a reuse
# of it: a listing-index page and one job's own detail page need different
# instructions (find every listing vs describe the one job this page is
# about), and a detail page fed through the listing prompt has, in
# practice, no reliable way to tell Claude which of several "related
# jobs" sidebar links is the one actually being asked about.
DETAIL_EXTRACTION_PROMPT = """You are looking at the scraped content of ONE job posting's own detail \
page (not a listing index). The content below is usually clean markdown, but may occasionally be raw \
HTML (a fallback fetch path used when the primary scraper is blocked) — read past any tags/scripts/nav \
markup either way and extract from the actual page text. Return a single JSON object describing that \
job — nothing else, no explanation, no markdown code fence.

The object must have exactly these fields:
- "description": the fullest available description of the role, responsibilities, and requirements as stated on the page, or null if the page genuinely has none beyond a title. Never write a sentence ABOUT the description being missing or incomplete (e.g. "No further details are provided") — that is not a description, use null instead.
- "salary": the salary/pay as stated on the page (keep it as free text exactly as written, e.g. "Le 2,000,000/month", "Negotiable", "$500-800/month" — do not convert or normalize it), or null if not stated
- "employment_type": the employment type/work arrangement as stated on the page (e.g. "Full-time", "Part-time", "Contract", "Internship", "Temporary" — use the source's own wording, do not invent or infer one it doesn't state), or null if not stated
- "location": the job's location (city/region) as stated on the page, or null if not stated
- "deadline": the application deadline / closing date as stated on the page, formatted as "YYYY-MM-DD" (convert whatever format the page uses into this one), or null if no deadline is stated or you are not confident of the exact date. Never guess a year or day that isn't actually stated on the page.

If the page doesn't look like a job posting at all (e.g. it 404'd, redirected to an unrelated page), return every field as null.

Page content:

{content}
"""


class ScanExtractionError(Exception):
    """
    Raised when Claude's response can't be parsed into the expected job
    schema at all (missing/invalid API key, network failure, non-JSON
    response). A single malformed *record* inside an otherwise-valid
    array is handled more leniently — see extract_jobs()'s docstring —
    this is only for total failures.
    """


def _strip_code_fence(text: str) -> str:
    """Claude sometimes wraps JSON output in a ```json fence despite being
    told not to — strip it rather than fail on otherwise-valid output.
    Matches an object OR an array, since this backs both extract_jobs
    (array) and extract_job_detail (object)."""
    match = re.search(r"```(?:json)?\s*([\[{].*[\]}])\s*```", text, re.DOTALL)
    return match.group(1) if match else text


def _call_claude_for_text(prompt_text: str, api_key: str | None, timeout: int) -> str:
    """
    Shared request/response handling behind both extract_jobs and
    extract_job_detail — auth, the network/HTTP-status error mapping, and
    the thinking-block-before-text-block parsing (see extract_jobs's own
    history) are identical regardless of what shape of JSON the caller
    goes on to parse out of the returned text.
    """
    if not api_key:
        raise ScanExtractionError("ANTHROPIC_API_KEY is not configured")

    try:
        resp = requests.post(
            ANTHROPIC_MESSAGES_URL,
            timeout=timeout,
            headers={
                "x-api-key": api_key,
                "anthropic-version": ANTHROPIC_API_VERSION,
                "content-type": "application/json",
            },
            json={
                "model": CLAUDE_MODEL,
                "max_tokens": 4096,
                "messages": [{"role": "user", "content": prompt_text}],
            },
        )
    except requests.RequestException as e:
        raise ScanExtractionError(f"Network error reaching Anthropic: {e}") from e

    if resp.status_code == 401:
        raise ScanExtractionError("Anthropic rejected the API key (401)")
    try:
        resp.raise_for_status()
    except requests.HTTPError as e:
        raise ScanExtractionError(f"Anthropic API returned {resp.status_code}: {resp.text[:300]}") from e

    try:
        payload = resp.json()
        content_blocks = payload["content"]
    except (ValueError, KeyError) as e:
        raise ScanExtractionError(f"Unexpected Anthropic response shape: {e}") from e

    # Real bug found via a live call, not assumed: content[0] is not
    # reliably the text block. Some accounts/models return a "thinking"
    # block first (extended thinking), with the actual "text" block
    # after it -- content[0]["text"] then raises KeyError on the
    # thinking block, which has no "text" field at all. Find the text
    # block(s) by type instead of assuming a fixed position; join in the
    # rare case a response splits text across more than one block.
    text_blocks = [b.get("text", "") for b in content_blocks if isinstance(b, dict) and b.get("type") == "text"]
    if not text_blocks:
        raise ScanExtractionError(f"No text block in Anthropic response (block types: {[b.get('type') for b in content_blocks if isinstance(b, dict)]})")
    return "".join(text_blocks)


def extract_jobs(markdown: str, api_key: str | None, logger=None, timeout: int = 60) -> list[dict]:
    """
    Returns a list of job dicts (see JOB_FIELDS). Records missing the
    required "title" field are dropped individually (logged, not fatal —
    a page with 9 good listings and 1 malformed one should still yield 9
    jobs, not zero). Raises ScanExtractionError only when the response as
    a whole can't be parsed into a job array at all — that's the one case
    scanner.pipeline needs to treat as a full scan failure.
    """
    text = _call_claude_for_text(EXTRACTION_PROMPT.format(content=markdown[:60000]), api_key, timeout)

    try:
        records = json.loads(_strip_code_fence(text.strip()))
    except json.JSONDecodeError as e:
        raise ScanExtractionError(f"Claude did not return valid JSON: {e}") from e

    if not isinstance(records, list):
        raise ScanExtractionError("Claude's JSON output was not an array")

    jobs = []
    for record in records:
        if not isinstance(record, dict) or not record.get("title"):
            if logger:
                logger.warning("Dropping extracted job record missing a title: %r", record)
            continue
        jobs.append({field: record.get(field) for field in JOB_FIELDS})
    return jobs


def extract_job_detail(markdown: str, api_key: str | None, logger=None, timeout: int = 60) -> dict:
    """
    Returns a single dict (see DETAIL_FIELDS) describing the one job a
    detail page is about. Unlike extract_jobs, a malformed/non-object
    response is not a per-record thing to drop — there's only one record
    — so it raises ScanExtractionError the same as a total failure would.
    Callers (scanner.pipeline's backfill pass) are expected to catch that
    per-job, not let one unreachable/malformed detail page fail an entire
    scan the way a malformed listing-page response would.
    """
    text = _call_claude_for_text(DETAIL_EXTRACTION_PROMPT.format(content=markdown[:60000]), api_key, timeout)

    try:
        record = json.loads(_strip_code_fence(text.strip()))
    except json.JSONDecodeError as e:
        raise ScanExtractionError(f"Claude did not return valid JSON: {e}") from e

    if not isinstance(record, dict):
        raise ScanExtractionError("Claude's JSON output was not an object")

    return {field: record.get(field) for field in DETAIL_FIELDS}
