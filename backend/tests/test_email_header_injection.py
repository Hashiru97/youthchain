"""
Regression coverage for a real SMTP header-injection (CRLF injection)
vulnerability found via a full security review: _send_email() used to
hand-build the raw SMTP message with an f-string
("Subject: {subject}\r\n\r\n{body}") and pass it straight to
smtplib.sendmail(), which performs no header/body validation of its own.
subject is built at several call sites (_notify_admins_of_new_report,
_notify_admins_of_new_listing_report, etc.) from user-controlled fields
(Employer.name, User.name, Job.title) that were only ever .strip()'d, not
validated against embedded CRLFs -- and reachable with zero admin
interaction, e.g. any authenticated youth calling POST /api/report_employer
against an employer whose name they chose at registration. An attacker
could inject arbitrary extra SMTP headers (a Bcc:, a spoofed body) into
mail sent from this app's own SMTP credentials.

Fixed by building the message with email.message.EmailMessage (which
rejects embedded newlines in header values by construction) instead of a
hand-built string, plus stripping control characters from
User.name/Employer.name/Job.title at the point they're first accepted.
"""
import app as app_module


def test_strip_control_chars_removes_cr_and_lf_but_keeps_normal_text():
    assert app_module._strip_control_chars("Evil Corp\r\nBcc: attacker@evil.com") == "Evil CorpBcc: attacker@evil.com"
    assert app_module._strip_control_chars("Normal Company Name") == "Normal Company Name"
    # Krio diacritics (used throughout this app's own l10n) must survive --
    # the filter is control-character-specific, not ASCII-only.
    assert app_module._strip_control_chars("Kɔmpani Nem ɛn Tin") == "Kɔmpani Nem ɛn Tin"
    # Tabs are allowed (not a header-injection vector on their own).
    assert app_module._strip_control_chars("A\tB") == "A\tB"


def test_send_email_rejects_a_crlf_injected_subject_instead_of_sending_a_corrupted_message(monkeypatch):
    """
    _send_email() fails closed (returns False, sends nothing) rather than
    transmitting a message with attacker-injected headers, when a caller
    passes a subject containing embedded CRLF -- proving the EmailMessage
    fix actually engages, not just that it exists in the source.
    """
    # "Configure" SMTP so _send_email doesn't take its early log-only-and-
    # return-False path before ever reaching the vulnerable construction
    # code.
    monkeypatch.setattr(app_module, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(app_module, "SMTP_USER", "user@example.com")
    monkeypatch.setattr(app_module, "SMTP_PASS", "password")
    monkeypatch.setattr(app_module, "SMTP_FROM", "no-reply@youthchain.example")

    class _FakeSMTPThatWouldSendIfReached:
        """If _send_email's EmailMessage construction didn't reject the
        malicious subject, execution would reach here and attempt a real
        network connection -- this stands in for the real smtplib.SMTP so
        the test can't accidentally try to connect to smtp.example.com,
        and so a real send_message() call (the injection succeeding) is
        observable."""

        sent = []

        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, *a, **kw):
            pass

        def login(self, *a, **kw):
            pass

        def send_message(self, msg):
            _FakeSMTPThatWouldSendIfReached.sent.append(msg)

    monkeypatch.setattr(app_module.smtplib, "SMTP", _FakeSMTPThatWouldSendIfReached)

    malicious_subject = "Evil Corp\r\nBcc: attacker@evil.com\r\nSubject: spoofed"
    result = app_module._send_email("admin@youthchain.example", malicious_subject, "body")

    assert result is False, "a CRLF-injected subject must not be treated as a successful send"
    assert _FakeSMTPThatWouldSendIfReached.sent == [], "no message should ever reach send_message() when header construction rejects the input"


def test_send_email_still_sends_a_normal_subject_successfully(monkeypatch):
    """Guards the fix against accidentally breaking the common case."""
    monkeypatch.setattr(app_module, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(app_module, "SMTP_USER", "user@example.com")
    monkeypatch.setattr(app_module, "SMTP_PASS", "password")
    monkeypatch.setattr(app_module, "SMTP_FROM", "no-reply@youthchain.example")

    sent = []

    class _FakeSMTP:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, *a, **kw):
            pass

        def login(self, *a, **kw):
            pass

        def send_message(self, msg):
            sent.append(msg)

    monkeypatch.setattr(app_module.smtplib, "SMTP", _FakeSMTP)

    result = app_module._send_email("admin@youthchain.example", "A normal subject", "A normal body")

    assert result is True
    assert len(sent) == 1
    assert sent[0]["Subject"] == "A normal subject"
    assert sent[0]["To"] == "admin@youthchain.example"
    assert sent[0].get_content().strip() == "A normal body"
