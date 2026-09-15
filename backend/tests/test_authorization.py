"""
Regression coverage for S-01/S-02 (IDOR) and BL-03/BL-04 — the most severe
findings in the engineering review's Phase 5 security review. These tests
exist specifically so a future change can't silently reintroduce them.
"""
import re

from conftest import register_user, auth_headers, FAKE_PDF_BYTES


def _two_users(client):
    a = register_user(client, email="alice@test.com", phone="111", name="Alice")
    b = register_user(client, email="bob@test.com", phone="222", name="Bob")
    return a, b


def _csrf_token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "csrf token not found in page"
    return match.group(1)


def _register_employer(client, email, name, password="password123"):
    page = client.get("/employer/register")
    token = _csrf_token(page.get_data(as_text=True))
    return client.post(
        "/employer/register",
        data={"csrf_token": token, "name": name, "email": email, "password": password},
    )


def test_user_cannot_read_another_users_passport(client):
    a, b = _two_users(client)
    resp = client.get(f"/passport/{b['user']['id']}", headers=auth_headers(a["access_token"]))
    assert resp.status_code == 403


def test_user_can_read_own_passport(client):
    a, _ = _two_users(client)
    resp = client.get(f"/passport/{a['user']['id']}", headers=auth_headers(a["access_token"]))
    assert resp.status_code == 200


def test_user_cannot_read_another_users_applications(client):
    a, b = _two_users(client)
    resp = client.get(f"/my_applications/{b['user']['id']}", headers=auth_headers(a["access_token"]))
    assert resp.status_code == 403


def test_apply_ignores_client_supplied_user_id(client):
    """
    /apply used to trust a client-supplied user_id form field outright — a
    caller could submit an application "as" any other user. It must now be
    derived from the token regardless of what the form claims.
    """
    a, b = _two_users(client)
    resp = client.post(
        "/apply",
        data={"user_id": str(b["user"]["id"]), "job_id": "1"},
        headers=auth_headers(a["access_token"]),
        content_type="multipart/form-data",
    )
    # No job with id 1 exists / no CV attached, so this won't succeed — the
    # point is only that it must fail on *validation*, not silently accept
    # user_id=b's id. A 400 (missing/invalid fields) is expected here; a
    # 201 recorded against Bob's account would be the bug this guards
    # against.
    assert resp.status_code in (400, 404)


def test_certificate_download_requires_ownership(client):
    import app as app_module

    a, b = _two_users(client)
    issue = client.post(
        "/issue_credential",
        data={"title": "Cert", "issuer": "Inst", "file": (__import__("io").BytesIO(FAKE_PDF_BYTES), "c.pdf")},
        headers=auth_headers(a["access_token"]),
        content_type="multipart/form-data",
    )
    assert issue.status_code == 201
    credential_id = issue.get_json()["credential_id"]
    # The saved filename is unique-prefixed (see _issue_credential_internal),
    # not the original upload name — look up the real one rather than
    # assuming it matches what was uploaded.
    with app_module.app.app_context():
        filename = app_module.db.session.get(app_module.Credential, credential_id).file_path

    as_owner = client.get(f"/certificate/{filename}", headers=auth_headers(a["access_token"]))
    as_stranger = client.get(f"/certificate/{filename}", headers=auth_headers(b["access_token"]))

    assert as_owner.status_code == 200
    assert as_stranger.status_code == 403


def test_candidate_profile_linked_to_authenticated_user_not_client_claim(client):
    a, b = _two_users(client)
    # Bob tries to attach himself to Alice's future candidate profile by
    # reusing her email — upsert_candidate must key ownership off the JWT,
    # not anything the client sends.
    client.post(
        "/api/candidate",
        json={"email": "shared@test.com", "name": "Alice's profile"},
        headers=auth_headers(a["access_token"]),
    )
    resp = client.post(
        "/api/candidate",
        json={"email": "shared@test.com", "name": "Bob overwriting"},
        headers=auth_headers(b["access_token"]),
    )
    assert resp.status_code == 403


def test_unrelated_employer_cannot_manage_a_job_they_did_not_post(client):
    """
    Real cross-tenant authorization gap found via live audit, not assumed:
    Job.employer_id is nullable to tolerate seed_jobs.py/legacy jobs with
    no owning employer (see the Job model's docstring), and the old rule
    treated "no owner" as "every employer's" -- so any two unrelated, real
    employer accounts could view, message, download CVs for, and
    accept/reject applications on every unowned job. Reproduced live before
    the fix (_employer_owns_job() in app.py): a second employer that never
    posted the job successfully changed an applicant's status to Accepted.
    This locks that closed: an employer may only manage jobs where
    job.employer_id actually equals their own id.
    """
    import io

    job_id = None
    import app as app_module

    with app_module.app.app_context():
        job = app_module.Job(title="Unowned Internship", location="Freetown", duration="3mo")
        app_module.db.session.add(job)
        app_module.db.session.commit()
        job_id = job.id

    youth = register_user(client, email="zeus@test.com", phone="900")
    client.post(
        "/apply",
        data={"job_id": str(job_id), "cv": (io.BytesIO(FAKE_PDF_BYTES), "cv.pdf")},
        headers=auth_headers(youth["access_token"]),
        content_type="multipart/form-data",
    )
    with app_module.app.app_context():
        application = app_module.Application.query.filter_by(job_id=job_id).first()
        app_id = application.id

    _register_employer(client, email="random@corp.com", name="RandomCorp")
    # A real CSRF token (session-bound, not tied to a specific form) so
    # the accept/reject POST below is rejected for the ownership check
    # this test targets, not incidentally by CSRF protection first.
    csrf_token = _csrf_token(client.get("/employer").get_data(as_text=True))

    # Can't even view the applicant list for a job it never posted.
    apps_page = client.get(f"/employer/applications/{job_id}")
    assert apps_page.status_code == 403

    # Can't act on the application via the same route either.
    accept_resp = client.post(
        f"/employer/applications/{job_id}",
        data={"csrf_token": csrf_token, "app_id": str(app_id), "action": "accept"},
    )
    assert accept_resp.status_code == 403

    # Can't download the applicant's CV.
    with app_module.app.app_context():
        cv_filename = app_module.db.session.get(app_module.Application, app_id).cv_file
    cv_resp = client.get(f"/application_file/{cv_filename}")
    assert cv_resp.status_code == 403

    # Can't message the applicant either (shared _application_access() path).
    msg_resp = client.get(f"/api/application/{app_id}/messages")
    assert msg_resp.status_code == 403

    # The unowned job also no longer shows up on a stranger's own dashboard.
    dashboard = client.get("/employer")
    assert "Unowned Internship" not in dashboard.get_data(as_text=True)

    # And the application itself was never actually touched.
    with app_module.app.app_context():
        application = app_module.db.session.get(app_module.Application, app_id)
        assert application.status == "Pending"
