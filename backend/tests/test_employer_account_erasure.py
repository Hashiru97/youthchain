"""
Employer-side counterpart to test_account_erasure.py (_erase_employer_data
in app.py) -- see that file's own module docstring for the full design
reasoning, which applies identically here. Covers: password re-
confirmation, real deletion of the uploaded verification document from
disk, redaction (not deletion) of this employer's own sent messages while
the counterparty's messages survive untouched, the open-dispute hold, and
that the applicant-facing page a worker would see still renders after the
employer that posted the job is erased.
"""
import io
import os
import re

import app as app_module
from conftest import register_user, auth_headers, FAKE_PDF_BYTES
from test_employer import register_employer, _csrf_token


def _upload_verification_document(client):
    page = client.get("/employer/verification")
    token = _csrf_token(page.get_data(as_text=True))
    resp = client.post(
        "/employer/verification",
        data={
            "csrf_token": token,
            "verification_type": "business",
            "document": (io.BytesIO(FAKE_PDF_BYTES), "reg-cert.pdf"),
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code in (200, 302)
    with app_module.app.app_context():
        employer = app_module.Employer.query.filter_by(email="acme@test.com").first()
        return employer.id, employer.verification_document


def test_employer_erase_requires_the_correct_password(client):
    register_employer(client)
    page = client.get("/employer/verification")
    token = _csrf_token(page.get_data(as_text=True))
    resp = client.post(
        "/api/employer/account/erase",
        data={"csrf_token": token, "password": "wrong-password"},
    )
    assert resp.status_code == 403
    with app_module.app.app_context():
        employer = app_module.Employer.query.filter_by(email="acme@test.com").first()
        assert employer.erased_at is None


def test_employer_erase_is_rate_limited_after_five_bad_password_attempts(client):
    """
    Same gap class as test_employer_login_rate_limited_after_five_bad_password_attempts
    in test_employer.py: /api/employer/account/erase checks a password
    against an existing session (see erase_own_employer_account()'s own
    docstring), so without a throttle here too, that password check is an
    unbounded guessing oracle -- exactly what employer/user/admin login
    already had to be fixed against.
    """
    register_employer(client, email="erase-bruteforce@test.com")
    page = client.get("/employer/verification")
    token = _csrf_token(page.get_data(as_text=True))

    for _ in range(5):
        resp = client.post(
            "/api/employer/account/erase",
            data={"csrf_token": token, "password": "wrong-password"},
        )
        assert resp.status_code == 403
    limited = client.post(
        "/api/employer/account/erase",
        data={"csrf_token": token, "password": "wrong-password"},
    )
    assert limited.status_code == 429

    with app_module.app.app_context():
        employer = app_module.Employer.query.filter_by(email="erase-bruteforce@test.com").first()
        assert employer.erased_at is None


def test_employer_erase_scrubs_pii_and_deletes_verification_document(client):
    register_employer(client)
    employer_id, doc_filename = _upload_verification_document(client)
    doc_path = os.path.join(app_module.EMPLOYER_VERIFICATION_FOLDER, doc_filename)
    assert os.path.exists(doc_path)

    page = client.get("/employer/verification")
    token = _csrf_token(page.get_data(as_text=True))
    resp = client.post(
        "/api/employer/account/erase",
        data={"csrf_token": token, "password": "password123"},
    )
    assert resp.status_code == 200
    assert resp.get_json()["success"] is True

    with app_module.app.app_context():
        employer = app_module.db.session.get(app_module.Employer, employer_id)
        assert employer.erased_at is not None
        assert employer.active is False
        assert employer.name == "Deleted employer"
        assert employer.email != "acme@test.com"
        assert employer.verification_document is None
    assert not os.path.exists(doc_path)


def test_employer_erase_is_blocked_while_an_open_report_exists(client):
    register_employer(client)
    with app_module.app.app_context():
        employer = app_module.Employer.query.filter_by(email="acme@test.com").first()
        reporter = app_module.User(
            name="Reporter", phone="123456", email="reporter@test.com",
            password_hash=app_module.generate_password_hash("password123"),
        )
        app_module.db.session.add(reporter)
        app_module.db.session.flush()
        report = app_module.EmployerReport(
            reporter_user_id=reporter.id, employer_id=employer.id,
            category="scam", status="open",
        )
        app_module.db.session.add(report)
        app_module.db.session.commit()
        employer_id = employer.id

    page = client.get("/employer/verification")
    token = _csrf_token(page.get_data(as_text=True))
    resp = client.post(
        "/api/employer/account/erase",
        data={"csrf_token": token, "password": "password123"},
    )
    assert resp.status_code == 409
    assert "open" in resp.get_json()["error"].lower()

    with app_module.app.app_context():
        employer = app_module.db.session.get(app_module.Employer, employer_id)
        assert employer.erased_at is None


def test_employer_erase_redacts_own_messages_but_not_the_workers(client):
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

    worker = register_user(client, email="worker@test.com", phone="321")
    client.post(
        "/apply",
        data={"job_id": str(job_id), "cv": (io.BytesIO(FAKE_PDF_BYTES), "cv.pdf")},
        headers=auth_headers(worker["access_token"]),
        content_type="multipart/form-data",
    )
    with app_module.app.app_context():
        app_id = app_module.Application.query.filter_by(user_id=worker["user"]["id"]).first().id

    client.post(
        f"/api/application/{app_id}/messages",
        json={"body": "When can I start?"},
        headers=auth_headers(worker["access_token"]),
    )

    login_page = client.get("/employer/login")
    login_token = _csrf_token(login_page.get_data(as_text=True))
    client.post(
        "/employer/login",
        data={"csrf_token": login_token, "email": "acme@test.com", "password": "password123"},
    )
    reply_page = client.get(f"/employer/applications/{job_id}/messages/{app_id}")
    reply_token = _csrf_token(reply_page.get_data(as_text=True))
    client.post(
        f"/employer/applications/{job_id}/messages/{app_id}",
        data={"csrf_token": reply_token, "body": "Next Monday works!"},
    )

    erase_page = client.get("/employer/verification")
    erase_token = _csrf_token(erase_page.get_data(as_text=True))
    resp = client.post(
        "/api/employer/account/erase",
        data={"csrf_token": erase_token, "password": "password123"},
    )
    assert resp.status_code == 200

    with app_module.app.app_context():
        messages = app_module.Message.query.filter_by(application_id=app_id).order_by(app_module.Message.id).all()
        bodies_by_sender = {m.sender_type: m.body for m in messages}
        assert bodies_by_sender["employer"] == "[deleted by employer]"
        assert bodies_by_sender["user"] == "When can I start?"

    # The job (and the worker's own application history against it)
    # survives -- the worker's own applications list must still render.
    my_apps = client.get(f"/my_applications/{worker['user']['id']}", headers=auth_headers(worker["access_token"]))
    assert my_apps.status_code == 200
    body_text = my_apps.get_data(as_text=True)
    assert "Deleted employer" in body_text or "Welder" in body_text


def test_admin_can_erase_an_employer(client):
    from test_credential_revocation import _create_admin, _login_admin

    register_employer(client, email="admin-erase-employer@test.com")
    logout_page = client.get("/employer/verification")
    client.post("/employer/logout", data={"csrf_token": _csrf_token(logout_page.get_data(as_text=True))})

    admin_email, admin_password = _create_admin("admin-emp-erasure@youthchain.test")
    _login_admin(client, admin_email, admin_password)

    with app_module.app.app_context():
        employer_id = app_module.Employer.query.filter_by(email="admin-erase-employer@test.com").first().id

    verif_page = client.get("/admin/employer_verifications")
    token = _csrf_token(verif_page.get_data(as_text=True))
    resp = client.post(f"/admin/employers/{employer_id}/erase", data={"csrf_token": token})
    assert resp.status_code == 200
    assert resp.get_json()["success"] is True

    with app_module.app.app_context():
        employer = app_module.db.session.get(app_module.Employer, employer_id)
        assert employer.erased_at is not None
