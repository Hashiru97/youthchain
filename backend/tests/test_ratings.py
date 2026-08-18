"""
Regression coverage for the gig-work lifecycle beyond "applied": mark
complete, then bidirectional rating (employer rates worker, worker rates
employer) -- see the Rating/RatingFlag models' own docstrings in app.py for
why both directions exist. Guard rails matter as much as the happy path
here since a rating is a reputational record with no edit route.
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


def _post_gig_job(client, title="Tailor Needed", category="Tailoring & Dressmaking"):
    page = client.get("/employer/post")
    client.post("/employer/post", data={
        "csrf_token": _csrf_token(page.get_data(as_text=True)),
        "title": title, "location": "Bo", "duration": "1 week",
        "job_type": "gig", "category": category,
    })
    import app as app_module
    with app_module.app.app_context():
        job = app_module.Job.query.filter_by(title=title).first()
        return job.id, job.employer_id


def _apply_as_worker(client, job_id, email="worker@test.com", phone="555"):
    worker = register_user(client, email=email, phone=phone)
    client.post(
        "/apply",
        data={"job_id": str(job_id)},
        headers=auth_headers(worker["access_token"]),
        content_type="multipart/form-data",
    )
    import app as app_module
    with app_module.app.app_context():
        application = app_module.Application.query.filter_by(user_id=worker["user"]["id"], job_id=job_id).first()
        return application.id, worker


def _accept(client, job_id, app_id):
    page = client.get(f"/employer/applications/{job_id}")
    client.post(f"/employer/applications/{job_id}", data={
        "csrf_token": _csrf_token(page.get_data(as_text=True)),
        "app_id": str(app_id), "action": "accept",
    })


def _complete(client, job_id, app_id):
    page = client.get(f"/employer/applications/{job_id}")
    return client.post(f"/employer/applications/{app_id}/complete", data={
        "csrf_token": _csrf_token(page.get_data(as_text=True)),
    })


def _setup_completed_gig(client):
    """Employer posts a gig, worker applies, employer accepts + marks complete. Returns (job_id, employer_id, app_id, worker)."""
    _register_employer(client)
    job_id, employer_id = _post_gig_job(client)
    app_id, worker = _apply_as_worker(client, job_id)
    _accept(client, job_id, app_id)
    _complete(client, job_id, app_id)
    return job_id, employer_id, app_id, worker


def test_employer_can_mark_gig_application_complete(client):
    import app as app_module

    job_id, _employer_id, app_id, _worker = _setup_completed_gig(client)
    with app_module.app.app_context():
        application = app_module.Application.query.get(app_id)
        assert application.status == "Completed"


def test_cannot_mark_complete_before_accepted(client):
    import app as app_module

    _register_employer(client)
    job_id, _employer_id = _post_gig_job(client)
    app_id, _worker = _apply_as_worker(client, job_id)
    # Still Pending -- never accepted.
    resp = _complete(client, job_id, app_id)
    assert resp.status_code == 403
    with app_module.app.app_context():
        assert app_module.Application.query.get(app_id).status == "Pending"


def test_cannot_mark_complete_for_formal_job(client):
    """The complete/rate lifecycle is gig-only in V1 -- a formal job's
    Accepted application has no path to Completed."""
    import app as app_module

    _register_employer(client)
    post_page = client.get("/employer/post")
    client.post("/employer/post", data={
        "csrf_token": _csrf_token(post_page.get_data(as_text=True)),
        "title": "Formal Accountant", "location": "Bo", "duration": "6mo",
    })
    with app_module.app.app_context():
        job = app_module.Job.query.filter_by(title="Formal Accountant").first()
        job_id = job.id

    worker = register_user(client, email="formalworker@test.com", phone="556")
    client.post(
        "/apply",
        data={"job_id": str(job_id), "cv": (__import__("io").BytesIO(b"%PDF-1.4\nfake"), "cv.pdf")},
        headers=auth_headers(worker["access_token"]),
        content_type="multipart/form-data",
    )
    with app_module.app.app_context():
        app_id = app_module.Application.query.filter_by(job_id=job_id).first().id

    _accept(client, job_id, app_id)
    resp = _complete(client, job_id, app_id)
    assert resp.status_code == 403


def test_employer_can_rate_worker_after_completion(client):
    import app as app_module

    job_id, employer_id, app_id, worker = _setup_completed_gig(client)
    page = client.get(f"/employer/applications/{job_id}")
    resp = client.post(f"/employer/applications/{app_id}/rate", data={
        "csrf_token": _csrf_token(page.get_data(as_text=True)),
        "score": "5", "comment": "Great work",
    })
    assert resp.status_code == 302

    with app_module.app.app_context():
        rating = app_module.Rating.query.filter_by(application_id=app_id, direction="employer_to_worker").first()
        assert rating is not None
        assert rating.score == 5
        assert rating.user_id == worker["user"]["id"]
        assert rating.employer_id == employer_id


def test_cannot_rate_before_completed(client):
    _register_employer(client)
    job_id, _employer_id = _post_gig_job(client)
    app_id, _worker = _apply_as_worker(client, job_id)
    _accept(client, job_id, app_id)  # Accepted, not Completed

    page = client.get(f"/employer/applications/{job_id}")
    resp = client.post(f"/employer/applications/{app_id}/rate", data={
        "csrf_token": _csrf_token(page.get_data(as_text=True)),
        "score": "4",
    })
    assert resp.status_code == 403


def test_cannot_rate_twice_same_direction(client):
    job_id, _employer_id, app_id, _worker = _setup_completed_gig(client)
    page = client.get(f"/employer/applications/{job_id}")
    client.post(f"/employer/applications/{app_id}/rate", data={
        "csrf_token": _csrf_token(page.get_data(as_text=True)), "score": "5",
    })

    page2 = client.get(f"/employer/applications/{job_id}")
    resp = client.post(f"/employer/applications/{app_id}/rate", data={
        "csrf_token": _csrf_token(page2.get_data(as_text=True)), "score": "1",
    })
    assert resp.status_code == 403


def test_rate_rejects_score_out_of_range(client):
    job_id, _employer_id, app_id, _worker = _setup_completed_gig(client)
    page = client.get(f"/employer/applications/{job_id}")
    resp = client.post(f"/employer/applications/{app_id}/rate", data={
        "csrf_token": _csrf_token(page.get_data(as_text=True)), "score": "9",
    })
    assert resp.status_code == 403


def test_worker_can_rate_employer_after_completion(client):
    import app as app_module

    job_id, employer_id, app_id, worker = _setup_completed_gig(client)
    resp = client.post(
        f"/api/applications/{app_id}/rate_employer",
        json={"score": 4, "comment": "Paid on time"},
        headers=auth_headers(worker["access_token"]),
    )
    assert resp.status_code == 201

    with app_module.app.app_context():
        rating = app_module.Rating.query.filter_by(application_id=app_id, direction="worker_to_employer").first()
        assert rating is not None
        assert rating.score == 4
        assert rating.employer_id == employer_id


def test_worker_cannot_rate_employer_on_someone_elses_application(client):
    job_id, _employer_id, app_id, _worker = _setup_completed_gig(client)
    intruder = register_user(client, email="intruder@test.com", phone="777")
    resp = client.post(
        f"/api/applications/{app_id}/rate_employer",
        json={"score": 1},
        headers=auth_headers(intruder["access_token"]),
    )
    assert resp.status_code == 403


def test_worker_trust_summary_reflects_avg_and_count(client):
    import app as app_module

    job_id, _employer_id, app_id, worker = _setup_completed_gig(client)
    page = client.get(f"/employer/applications/{job_id}")
    client.post(f"/employer/applications/{app_id}/rate", data={
        "csrf_token": _csrf_token(page.get_data(as_text=True)), "score": "4",
    })

    resp = client.get(
        f"/api/candidate/{worker['user']['id']}/trust_summary",
        headers=auth_headers(worker["access_token"]),
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["completed_gigs"] == 1
    assert data["avg_rating"] == 4.0
    assert data["rating_count"] == 1
    assert len(data["rated_gigs"]) == 1


def test_hidden_rating_excluded_from_average(client):
    import app as app_module

    job_id, _employer_id, app_id, worker = _setup_completed_gig(client)
    page = client.get(f"/employer/applications/{job_id}")
    client.post(f"/employer/applications/{app_id}/rate", data={
        "csrf_token": _csrf_token(page.get_data(as_text=True)), "score": "1",
    })
    with app_module.app.app_context():
        rating = app_module.Rating.query.filter_by(application_id=app_id, direction="employer_to_worker").first()
        rating.hidden = True
        app_module.db.session.commit()

    resp = client.get(
        f"/api/candidate/{worker['user']['id']}/trust_summary",
        headers=auth_headers(worker["access_token"]),
    )
    data = resp.get_json()
    assert data["rating_count"] == 0
    assert data["avg_rating"] is None


def test_my_applications_exposes_can_rate_employer_flag(client):
    job_id, _employer_id, app_id, worker = _setup_completed_gig(client)

    resp = client.get(f"/my_applications/{worker['user']['id']}", headers=auth_headers(worker["access_token"]))
    apps = resp.get_json()
    assert len(apps) == 1
    assert apps[0]["can_rate_employer"] is True
    assert apps[0]["employer_rating"] is None

    client.post(
        f"/api/applications/{app_id}/rate_employer",
        json={"score": 5},
        headers=auth_headers(worker["access_token"]),
    )

    resp = client.get(f"/my_applications/{worker['user']['id']}", headers=auth_headers(worker["access_token"]))
    apps = resp.get_json()
    assert apps[0]["can_rate_employer"] is False
    assert apps[0]["employer_rating"]["score"] == 5


# ---- Cross-employer IDOR guards ----
#
# The exact vulnerability class employer_applications()'s own accept/reject
# code documents at length (a real, "severe" cross-tenant IDOR found via
# live audit: Employer B, owning some job of their own, could act on
# Employer A's applicants by supplying an app_id that didn't belong to the
# job_id they were actually authorized for). complete()/rate() derive their
# job from the application itself rather than trusting two separately
# supplied ids, so they aren't the same shape of bug -- but that's a claim,
# not a test, until proven the same way this codebase already insists on
# for everything else ("reproduced live," not assumed).

def _logout_employer(client):
    page = client.get("/employer/post")
    client.post("/employer/logout", data={"csrf_token": _csrf_token(page.get_data(as_text=True))})


def test_unrelated_employer_cannot_complete_or_rate_another_employers_application(client):
    job_id, _employer_id, app_id, _worker = _setup_completed_gig(client)
    _logout_employer(client)

    _register_employer(client, email="rival@corp.com", name="RivalCorp")
    csrf_token = _csrf_token(client.get("/employer/post").get_data(as_text=True))

    rate_resp = client.post(f"/employer/applications/{app_id}/rate", data={
        "csrf_token": csrf_token, "score": "5",
    })
    assert rate_resp.status_code == 403

    import app as app_module
    with app_module.app.app_context():
        assert app_module.Rating.query.filter_by(application_id=app_id, direction="employer_to_worker").first() is None


def test_unrelated_employer_cannot_mark_another_employers_application_complete(client):
    _register_employer(client)
    job_id, _employer_id = _post_gig_job(client)
    app_id, _worker = _apply_as_worker(client, job_id)
    _accept(client, job_id, app_id)
    _logout_employer(client)

    _register_employer(client, email="rival2@corp.com", name="RivalCorp2")
    csrf_token = _csrf_token(client.get("/employer/post").get_data(as_text=True))
    resp = client.post(f"/employer/applications/{app_id}/complete", data={"csrf_token": csrf_token})
    assert resp.status_code == 403

    import app as app_module
    with app_module.app.app_context():
        assert app_module.Application.query.get(app_id).status == "Accepted"
