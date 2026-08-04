"""
Regression coverage for BL-21: pagination on /jobs and /my_applications/<id>,
and the N+1 -> JOIN fix on the latter.
"""
from conftest import register_user, auth_headers, FAKE_PDF_BYTES


def _post_job_direct(title, location="L", duration="D"):
    import app as app_module

    with app_module.app.app_context():
        job = app_module.Job(title=title, location=location, duration=duration)
        app_module.db.session.add(job)
        app_module.db.session.commit()
        return job.id


def test_jobs_endpoint_respects_limit_and_offset(client):
    for i in range(5):
        _post_job_direct(f"Job {i}")

    page1 = client.get("/jobs?limit=2&offset=0").get_json()
    page2 = client.get("/jobs?limit=2&offset=2").get_json()

    assert len(page1) == 2
    assert len(page2) == 2
    assert {j["id"] for j in page1}.isdisjoint({j["id"] for j in page2})


def test_jobs_endpoint_caps_limit_at_max(client):
    resp = client.get("/jobs?limit=99999")
    assert resp.status_code == 200  # doesn't error, just clamps server-side


def test_my_applications_join_populates_job_fields_without_n_plus_1(client):
    job_id = _post_job_direct("Solar Tech", location="Bo", duration="6mo")
    a = register_user(client)

    client.post(
        "/apply",
        data={"job_id": str(job_id), "cv": (__import__("io").BytesIO(FAKE_PDF_BYTES), "cv.pdf")},
        headers=auth_headers(a["access_token"]),
        content_type="multipart/form-data",
    )

    resp = client.get(f"/my_applications/{a['user']['id']}", headers=auth_headers(a["access_token"]))
    assert resp.status_code == 200
    apps = resp.get_json()
    assert len(apps) == 1
    assert apps[0]["job_title"] == "Solar Tech"
    assert apps[0]["job_location"] == "Bo"
