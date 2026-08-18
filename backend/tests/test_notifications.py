"""
Regression coverage for BL-38: in-app notifications, triggered by
application status changes and employer messages.
"""
import re

from conftest import register_user, auth_headers


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


def test_status_change_creates_a_notification(client):
    import app as app_module

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

    youth = register_user(client, email="youth@test.com", phone="555")
    import io

    from conftest import FAKE_PDF_BYTES

    client.post(
        "/apply",
        data={"job_id": str(job_id), "cv": (io.BytesIO(FAKE_PDF_BYTES), "cv.pdf")},
        headers=auth_headers(youth["access_token"]),
        content_type="multipart/form-data",
    )
    with app_module.app.app_context():
        application = app_module.Application.query.filter_by(user_id=youth["user"]["id"]).first()
        app_id = application.id

    # log back in as employer, accept the application
    login_page = client.get("/employer/login")
    login_token = _csrf_token(login_page.get_data(as_text=True))
    client.post(
        "/employer/login",
        data={"csrf_token": login_token, "email": "acme@test.com", "password": "password123"},
    )
    apps_page = client.get(f"/employer/applications/{job_id}")
    accept_token = _csrf_token(apps_page.get_data(as_text=True))
    client.post(
        f"/employer/applications/{job_id}",
        data={"csrf_token": accept_token, "app_id": str(app_id), "action": "accept"},
    )

    resp = client.get("/api/notifications", headers=auth_headers(youth["access_token"]))
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["unread_count"] >= 1
    types = [n["type"] for n in body["notifications"]]
    assert "application_status_changed" in types


def test_mark_notification_read(client):
    import app as app_module

    a = register_user(client)
    with app_module.app.app_context():
        app_module.notify_user(a["user"]["id"], "test_type", "Test", "body")
        notif = app_module.Notification.query.first()
        notif_id = notif.id

    resp = client.post(
        f"/api/notifications/{notif_id}/read", headers=auth_headers(a["access_token"])
    )
    assert resp.status_code == 200

    listing = client.get("/api/notifications", headers=auth_headers(a["access_token"])).get_json()
    assert listing["unread_count"] == 0


def test_notification_meta_round_trips_application_id(client):
    """notify_user() stores arbitrary kwargs (e.g. application_id) as a
    JSON-encoded string in Notification.meta — to_dict() must parse it back
    into a real dict so mobile clients can read it (e.g. to navigate to the
    right conversation when a "new_message" notification is tapped)."""
    import app as app_module

    a = register_user(client)
    with app_module.app.app_context():
        app_module.notify_user(
            a["user"]["id"], "new_message", "New message about your application", "hi", application_id=42
        )

    resp = client.get("/api/notifications", headers=auth_headers(a["access_token"]))
    assert resp.status_code == 200
    notif = resp.get_json()["notifications"][0]
    assert notif["meta"] == {"application_id": 42}


def test_notification_meta_defaults_to_empty_dict_when_absent(client):
    import app as app_module

    a = register_user(client)
    with app_module.app.app_context():
        app_module.notify_user(a["user"]["id"], "test_type", "Test", "body")

    resp = client.get("/api/notifications", headers=auth_headers(a["access_token"]))
    notif = resp.get_json()["notifications"][0]
    assert notif["meta"] == {}


def test_cannot_mark_another_users_notification_read(client):
    import app as app_module

    a = register_user(client, email="alice@test.com", phone="111")
    b = register_user(client, email="bob@test.com", phone="222")
    with app_module.app.app_context():
        app_module.notify_user(a["user"]["id"], "test_type", "Test", "body")
        notif_id = app_module.Notification.query.first().id

    resp = client.post(
        f"/api/notifications/{notif_id}/read", headers=auth_headers(b["access_token"])
    )
    assert resp.status_code == 403
