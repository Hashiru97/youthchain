"""
Regression coverage for _employer_trust_summary() -- the employer-side
mirror of _worker_trust_summary(): an individual employer with no business
papers at all (see Employer.verification_type) can still show real,
earned trust from past workers' ratings. The underlying worker_to_employer
Rating rows were already being written before this; this is the first
place that data is ever aggregated or shown to anyone.
"""
import re

from conftest import register_user, auth_headers


def _csrf_token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "csrf token not found in page"
    return match.group(1)


def _register_employer(client, email="acme@test.com", name="Acme", password="password123"):
    page = client.get("/employer/register")
    token = _csrf_token(page.get_data(as_text=True))
    return client.post(
        "/employer/register",
        data={"csrf_token": token, "name": name, "email": email, "password": password},
    )


def _post_gig_job(client, title="Cleaner Needed", category="Cleaning"):
    page = client.get("/employer/post")
    client.post("/employer/post", data={
        "csrf_token": _csrf_token(page.get_data(as_text=True)),
        "title": title, "location": "Bo", "duration": "1 day",
        "job_type": "gig", "category": category,
    })
    import app as app_module
    with app_module.app.app_context():
        job = app_module.Job.query.filter_by(title=title).first()
        return job.id, job.employer_id


def _complete_gig_and_rate_employer(client, job_id, score, worker_email):
    """Worker applies (mobile/JWT), employer accepts + completes, worker rates the employer."""
    phone = str(abs(hash(worker_email)) % 900000000 + 100000000)
    worker = register_user(client, email=worker_email, phone=phone)
    client.post(
        "/apply", data={"job_id": str(job_id)},
        headers=auth_headers(worker["access_token"]), content_type="multipart/form-data",
    )
    import app as app_module
    with app_module.app.app_context():
        app_id = app_module.Application.query.filter_by(job_id=job_id, user_id=worker["user"]["id"]).first().id

    accept_page = client.get(f"/employer/applications/{job_id}")
    client.post(f"/employer/applications/{job_id}", data={
        "csrf_token": _csrf_token(accept_page.get_data(as_text=True)), "app_id": str(app_id), "action": "accept",
    })
    complete_page = client.get(f"/employer/applications/{job_id}")
    client.post(f"/employer/applications/{app_id}/complete", data={
        "csrf_token": _csrf_token(complete_page.get_data(as_text=True)),
    })
    client.post(
        f"/api/applications/{app_id}/rate_employer",
        json={"score": score},
        headers=auth_headers(worker["access_token"]),
    )
    return app_id


def test_employer_trust_summary_reflects_multiple_worker_ratings(client):
    import app as app_module

    _register_employer(client)
    job_id, employer_id = _post_gig_job(client)
    _complete_gig_and_rate_employer(client, job_id, 5, "workera@test.com")
    _complete_gig_and_rate_employer(client, job_id, 3, "workerb@test.com")

    with app_module.app.app_context():
        summary = app_module._employer_trust_summary(employer_id)
        assert summary["rating_count"] == 2
        assert summary["avg_rating"] == 4.0


def test_employer_trust_summary_is_none_and_zero_with_no_ratings(client):
    import app as app_module

    _register_employer(client)
    _job_id, employer_id = _post_gig_job(client)

    with app_module.app.app_context():
        summary = app_module._employer_trust_summary(employer_id)
        assert summary["rating_count"] == 0
        assert summary["avg_rating"] is None


def test_hidden_rating_excluded_from_employer_trust_summary(client):
    import app as app_module

    _register_employer(client)
    job_id, employer_id = _post_gig_job(client)
    app_id = _complete_gig_and_rate_employer(client, job_id, 1, "workerc@test.com")

    with app_module.app.app_context():
        rating = app_module.Rating.query.filter_by(application_id=app_id, direction="worker_to_employer").first()
        rating.hidden = True
        app_module.db.session.commit()
        summary = app_module._employer_trust_summary(employer_id)
        assert summary["rating_count"] == 0
        assert summary["avg_rating"] is None


def test_job_payload_includes_employer_trust_summary(client):
    """Job.to_dict() -> _employer_summary() is the shared shape mobile
    reads from -- this is what actually makes the trust rating show up
    on the mobile job detail screen."""
    _register_employer(client)
    job_id, _employer_id = _post_gig_job(client, title="Rated Gig")
    _complete_gig_and_rate_employer(client, job_id, 5, "workerd@test.com")

    resp = client.get("/jobs")
    jobs = {j["title"]: j for j in resp.get_json()}
    employer_info = jobs["Rated Gig"]["employer"]
    assert employer_info["avg_rating"] == 5.0
    assert employer_info["rating_count"] == 1


def test_portal_job_listing_shows_employer_rating(client):
    _register_employer(client)
    job_id, _employer_id = _post_gig_job(client, title="Portal Rated Gig")
    _complete_gig_and_rate_employer(client, job_id, 4, "workere@test.com")

    resp = client.get("/portal?job_type=gig")
    body = resp.get_data(as_text=True)
    assert "Portal Rated Gig" in body
    assert "4.0" in body
    assert "from past workers" in body


def test_portal_job_detail_shows_employer_rating(client):
    _register_employer(client)
    job_id, _employer_id = _post_gig_job(client, title="Detail Rated Gig")
    _complete_gig_and_rate_employer(client, job_id, 2, "workerf@test.com")

    resp = client.get(f"/portal/jobs/{job_id}")
    body = resp.get_data(as_text=True)
    assert "2.0" in body
    assert "from past workers" in body


def test_employer_dashboard_shows_own_trust_summary(client):
    _register_employer(client)
    job_id, _employer_id = _post_gig_job(client, title="Own Dashboard Gig")
    _complete_gig_and_rate_employer(client, job_id, 5, "workerg@test.com")

    resp = client.get("/employer")
    body = resp.get_data(as_text=True)
    assert "Workers rate you" in body
    assert "5.0/5" in body


def test_employer_dashboard_hides_trust_summary_with_no_ratings_yet(client):
    _register_employer(client, email="freshco@test.com", name="FreshCo")
    resp = client.get("/employer")
    body = resp.get_data(as_text=True)
    assert "Workers rate you" not in body
