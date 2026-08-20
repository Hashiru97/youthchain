"""
Regression coverage for account erasure (_erase_user_data in app.py) --
built after an engineering-review conversation about data-protection
erasure requests and how they interact with an immutable credential
registry. Covers: the tombstone design (User row kept, PII scrubbed,
every other table's user_id FK stays valid untouched), real file deletion
from disk, redaction-not-deletion for records a counterparty has a
legitimate interest in (Application, Message), full deletion for records
that are purely the user's own (Credential -- and specifically that
/verify/<id> then returns the same judgment-free "not_found" state as a
hash that was never registered), and the open-dispute hold that stops
erasure being used to destroy evidence mid-investigation.
"""
import io
import os
import re

import app as app_module
from conftest import register_user, auth_headers, FAKE_PDF_BYTES


def _csrf_token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "csrf token not found in page"
    return match.group(1)


def register_employer(client, email="acme@test.com", name="Acme", password="password123"):
    page = client.get("/employer/register")
    token = _csrf_token(page.get_data(as_text=True))
    return client.post(
        "/employer/register",
        data={"csrf_token": token, "name": name, "email": email, "password": password},
    )


def _setup_application(client, email="youth@test.com", phone="333"):
    """Employer posts a job, youth applies to it with a CV; returns (job_id, app_id, youth)."""
    register_employer(client)
    page = client.get("/employer/post")
    token = _csrf_token(page.get_data(as_text=True))
    client.post(
        "/employer/post",
        data={"csrf_token": token, "title": "Welder", "location": "Bo", "duration": "6mo"},
    )
    with app_module.app.app_context():
        job = app_module.Job.query.filter_by(title="Welder").first()
        job_id = job.id

    logout_page = client.get("/employer/post")
    client.post("/employer/logout", data={"csrf_token": _csrf_token(logout_page.get_data(as_text=True))})

    youth = register_user(client, email=email, phone=phone)
    client.post(
        "/apply",
        data={"job_id": str(job_id), "cv": (io.BytesIO(FAKE_PDF_BYTES), "cv.pdf")},
        headers=auth_headers(youth["access_token"]),
        content_type="multipart/form-data",
    )
    with app_module.app.app_context():
        application = app_module.Application.query.filter_by(user_id=youth["user"]["id"]).first()
        app_id = application.id

    return job_id, app_id, youth


def _issue_credential(client, token):
    resp = client.post(
        "/issue_credential",
        data={"title": "Welding Cert", "issuer": "Bo Technical Institute", "file": (io.BytesIO(FAKE_PDF_BYTES), "c.pdf")},
        headers=auth_headers(token),
        content_type="multipart/form-data",
    )
    assert resp.status_code == 201
    return resp.get_json()["credential_id"]


def test_erase_requires_the_correct_password(client):
    user = register_user(client)
    resp = client.post(
        "/api/account/erase",
        json={"password": "wrong-password"},
        headers=auth_headers(user["access_token"]),
    )
    assert resp.status_code == 403
    with app_module.app.app_context():
        row = app_module.User.query.get(user["user"]["id"])
        assert row.erased_at is None
        assert row.name != "Deleted user"


def test_erase_scrubs_pii_deletes_credential_and_its_file(client):
    user = register_user(client, email="erase-me@test.com", phone="444")
    cred_id = _issue_credential(client, user["access_token"])

    with app_module.app.app_context():
        cred = app_module.Credential.query.get(cred_id)
        file_path = os.path.join(app_module.UPLOAD_FOLDER, cred.file_path)
    assert os.path.exists(file_path)

    resp = client.post(
        "/api/account/erase",
        json={"password": "password123"},
        headers=auth_headers(user["access_token"]),
    )
    assert resp.status_code == 200
    assert resp.get_json()["success"] is True

    with app_module.app.app_context():
        row = app_module.User.query.get(user["user"]["id"])
        assert row.erased_at is not None
        assert row.active is False
        assert row.name == "Deleted user"
        assert row.email != "erase-me@test.com"
        assert row.phone != "444"
        assert row.ncra_id is None
        assert app_module.Credential.query.get(cred_id) is None

    assert not os.path.exists(file_path)

    # Same judgment-free "not_found" state as a hash that was never
    # registered -- not a "revoked" status, which would wrongly imply fraud.
    verify_page = client.get(f"/verify/{cred_id}")
    body = verify_page.get_data(as_text=True)
    assert "not" in body.lower() and "found" in body.lower()


def test_erased_user_cannot_log_in_again(client):
    user = register_user(client, email="gone@test.com", phone="555")
    client.post(
        "/api/account/erase",
        json={"password": "password123"},
        headers=auth_headers(user["access_token"]),
    )
    resp = client.post("/login", json={"email": "gone@test.com", "password": "password123"})
    assert resp.status_code in (401, 403)


