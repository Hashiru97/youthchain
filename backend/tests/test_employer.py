"""
Regression coverage for S-03/BL-05 (employer session auth) and the CSRF
protection added alongside it.
"""
import re


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


def test_employer_dashboard_requires_login(client):
    resp = client.get("/employer", follow_redirects=False)
    assert resp.status_code == 302
    assert "/employer/login" in resp.headers["Location"]


def test_employer_register_then_dashboard_reachable(client):
    resp = register_employer(client)
    assert resp.status_code == 302
    dash = client.get("/employer")
    assert dash.status_code == 200


def test_post_job_without_csrf_token_rejected(client):
    register_employer(client)
    resp = client.post("/employer/post", data={"title": "T", "location": "L", "duration": "D"})
    assert resp.status_code == 400


def test_post_job_with_csrf_token_succeeds_and_sets_employer_id(client):
    register_employer(client)
    page = client.get("/employer/post")
    token = _csrf_token(page.get_data(as_text=True))
    resp = client.post(
        "/employer/post",
        data={"csrf_token": token, "title": "Welder", "location": "Bo", "duration": "6mo"},
        follow_redirects=False,
    )
    assert resp.status_code == 302

    import app as app_module

    with app_module.app.app_context():
        job = app_module.Job.query.filter_by(title="Welder").first()
        assert job is not None
        assert job.employer_id is not None


def test_second_employer_cannot_manage_first_employers_job(client):
    register_employer(client, email="acme@test.com")
    page = client.get("/employer/post")
    token = _csrf_token(page.get_data(as_text=True))
    client.post(
        "/employer/post",
        data={"csrf_token": token, "title": "Job1", "location": "L", "duration": "D"},
    )

    import app as app_module

    with app_module.app.app_context():
        job = app_module.Job.query.filter_by(title="Job1").first()
        job_id = job.id

    # Log out, register a second, unrelated employer
    logout_page = client.get("/employer/post")  # still authed as acme
    client.post(
        "/employer/logout",
        data={"csrf_token": _csrf_token(logout_page.get_data(as_text=True))},
    )
    register_employer(client, email="other@test.com")

    resp = client.get(f"/employer/applications/{job_id}")
    assert resp.status_code == 403
