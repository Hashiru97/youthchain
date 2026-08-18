"""
Regression coverage for the web-portal (session-authenticated) half of the
bidirectional gig rating -- portal_rate_employer()/portal_flag_rating()/
portal_work_history() mirror the JWT/mobile routes (rate_employer(),
flag_rating(), api_candidate_trust_summary()) exactly, so a youth using the
web portal instead of the app has the same trust-building path, not just a
"download the app" teaser the way messaging deliberately is.
"""
import re

from conftest import register_user, auth_headers


def _csrf_token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "csrf token not found in page"
    return match.group(1)


def register_portal_user(client, email="youth@test.com", name="Kadiatu", phone="23276111222", password="Str0ng!Passw0rd9", channel="email"):
    """Mirrors test_portal.py's helper of the same name -- see that
    module's docstring for why this is 3 real POSTs, not 2."""
    identifier = email if channel == "email" else phone
    step1_page = client.get("/portal/register")
    token = _csrf_token(step1_page.get_data(as_text=True))
    step2 = client.post(
        "/portal/register",
        data={"csrf_token": token, "step": "contact", "channel": channel, "identifier": identifier},
    )
    token2 = _csrf_token(step2.get_data(as_text=True))
    step3 = client.post(
        "/portal/register",
        data={"csrf_token": token2, "step": "code", "otp_code": ""},
    )
    token3 = _csrf_token(step3.get_data(as_text=True))
    other = phone if channel == "email" else email
    first_name, _, last_name = name.partition(" ")
    return client.post(
        "/portal/register",
        data={
            "csrf_token": token3, "step": "details",
            "first_name": first_name, "last_name": last_name or "Sesay", "other": other,
            "password": password, "consent": "on",
        },
    )


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


def _setup_completed_gig_via_portal(client):
    """
    Employer (web session) posts a gig, a youth applies via the PORTAL
    (not the mobile /apply route), employer accepts + completes it.
    Returns (job_id, employer_id, app_id).
    """
    _register_employer(client)
    job_id, employer_id = _post_gig_job(client)

    _logout_employer(client)
    register_portal_user(client)

    page = client.get(f"/portal/jobs/{job_id}")
    token = _csrf_token(page.get_data(as_text=True))
    client.post(f"/portal/jobs/{job_id}/apply", data={"csrf_token": token})

    import app as app_module
    with app_module.app.app_context():
        app_id = app_module.Application.query.filter_by(job_id=job_id).first().id

    _logout_portal(client)
    _login_employer(client, "acme@test.com", "password123")

    accept_page = client.get(f"/employer/applications/{job_id}")
    client.post(f"/employer/applications/{job_id}", data={
        "csrf_token": _csrf_token(accept_page.get_data(as_text=True)),
        "app_id": str(app_id), "action": "accept",
    })
    complete_page = client.get(f"/employer/applications/{job_id}")
    client.post(f"/employer/applications/{app_id}/complete", data={
        "csrf_token": _csrf_token(complete_page.get_data(as_text=True)),
    })

    _logout_employer(client)
    _login_portal(client, "youth@test.com", "Str0ng!Passw0rd9")

    return job_id, employer_id, app_id


def _logout_employer(client):
    page = client.get("/employer/post")
    client.post("/employer/logout", data={"csrf_token": _csrf_token(page.get_data(as_text=True))})


def _login_employer(client, email, password):
    page = client.get("/employer/login")
    client.post("/employer/login", data={
        "csrf_token": _csrf_token(page.get_data(as_text=True)), "email": email, "password": password,
    })


def _logout_portal(client):
    page = client.get("/portal/applications")
    client.post("/portal/logout", data={"csrf_token": _csrf_token(page.get_data(as_text=True))})


def _login_portal(client, identifier, password):
    page = client.get("/portal/login")
    client.post("/portal/login", data={
        "csrf_token": _csrf_token(page.get_data(as_text=True)), "identifier": identifier, "password": password,
    })


