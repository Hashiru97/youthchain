"""
Regression coverage for the applicant-review credential-trust summary
(real gap found via a full-codebase review: an employer reviewing
applicants had zero visibility into a candidate's on-chain-verified
credentials anywhere in that workflow -- the only way to check one was
the separate, undiscoverable /employer/verify manual hash-paste tool, one
credential at a time, with no indication a candidate even had any. See
employer_applications()'s own docstring comment in app.py.
"""
import io
import re

import app as app_module
from conftest import register_user, auth_headers, FAKE_PDF_BYTES


def _csrf_token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "csrf token not found in page"
    return match.group(1)


def _register_employer(client, email="acme@test.com", name="Acme", password="password123"):
    page = client.get("/employer/register")
    token = _csrf_token(page.get_data(as_text=True))
    return client.post(
        "/employer/register",
        data={"csrf_token": token, "name": name, "email": email, "password": password},
    )


def _post_job(client, title="Welder Needed"):
    page = client.get("/employer/post")
    client.post("/employer/post", data={
        "csrf_token": _csrf_token(page.get_data(as_text=True)),
        "title": title, "location": "Bo", "duration": "3 months",
    })
    with app_module.app.app_context():
        job = app_module.Job.query.filter_by(title=title).first()
        return job.id


def _apply(client, job_id, email="worker@test.com", phone="555"):
    worker = register_user(client, email=email, phone=phone)
    resp = client.post(
        "/apply",
        data={
            "job_id": str(job_id),
            "cv": (io.BytesIO(FAKE_PDF_BYTES), "cv.pdf"),  # required for a formal job
        },
        headers=auth_headers(worker["access_token"]),
        content_type="multipart/form-data",
    )
    assert resp.status_code in (200, 201), resp.get_data(as_text=True)
    return worker


def _issue_credential(client, token, title, issuer="Test Institute", content=None):
    resp = client.post(
        "/issue_credential",
        data={"title": title, "issuer": issuer, "file": (io.BytesIO(content or FAKE_PDF_BYTES), f"{title}.pdf")},
        headers=auth_headers(token),
    )
    assert resp.status_code in (200, 201), resp.get_data(as_text=True)
    return resp.get_json()["credential_id"]


def test_applicant_with_a_verified_credential_shows_a_verified_badge(client, monkeypatch):
    monkeypatch.setattr(app_module, "_write_onchain_tx_for_credential", lambda credential: "0xverified")

    _register_employer(client)
    job_id = _post_job(client)
    worker = _apply(client, job_id)
    _issue_credential(client, worker["access_token"], "Welding Certification")

    page = client.get(f"/employer/applications/{job_id}")
    body = page.get_data(as_text=True)
    assert "1 verified" in body
    assert "Welding Certification" in body
    assert "No credentials on file" not in body


def test_applicant_with_no_credentials_shows_the_empty_state(client):
    _register_employer(client)
    job_id = _post_job(client)
    _apply(client, job_id)

    body = client.get(f"/employer/applications/{job_id}").get_data(as_text=True)
    assert "No credentials on file" in body


def test_applicant_with_a_revoked_credential_shows_a_revoked_badge_not_verified(client, monkeypatch):
    """
    The exact real bug this whole feature exists to prevent from
    recurring in a new place: a revoked credential must never read as
    trustworthy just because it has an onchain_tx.
    """
    monkeypatch.setattr(app_module, "_write_onchain_tx_for_credential", lambda credential: "0xwillberevoked")
    monkeypatch.setattr(app_module, "_revoke_credential_onchain", lambda credential: "0xrevoketx")

    _register_employer(client, email="acme2@test.com")
    job_id = _post_job(client, title="Job Two")
    worker = _apply(client, job_id, email="worker2@test.com", phone="556")
    cred_id = _issue_credential(client, worker["access_token"], "Suspicious Cert")

    # A separate test client (own cookie jar, same app/DB) for the admin
    # revoke step -- `client` is already employer-authenticated from
    # _register_employer/_post_job above and must stay that way for the
    # final request below, not get its session cookie overwritten by an
    # admin login.
    admin_client = app_module.app.test_client()
    admin_email = "credadmin@youthchain.test"
    with app_module.app.app_context():
        admin = app_module.Admin(
            name="Ops", email=admin_email, role="admin", active=True,
            password_hash=app_module.generate_password_hash("adminpass123"),
        )
        app_module.db.session.add(admin)
        app_module.db.session.commit()
    admin_login_page = admin_client.get("/admin/login")
    admin_token = _csrf_token(admin_login_page.get_data(as_text=True))
    admin_client.post("/admin/login", data={"csrf_token": admin_token, "email": admin_email, "password": "adminpass123"})
    admin_creds_page = admin_client.get("/admin/credentials")
    revoke_token = _csrf_token(admin_creds_page.get_data(as_text=True))
    admin_client.post(f"/admin/credentials/{cred_id}/revoke", data={"csrf_token": revoke_token})

    page = client.get(f"/employer/applications/{job_id}")
    body = page.get_data(as_text=True)
    assert "1 revoked" in body
    assert "1 verified" not in body
    assert "Suspicious Cert" in body


def test_credentials_do_not_leak_between_different_applicants(client, monkeypatch):
    """
    Direct correctness check on the grouped single-query fetch
    (credentials_by_user_id) -- two applicants to the same job, each with
    their own distinct credential, must never see each other's.
    """
    monkeypatch.setattr(app_module, "_write_onchain_tx_for_credential", lambda credential: "0xok")

    _register_employer(client, email="acme3@test.com")
    job_id = _post_job(client, title="Job Three")
    worker_a = _apply(client, job_id, email="workera@test.com", phone="601")
    worker_b = _apply(client, job_id, email="workerb@test.com", phone="602")
    _issue_credential(client, worker_a["access_token"], "Worker A Cert")
    _issue_credential(client, worker_b["access_token"], "Worker B Cert")

    body = client.get(f"/employer/applications/{job_id}").get_data(as_text=True)
    assert body.count("Worker A Cert") == 1
    assert body.count("Worker B Cert") == 1
