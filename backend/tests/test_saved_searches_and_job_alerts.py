"""
Coverage for the Saved Search / job-alert feature: SavedSearch CRUD
(POST/GET/delete /api/saved_searches), Candidate.job_alerts_enabled, and
_dispatch_job_alerts_for_scan -- the function scanner.poller's
on_scan_complete callback and scripts/run_scan.py both call after a real
scan, which turns scanner.pipeline.ScanOutcome.created_job_ids into
notify_user() alerts for matching Saved Searches and opted-in candidates.
"""
from unittest.mock import MagicMock

from scanner.pipeline import ScanOutcome

from conftest import register_user, auth_headers


def _make_scraped_job(app_module, title="Scraped Job", **kwargs):
    with app_module.app.app_context():
        source = app_module.JobSource(name="Careers.sl", base_url="https://careers.sl")
        app_module.db.session.add(source)
        app_module.db.session.commit()
        job = app_module.Job(
            title=title, location="Freetown", duration="Full-time",
            source="scraped", source_id=source.id, **kwargs,
        )
        app_module.db.session.add(job)
        app_module.db.session.commit()
        return job.id


# ----------------- SavedSearch CRUD -----------------

def test_create_saved_search_requires_at_least_one_filter(client):
    user = register_user(client)
    resp = client.post("/api/saved_searches", json={}, headers=auth_headers(user["access_token"]))
    assert resp.status_code == 400


def test_create_saved_search_succeeds_and_appears_in_list(client):
    user = register_user(client)
    headers = auth_headers(user["access_token"])

    resp = client.post(
        "/api/saved_searches", json={"skill": "python", "location": "Freetown"}, headers=headers,
    )
    assert resp.status_code == 201
    body = resp.get_json()["saved_search"]
    assert body["skill"] == "python"
    assert body["location"] == "Freetown"
    assert body["q"] is None

    resp = client.get("/api/saved_searches", headers=headers)
    rows = resp.get_json()
    assert len(rows) == 1
    assert rows[0]["skill"] == "python"


def test_saved_searches_require_auth(client):
    resp = client.get("/api/saved_searches")
    assert resp.status_code == 401
    resp = client.post("/api/saved_searches", json={"q": "driver"})
    assert resp.status_code == 401


def test_saved_search_cap_is_enforced(client):
    import app as app_module

    user = register_user(client)
    headers = auth_headers(user["access_token"])
    for i in range(app_module._MAX_SAVED_SEARCHES_PER_USER):
        resp = client.post("/api/saved_searches", json={"skill": f"skill-{i}"}, headers=headers)
        assert resp.status_code == 201

    resp = client.post("/api/saved_searches", json={"skill": "one-too-many"}, headers=headers)
    assert resp.status_code == 400


def test_delete_saved_search_removes_it(client):
    user = register_user(client)
    headers = auth_headers(user["access_token"])
    search_id = client.post(
        "/api/saved_searches", json={"q": "driver"}, headers=headers,
    ).get_json()["saved_search"]["id"]

    resp = client.post(f"/api/saved_searches/{search_id}/delete", headers=headers)
    assert resp.status_code == 200
    assert client.get("/api/saved_searches", headers=headers).get_json() == []


def test_deleting_someone_elses_saved_search_is_forbidden(client):
    user_a = register_user(client, email="a@test.com", phone="201")
    user_b = register_user(client, email="b@test.com", phone="202")
    search_id = client.post(
        "/api/saved_searches", json={"q": "driver"}, headers=auth_headers(user_a["access_token"]),
    ).get_json()["saved_search"]["id"]

    resp = client.post(
        f"/api/saved_searches/{search_id}/delete", headers=auth_headers(user_b["access_token"]),
    )
    assert resp.status_code == 403


# ----------------- Candidate.job_alerts_enabled -----------------

def test_candidate_job_alerts_enabled_defaults_false_and_round_trips(client):
    user = register_user(client)
    headers = auth_headers(user["access_token"])
    payload = {"name": "Alice", "email": "alice-alerts@test.com", "skills": "python"}

    resp = client.post("/api/candidate", json=payload, headers=headers)
    assert resp.status_code in (200, 201)

    me = client.get("/api/candidate/me", headers=headers).get_json()["candidate"]
    assert me["job_alerts_enabled"] is False

    payload["job_alerts_enabled"] = True
    client.post("/api/candidate", json=payload, headers=headers)
    me = client.get("/api/candidate/me", headers=headers).get_json()["candidate"]
    assert me["job_alerts_enabled"] is True


