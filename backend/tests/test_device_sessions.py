"""
Regression coverage for UserSession -- the "connected devices" feature.
Real gap found (not anticipated in advance): the app already had per-token
revocation on self-logout (_add_to_jwt_blocklist) and per-account
revocation on suspension (Employer.active / Admin.active re-checked every
request), but a youth had no way to see how many devices were logged into
their own account, or kill one remotely (lost/stolen phone, a shared
device they forgot to log out of). One UserSession row per login, on
either channel ("app" JWT or "web" session-cookie), backing a symmetric
pair of routes on both surfaces.
"""
import re

import app as app_module
from conftest import register_user, auth_headers
from test_portal import register_portal_user, _csrf_token


def _login_jwt(client, email="alice@test.com", password="password123"):
    return client.post("/login", json={"email": email, "password": password})


class TestJWTChannel:
    def test_register_creates_a_session_row_without_a_notification(self, client):
        register_user(client, email="reg@test.com")
        with app_module.app.app_context():
            user = app_module.User.query.filter_by(email="reg@test.com").first()
            rows = app_module.UserSession.query.filter_by(user_id=user.id).all()
            assert len(rows) == 1
            assert rows[0].channel == "app"
            assert rows[0].revoked_at is None
            # notify=False at registration -- a "new device" alert one
            # second after "you just made an account" is noise.
            notifs = app_module.Notification.query.filter_by(user_id=user.id, type="new_device_login").all()
            assert notifs == []

    def test_explicit_login_creates_a_session_row_and_notifies(self, client):
        register_user(client, email="alice@test.com")
        resp = _login_jwt(client)
        assert resp.status_code == 200
        with app_module.app.app_context():
            user = app_module.User.query.filter_by(email="alice@test.com").first()
            rows = app_module.UserSession.query.filter_by(user_id=user.id, channel="app").all()
            # One from registration, one from this explicit login.
            assert len(rows) == 2
            notifs = app_module.Notification.query.filter_by(user_id=user.id, type="new_device_login").all()
            assert len(notifs) == 1

    def test_otp_login_creates_a_session_row(self, client):
        register_user(client, email="otpdev@test.com")
        client.post("/auth/otp/request", json={"email": "otpdev@test.com"})
        with app_module.app.app_context():
            code = app_module.OTPCode.query.filter_by(identifier="otpdev@test.com", used=False).first().code
        resp = client.post("/auth/otp/verify", json={"email": "otpdev@test.com", "code": code})
        assert resp.status_code == 200
        with app_module.app.app_context():
            user = app_module.User.query.filter_by(email="otpdev@test.com").first()
            rows = app_module.UserSession.query.filter_by(user_id=user.id, channel="app").all()
            assert len(rows) == 2  # registration + this OTP login

    def test_api_devices_lists_sessions_and_flags_the_current_one(self, client):
        a = register_user(client, email="listme@test.com")
        resp = client.get("/api/devices", headers=auth_headers(a["access_token"]))
        assert resp.status_code == 200
        sessions = resp.get_json()["sessions"]
        assert len(sessions) == 1
        assert sessions[0]["is_current"] is True
        assert sessions[0]["channel"] == "app"
        assert "session_token" not in sessions[0]  # never leak the raw correlator to the client

    def test_revoking_another_devices_token_blocklists_it_immediately(self, client):
        a = register_user(client, email="twodev@test.com")
        b = _login_jwt(client, email="twodev@test.com").get_json()  # a second "device" login

        devices = client.get("/api/devices", headers=auth_headers(a["access_token"])).get_json()["sessions"]
        other = next(d for d in devices if not d["is_current"])

        resp = client.post(f"/api/devices/{other['id']}/revoke", headers=auth_headers(a["access_token"]))
        assert resp.status_code == 200

        # The OTHER device's own token must be dead now, not just marked
        # revoked in the DB -- this is the actual security property.
        still_works = client.get("/api/candidate/me", headers=auth_headers(b["access_token"]))
        assert still_works.status_code == 401

    def test_cannot_revoke_someone_elses_device(self, client):
        register_user(client, email="victim@test.com", phone="700111")
        attacker = register_user(client, email="attacker@test.com", phone="700222")
        with app_module.app.app_context():
            victim = app_module.User.query.filter_by(email="victim@test.com").first()
            victim_session_id = app_module.UserSession.query.filter_by(user_id=victim.id).first().id

        resp = client.post(f"/api/devices/{victim_session_id}/revoke", headers=auth_headers(attacker["access_token"]))
        assert resp.status_code == 403
        with app_module.app.app_context():
            row = app_module.UserSession.query.get(victim_session_id)
            assert row.revoked_at is None

    def test_logout_marks_its_own_session_row_revoked(self, client):
        a = register_user(client, email="logoutdev@test.com")
        client.post("/logout", headers=auth_headers(a["access_token"]))
        with app_module.app.app_context():
            user = app_module.User.query.filter_by(email="logoutdev@test.com").first()
            rows = app_module.UserSession.query.filter_by(user_id=user.id).all()
            assert all(r.revoked_at is not None for r in rows)