def test_erase_is_blocked_while_an_open_appeal_exists(client):
    user = register_user(client, email="disputed@test.com", phone="666")
    with app_module.app.app_context():
        appeal = app_module.UserAppeal(user_id=user["user"]["id"], message="Please reinstate me", status="open")
        app_module.db.session.add(appeal)
        app_module.db.session.commit()

    resp = client.post(
        "/api/account/erase",
        json={"password": "password123"},
        headers=auth_headers(user["access_token"]),
    )
    assert resp.status_code == 409
    assert "open" in resp.get_json()["error"].lower()

    with app_module.app.app_context():
        row = app_module.User.query.get(user["user"]["id"])
        assert row.erased_at is None


def test_erase_redacts_application_files_but_keeps_the_row_for_the_employer(client):
    job_id, app_id, youth = _setup_application(client, email="worker@test.com", phone="777")

    with app_module.app.app_context():
        application = app_module.Application.query.get(app_id)
        cv_filename = application.cv_file
        cv_path = os.path.join(app_module.APPLICATION_FOLDER, cv_filename)
    assert os.path.exists(cv_path)

    resp = client.post(
        "/api/account/erase",
        json={"password": "password123"},
        headers=auth_headers(youth["access_token"]),
    )
    assert resp.status_code == 200

    with app_module.app.app_context():
        # The row survives -- job_id/status are the employer's own hiring
        # record -- only the uploaded file and its reference are gone.
        application = app_module.Application.query.get(app_id)
        assert application is not None
        assert application.job_id == job_id
        assert application.cv_file is None
    assert not os.path.exists(cv_path)


def test_erase_redacts_own_messages_but_not_the_employers(client):
    job_id, app_id, youth = _setup_application(client, email="chatty@test.com", phone="888")

    client.post(
        f"/api/application/{app_id}/messages",
        json={"body": "When can I start?"},
        headers=auth_headers(youth["access_token"]),
    )

    login_page = client.get("/employer/login")
    token = _csrf_token(login_page.get_data(as_text=True))
    client.post(
        "/employer/login",
        data={"csrf_token": token, "email": "acme@test.com", "password": "password123"},
    )
    page = client.get(f"/employer/applications/{job_id}/messages/{app_id}")
    reply_token = _csrf_token(page.get_data(as_text=True))
    client.post(
        f"/employer/applications/{job_id}/messages/{app_id}",
        data={"csrf_token": reply_token, "body": "Next Monday works!"},
    )
    client.post("/employer/logout", data={"csrf_token": reply_token})

    resp = client.post(
        "/api/account/erase",
        json={"password": "password123"},
        headers=auth_headers(youth["access_token"]),
    )
    assert resp.status_code == 200

    with app_module.app.app_context():
        messages = app_module.Message.query.filter_by(application_id=app_id).order_by(app_module.Message.id).all()
        bodies_by_sender = {m.sender_type: m.body for m in messages}
        assert bodies_by_sender["user"] == "[deleted by user]"
        assert bodies_by_sender["employer"] == "Next Monday works!"


def test_admin_can_erase_a_user(client):
    from test_credential_revocation import _create_admin, _login_admin

    user = register_user(client, email="admin-erase-me@test.com", phone="999")
    admin_email, admin_password = _create_admin("admin-erasure@youthchain.test")
    _login_admin(client, admin_email, admin_password)

    users_page = client.get("/admin/users")
    csrf_token = _csrf_token(users_page.get_data(as_text=True))
    resp = client.post(f"/admin/users/{user['user']['id']}/erase", data={"csrf_token": csrf_token})
    assert resp.status_code == 200
    assert resp.get_json()["success"] is True

    with app_module.app.app_context():
        row = app_module.User.query.get(user["user"]["id"])
        assert row.erased_at is not None


def test_employer_applicant_list_and_review_pages_render_fine_after_erasure(client):
    """
    Live-rendering check, not just DB-state assertions: the applicant list
    (employer_applications) and the per-application review/messages pages
    both display application.user's name/phone/cv_file -- confirms none of
    that crashes on a tombstoned name or a None cv_file after erasure,
    since those are exactly the values _erase_user_data() changes.
    """
    job_id, app_id, youth = _setup_application(client, email="renders-ok@test.com", phone="222")
    client.post(
        "/api/account/erase",
        json={"password": "password123"},
        headers=auth_headers(youth["access_token"]),
    )

    login_page = client.get("/employer/login")
    token = _csrf_token(login_page.get_data(as_text=True))
    client.post(
        "/employer/login",
        data={"csrf_token": token, "email": "acme@test.com", "password": "password123"},
    )

    applicants_page = client.get(f"/employer/applications/{job_id}")
    assert applicants_page.status_code == 200
    assert "Deleted user" in applicants_page.get_data(as_text=True)

    messages_page = client.get(f"/employer/applications/{job_id}/messages/{app_id}")
    assert messages_page.status_code == 200
