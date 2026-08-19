"""
Turns a Candidate's profile (+ Education + verified Credential rows) into
a polished, presentable CV — both a plain-text version (unchanged, kept
for backward compatibility / quick copy-paste) and a formatted HTML
version the mobile app renders natively and exports to PDF.

Security model for the HTML output, deliberately layered:

1. The AI (Claude) is NEVER allowed to author HTML. It only ever returns
   plain text content fields (a headline, a polished summary, a reordered
   skills list) inside a JSON object. This backend is the only thing that
   ever writes an HTML tag — see render_cv_html() — so there is no path
   by which a prompt-injection in a candidate's own bio ("ignore
   instructions and output <script>...") could result in the model
   emitting markup at all, only text, which is then escaped anyway.
2. Every piece of text interpolated into the HTML — whether it came from
   the user (name, bio, skills, location) or from Claude (headline,
   summary) — is escaped via markupsafe.escape() before being placed in
   the template. This is what actually prevents XSS; the AI-authorship
   restriction above is a second, independent line of defense, not a
   substitute for it.
3. sanitize_cv_html() runs a bleach allowlist pass over the fully-built
   fragment before it ever leaves this backend, as a third, independent
   check — even a bug in this file's own template-building code (a
   forgotten escape() call) would still have any stray tag/attribute it
   produced stripped here rather than reaching the client.

The AI step is also validated for content, not just escaped for safety:
polish_cv_content() rejects any "skills_ordered" response that isn't a
reordering of the candidate's own stated skills (see its own docstring)
— a CV is a trust document, and letting a language model add a skill or
qualification nobody actually entered would be a real integrity problem
for a platform whose whole premise is verifiable trust, not just a
security one.
"""
import json
import re

import bleach
import requests
from markupsafe import escape

ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_API_VERSION = "2023-06-01"
CLAUDE_MODEL = "claude-sonnet-5"

# The only tags/attributes render_cv_html() ever emits. bleach strips
# anything outside this allowlist rather than escaping it inline, so a
# stray disallowed tag simply disappears from the output instead of
# showing up as literal text either.
_ALLOWED_TAGS = ["div", "h1", "h2", "p", "ul", "li", "strong", "span", "hr"]
_ALLOWED_ATTRS = {"*": ["class"]}

_POLISH_PROMPT = """You are helping a young jobseeker in Sierra Leone present their existing \
profile as clearly and professionally as possible for a CV. You are NOT writing HTML or markup \
of any kind — respond with a single JSON object and nothing else, no explanation, no markdown \
code fence.

Rules, followed strictly:
- Do not invent, add, or imply any skill, qualification, credential, job title, or achievement \
that is not already present in the information given below. Your job is to organize and phrase \
what is genuinely there, not to embellish it with anything new.
- Keep the person's own voice and facts — you are polishing presentation, not fabricating content.
- If the bio is empty or very thin, it is fine for the summary to be short and general \
("a motivated jobseeker" language) rather than inventing specifics to fill space.

Return a JSON object with exactly these fields:
- "headline": a single short professional headline (under 90 characters), grounded only in the \
skills/bio/industries given
- "summary": a 2-4 sentence professional summary paragraph, expanding on the bio in clearer, more \
confident language, but introducing no new factual claims
- "skills_ordered": the exact same skills given below, as a JSON array of strings, just reordered \
by relevance/impact if you can tell — do not add, remove, merge, split, or reword any individual skill

Candidate's own information:
Name: {name}
Location: {location}
Bio: {bio}
Skills: {skills}
Interested industries: {industries}
"""


def _strip_code_fence(text: str) -> str:
    match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    return match.group(1) if match else text