def test_worker_can_rate_employer_via_portal_after_completion(client):
    import app as app_module

    _job_id, employer_id, app_id = _setup_completed_gig_via_portal(client)
    page = client.get("/portal/applications")
    token = _csrf_token(page.get_data(as_text=True))
    resp = client.post(f"/portal/applications/{app_id}/rate_employer", data={
        "csrf_token": token, "score": "5", "comment": "Paid on time",
    })
    assert resp.status_code == 302

    with app_module.app.app_context():
        rating = app_module.Rating.query.filter_by(application_id=app_id, direction="worker_to_employer").first()
        assert rating is not None
        assert rating.score == 5
        assert rating.employer_id == employer_id


def test_cannot_rate_employer_via_portal_before_completed(client):
    _register_employer(client)
    job_id, _employer_id = _post_gig_job(client)
    _logout_employer(client)
    register_portal_user(client)
    page = client.get(f"/portal/jobs/{job_id}")
    token = _csrf_token(page.get_data(as_text=True))
    client.post(f"/portal/jobs/{job_id}/apply", data={"csrf_token": token})

    import app as app_module
    with app_module.app.app_context():
        app_id = app_module.Application.query.filter_by(job_id=job_id).first().id

    page = client.get("/portal/applications")
    token = _csrf_token(page.get_data(as_text=True))
    resp = client.post(f"/portal/applications/{app_id}/rate_employer", data={"csrf_token": token, "score": "5"})
    assert resp.status_code == 403


def test_cannot_rate_employer_via_portal_twice(client):
    _job_id, _employer_id, app_id = _setup_completed_gig_via_portal(client)
    page = client.get("/portal/applications")
    token = _csrf_token(page.get_data(as_text=True))
    client.post(f"/portal/applications/{app_id}/rate_employer", data={"csrf_token": token, "score": "5"})

    page2 = client.get("/portal/applications")
    token2 = _csrf_token(page2.get_data(as_text=True))
    resp = client.post(f"/portal/applications/{app_id}/rate_employer", data={"csrf_token": token2, "score": "1"})
    assert resp.status_code == 403


def test_worker_cannot_rate_someone_elses_application_via_portal(client):
    _job_id, _employer_id, app_id = _setup_completed_gig_via_portal(client)
    _logout_portal(client)
    register_portal_user(client, email="intruder@test.com", phone="23279000111")

    page = client.get("/portal/applications")
    token = _csrf_token(page.get_data(as_text=True))
    resp = client.post(f"/portal/applications/{app_id}/rate_employer", data={"csrf_token": token, "score": "1"})
    assert resp.status_code == 403


def test_worker_can_flag_a_received_rating_via_portal(client):
    import app as app_module

    job_id, _employer_id, app_id = _setup_completed_gig_via_portal(client)
    _logout_portal(client)
    _login_employer(client, "acme@test.com", "password123")
    rate_page = client.get(f"/employer/applications/{job_id}")
    client.post(f"/employer/applications/{app_id}/rate", data={
        "csrf_token": _csrf_token(rate_page.get_data(as_text=True)), "score": "1",
    })
    _logout_employer(client)
    _login_portal(client, "youth@test.com", "Str0ng!Passw0rd9")

    with app_module.app.app_context():
        rating_id = app_module.Rating.query.filter_by(application_id=app_id, direction="employer_to_worker").first().id

    page = client.get("/portal/applications")
    token = _csrf_token(page.get_data(as_text=True))
    resp = client.post(f"/portal/ratings/{rating_id}/flag", data={
        "csrf_token": token, "reason": "This rating is unfair",
    })
    assert resp.status_code == 302

    with app_module.app.app_context():
        flag = app_module.RatingFlag.query.filter_by(rating_id=rating_id).first()
        assert flag is not None
        assert flag.flagged_by_role == "worker"