# ----------------- _dispatch_job_alerts_for_scan -----------------

def test_dispatch_fires_for_a_matching_saved_search(client, monkeypatch):
    import app as app_module

    user = register_user(client)
    with app_module.app.app_context():
        app_module.db.session.add(app_module.SavedSearch(user_id=user["user"]["id"], skill="excel"))
        app_module.db.session.commit()

    job_id = _make_scraped_job(app_module, title="Office Admin", required_skills="Excel, filing")
    outcome = ScanOutcome(success=True, jobs_found=1, jobs_created=1, jobs_updated=0, created_job_ids=[job_id])

    with app_module.app.app_context():
        app_module._dispatch_job_alerts_for_scan(outcome)
        notifications = app_module.Notification.query.filter_by(
            user_id=user["user"]["id"], type="saved_search_match",
        ).all()
        assert len(notifications) == 1
        assert notifications[0].body == "Office Admin"


def test_dispatch_fires_for_an_opted_in_candidates_skill_overlap(client, monkeypatch):
    import app as app_module

    user = register_user(client)
    headers = auth_headers(user["access_token"])
    client.post(
        "/api/candidate",
        json={
            "name": "Alice", "email": "alice-skills@test.com",
            "skills": "python, excel", "job_alerts_enabled": True,
        },
        headers=headers,
    )

    job_id = _make_scraped_job(app_module, title="Data Clerk", required_skills="Excel, communication")
    outcome = ScanOutcome(success=True, jobs_found=1, jobs_created=1, jobs_updated=0, created_job_ids=[job_id])

    with app_module.app.app_context():
        app_module._dispatch_job_alerts_for_scan(outcome)
        notifications = app_module.Notification.query.filter_by(
            user_id=user["user"]["id"], type="job_alert_match",
        ).all()
        assert len(notifications) == 1


def test_dispatch_skips_a_candidate_who_never_opted_in(client, monkeypatch):
    import app as app_module

    user = register_user(client)
    headers = auth_headers(user["access_token"])
    client.post(
        "/api/candidate",
        json={"name": "Alice", "email": "alice-noopt@test.com", "skills": "excel"},
        headers=headers,
    )

    job_id = _make_scraped_job(app_module, title="Data Clerk", required_skills="Excel")
    outcome = ScanOutcome(success=True, jobs_found=1, jobs_created=1, jobs_updated=0, created_job_ids=[job_id])

    with app_module.app.app_context():
        app_module._dispatch_job_alerts_for_scan(outcome)
        assert app_module.Notification.query.filter_by(
            user_id=user["user"]["id"], type="job_alert_match",
        ).count() == 0


def test_dispatch_only_considers_created_job_ids_not_every_job(client, monkeypatch):
    """A rescan that merely updates an existing job must never re-fire an
    alert for it -- see ScanOutcome.created_job_ids' own docstring. This
    exercises the dispatch side of that contract directly: a Job that
    exists but isn't in created_job_ids is invisible to this function."""
    import app as app_module

    user = register_user(client)
    with app_module.app.app_context():
        app_module.db.session.add(app_module.SavedSearch(user_id=user["user"]["id"], skill="excel"))
        app_module.db.session.commit()

    _make_scraped_job(app_module, title="Old Listing", required_skills="Excel")
    outcome = ScanOutcome(success=True, jobs_found=0, jobs_created=0, jobs_updated=1, created_job_ids=[])

    with app_module.app.app_context():
        app_module._dispatch_job_alerts_for_scan(outcome)
        assert app_module.Notification.query.filter_by(
            user_id=user["user"]["id"], type="saved_search_match",
        ).count() == 0


