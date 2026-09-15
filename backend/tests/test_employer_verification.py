"""
Regression coverage for the employer verification workflow (Phase 3 #5
follow-up): employer uploads a business document, an admin approves or
rejects it, and document access stays ownership-scoped. No automated
approval anywhere — this only tests the human-review plumbing.
"""
import io
import re

from conftest import FAKE_PDF_BYTES


def _csrf_token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "csrf token not found in page"
    return match.group(1)


def register_employer(client, email="acme@test.com", name="Acme", password="password123", account_type=None):
    page = client.get("/employer/register")
    token = _csrf_token(page.get_data(as_text=True))
    data = {"csrf_token": token, "name": name, "email": email, "password": password}
    if account_type is not None:
        data["account_type"] = account_type
    return client.post("/employer/register", data=data)


def _create_admin(email, password="adminpass123", role="admin"):
    import app as app_module

    with app_module.app.app_context():
        admin = app_module.Admin(
            name="Ops",
            email=email,
            role=role,
            password_hash=app_module.generate_password_hash(password),
        )
        app_module.db.session.add(admin)
        app_module.db.session.commit()
        admin_id = admin.id
    return admin_id


def login_admin(client, email, password):
    login_page = client.get("/admin/login")
    token = _csrf_token(login_page.get_data(as_text=True))
    return client.post(
        "/admin/login",
        data={"csrf_token": token, "email": email, "password": password},
    )


def logout(client, path, logout_path):
    page = client.get(path)
    client.post(logout_path, data={"csrf_token": _csrf_token(page.get_data(as_text=True))})


def test_new_employer_defaults_to_unverified(client):
    register_employer(client)
    resp = client.get("/employer/verification")
    assert resp.status_code == 200
    assert "Unverified" in resp.get_data(as_text=True)


# ---- Employer.account_type (self-declared intent at registration) ----

def test_registration_defaults_account_type_to_business_when_omitted(client):
    """Every existing client that doesn't send this field yet (matching
    every pre-existing registration flow) must keep defaulting to today's
    only behavior, unchanged."""
    register_employer(client)
    import app as app_module

    with app_module.app.app_context():
        employer = app_module.Employer.query.filter_by(email="acme@test.com").first()
        assert employer.account_type == "business"


def test_registration_captures_account_type_individual(client):
    register_employer(client, account_type="individual")
    import app as app_module

    with app_module.app.app_context():
        employer = app_module.Employer.query.filter_by(email="acme@test.com").first()
        assert employer.account_type == "individual"


def test_verification_form_defaults_radio_from_account_type(client):
    register_employer(client, account_type="individual")
    resp = client.get("/employer/verification")
    body = resp.get_data(as_text=True)
    individual_input = body[body.index('id="verification_type_individual"'):]
    business_input = body[body.index('id="verification_type_business"'):]
    assert "checked" in individual_input.split(">")[0]
    assert "checked" not in business_input.split(">")[0]


def test_post_job_form_defaults_radio_from_account_type(client):
    register_employer(client, account_type="individual")
    resp = client.get("/employer/post")
    body = resp.get_data(as_text=True)
    gig_input = body[body.index('id="job_type_gig"'):]
    formal_input = body[body.index('id="job_type_formal"'):]
    assert "checked" in gig_input.split(">")[0]
    assert "checked" not in formal_input.split(">")[0]


def test_admin_employer_list_shows_signed_up_as(client):
    register_employer(client, account_type="individual")
    logout(client, "/employer/verification", "/employer/logout")
    _create_admin("admin4@youthchain.test")
    login_admin(client, "admin4@youthchain.test", "adminpass123")

    resp = client.get("/admin/employer_verifications")
    body = resp.get_data(as_text=True)
    idx = body.index("All employers")
    assert "Individual" in body[idx:]