def test_worker_cannot_flag_their_own_given_rating_via_portal(client):
    """Direction restriction, same reasoning as flag_rating()'s JWT counterpart."""
    import app as app_module

    _job_id, _employer_id, app_id = _setup_completed_gig_via_portal(client)
    page = client.get("/portal/applications")
    token = _csrf_token(page.get_data(as_text=True))
    client.post(f"/portal/applications/{app_id}/rate_employer", data={"csrf_token": token, "score": "3"})

    with app_module.app.app_context():
        own_rating_id = app_module.Rating.query.filter_by(application_id=app_id, direction="worker_to_employer").first().id

    page2 = client.get("/portal/applications")
    token2 = _csrf_token(page2.get_data(as_text=True))
    resp = client.post(f"/portal/ratings/{own_rating_id}/flag", data={"csrf_token": token2, "reason": "changed my mind"})
    assert resp.status_code == 403


def test_portal_flag_rating_is_rate_limited(client, monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, "_REPORT_MAX_ATTEMPTS", 2)
    job_id, _employer_id, app_id = _setup_completed_gig_via_portal(client)
    _logout_portal(client)
    _login_employer(client, "acme@test.com", "password123")
    rate_page = client.get(f"/employer/applications/{job_id}")
    client.post(f"/employer/applications/{app_id}/rate", data={
        "csrf_token": _csrf_token(rate_page.get_data(as_text=True)), "score": "1",
    })
    _logout_employer(client)
    _login_portal(client, "youth@test.com", "Str0ng!Passw0rd9")

    with app_module.app.app_context():
        rating_id = app_module.Rating.query.filter_by(application_id=app_id, direction="employer_to_worker").first().id

    for i in range(2):
        page = client.get("/portal/applications")
        token = _csrf_token(page.get_data(as_text=True))
        resp = client.post(f"/portal/ratings/{rating_id}/flag", data={"csrf_token": token, "reason": f"attempt {i}"})
        assert resp.status_code == 302

    page = client.get("/portal/applications")
    token = _csrf_token(page.get_data(as_text=True))
    third = client.post(f"/portal/ratings/{rating_id}/flag", data={"csrf_token": token, "reason": "attempt 3"})
    assert third.status_code == 429


def test_portal_work_history_shows_completed_gigs_and_avg_rating(client):
    job_id, _employer_id, app_id = _setup_completed_gig_via_portal(client)
    _logout_portal(client)
    _login_employer(client, "acme@test.com", "password123")
    rate_page = client.get(f"/employer/applications/{job_id}")
    client.post(f"/employer/applications/{app_id}/rate", data={
        "csrf_token": _csrf_token(rate_page.get_data(as_text=True)), "score": "4", "comment": "Solid work",
    })
    _logout_employer(client)
    _login_portal(client, "youth@test.com", "Str0ng!Passw0rd9")

    resp = client.get("/portal/work_history")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Cleaner Needed" in body
    assert "Solid work" in body
    assert "4/5" in body


def test_portal_work_history_requires_login(client):
    resp = client.get("/portal/work_history")
    assert resp.status_code == 302


def test_portal_applications_page_shows_rate_form_for_completed_gig(client):
    _job_id, _employer_id, app_id = _setup_completed_gig_via_portal(client)
    resp = client.get("/portal/applications")
    body = resp.get_data(as_text=True)
    assert 'action="/portal/applications/%d/rate_employer"' % app_id in body


def test_portal_applications_page_shows_given_rating_not_form_after_rating(client):
    _job_id, _employer_id, app_id = _setup_completed_gig_via_portal(client)
    page = client.get("/portal/applications")
    token = _csrf_token(page.get_data(as_text=True))
    client.post(f"/portal/applications/{app_id}/rate_employer", data={"csrf_token": token, "score": "5"})

    resp = client.get("/portal/applications")
    body = resp.get_data(as_text=True)
    assert "You rated them 5/5" in body
    assert 'action="/portal/applications/%d/rate_employer"' % app_id not in body