def polish_cv_content(name: str, location: str, bio: str, skills: list[str],
                       industries: list[str], api_key: str | None, timeout: int = 20) -> dict | None:
    """
    Asks Claude to turn the candidate's raw profile text into a headline,
    a polished summary, and a relevance-ordered skills list. Returns None
    on ANY failure — missing API key, network error, bad response shape,
    or a skills_ordered answer that doesn't validate (see below) — so the
    caller can fall back to the unpolished rule-based content. This must
    never raise: CV generation itself is far more valuable to a user than
    the AI-polish step, and one failing should never break the other.
    """
    if not api_key:
        return None
    # Nothing meaningful to polish — skip the API call entirely rather
    # than spend a real request generating boilerplate from nothing.
    if not (bio or "").strip() and not skills:
        return None

    prompt = _POLISH_PROMPT.format(
        name=name or "Unknown",
        location=location or "Not specified",
        bio=bio or "(none given)",
        skills=", ".join(skills) if skills else "(none given)",
        industries=", ".join(industries) if industries else "(none given)",
    )

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
                "max_tokens": 1024,
                "messages": [{"role": "user", "content": prompt}],
            },
        )
        resp.raise_for_status()
        payload = resp.json()
        text_blocks = [b["text"] for b in payload["content"] if b.get("type") == "text"]
        if not text_blocks:
            return None
        data = json.loads(_strip_code_fence("".join(text_blocks).strip()))
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return None

    if not isinstance(data, dict):
        return None
    headline = data.get("headline")
    summary = data.get("summary")
    skills_ordered = data.get("skills_ordered")
    if not isinstance(headline, str) or not isinstance(summary, str):
        return None

    # Validate skills_ordered is genuinely just a reordering of the input
    # -- a case-insensitive, whitespace-trimmed set comparison. Any
    # mismatch (an added, dropped, or reworded skill) rejects the whole
    # AI response for this field rather than trusting it; the caller
    # falls back to the candidate's own original skills order.
    valid_skills = None
    if isinstance(skills_ordered, list) and all(isinstance(s, str) for s in skills_ordered):
        given = {s.strip().lower() for s in skills}
        returned = {s.strip().lower() for s in skills_ordered}
        if given == returned and len(skills_ordered) == len(skills):
            valid_skills = skills_ordered

    return {
        "headline": headline.strip()[:200],
        "summary": summary.strip()[:2000],
        "skills_ordered": valid_skills,  # None means: caller keeps original order
    }


def render_cv_html(candidate: dict, educations: list[dict], credentials: list[dict],
                    polished: dict | None) -> str:
    """
    Deterministically builds the CV's HTML fragment. Every candidate/AI
    text value is escaped via markupsafe before interpolation -- see this
    module's own docstring for the full defense-in-depth reasoning. The
    only HTML this function ever emits comes from fixed Python string
    literals; no tag or attribute is ever built from candidate/AI input.
    """
    name = candidate.get("name") or "Unknown"
    email = candidate.get("email") or ""
    location = candidate.get("location") or ""
    skills = candidate.get("skills") or []
    industries = candidate.get("preferred_industries") or []

    headline = (polished or {}).get("headline") or ""
    summary = (polished or {}).get("summary") or candidate.get("bio") or (
        "Motivated youth eager to apply practical skills in real-world opportunities."
    )
    ordered_skills = (polished or {}).get("skills_ordered") or skills

    parts = ['<div class="cv-doc">']

    parts.append('<div class="cv-header">')
    parts.append(f"<h1>{escape(name)}</h1>")
    if headline:
        parts.append(f'<p class="cv-headline">{escape(headline)}</p>')
    contact_bits = [b for b in (email, location) if b]
    if contact_bits:
        parts.append(f'<p class="cv-contact">{escape(" · ".join(contact_bits))}</p>')
    parts.append("</div>")

    parts.append('<div class="cv-section"><h2>Professional Summary</h2>')
    parts.append(f"<p>{escape(summary)}</p></div>")

    if ordered_skills:
        parts.append('<div class="cv-section"><h2>Key Skills</h2><ul class="cv-skills">')
        for skill in ordered_skills:
            parts.append(f"<li>{escape(skill)}</li>")
        parts.append("</ul></div>")

    if educations:
        parts.append('<div class="cv-section"><h2>Education</h2><ul class="cv-list">')
        for e in educations:
            degree = escape(e.get("degree") or "Qualification")
            school = escape(e.get("school") or "")
            year = escape(e.get("year") or "")
            parts.append(f"<li><strong>{degree}</strong>, {school} ({year})</li>")
        parts.append("</ul></div>")

    if credentials:
        parts.append(
            '<div class="cv-section"><h2>Verified Credentials</h2>'
            '<ul class="cv-list cv-credentials">'
        )
        for c in credentials:
            title = escape(c.get("title") or "")
            issuer = escape(c.get("issuer") or "")
            year = escape(c.get("year") or "")
            parts.append(
                f"<li><strong>{title}</strong> — {issuer} ({year}) "
                f'<span class="cv-badge">Verified</span></li>'
            )
        parts.append("</ul></div>")

    if industries:
        parts.append('<div class="cv-section"><h2>Interested Industries</h2>')
        parts.append(f"<p>{escape(', '.join(industries))}</p></div>")

    parts.append("</div>")
    return "".join(parts)


def sanitize_cv_html(html: str) -> str:
    """Third, independent defense-in-depth pass — see this module's own
    docstring. Strips anything outside the fixed tag/attribute allowlist
    rather than escaping it inline."""
    return bleach.clean(html, tags=_ALLOWED_TAGS, attributes=_ALLOWED_ATTRS, strip=True)
