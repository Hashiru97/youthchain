"""
Pytest fixtures for the backend test suite (BL-14).

app.py builds the Flask app, SQLAlchemy db, and JWT manager at import time
(there is no app-factory function) — rather than refactor that as part of
adding tests, which would be a much larger, riskier change, this conftest
sets the environment variables app.py already reads (DATABASE_URL,
ENFORCE_EMAIL_OTP_REG, JWT_SECRET_KEY) *before* the module is imported, then
reuses one database for the whole test session, clearing row data between
tests instead of recreating the schema each time.
"""
import os
import sys
import tempfile

import pytest

_db_fd, _db_path = tempfile.mkstemp(suffix=".db")
# Defaults to a throwaway file-based SQLite db (zero-config, what every
# local `pytest` run and the CI job's sqlite matrix leg use) — but real
# gap found and closed alongside the pool-tuning change: this used to set
# DATABASE_URL unconditionally, silently overwriting whatever the caller
# already exported. That made it impossible for CI to actually run this
# suite against Postgres (the documented production database — see
# docker-compose.yml/docs/load-testing.md) no matter what its job env set,
# since this line always won. Now: only fills in the SQLite default if
# DATABASE_URL isn't already set to something real.
if not os.getenv("DATABASE_URL"):
    os.environ["DATABASE_URL"] = f"sqlite:///{_db_path}"
os.environ["ENFORCE_EMAIL_OTP_REG"] = "0"
os.environ["JWT_SECRET_KEY"] = "test-secret-key-not-for-production"

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import app as app_module  # noqa: E402  (import must follow env setup above)


def pytest_sessionfinish(session, exitstatus):
    os.close(_db_fd)
    try:
        os.unlink(_db_path)
    except OSError:
        pass


@pytest.fixture(autouse=True)
def _no_real_blockchain_subprocess(monkeypatch):
    """
    Credential issuance shells out to a real `npx hardhat` subprocess to
    write on-chain — appropriate for the backend to do, but not for a unit
    test to depend on (slow, and its result depends on whether a Hardhat
    node happens to be running on the machine executing the suite, which
    made these tests flaky in practice: they passed or failed depending on
    unrelated local dev-environment state). The on-chain write path itself
    is covered separately: by blockchain/test/YouthChainRegistry.test.js and
    by a manual end-to-end run against a real local node (see the
    engineering report's remediation log). Applied globally (not per-file)
    since several test modules exercise /issue_credential incidentally.

    Also stubs _spawn_background_onchain_write to run its target inline
    instead of actually spawning a thread. The real write is stubbed to a
    no-op above regardless, so the thread's own work is harmless either
    way -- but a real background thread reading Credential rows during
    the brief window between one test's assertions and the next test's
    client fixture wiping every table is an unnecessary, avoidable race
    (SQLite in particular can raise "database is locked" under real
    concurrent access) for zero test-value. Deterministic synchronous
    execution is the right default for the whole suite; the one test that
    needs to verify real backgrounding actually happens
    (test_issue_credential_returns_before_onchain_write_completes)
    overrides this back to the real threaded version itself.
    """
    monkeypatch.setattr(app_module, "_write_onchain_tx_for_credential", lambda credential: None)
    monkeypatch.setattr(
        app_module,
        "_spawn_background_onchain_write",
        lambda credential_id: app_module._write_onchain_tx_for_credential_async(app_module.app, credential_id),
    )
    # Same reasoning, for the revocation write/read paths added alongside
    # YouthChainRegistry.sol's revokeCredential()/isValid(). Defaults
    # _check_onchain_valid to True (the common "still valid" case) --
    # individual tests that need to assert on the revoked-credential
    # display path override this themselves.
    monkeypatch.setattr(app_module, "_check_onchain_valid", lambda hash_hex: True)
    monkeypatch.setattr(app_module, "_revoke_credential_onchain", lambda credential: None)
    monkeypatch.setattr(
        app_module,
        "_spawn_background_credential_revoke",
        lambda credential_id: app_module._revoke_credential_onchain_async(app_module.app, credential_id),
    )
    # Same reasoning, for the issuer-accreditation write/read paths added
    # alongside YouthChainRegistry.sol's accreditIssuer()/revokeIssuer().
    # Unlike credential issuance/revocation, these routes have no DB row
    # to fall back on -- the route's success path depends entirely on
    # these return values, so (unlike _write_onchain_tx_for_credential's
    # default no-op above) the default here is "the chain call
    # succeeded", so unrelated tests that happen to exercise
    # /admin/issuers/* don't spuriously see the failure branch. Individual
    # tests that need the failure path override these themselves.
    monkeypatch.setattr(app_module, "_accredit_issuer_onchain", lambda address: "0xfaketxhash")
    monkeypatch.setattr(app_module, "_revoke_issuer_onchain", lambda address: "0xfaketxhash")
    monkeypatch.setattr(app_module, "_list_issuers_onchain", lambda: [])


@pytest.fixture(autouse=True)
def _no_real_pwned_passwords_lookup(monkeypatch):
    """
    _password_is_breached() (added during a full OWASP Top 10 review, A07)
    calls the real Pwned Passwords API over HTTPS on every registration --
    same problem as the blockchain subprocess above: real network I/O on
    every single register_user() call across this whole suite would be
    slow and make tests depend on an external service's uptime/reachability
    rather than this codebase's own behavior. Defaults every test to
    "not breached" (the common case, and what most existing tests
    implicitly assume); the specific tests that verify the breach-rejection
    path and the fail-open-on-network-error path override this themselves.
    """
    monkeypatch.setattr(app_module, "_password_is_breached", lambda password: False)


@pytest.fixture()
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.app_context():
        app_module.db.session.remove()
        for table in reversed(app_module.db.metadata.sorted_tables):
            app_module.db.session.execute(table.delete())
        app_module.db.session.commit()
        app_module._otp_attempts.clear()  # reset the in-memory rate limiter too
        # When REDIS_URL is set (e.g. the CI job's Redis service), the OTP
        # rate limiter uses real Redis keys instead of the in-memory dict
        # above — flush those too, or a previous test's attempt count would
        # leak into the next test and cause spurious 429s.
        if app_module._redis_client is not None:
            app_module._redis_client.flushdb()
    yield app_module.app.test_client()


def register_user(client, email="alice@test.com", phone="111", password="password123", name="Alice", consent=True):
    payload = {"name": name, "phone": phone, "email": email, "password": password}
    if consent is not None:
        payload["consent"] = consent
    resp = client.post("/register", json=payload)
    return resp.get_json()


def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


# Minimal bytes carrying a real PDF magic-byte signature (BL-23 added
# content-based file validation via magic-byte sniffing, not just extension
# checking — plain text content named "*.pdf" is correctly rejected now,
# so tests need to look like *some* recognizable file type on purpose).
FAKE_PDF_BYTES = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\nfake but signature-valid pdf content for tests\n"
