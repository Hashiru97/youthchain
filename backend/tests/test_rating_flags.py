"""
Regression coverage for RatingFlag -- the dispute path for a Rating either
side thinks is unfair. Mirrors test_reports.py's own conventions closely on
purpose: RatingFlag is the exact same "someone flags a record, an admin
reviews it" shape as EmployerReport, just for ratings instead of employers.
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


def _create_admin(email, password="adminpass123", role="admin"):
    import app as app_module

    with app_module.app.app_context():
        admin = app_module.Admin(
            name="Ops",
            email=email,
            role=role,
            active=True,
            password_hash=app_module.generate_password_hash(password),
        )
        app_module.db.session.add(admin)
        app_module.db.session.commit()
    return email, password


def _login_admin(client, email, password):
    page = client.get("/admin/login")
    token = _csrf_token(page.get_data(as_text=True))
    return client.post(
        "/admin/login",
        data={"csrf_token": token, "email": email, "password": password},
    )


def _setup_rated_gig(client):
    """Employer posts a gig, worker applies, gets accepted + completed, employer rates worker.
    Returns (job_id, employer_id, app_id, worker, rating_id)."""
    import app as app_module

    _register_employer(client)
    page = client.get("/employer/post")
    client.post("/employer/post", data={
        "csrf_token": _csrf_token(page.get_data(as_text=True)),
        "title": "Cleaner Needed", "location": "Bo", "duration": "1 day",
        "job_type": "gig", "category": "Cleaning",
    })
    with app_module.app.app_context():
        job = app_module.Job.query.filter_by(title="Cleaner Needed").first()
        job_id, employer_id = job.id, job.employer_id

    worker = register_user(client, email="worker@test.com", phone="555")
    client.post(
        "/apply", data={"job_id": str(job_id)},
        headers=auth_headers(worker["access_token"]), content_type="multipart/form-data",
    )
    with app_module.app.app_context():
        app_id = app_module.Application.query.filter_by(job_id=job_id).first().id

    accept_page = client.get(f"/employer/applications/{job_id}")
    client.post(f"/employer/applications/{job_id}", data={
        "csrf_token": _csrf_token(accept_page.get_data(as_text=True)),
        "app_id": str(app_id), "action": "accept",
    })
    complete_page = client.get(f"/employer/applications/{job_id}")
    client.post(f"/employer/applications/{app_id}/complete", data={
        "csrf_token": _csrf_token(complete_page.get_data(as_text=True)),
    })
    rate_page = client.get(f"/employer/applications/{job_id}")
    client.post(f"/employer/applications/{app_id}/rate", data={
        "csrf_token": _csrf_token(rate_page.get_data(as_text=True)), "score": "1",
    })
    with app_module.app.app_context():
        rating_id = app_module.Rating.query.filter_by(application_id=app_id, direction="employer_to_worker").first().id

    return job_id, employer_id, app_id, worker, rating_id


def test_worker_can_flag_a_rating(client):
    import app as app_module

    _job_id, _employer_id, _app_id, worker, rating_id = _setup_rated_gig(client)
    resp = client.post(
        f"/api/ratings/{rating_id}/flag",
        json={"reason": "This is unfair, I did the job well"},
        headers=auth_headers(worker["access_token"]),
    )
    assert resp.status_code == 201

    with app_module.app.app_context():
        flag = app_module.RatingFlag.query.filter_by(rating_id=rating_id).first()
        assert flag is not None
        assert flag.flagged_by_role == "worker"
        assert flag.status == "open"


def test_flag_requires_a_reason(client):
    _job_id, _employer_id, _app_id, worker, rating_id = _setup_rated_gig(client)
    resp = client.post(
        f"/api/ratings/{rating_id}/flag",
        json={"reason": ""},
        headers=auth_headers(worker["access_token"]),
    )
    assert resp.status_code == 400


def test_worker_cannot_flag_someone_elses_rating(client):
    _job_id, _employer_id, _app_id, _worker, rating_id = _setup_rated_gig(client)
    intruder = register_user(client, email="intruder@test.com", phone="888")
    resp = client.post(
        f"/api/ratings/{rating_id}/flag",
        json={"reason": "Not mine but trying anyway"},
        headers=auth_headers(intruder["access_token"]),
    )
    assert resp.status_code == 403


def test_admin_can_view_and_dismiss_a_rating_flag(client):
    import app as app_module

    _job_id, _employer_id, _app_id, worker, rating_id = _setup_rated_gig(client)
    client.post(
        f"/api/ratings/{rating_id}/flag",
        json={"reason": "Unfair"},
        headers=auth_headers(worker["access_token"]),
    )

    admin_email, admin_password = _create_admin("admin2@youthchain.test")
    _login_admin(client, admin_email, admin_password)

    page = client.get("/admin/rating_flags")
    assert page.status_code == 200
    # Real gap found via re-checking this page: without the job title, an
    # admin sees "1/5, Bob, Acme Corp" with no way to tell which of
    # potentially several gigs between that worker and employer the
    # dispute is even about.
    assert "Cleaner Needed" in page.get_data(as_text=True)
    with app_module.app.app_context():
        flag_id = app_module.RatingFlag.query.filter_by(rating_id=rating_id).first().id

    resp = client.post(f"/admin/rating_flags/{flag_id}/resolve", data={
        "csrf_token": _csrf_token(page.get_data(as_text=True)), "decision": "dismiss",
    })
    assert resp.status_code == 302

    with app_module.app.app_context():
        flag = app_module.RatingFlag.query.get(flag_id)
        assert flag.status == "dismissed"
        assert flag.reviewed_by_admin_id is not None
        rating = app_module.Rating.query.get(rating_id)
        assert rating.hidden is False


def test_admin_can_hide_rating_via_flag_action(client):
    import app as app_module

    _job_id, _employer_id, _app_id, worker, rating_id = _setup_rated_gig(client)
    client.post(
        f"/api/ratings/{rating_id}/flag",
        json={"reason": "Unfair"},
        headers=auth_headers(worker["access_token"]),
    )

    admin_email, admin_password = _create_admin("admin3@youthchain.test")
    _login_admin(client, admin_email, admin_password)

    page = client.get("/admin/rating_flags")
    with app_module.app.app_context():
        flag_id = app_module.RatingFlag.query.filter_by(rating_id=rating_id).first().id

    client.post(f"/admin/rating_flags/{flag_id}/resolve", data={
        "csrf_token": _csrf_token(page.get_data(as_text=True)), "decision": "hide",
    })

    with app_module.app.app_context():
        flag = app_module.RatingFlag.query.get(flag_id)
        assert flag.status == "actioned"
        rating = app_module.Rating.query.get(rating_id)
        assert rating.hidden is True


def test_verifier_role_admin_cannot_reach_rating_flags(client):
    verifier_email, verifier_password = _create_admin("verifier@youthchain.test", role="verifier")
    _login_admin(client, verifier_email, verifier_password)

    resp = client.get("/admin/rating_flags")
    assert resp.status_code == 403


def test_employer_can_flag_a_rating(client):
    """The employer-side (session-authenticated) half of the dispute path -- see employer_flag_rating()."""
    import app as app_module

    job_id, _employer_id, app_id, worker, _rating_id = _setup_rated_gig(client)
    client.post(
        f"/api/applications/{app_id}/rate_employer",
        json={"score": 1, "comment": "Never paid me"},
        headers=auth_headers(worker["access_token"]),
    )
    with app_module.app.app_context():
        worker_rating_id = app_module.Rating.query.filter_by(application_id=app_id, direction="worker_to_employer").first().id

    # Employer session is still active from _setup_rated_gig's registration.
    page = client.get(f"/employer/applications/{job_id}")
    resp = client.post(f"/employer/ratings/{worker_rating_id}/flag", data={
        "csrf_token": _csrf_token(page.get_data(as_text=True)), "reason": "This is retaliation",
    })
    assert resp.status_code == 302

    with app_module.app.app_context():
        flag = app_module.RatingFlag.query.filter_by(rating_id=worker_rating_id).first()
        assert flag is not None
        assert flag.flagged_by_role == "employer"


def test_worker_cannot_flag_a_rating_they_themselves_gave(client):
    """
    Rating.user_id is always the worker regardless of direction -- without
    restricting flag_rating() to employer_to_worker ratings, a worker
    could "dispute" their own worker_to_employer submission, which isn't a
    real dispute, just noise in the admin queue.
    """
    _job_id, _employer_id, app_id, worker, _rating_id = _setup_rated_gig(client)
    client.post(
        f"/api/applications/{app_id}/rate_employer",
        json={"score": 3},
        headers=auth_headers(worker["access_token"]),
    )
    import app as app_module
    with app_module.app.app_context():
        own_rating_id = app_module.Rating.query.filter_by(application_id=app_id, direction="worker_to_employer").first().id

    resp = client.post(
        f"/api/ratings/{own_rating_id}/flag",
        json={"reason": "changed my mind"},
        headers=auth_headers(worker["access_token"]),
    )
    assert resp.status_code == 403


def test_employer_cannot_flag_a_rating_they_themselves_gave(client):
    """Symmetric to the worker-side restriction above, for employer_flag_rating()."""
    job_id, _employer_id, _app_id, _worker, rating_id = _setup_rated_gig(client)
    # rating_id from _setup_rated_gig is the employer's own employer_to_worker rating.
    page = client.get(f"/employer/applications/{job_id}")
    resp = client.post(f"/employer/ratings/{rating_id}/flag", data={
        "csrf_token": _csrf_token(page.get_data(as_text=True)), "reason": "changed my mind",
    })
    assert resp.status_code == 403


def test_unrelated_employer_cannot_flag_another_employers_rating(client):
    """
    Same cross-tenant IDOR class as employer_applications()'s documented
    accept/reject fix, proven the same way this codebase insists
    everything else is: live, not assumed.
    """
    job_id, _employer_id, app_id, worker, _rating_id = _setup_rated_gig(client)
    client.post(
        f"/api/applications/{app_id}/rate_employer",
        json={"score": 2},
        headers=auth_headers(worker["access_token"]),
    )
    import app as app_module
    with app_module.app.app_context():
        worker_rating_id = app_module.Rating.query.filter_by(application_id=app_id, direction="worker_to_employer").first().id

    post_page = client.get(f"/employer/post")
    client.post("/employer/logout", data={"csrf_token": _csrf_token(post_page.get_data(as_text=True))})
    _register_employer(client, email="rival3@corp.com", name="RivalCorp3")
    csrf_token = _csrf_token(client.get("/employer/post").get_data(as_text=True))

    resp = client.post(f"/employer/ratings/{worker_rating_id}/flag", data={
        "csrf_token": csrf_token, "reason": "not mine but trying",
    })
    assert resp.status_code == 403
    with app_module.app.app_context():
        assert app_module.RatingFlag.query.filter_by(rating_id=worker_rating_id).count() == 0


def test_flag_rating_is_rate_limited(client, monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, "_REPORT_MAX_ATTEMPTS", 2)
    _job_id, _employer_id, _app_id, worker, rating_id = _setup_rated_gig(client)

    for i in range(2):
        resp = client.post(
            f"/api/ratings/{rating_id}/flag",
            json={"reason": f"attempt {i}"},
            headers=auth_headers(worker["access_token"]),
        )
        assert resp.status_code == 201

    third = client.post(
        f"/api/ratings/{rating_id}/flag",
        json={"reason": "attempt 3"},
        headers=auth_headers(worker["access_token"]),
    )
    assert third.status_code == 429
