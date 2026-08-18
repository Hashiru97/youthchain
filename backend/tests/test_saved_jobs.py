"""
Coverage for the saved-jobs (bookmark) feature: POST /api/jobs/<id>/save,
POST /api/jobs/<id>/unsave, GET /api/saved_jobs. One join table covers
both Home (employer/gig) and Discover (scraped) jobs since they already
share one Job table (see SavedJob's own docstring in app.py) -- tests
below deliberately exercise both kinds, not just one.
"""
from conftest import register_user, auth_headers


def _make_job(app_module, source="employer", title="Job", **kwargs):
    with app_module.app.app_context():
        job = app_module.Job(title=title, location="Freetown", duration="Full-time", source=source, **kwargs)
        app_module.db.session.add(job)
        app_module.db.session.commit()
        return job.id


def test_save_job_then_list_includes_it(client):
    import app as app_module

    job_id = _make_job(app_module, title="Junior Developer")
    user = register_user(client)
    headers = auth_headers(user["access_token"])

    resp = client.post(f"/api/jobs/{job_id}/save", headers=headers)
    assert resp.status_code == 200
    assert resp.get_json() == {"success": True, "saved": True}

    resp = client.get("/api/saved_jobs", headers=headers)
    assert resp.status_code == 200
    titles = [j["title"] for j in resp.get_json()]
    assert titles == ["Junior Developer"]


def test_save_is_idempotent_under_a_double_tap(client):
    """Confirmed via the model's own unique constraint (uq_saved_job_user_job)
    catching the race a fast double-tap creates, not just app-level logic."""
    import app as app_module

    job_id = _make_job(app_module)
    user = register_user(client)
    headers = auth_headers(user["access_token"])

    resp1 = client.post(f"/api/jobs/{job_id}/save", headers=headers)
    resp2 = client.post(f"/api/jobs/{job_id}/save", headers=headers)
    assert resp1.status_code == 200
    assert resp2.status_code == 200

    with app_module.app.app_context():
        assert app_module.SavedJob.query.count() == 1


def test_unsave_removes_it_from_the_list(client):
    import app as app_module

    job_id = _make_job(app_module)
    user = register_user(client)
    headers = auth_headers(user["access_token"])

    client.post(f"/api/jobs/{job_id}/save", headers=headers)
    resp = client.post(f"/api/jobs/{job_id}/unsave", headers=headers)
    assert resp.status_code == 200
    assert resp.get_json() == {"success": True, "saved": False}

    resp = client.get("/api/saved_jobs", headers=headers)
    assert resp.get_json() == []


def test_unsave_a_job_that_was_never_saved_is_a_harmless_no_op(client):
    import app as app_module

    job_id = _make_job(app_module)
    user = register_user(client)
    headers = auth_headers(user["access_token"])

    resp = client.post(f"/api/jobs/{job_id}/unsave", headers=headers)
    assert resp.status_code == 200
    assert resp.get_json() == {"success": True, "saved": False}


def test_save_nonexistent_job_returns_404(client):
    user = register_user(client)
    headers = auth_headers(user["access_token"])
    resp = client.post("/api/jobs/999999/save", headers=headers)
    assert resp.status_code == 404


def test_saved_jobs_list_is_most_recently_saved_first(client):
    import app as app_module

    job_a = _make_job(app_module, title="Job A")
    job_b = _make_job(app_module, title="Job B")
    user = register_user(client)
    headers = auth_headers(user["access_token"])

    client.post(f"/api/jobs/{job_a}/save", headers=headers)
    client.post(f"/api/jobs/{job_b}/save", headers=headers)

    resp = client.get("/api/saved_jobs", headers=headers)
    titles = [j["title"] for j in resp.get_json()]
    assert titles == ["Job B", "Job A"]


def test_saved_jobs_list_mixes_employer_and_scraped_jobs(client):
    """The whole point of one shared join table instead of two --
    a user's saved list is one list regardless of where each job came
    from."""
    import app as app_module

    with app_module.app.app_context():
        source = app_module.JobSource(name="Careers.sl", base_url="https://careers.sl")
        app_module.db.session.add(source)
        app_module.db.session.commit()
        source_id = source.id

    employer_job = _make_job(app_module, source="employer", title="Employer Job")
    scraped_job = _make_job(app_module, source="scraped", title="Scraped Job", source_id=source_id)

    user = register_user(client)
    headers = auth_headers(user["access_token"])
    client.post(f"/api/jobs/{employer_job}/save", headers=headers)
    client.post(f"/api/jobs/{scraped_job}/save", headers=headers)

    resp = client.get("/api/saved_jobs", headers=headers)
    sources = {j["source"] for j in resp.get_json()}
    assert sources == {"employer", "scraped"}


def test_saved_jobs_are_private_to_each_user(client):
    import app as app_module

    job_id = _make_job(app_module)
    alice = register_user(client, email="alice2@test.com", phone="222")
    bob = register_user(client, email="bob2@test.com", phone="333")

    client.post(f"/api/jobs/{job_id}/save", headers=auth_headers(alice["access_token"]))

    resp = client.get("/api/saved_jobs", headers=auth_headers(bob["access_token"]))
    assert resp.get_json() == []


def test_save_requires_auth(client):
    import app as app_module

    job_id = _make_job(app_module)
    resp = client.post(f"/api/jobs/{job_id}/save")
    assert resp.status_code == 401


def test_unsave_requires_auth(client):
    import app as app_module

    job_id = _make_job(app_module)
    resp = client.post(f"/api/jobs/{job_id}/unsave")
    assert resp.status_code == 401


def test_saved_jobs_list_requires_auth(client):
    resp = client.get("/api/saved_jobs")
    assert resp.status_code == 401
