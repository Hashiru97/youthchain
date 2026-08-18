"""
Regression coverage for BL-39: employer<->applicant messaging, scoped to a
specific Application, reachable from both the mobile JWT path and the
employer session-cookie path.
"""
import io
import re

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


def _setup_application(client):
    """Employer posts a job, youth applies to it; returns (job_id, app_id, youth_token, youth_id)."""
    register_employer(client)
    page = client.get("/employer/post")
    token = _csrf_token(page.get_data(as_text=True))
    client.post(
        "/employer/post",
        data={"csrf_token": token, "title": "Welder", "location": "Bo", "duration": "6mo"},
    )

    import app as app_module

    with app_module.app.app_context():
        job = app_module.Job.query.filter_by(title="Welder").first()
        job_id = job.id

    # log out employer session before youth registers (avoid session bleed)
    logout_page = client.get("/employer/post")
    client.post("/employer/logout", data={"csrf_token": _csrf_token(logout_page.get_data(as_text=True))})

    youth = register_user(client, email="youth@test.com", phone="333")
    client.post(
        "/apply",
        data={
            "job_id": str(job_id),
            "cv": (io.BytesIO(FAKE_PDF_BYTES), "cv.pdf"),
        },
        headers=auth_headers(youth["access_token"]),
        content_type="multipart/form-data",
    )
    with app_module.app.app_context():
        application = app_module.Application.query.filter_by(user_id=youth["user"]["id"]).first()
        app_id = application.id

    return job_id, app_id, youth["access_token"], youth["user"]["id"]


def test_youth_can_send_and_read_messages_via_json_api(client):
    _job_id, app_id, token, _uid = _setup_application(client)

    send = client.post(
        f"/api/application/{app_id}/messages",
        json={"body": "Hi, I'm interested in this role!"},
        headers=auth_headers(token),
    )
    assert send.status_code == 201
    assert send.get_json()["message"]["sender_type"] == "user"

    fetch = client.get(f"/api/application/{app_id}/messages", headers=auth_headers(token))
    assert fetch.status_code == 200
    bodies = [m["body"] for m in fetch.get_json()["messages"]]
    assert "Hi, I'm interested in this role!" in bodies


def test_stranger_cannot_read_or_send_messages(client):
    _job_id, app_id, _token, _uid = _setup_application(client)

    stranger = register_user(client, email="stranger@test.com", phone="444")
    resp = client.get(f"/api/application/{app_id}/messages", headers=auth_headers(stranger["access_token"]))
    assert resp.status_code == 403

    resp2 = client.post(
        f"/api/application/{app_id}/messages",
        json={"body": "snooping"},
        headers=auth_headers(stranger["access_token"]),
    )
    assert resp2.status_code == 403


def test_unauthenticated_request_is_rejected(client):
    _job_id, app_id, _token, _uid = _setup_application(client)
    resp = client.get(f"/api/application/{app_id}/messages")
    assert resp.status_code == 403  # application exists but caller has no identity at all


def test_employer_can_message_via_web_portal(client):
    job_id, app_id, youth_token, _uid = _setup_application(client)

    # youth sends first (still logged in via the JWT from setup, no session needed)
    client.post(
        f"/api/application/{app_id}/messages",
        json={"body": "When can I start?"},
        headers=auth_headers(youth_token),
    )

    # log back in as the employer
    login_page = client.get("/employer/login")
    token = _csrf_token(login_page.get_data(as_text=True))
    client.post(
        "/employer/login",
        data={"csrf_token": token, "email": "acme@test.com", "password": "password123"},
    )

    page = client.get(f"/employer/applications/{job_id}/messages/{app_id}")
    assert page.status_code == 200
    assert "When can I start?" in page.get_data(as_text=True)

    reply_token = _csrf_token(page.get_data(as_text=True))
    reply = client.post(
        f"/employer/applications/{job_id}/messages/{app_id}",
        data={"csrf_token": reply_token, "body": "Next Monday works!"},
        follow_redirects=True,
    )
    assert reply.status_code == 200
    assert "Next Monday works!" in reply.get_data(as_text=True)


def test_fetching_thread_marks_the_other_partys_messages_read(client):
    job_id, app_id, youth_token, _uid = _setup_application(client)

    send = client.post(
        f"/api/application/{app_id}/messages",
        json={"body": "When can I start?"},
        headers=auth_headers(youth_token),
    )
    assert send.get_json()["message"]["read"] is False

    # Confirmed unread before the employer has ever viewed the thread —
    # checked BEFORE logging the employer session in on this same test
    # client, since the dual JWT+session-cookie auth model means a
    # subsequent JWT-bearing request would otherwise also carry the
    # employer session cookie, changing which party _mark_messages_read
    # treats the viewer as.
    fetch_before = client.get(f"/api/application/{app_id}/messages", headers=auth_headers(youth_token))
    assert fetch_before.get_json()["messages"][0]["read"] is False

    login_page = client.get("/employer/login")
    token = _csrf_token(login_page.get_data(as_text=True))
    client.post(
        "/employer/login",
        data={"csrf_token": token, "email": "acme@test.com", "password": "password123"},
    )

    # Employer views the web portal thread — this should mark the youth's
    # message read.
    client.get(f"/employer/applications/{job_id}/messages/{app_id}")

    import app as app_module
    with app_module.app.app_context():
        msg = app_module.Message.query.filter_by(application_id=app_id).first()
        assert msg.read is True
        assert msg.read_at is not None