def test_dispatch_is_a_noop_when_the_scan_itself_failed(client):
    import app as app_module

    user = register_user(client)
    with app_module.app.app_context():
        app_module.db.session.add(app_module.SavedSearch(user_id=user["user"]["id"], skill="excel"))
        app_module.db.session.commit()

    job_id = _make_scraped_job(app_module, title="Office Admin", required_skills="Excel")
    outcome = ScanOutcome(
        success=False, jobs_found=0, jobs_created=0, jobs_updated=0,
        error_message="boom", created_job_ids=[job_id],
    )

    with app_module.app.app_context():
        app_module._dispatch_job_alerts_for_scan(outcome)
        assert app_module.Notification.query.count() == 0


def test_dispatch_sends_sms_to_a_saved_search_user_who_opted_into_sms(client, monkeypatch):
    import app as app_module

    user = register_user(client)
    fake_client = MagicMock()
    monkeypatch.setattr(app_module, "_twilio_client", fake_client)
    monkeypatch.setattr(app_module, "_TWILIO_FROM_NUMBER", "+15005550006")

    with app_module.app.app_context():
        u = app_module.db.session.get(app_module.User, user["user"]["id"])
        u.sms_alerts_enabled = True
        app_module.db.session.add(app_module.SavedSearch(user_id=user["user"]["id"], skill="excel"))
        app_module.db.session.commit()

    job_id = _make_scraped_job(app_module, title="Office Admin", required_skills="Excel, filing")
    outcome = ScanOutcome(success=True, jobs_found=1, jobs_created=1, jobs_updated=0, created_job_ids=[job_id])

    with app_module.app.app_context():
        app_module._dispatch_job_alerts_for_scan(outcome)

    fake_client.messages.create.assert_called_once()
    assert fake_client.messages.create.call_args.kwargs["to"] == user["user"]["phone"]


def test_dispatch_does_not_sms_a_saved_search_user_who_never_opted_into_sms(client, monkeypatch):
    """In-app/push alerts still fire (see the earlier saved-search test) --
    only the SMS channel specifically is gated on the extra opt-in."""
    import app as app_module

    user = register_user(client)
    fake_client = MagicMock()
    monkeypatch.setattr(app_module, "_twilio_client", fake_client)
    monkeypatch.setattr(app_module, "_TWILIO_FROM_NUMBER", "+15005550006")

    with app_module.app.app_context():
        app_module.db.session.add(app_module.SavedSearch(user_id=user["user"]["id"], skill="excel"))
        app_module.db.session.commit()

    job_id = _make_scraped_job(app_module, title="Office Admin", required_skills="Excel, filing")
    outcome = ScanOutcome(success=True, jobs_found=1, jobs_created=1, jobs_updated=0, created_job_ids=[job_id])

    with app_module.app.app_context():
        app_module._dispatch_job_alerts_for_scan(outcome)
        assert app_module.Notification.query.filter_by(
            user_id=user["user"]["id"], type="saved_search_match",
        ).count() == 1  # in-app/push alert still went out

    fake_client.messages.create.assert_not_called()


def test_dispatch_one_failing_match_does_not_block_the_rest(client, monkeypatch):
    """Best-effort per match, same posture run_scan_for_source's own
    per-job try/except already has: one bad notify_user() call must never
    stop the rest of a scan's matches from going out."""
    import app as app_module

    user_a = register_user(client, email="fail-a@test.com", phone="301")
    user_b = register_user(client, email="fail-b@test.com", phone="302")
    with app_module.app.app_context():
        app_module.db.session.add_all([
            app_module.SavedSearch(user_id=user_a["user"]["id"], skill="excel"),
            app_module.SavedSearch(user_id=user_b["user"]["id"], skill="excel"),
        ])
        app_module.db.session.commit()

    job_id = _make_scraped_job(app_module, title="Office Admin", required_skills="Excel")
    outcome = ScanOutcome(success=True, jobs_found=1, jobs_created=1, jobs_updated=0, created_job_ids=[job_id])

    real_notify_user = app_module.notify_user

    def flaky_notify_user(user_id, *a, **k):
        if user_id == user_a["user"]["id"]:
            raise RuntimeError("simulated notify_user failure")
        return real_notify_user(user_id, *a, **k)

    monkeypatch.setattr(app_module, "notify_user", flaky_notify_user)

    with app_module.app.app_context():
        app_module._dispatch_job_alerts_for_scan(outcome)
        assert app_module.Notification.query.filter_by(
            user_id=user_b["user"]["id"], type="saved_search_match",
        ).count() == 1
