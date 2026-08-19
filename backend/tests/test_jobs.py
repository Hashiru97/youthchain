"""
Regression coverage for BL-21: pagination on /jobs and /my_applications/<id>,
and the N+1 -> JOIN fix on the latter.
"""
import re

from conftest import register_user, auth_headers, FAKE_PDF_BYTES


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


def _post_job_direct(title, location="L", duration="D", required_skills=None):
    import app as app_module

    with app_module.app.app_context():
        job = app_module.Job(title=title, location=location, duration=duration, required_skills=required_skills)
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


def test_jobs_search_by_title(client):
    _post_job_direct("Solar Panel Installer", location="Kenema")
    _post_job_direct("ICT Trainer", location="Bo")

    resp = client.get("/jobs?q=solar")
    titles = [j["title"] for j in resp.get_json()]
    assert titles == ["Solar Panel Installer"]


def test_jobs_full_text_search_matches_multiple_words_out_of_order(client):
    """
    Regression coverage for the real FTS5 upgrade — the old implementation
    was a plain title-only substring match, which "junior dev" would never
    have matched against "Junior Software Developer" (the words aren't
    contiguous). Full-text search should.
    """
    _post_job_direct("Junior Software Developer", location="Freetown")
    _post_job_direct("Solar Panel Installer", location="Kenema")

    resp = client.get("/jobs?q=junior+dev")
    titles = [j["title"] for j in resp.get_json()]
    assert titles == ["Junior Software Developer"]


def test_jobs_full_text_search_matches_required_skills_not_just_title(client):
    """The old implementation only searched Job.title — the real upgrade
    searches required_skills and location too."""
    _post_job_direct("Data Clerk", location="Bo", required_skills="excel, data entry")
    _post_job_direct("Warehouse Assistant", location="Bo", required_skills="lifting, driving")

    resp = client.get("/jobs?q=excel")
    titles = [j["title"] for j in resp.get_json()]
    assert titles == ["Data Clerk"]


def test_jobs_full_text_search_ranks_title_match_above_incidental_skill_match(client):
    """A job whose *title* matches the query should outrank one where the
    term only appears incidentally in required_skills — real relevance
    ranking (bm25 on SQLite), not just "any match, arbitrary order"."""
    _post_job_direct("Warehouse Assistant", location="Bo", required_skills="forklift, solar panel maintenance")
    _post_job_direct("Solar Panel Installer", location="Kenema", required_skills="wiring")

    resp = client.get("/jobs?q=solar+panel")
    titles = [j["title"] for j in resp.get_json()]
    assert titles[0] == "Solar Panel Installer"


def test_jobs_filter_by_location(client):
    _post_job_direct("Solar Panel Installer", location="Kenema")
    _post_job_direct("ICT Trainer", location="Bo")

    resp = client.get("/jobs?location=Bo")
    titles = [j["title"] for j in resp.get_json()]
    assert titles == ["ICT Trainer"]


def test_jobs_filter_by_skill(client):
    import app as app_module

    with app_module.app.app_context():
        job = app_module.Job(
            title="Wiring Job", location="X", duration="D", required_skills="wiring,electrical"
        )
        app_module.db.session.add(job)
        app_module.db.session.commit()
    _post_job_direct("Unrelated Job")

    resp = client.get("/jobs?skill=electrical")
    titles = [j["title"] for j in resp.get_json()]
    assert titles == ["Wiring Job"]


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


def test_job_payload_includes_owning_employer_name_and_verification_status(client):
    import app as app_module

    with app_module.app.app_context():
        employer = app_module.Employer(
            name="Acme Corp",
            email="acme-jobs@test.com",
            password_hash="x",
            verification_status="verified",
        )
        app_module.db.session.add(employer)
        app_module.db.session.commit()
        employer_id = employer.id
        job = app_module.Job(title="Owned Job", location="Bo", duration="6mo", employer_id=employer_id)
        app_module.db.session.add(job)
        app_module.db.session.commit()

    resp = client.get("/jobs")
    jobs = {j["title"]: j for j in resp.get_json()}
    assert jobs["Owned Job"]["employer"] == {
        "name": "Acme Corp", "verification_status": "verified", "verification_type": "business",
        "industry": None, "avg_rating": None, "rating_count": 0,
        # See test_employer_trust.py for full composite-score coverage --
        # this just pins that _employer_summary() actually includes it in
        # the shape every job payload returns. verified + no reports/
        # ratings/applications = 20+24+20+10 = 74, "good".
        "trust_score": 74, "trust_tier": "good",
    }


def test_job_payload_employer_is_null_when_no_owning_employer(client):
    _post_job_direct("Unowned Job")

    resp = client.get("/jobs")
    jobs = {j["title"]: j for j in resp.get_json()}
    assert jobs["Unowned Job"]["employer"] is None


# ---- Gig/informal-work job_type (see the gig-lifecycle plan) ----

def test_post_job_defaults_to_formal_job_type(client):
    """An employer form that doesn't send job_type at all -- exactly what
    every pre-existing client does -- must keep creating today's
    CV-required formal job, unchanged."""
    import app as app_module

    _register_employer(client)
    page = client.get("/employer/post")
    client.post("/employer/post", data={
        "csrf_token": _csrf_token(page.get_data(as_text=True)),
        "title": "Accountant", "location": "Freetown", "duration": "Full-time",
    })

    with app_module.app.app_context():
        job = app_module.Job.query.filter_by(title="Accountant").first()
        assert job.job_type == "formal"
        assert job.category is None


def test_post_job_can_create_gig_job_with_category(client):
    import app as app_module

    _register_employer(client)
    page = client.get("/employer/post")
    client.post("/employer/post", data={
        "csrf_token": _csrf_token(page.get_data(as_text=True)),
        "title": "Tailor Needed", "location": "Bo", "duration": "1 week",
        "job_type": "gig", "category": "Tailoring & Dressmaking",
    })

    with app_module.app.app_context():
        job = app_module.Job.query.filter_by(title="Tailor Needed").first()
        assert job.job_type == "gig"
        assert job.category == "Tailoring & Dressmaking"


def test_post_job_falls_back_to_other_for_unrecognized_category(client):
    import app as app_module

    _register_employer(client)
    page = client.get("/employer/post")
    client.post("/employer/post", data={
        "csrf_token": _csrf_token(page.get_data(as_text=True)),
        "title": "Odd Gig", "location": "Bo", "duration": "1 day",
        "job_type": "gig", "category": "Not A Real Category",
    })

    with app_module.app.app_context():
        job = app_module.Job.query.filter_by(title="Odd Gig").first()
        assert job.category == "Other"


def test_jobs_endpoint_filters_by_job_type(client):
    import app as app_module

    _post_job_direct("Formal Role")
    with app_module.app.app_context():
        gig = app_module.Job(title="Gig Role", location="X", duration="D", job_type="gig", category="Cleaning")
        app_module.db.session.add(gig)
        app_module.db.session.commit()

    resp = client.get("/jobs?job_type=gig")
    titles = [j["title"] for j in resp.get_json()]
    assert titles == ["Gig Role"]

    resp = client.get("/jobs?job_type=formal")
    titles = [j["title"] for j in resp.get_json()]
    assert titles == ["Formal Role"]

    # Omitted job_type -- today's behavior, both kinds returned.
    resp = client.get("/jobs")
    titles = {j["title"] for j in resp.get_json()}
    assert titles == {"Formal Role", "Gig Role"}