def test_employer_can_upload_document_and_status_becomes_pending(client):
    register_employer(client)
    page = client.get("/employer/verification")
    token = _csrf_token(page.get_data(as_text=True))

    resp = client.post(
        "/employer/verification",
        data={
            "csrf_token": token,
            "document": (io.BytesIO(FAKE_PDF_BYTES), "registration.pdf"),
        },
        content_type="multipart/form-data",
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "Pending" in resp.get_data(as_text=True)

    import app as app_module

    with app_module.app.app_context():
        employer = app_module.Employer.query.filter_by(email="acme@test.com").first()
        assert employer.verification_status == "pending"
        assert employer.verification_document is not None


def test_upload_rejects_disallowed_file_type(client):
    register_employer(client)
    page = client.get("/employer/verification")
    token = _csrf_token(page.get_data(as_text=True))

    resp = client.post(
        "/employer/verification",
        data={
            "csrf_token": token,
            "document": (io.BytesIO(b"#!/bin/sh\necho pwn"), "evil.sh"),
        },
        content_type="multipart/form-data",
    )
    assert resp.status_code == 200
    assert "Unsupported file type" in resp.get_data(as_text=True)


def test_admin_sees_pending_employer_in_queue_and_can_approve(client):
    register_employer(client)
    page = client.get("/employer/verification")
    token = _csrf_token(page.get_data(as_text=True))
    client.post(
        "/employer/verification",
        data={"csrf_token": token, "document": (io.BytesIO(FAKE_PDF_BYTES), "reg.pdf")},
        content_type="multipart/form-data",
    )
    logout(client, "/employer/verification", "/employer/logout")

    _create_admin("admin@youthchain.test")
    login_admin(client, "admin@youthchain.test", "adminpass123")

    queue = client.get("/admin/employer_verifications")
    assert queue.status_code == 200
    assert "Acme" in queue.get_data(as_text=True)

    import app as app_module

    with app_module.app.app_context():
        employer = app_module.Employer.query.filter_by(email="acme@test.com").first()
        employer_id = employer.id

    decide_page_html = queue.get_data(as_text=True)
    approve_token = _csrf_token(decide_page_html)
    resp = client.post(
        f"/admin/employer_verifications/{employer_id}/decide",
        data={"csrf_token": approve_token, "decision": "verified"},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app_module.app.app_context():
        employer = app_module.db.session.get(app_module.Employer, employer_id)
        assert employer.verification_status == "verified"
        assert employer.verification_reviewed_at is not None
        assert employer.verification_reviewed_by_admin_id is not None


def test_admin_can_reject_employer_document(client):
    register_employer(client)
    page = client.get("/employer/verification")
    token = _csrf_token(page.get_data(as_text=True))
    client.post(
        "/employer/verification",
        data={"csrf_token": token, "document": (io.BytesIO(FAKE_PDF_BYTES), "reg.pdf")},
        content_type="multipart/form-data",
    )
    logout(client, "/employer/verification", "/employer/logout")

    _create_admin("admin2@youthchain.test")
    login_admin(client, "admin2@youthchain.test", "adminpass123")

    import app as app_module

    with app_module.app.app_context():
        employer = app_module.Employer.query.filter_by(email="acme@test.com").first()
        employer_id = employer.id

    queue = client.get("/admin/employer_verifications")
    reject_token = _csrf_token(queue.get_data(as_text=True))
    resp = client.post(
        f"/admin/employer_verifications/{employer_id}/decide",
        data={"csrf_token": reject_token, "decision": "rejected"},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app_module.app.app_context():
        employer = app_module.db.session.get(app_module.Employer, employer_id)
        assert employer.verification_status == "rejected"


def test_verification_type_defaults_to_business_when_omitted(client):
    """Real gap this closes: an individual hiring informally (no business
    to register) previously had no path to verification at all -- see
    Employer.verification_type's docstring. Every existing caller that
    doesn't send this field (matching every pre-existing client) must
    keep defaulting to today's only track, unchanged."""
    register_employer(client)
    page = client.get("/employer/verification")
    token = _csrf_token(page.get_data(as_text=True))
    client.post(
        "/employer/verification",
        data={"csrf_token": token, "document": (io.BytesIO(FAKE_PDF_BYTES), "reg.pdf")},
        content_type="multipart/form-data",
    )
    import app as app_module

    with app_module.app.app_context():
        employer = app_module.Employer.query.filter_by(email="acme@test.com").first()
        assert employer.verification_type == "business"


def test_employer_can_submit_as_individual_and_gets_a_distinct_verified_badge(client):
    register_employer(client)
    page = client.get("/employer/verification")
    token = _csrf_token(page.get_data(as_text=True))
    client.post(
        "/employer/verification",
        data={"csrf_token": token, "verification_type": "individual", "document": (io.BytesIO(FAKE_PDF_BYTES), "national_id.pdf")},
        content_type="multipart/form-data",
    )

    import app as app_module

    with app_module.app.app_context():
        employer = app_module.Employer.query.filter_by(email="acme@test.com").first()
        assert employer.verification_type == "individual"
        employer_id = employer.id

    logout(client, "/employer/verification", "/employer/logout")
    _create_admin("admin3@youthchain.test")
    login_admin(client, "admin3@youthchain.test", "adminpass123")

    # The pending queue shows which track this submission is on, not just
    # that something is pending -- an admin needs to know whether to
    # expect a business document or a personal ID.
    queue = client.get("/admin/employer_verifications")
    assert "Individual" in queue.get_data(as_text=True)

    approve_token = _csrf_token(queue.get_data(as_text=True))
    client.post(
        f"/admin/employer_verifications/{employer_id}/decide",
        data={"csrf_token": approve_token, "decision": "verified"},
    )
    logout(client, "/admin/employer_verifications", "/admin/logout")

    login_page = client.get("/employer/login")
    token = _csrf_token(login_page.get_data(as_text=True))
    client.post("/employer/login", data={"csrf_token": token, "email": "acme@test.com", "password": "password123"})
    resp = client.get("/employer/verification")
    body = resp.get_data(as_text=True)
    assert "Verified Individual" in body
    assert "Verified Business" not in body


def test_verifier_role_admin_cannot_reach_decide_route(client):
    register_employer(client)
    page = client.get("/employer/verification")
    token = _csrf_token(page.get_data(as_text=True))
    client.post(
        "/employer/verification",
        data={"csrf_token": token, "document": (io.BytesIO(FAKE_PDF_BYTES), "reg.pdf")},
        content_type="multipart/form-data",
    )

    import app as app_module

    with app_module.app.app_context():
        employer = app_module.Employer.query.filter_by(email="acme@test.com").first()
        employer_id = employer.id

    logout(client, "/employer/verification", "/employer/logout")

    _create_admin("verifier@youthchain.test", role="verifier")
    login_admin(client, "verifier@youthchain.test", "adminpass123")

    resp = client.get("/admin/employer_verifications")
    assert resp.status_code == 403

    resp = client.post(
        f"/admin/employer_verifications/{employer_id}/decide",
        data={"csrf_token": "irrelevant", "decision": "verified"},
    )
    assert resp.status_code == 403


def test_document_download_is_ownership_scoped(client):
    register_employer(client, email="owner@test.com", name="Owner Co")
    page = client.get("/employer/verification")
    token = _csrf_token(page.get_data(as_text=True))
    client.post(
        "/employer/verification",
        data={"csrf_token": token, "document": (io.BytesIO(FAKE_PDF_BYTES), "reg.pdf")},
        content_type="multipart/form-data",
    )

    import app as app_module

    with app_module.app.app_context():
        owner = app_module.Employer.query.filter_by(email="owner@test.com").first()
        filename = owner.verification_document
    assert filename is not None

    # owning employer can download
    resp = client.get(f"/employer_verification_document/{filename}")
    assert resp.status_code == 200

    logout(client, "/employer/verification", "/employer/logout")

    # a second, unrelated employer cannot
    register_employer(client, email="stranger@test.com", name="Stranger Co")
    resp = client.get(f"/employer_verification_document/{filename}")
    assert resp.status_code == 403

    logout(client, "/employer/verification", "/employer/logout")

    # but a logged-in admin can
    _create_admin("admin3@youthchain.test")
    login_admin(client, "admin3@youthchain.test", "adminpass123")
    resp = client.get(f"/employer_verification_document/{filename}")
    assert resp.status_code == 200


def test_unknown_filename_returns_403_not_500(client):
    register_employer(client)
    resp = client.get("/employer_verification_document/does-not-exist.pdf")
    assert resp.status_code == 403