def test_own_messages_are_never_marked_read_by_own_fetch(client):
    """A read receipt means "the other party saw it" — re-fetching your
    own sent message shouldn't flip it to read against yourself."""
    _job_id, app_id, youth_token, _uid = _setup_application(client)

    client.post(
        f"/api/application/{app_id}/messages",
        json={"body": "Hello?"},
        headers=auth_headers(youth_token),
    )
    # Youth fetches their own thread again — their own message stays
    # unread (only the employer viewing it should mark it read).
    fetch = client.get(f"/api/application/{app_id}/messages", headers=auth_headers(youth_token))
    assert fetch.get_json()["messages"][0]["read"] is False


def test_send_message_with_file_attachment_via_json_api(client):
    _job_id, app_id, token, _uid = _setup_application(client)

    resp = client.post(
        f"/api/application/{app_id}/messages",
        data={
            "body": "Here's my portfolio",
            "attachment": (io.BytesIO(FAKE_PDF_BYTES), "portfolio.pdf"),
        },
        headers=auth_headers(token),
        content_type="multipart/form-data",
    )
    assert resp.status_code == 201
    msg = resp.get_json()["message"]
    assert msg["attachment_file"] is not None
    assert msg["attachment_file"].endswith("portfolio.pdf")


def test_message_attachment_download_is_ownership_scoped(client):
    _job_id, app_id, token, _uid = _setup_application(client)

    send = client.post(
        f"/api/application/{app_id}/messages",
        data={"body": "doc attached", "attachment": (io.BytesIO(FAKE_PDF_BYTES), "doc.pdf")},
        headers=auth_headers(token),
        content_type="multipart/form-data",
    )
    filename = send.get_json()["message"]["attachment_file"]

    # Owner (the sender) can download it.
    owner_resp = client.get(f"/message_attachment/{filename}", headers=auth_headers(token))
    assert owner_resp.status_code == 200

    # A stranger cannot.
    stranger = register_user(client, email="attach-stranger@test.com", phone="555")
    stranger_resp = client.get(
        f"/message_attachment/{filename}", headers=auth_headers(stranger["access_token"])
    )
    assert stranger_resp.status_code == 403


def test_message_with_only_attachment_no_body_is_allowed(client):
    _job_id, app_id, token, _uid = _setup_application(client)

    resp = client.post(
        f"/api/application/{app_id}/messages",
        data={"body": "", "attachment": (io.BytesIO(FAKE_PDF_BYTES), "doc.pdf")},
        headers=auth_headers(token),
        content_type="multipart/form-data",
    )
    assert resp.status_code == 201


def test_message_body_required_and_length_capped(client):
    _job_id, app_id, token, _uid = _setup_application(client)

    empty = client.post(
        f"/api/application/{app_id}/messages", json={"body": ""}, headers=auth_headers(token)
    )
    assert empty.status_code == 400

    too_long = client.post(
        f"/api/application/{app_id}/messages",
        json={"body": "x" * 2001},
        headers=auth_headers(token),
    )
    assert too_long.status_code == 400


def test_messages_response_includes_the_owning_employers_name_and_verification_status(client):
    """The mobile Messages screen needs to show the applicant who they're
    talking to (and whether that employer is verified) — a job created via
    the employer web portal has a real owning Employer row, so the
    response's "employer" key should surface its name + verification_status."""
    _job_id, app_id, token, _uid = _setup_application(client)

    fetch = client.get(f"/api/application/{app_id}/messages", headers=auth_headers(token))
    assert fetch.status_code == 200
    body = fetch.get_json()
    assert body["employer"] == {
        "name": "Acme", "verification_status": "unverified", "verification_type": "business",
        "industry": None, "avg_rating": None, "rating_count": 0,
    }


def test_messages_response_includes_raw_employer_id_for_reporting(client):
    """The mobile Messages screen originally couldn't build a "report this
    employer" action because the response only ever exposed the safe,
    trimmed employer summary (name/verification_status/industry), never a
    raw id -- POST /api/report_employer requires one. This is that gap,
    closed the same way Job.to_dict() already exposes it."""
    import app as app_module

    _job_id, app_id, token, _uid = _setup_application(client)

    fetch = client.get(f"/api/application/{app_id}/messages", headers=auth_headers(token))
    assert fetch.status_code == 200
    body = fetch.get_json()
    with app_module.app.app_context():
        job = app_module.Job.query.get(_job_id)
        assert body["employer_id"] == job.employer_id
        assert body["employer_id"] is not None


def test_messages_response_employer_is_null_for_jobs_with_no_owning_employer(client):
    """Jobs seeded via seed_jobs.py (or any pre-employer-accounts legacy
    job) have employer_id = None — this is a real, common case, not an
    edge case to skip. The response must still succeed and report
    employer: null rather than 500ing or guessing an employer."""
    import io

    import app as app_module
    from conftest import FAKE_PDF_BYTES

    with app_module.app.app_context():
        job = app_module.Job(title="Seeded Role", location="Freetown", duration="3mo", employer_id=None)
        app_module.db.session.add(job)
        app_module.db.session.commit()
        job_id = job.id

    youth = register_user(client, email="seeded-youth@test.com", phone="666")
    client.post(
        "/apply",
        data={"job_id": str(job_id), "cv": (io.BytesIO(FAKE_PDF_BYTES), "cv.pdf")},
        headers=auth_headers(youth["access_token"]),
        content_type="multipart/form-data",
    )
    with app_module.app.app_context():
        application = app_module.Application.query.filter_by(user_id=youth["user"]["id"]).first()
        app_id = application.id

    fetch = client.get(f"/api/application/{app_id}/messages", headers=auth_headers(youth["access_token"]))
    assert fetch.status_code == 200
    assert fetch.get_json()["employer"] is None
