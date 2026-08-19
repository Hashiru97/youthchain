"""
Unit coverage for scanner.scam_signals.detect_scam_signals() -- the
"AI scam-signal detection" the user asked for (fee requests, personal
email, vague pay). See that module's own docstring for why this is
deterministic pattern-matching, not a second Claude call: these tests
depend on that determinism to make exact assertions.
"""
from scanner.scam_signals import detect_scam_signals


def test_no_signals_on_an_ordinary_legitimate_listing():
    signals = detect_scam_signals(
        title="Office Administrator",
        description="We are looking for an organized administrator to manage front-office duties.",
        salary="Le 2,000,000/month",
        apply_url="https://acmecorp.sl/careers/office-admin",
    )
    assert signals == []


# ----------------- fee_request -----------------

def test_flags_a_registration_fee_demand():
    signals = detect_scam_signals(
        title="Data Entry Clerk",
        description="Successful applicants must pay a registration fee of Le 50,000 before starting.",
        salary="Le 800,000/month",
        apply_url="https://example.com/apply",
    )
    assert "fee_request" in signals


def test_flags_a_western_union_demand():
    signals = detect_scam_signals(
        title="Remote Assistant",
        description="To receive your starter kit, send money via Western Union to our coordinator.",
        salary=None,
        apply_url=None,
    )
    assert "fee_request" in signals


def test_does_not_flag_an_explicit_no_fee_reassurance():
    """A listing reassuring applicants there's no cost is the opposite
    of a red flag -- must not be flagged identically to a real demand."""
    signals = detect_scam_signals(
        title="Sales Associate",
        description="This is a genuine opportunity -- there is no registration fee required to apply.",
        salary="Negotiable based on experience",
        apply_url=None,
    )
    assert "fee_request" not in signals


def test_does_not_flag_ordinary_mention_of_money():
    signals = detect_scam_signals(
        title="Cashier",
        description="Handle daily cash reconciliation and mobile money transactions for customers.",
        salary="Le 1,200,000/month",
        apply_url=None,
    )
    assert "fee_request" not in signals


# ----------------- personal_email -----------------

def test_flags_a_personal_gmail_only_contact():
    signals = detect_scam_signals(
        title="Marketing Intern",
        description="Interested candidates should send their CV to hr.recruiter2026@gmail.com.",
        salary="Le 900,000/month",
        apply_url=None,
    )
    assert "personal_email" in signals


def test_flags_a_personal_email_in_the_apply_url_field():
    """Some scrapers land a mailto: link in apply_url rather than the
    description -- must be checked too, not just description text."""
    signals = detect_scam_signals(
        title="Driver",
        description="Full-time driver needed.",
        salary="Le 1,000,000/month",
        apply_url="mailto:jobsnow123@yahoo.com",
    )
    assert "personal_email" in signals


def test_does_not_flag_a_corporate_domain_email():
    signals = detect_scam_signals(
        title="Accountant",
        description="Send applications to careers@sierraleonebank.sl.",
        salary="Le 3,000,000/month",
        apply_url=None,
    )
    assert "personal_email" not in signals


# ----------------- vague_pay -----------------

def test_flags_vague_pay_with_no_figure_at_all():
    signals = detect_scam_signals(
        title="Sales Rep",
        description="Great opportunity for a motivated individual.",
        salary="Competitive",
        apply_url=None,
    )
    assert "vague_pay" in signals


def test_does_not_flag_missing_salary_as_vague_pay():
    """A null/absent salary is extremely common on legitimate scraped
    listings -- only a non-empty, figure-free VAGUE salary counts,
    or this would fire on most ordinary listings and be useless noise."""
    signals = detect_scam_signals(
        title="Teacher",
        description="Secondary school teacher needed for the new term.",
        salary=None,
        apply_url=None,
    )
    assert "vague_pay" not in signals


def test_does_not_flag_negotiable_when_a_figure_is_also_given():
    signals = detect_scam_signals(
        title="Consultant",
        description="Short-term consulting engagement.",
        salary="Negotiable, typically Le 1,500,000-2,000,000/month",
        apply_url=None,
    )
    assert "vague_pay" not in signals


# ----------------- multiple signals -----------------

def test_flags_multiple_signals_at_once_on_a_real_looking_scam():
    signals = detect_scam_signals(
        title="Work From Home Data Entry",
        description=(
            "Earn money from home! To secure your position, pay a small processing fee. "
            "Contact easyworksl2026@gmail.com to begin."
        ),
        salary="Very attractive",
        apply_url=None,
    )
    assert set(signals) == {"fee_request", "personal_email", "vague_pay"}