class TestWebChannel:
    def test_login_creates_a_web_session_row(self, client):
        register_portal_user(client, email="webdev@test.com")
        with client.session_transaction() as sess:
            token = sess["portal_session_token"]
        with app_module.app.app_context():
            row = app_module.UserSession.query.filter_by(session_token=token).first()
            assert row is not None
            assert row.channel == "web"

    def test_registration_auto_login_does_not_notify(self, client):
        register_portal_user(client, email="webreg@test.com")
        with app_module.app.app_context():
            user = app_module.User.query.filter_by(email="webreg@test.com").first()
            notifs = app_module.Notification.query.filter_by(user_id=user.id, type="new_device_login").all()
            assert notifs == []

    def test_explicit_web_login_notifies(self, client):
        register_portal_user(client, email="webnotify@test.com")
        client.post("/portal/logout", data={"csrf_token": _csrf_token(client.get("/portal").get_data(as_text=True))})
        login_page = client.get("/portal/login")
        client.post("/portal/login", data={
            "csrf_token": _csrf_token(login_page.get_data(as_text=True)),
            "identifier": "webnotify@test.com", "password": "Str0ng!Passw0rd9",
        })
        with app_module.app.app_context():
            user = app_module.User.query.filter_by(email="webnotify@test.com").first()
            notifs = app_module.Notification.query.filter_by(user_id=user.id, type="new_device_login").all()
            assert len(notifs) == 1

    def test_devices_page_lists_session_and_marks_current(self, client):
        register_portal_user(client, email="devpage@test.com")
        page = client.get("/portal/devices")
        assert page.status_code == 200
        body = page.get_data(as_text=True)
        assert "This device" in body
        assert "Web browser" in body

    def test_revoking_a_session_from_another_browser_logs_the_other_one_out(self, client):
        """
        Simulates two real browsers on the same account: two independent
        Flask test clients (independent cookie jars) against the same app,
        exactly like two devices hitting the same server.
        """
        register_portal_user(client, email="crossdev@test.com")

        other = app_module.app.test_client()
        login_page = other.get("/portal/login")
        other.post("/portal/login", data={
            "csrf_token": _csrf_token(login_page.get_data(as_text=True)),
            "identifier": "crossdev@test.com", "password": "Str0ng!Passw0rd9",
        })
        assert other.get("/portal/devices").status_code == 200  # confirms `other` really is logged in

        devices = client.get("/portal/devices").get_data(as_text=True)
        with client.session_transaction() as sess:
            my_token = sess["portal_session_token"]
        with app_module.app.app_context():
            other_row = app_module.UserSession.query.filter(
                app_module.UserSession.session_token != my_token
            ).order_by(app_module.UserSession.id.desc()).first()

        revoke_page = client.get("/portal/devices")
        client.post(
            f"/portal/devices/{other_row.id}/revoke",
            data={"csrf_token": _csrf_token(revoke_page.get_data(as_text=True))},
        )

        # The revoked browser is now logged out on its very next request,
        # not just "eventually" -- _current_portal_user_id re-checks every time.
        redirected = other.get("/portal/devices")
        assert redirected.status_code == 302
        assert "/portal/login" in redirected.headers["Location"]

    def test_revoking_your_own_current_device_logs_you_out_too(self, client):
        register_portal_user(client, email="selfrevoke@test.com")
        page = client.get("/portal/devices")
        with app_module.app.app_context():
            user = app_module.User.query.filter_by(email="selfrevoke@test.com").first()
            own_session_id = app_module.UserSession.query.filter_by(user_id=user.id).first().id

        resp = client.post(
            f"/portal/devices/{own_session_id}/revoke",
            data={"csrf_token": _csrf_token(page.get_data(as_text=True))},
        )
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/portal/login")

        # And actually logged out, not just redirected once.
        assert client.get("/portal/devices").status_code == 302

    def test_cannot_revoke_someone_elses_web_session(self, client):
        """
        Two independent clients (two real browsers) so the victim's own
        session stays genuinely live throughout -- logging the victim out
        first would trigger their own legitimate self-logout revoke
        (portal_logout marks its own row), which would make a passing
        assertion meaningless: it wouldn't be testing the IDOR guard at
        all, just re-observing an already-expected self-revoke.
        """
        register_portal_user(client, email="webvictim@test.com", phone="23276111333")
        with app_module.app.app_context():
            victim = app_module.User.query.filter_by(email="webvictim@test.com").first()
            victim_session_id = app_module.UserSession.query.filter_by(user_id=victim.id).first().id

        attacker = app_module.app.test_client()
        register_portal_user(attacker, email="webattacker@test.com", phone="23276111444")
        page = attacker.get("/portal/devices")
        resp = attacker.post(
            f"/portal/devices/{victim_session_id}/revoke",
            data={"csrf_token": _csrf_token(page.get_data(as_text=True))},
        )
        assert resp.status_code == 403
        with app_module.app.app_context():
            row = app_module.UserSession.query.get(victim_session_id)
            assert row.revoked_at is None
        # And the victim's own session is still genuinely usable.
        assert client.get("/portal/devices").status_code == 200

    def test_web_logout_marks_its_session_row_revoked(self, client):
        register_portal_user(client, email="weblogout@test.com")
        with client.session_transaction() as sess:
            token = sess["portal_session_token"]
        client.post("/portal/logout", data={"csrf_token": _csrf_token(client.get("/portal").get_data(as_text=True))})
        with app_module.app.app_context():
            row = app_module.UserSession.query.filter_by(session_token=token).first()
            assert row.revoked_at is not None

    def test_pre_existing_session_without_a_token_heals_forward_instead_of_logging_out(self, client):
        """
        Simulates a session cookie that predates this feature (portal_user_id
        set, no portal_session_token) -- must not force a surprise logout.
        """
        register_portal_user(client, email="legacy@test.com")
        with client.session_transaction() as sess:
            del sess["portal_session_token"]

        resp = client.get("/portal/devices")
        assert resp.status_code == 200  # healed forward, not logged out
        with client.session_transaction() as sess:
            assert "portal_session_token" in sess  # a token was minted lazily
