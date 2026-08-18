"""
Regression coverage for a real, severe gap found via a full-codebase
review: every socketio.emit() in app.py used to broadcast globally, with
no authentication at connect time and no room targeting -- any client
that opened a WebSocket connection (no login required) received every
other user's private message bodies, notification previews, and
application status changes platform-wide. Confirmed this wasn't just an
unused wire payload: the mobile app's notification_created handler reads
the event body straight into a UI toast. Fixed via _socketio_connect (JWT
from the connect `auth` payload for youth, the existing session cookie for
employers) joining each client to a private user:<id>/employer:<id> room,
with every privacy-sensitive emit() now targeted at only those rooms.
"""
from conftest import register_user


def test_notification_is_delivered_only_to_the_owning_users_socket_room(client):
    import app as app_module

    alice = register_user(client, email="alice@sock.com", phone="7001", name="Alice")
    bob = register_user(client, email="bob@sock.com", phone="7002", name="Bob")

    alice_socket = app_module.socketio.test_client(
        app_module.app, auth={"token": alice["access_token"]}, flask_test_client=client
    )
    bob_socket = app_module.socketio.test_client(
        app_module.app, auth={"token": bob["access_token"]}, flask_test_client=client
    )
    try:
        assert alice_socket.is_connected()
        assert bob_socket.is_connected()

        with app_module.app.app_context():
            app_module.notify_user(alice["user"]["id"], "test_event", "Hello Alice", "private body")

        alice_received = alice_socket.get_received()
        bob_received = bob_socket.get_received()

        alice_events = [e["name"] for e in alice_received]
        bob_events = [e["name"] for e in bob_received]

        assert "notification_created" in alice_events
        assert "notification_created" not in bob_events
    finally:
        alice_socket.disconnect()
        bob_socket.disconnect()


def test_an_unauthenticated_socket_connection_receives_no_private_notification(client):
    import app as app_module

    alice = register_user(client, email="alice2@sock.com", phone="7003", name="Alice")

    anon_socket = app_module.socketio.test_client(app_module.app, flask_test_client=client)
    try:
        assert anon_socket.is_connected()

        with app_module.app.app_context():
            app_module.notify_user(alice["user"]["id"], "test_event", "Hello Alice", "private body")

        anon_events = [e["name"] for e in anon_socket.get_received()]
        assert "notification_created" not in anon_events
    finally:
        anon_socket.disconnect()


def test_message_created_is_delivered_only_to_the_two_real_parties(client):
    """
    Drives the real HTTP routes end-to-end (employer session login, job
    post, apply, then the employer's actual messaging route) rather than
    calling _create_message() directly -- that internal function assumes
    a real Flask request context (it reads the employer's session cookie
    via _current_employer_id()), which only exists on a genuine request,
    not a bare app_context().
    """
    import re
    import app as app_module
    from tests.conftest import FAKE_PDF_BYTES
    from io import BytesIO

    def csrf_token(html):
        match = re.search(r'name="csrf_token" value="([^"]+)"', html)
        assert match, "csrf token not found in page"
        return match.group(1)

    alice = register_user(client, email="alice3@sock.com", phone="7004", name="Alice")
    mallory = register_user(client, email="mallory@sock.com", phone="7005", name="Mallory")

    # Real employer session (cookie-based), established via the actual
    # /employer/register route -- this is a SEPARATE auth mechanism
    # (session cookie) from Alice/Mallory's JWT bearer tokens above, and
    # both coexist fine on the same test client.
    reg_page = client.get("/employer/register")
    reg_resp = client.post(
        "/employer/register",
        data={
            "csrf_token": csrf_token(reg_page.get_data(as_text=True)),
            "name": "Acme",
            "email": "acme-sock@test.com",
            "password": "password123",
        },
    )
    assert reg_resp.status_code == 302

    post_page = client.get("/employer/post")
    post_resp = client.post(
        "/employer/post",
        data={
            "csrf_token": csrf_token(post_page.get_data(as_text=True)),
            "title": "Welder",
            "location": "Bo",
            "duration": "6mo",
        },
        follow_redirects=False,
    )
    assert post_resp.status_code == 302

    with app_module.app.app_context():
        job_id = app_module.Job.query.filter_by(title="Welder").first().id

    apply_resp = client.post(
        "/apply",
        data={"job_id": str(job_id), "cv": (BytesIO(FAKE_PDF_BYTES), "cv.pdf")},
        headers={"Authorization": f"Bearer {alice['access_token']}"},
        content_type="multipart/form-data",
    )
    assert apply_resp.status_code == 201

    with app_module.app.app_context():
        app_id = app_module.Application.query.filter_by(user_id=alice["user"]["id"], job_id=job_id).first().id

    # Deliberately NOT `flask_test_client=client` for either of these: that
    # client already carries the employer's session cookie (set by
    # /employer/register above), and flask-socketio's test client reuses
    # whatever cookies its flask_test_client has -- reusing `client` for
    # BOTH sockets would incorrectly join both Alice's and Mallory's
    # connections to the employer's own room too, defeating the point of
    # this test. Each of Alice/Mallory gets a clean client with no cookies
    # at all; their identity here comes only from the JWT in `auth`.
    alice_socket = app_module.socketio.test_client(
        app_module.app, auth={"token": alice["access_token"]}, flask_test_client=app_module.app.test_client()
    )
    mallory_socket = app_module.socketio.test_client(
        app_module.app, auth={"token": mallory["access_token"]}, flask_test_client=app_module.app.test_client()
    )
    try:
        alice_socket.get_received()  # drain application_created/notification noise
        mallory_socket.get_received()

        # The employer (still logged in on `client` via the session cookie
        # from registration above) sends a message on this application.
        messages_page = client.get(f"/employer/applications/{job_id}/messages/{app_id}")
        client.post(
            f"/employer/applications/{job_id}/messages/{app_id}",
            data={
                "csrf_token": csrf_token(messages_page.get_data(as_text=True)),
                "body": "hello Alice",
            },
        )

        alice_events = [e["name"] for e in alice_socket.get_received()]
        mallory_events = [e["name"] for e in mallory_socket.get_received()]

        assert "message_created" in alice_events
        assert "message_created" not in mallory_events
    finally:
        alice_socket.disconnect()
        mallory_socket.disconnect()
