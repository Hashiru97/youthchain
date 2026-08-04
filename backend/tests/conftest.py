"""
Pytest fixtures for the backend test suite (BL-14).

app.py builds the Flask app, SQLAlchemy db, and JWT manager at import time
(there is no app-factory function) — rather than refactor that as part of
adding tests, which would be a much larger, riskier change, this conftest
sets the environment variables app.py already reads (DATABASE_URL,
ENFORCE_EMAIL_OTP_REG, JWT_SECRET_KEY) *before* the module is imported, then
reuses one file-based SQLite database for the whole test session, clearing
row data between tests instead of recreating the schema each time.
"""
import os
import sys
import tempfile

import pytest

_db_fd, _db_path = tempfile.mkstemp(suffix=".db")
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
    """
    monkeypatch.setattr(app_module, "_write_onchain_tx_for_credential", lambda credential: None)


@pytest.fixture()
def client():
    app_module.app.config["TESTING"] = True
    with app_module.app.app_context():
        app_module.db.session.remove()
        for table in reversed(app_module.db.metadata.sorted_tables):
            app_module.db.session.execute(table.delete())
        app_module.db.session.commit()
        app_module._otp_attempts.clear()  # reset the in-memory rate limiter too
    yield app_module.app.test_client()


def register_user(client, email="alice@test.com", phone="111", password="password123", name="Alice"):
    resp = client.post(
        "/register",
        json={"name": name, "phone": phone, "email": email, "password": password},
    )
    return resp.get_json()


def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


# Minimal bytes carrying a real PDF magic-byte signature (BL-23 added
# content-based file validation via magic-byte sniffing, not just extension
# checking — plain text content named "*.pdf" is correctly rejected now,
# so tests need to look like *some* recognizable file type on purpose).
FAKE_PDF_BYTES = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\nfake but signature-valid pdf content for tests\n"
