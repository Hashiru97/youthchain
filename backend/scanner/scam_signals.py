"""
Scam-signal detection for scraped job listings -- fee requests, a
personal-email-only contact method, and vague/missing pay. See
detect_scam_signals()'s own docstring for why this is deliberately
deterministic pattern-matching rather than a second Claude API call.
"""
import re

# Fee/deposit/training-cost language specifically in the context of
# paying to GET the job/interview/materials -- not just any mention of
# money (a legitimate listing saying "salary" or "$500" isn't a fee
# scam). Real Sierra Leone-relevant payment rails included alongside the
# generic ones, since a local scam is more likely to ask for mobile
# money than a wire transfer.
_FEE_PATTERNS = [
    r"registration fee",
    r"processing fee",
    r"application fee",
    r"training fee",
    r"admin(?:istration)? fee",
    r"activation fee",
    r"security deposit",
    r"pay(?:ment)? (?:is |will be )?required (?:to|before)",
    r"send (?:money|payment)",
    r"western union",
    r"money ?gram",
    r"mobile money (?:to|before)",
    r"orange money (?:to|before)",
    r"pay to (?:receive|get|start|secure)",
]
_FEE_RE = re.compile("|".join(_FEE_PATTERNS), re.IGNORECASE)
# A match preceded closely by a negation ("no registration fee", "without
# any payment") is the OPPOSITE of a red flag -- a listing reassuring
# applicants there's no cost is common and legitimate, and without this
# check it would be flagged identically to an actual fee demand.
_NEGATION_RE = re.compile(r"\b(?:no|not|never|without|free of)\s+\S*\s*$", re.IGNORECASE)
_NEGATION_LOOKBACK_CHARS = 24

_PERSONAL_EMAIL_DOMAINS = {
    "gmail.com", "yahoo.com", "hotmail.com", "outlook.com", "aol.com",
    "icloud.com", "live.com", "yahoo.co.uk", "ymail.com", "protonmail.com",
}
_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@([a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})")

# Only fires against a NON-empty salary field that's all vague wording
# and no figure at all (e.g. "Competitive salary", "Negotiable") -- a
# missing/null salary is extremely common on legitimate scraped listings
# (most sources just don't state one) and is deliberately NOT treated as
# a signal on its own, or this would fire on the majority of real,
# ordinary listings and drown out the two genuinely meaningful signals.
_VAGUE_PAY_RE = re.compile(
    r"competitive|negotiable|attractive|good salary|to be discussed|\btbd\b|depend(?:s|ing) on experience",
    re.IGNORECASE,
)
_HAS_NUMBER_RE = re.compile(r"\d")


def _has_fee_request(text: str) -> bool:
    for match in _FEE_RE.finditer(text):
        preceding = text[max(0, match.start() - _NEGATION_LOOKBACK_CHARS):match.start()]
        if _NEGATION_RE.search(preceding):
            continue
        return True
    return False


def _has_personal_email(*texts: str) -> bool:
    for text in texts:
        for match in _EMAIL_RE.finditer(text or ""):
            if match.group(1).lower() in _PERSONAL_EMAIL_DOMAINS:
                return True
    return False


def _has_vague_pay(salary: str | None) -> bool:
    if not salary or not salary.strip():
        return False
    return bool(_VAGUE_PAY_RE.search(salary)) and not _HAS_NUMBER_RE.search(salary)


def detect_scam_signals(
    title: str | None, description: str | None, salary: str | None, apply_url: str | None,
) -> list[str]:
    """
    Returns zero or more of ("fee_request", "personal_email",
    "vague_pay") for a scraped job's current fields. Deliberately
    deterministic pattern-matching, not a second Claude call: these
    three signals are well-defined enough that regex/keyword/domain
    checks are both free (no added per-job API cost beyond the
    extraction call scanner.pipeline already makes) and exactly,
    deterministically testable -- unlike an LLM's probabilistic
    judgment, the same input always produces the same output here, which
    matters for a signal an admin/user will see attached to real
    listings. Intended to be called on the FINAL, merged Job fields
    (after any detail-page backfill — see scanner.pipeline), not the raw
    per-pass extraction, so it always sees the fullest text available
    regardless of which pass populated it.

    Not a numeric risk score, deliberately -- the three signals are
    reported as-is (a caller/UI can decide "any signal at all" is enough
    to show a caution badge) rather than this function inventing a
    weighted point system nobody asked for.
    """
    text = " ".join(filter(None, [title, description]))
    signals = []
    if _has_fee_request(text):
        signals.append("fee_request")
    if _has_personal_email(text, apply_url or ""):
        signals.append("personal_email")
    if _has_vague_pay(salary):
        signals.append("vague_pay")
    return signals
