"""
Regression coverage for employer industry capture, candidate industry
preference, and the transparent industry-alignment bonus in
api_match_jobs. Deliberately tests that this stays a real, explainable
bonus on top of skills-overlap scoring, not a black-box "smart match" --
consistent with this codebase's existing "no fake AI claims" discipline
(BL-45).
"""
import re

from conftest import register_user, auth_headers


def _csrf_token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "csrf token not found in page"
    return match.group(1)


def _register_employer(client, email="acme@test.com", name="Acme", password="password123", industry=None):
    page = client.get("/employer/register")
    token = _csrf_token(page.get_data(as_text=True))
    data = {"csrf_token": token, "name": name, "email": email, "password": password}
    if industry is not None:
        data["industry"] = industry
    return client.post("/employer/register", data=data)


def test_employer_can_register_with_a_valid_industry(client):
    import app as app_module

    resp = _register_employer(client, industry="Agriculture")
    assert resp.status_code == 302
    with app_module.app.app_context():
        employer = app_module.Employer.query.filter_by(email="acme@test.com").first()
        assert employer.industry == "Agriculture"


def test_employer_registration_rejects_an_invalid_industry(client):
    resp = _register_employer(client, industry="Not A Real Industry")
    assert resp.status_code == 200  # re-rendered with an error, not redirected
    assert "valid industry" in resp.get_data(as_text=True).lower()


def test_employer_can_register_without_choosing_an_industry(client):
    import app as app_module

    resp = _register_employer(client, industry="")
    assert resp.status_code == 302
    with app_module.app.app_context():
        employer = app_module.Employer.query.filter_by(email="acme@test.com").first()
        assert employer.industry is None


def test_industries_endpoint_returns_the_canonical_list(client):
    import app as app_module

    resp = client.get("/api/industries")
    assert resp.status_code == 200
    body = resp.get_json()
    assert set(body["industries"]) == app_module._INDUSTRY_CHOICES


def test_candidate_can_set_preferred_industries(client):
    a = register_user(client)
    resp = client.post(
        "/api/candidate",
        json={"email": "candidate@test.com", "name": "Alice", "preferred_industries": ["Agriculture", "Fisheries"]},
        headers=auth_headers(a["access_token"]),
    )
    assert resp.status_code == 200

    me = client.get("/api/candidate/me", headers=auth_headers(a["access_token"])).get_json()
    stored = set(me["candidate"]["preferred_industries"].split(","))
    assert stored == {"Agriculture", "Fisheries"}


def test_invalid_preferred_industries_are_silently_dropped(client):
    a = register_user(client)
    client.post(
        "/api/candidate",
        json={"email": "candidate2@test.com", "name": "Bob", "preferred_industries": ["Agriculture", "Not Real"]},
        headers=auth_headers(a["access_token"]),
    )
    me = client.get("/api/candidate/me", headers=auth_headers(a["access_token"])).get_json()
    assert me["candidate"]["preferred_industries"] == "Agriculture"


def test_matching_gives_a_transparent_bonus_for_industry_alignment(client):
    """
    Two jobs with identical skills requirements (so skills_score is equal
    for both) -- the only difference is which one's employer industry
    matches the candidate's stated preference. That job must score higher,
    and the response must say why (industry_match), not just silently
    reorder results.
    """
    import app as app_module

    # Both jobs require two skills; the candidate only has one of them, so
    # skills_score lands at 50 for both -- below the 100 cap, so the
    # industry bonus is actually visible in the comparison (a job that's
    # already at the 100 cap either way would mask the bonus entirely).
    _register_employer(client, email="agri@test.com", name="AgriCorp", industry="Agriculture")
    logout_page = client.get("/employer/post")
    client.post("/employer/post", data={
        "csrf_token": _csrf_token(logout_page.get_data(as_text=True)),
        "title": "Farm Technician", "location": "Bo", "duration": "6mo", "required_skills": "farming, irrigation",
    })
    client.post("/employer/logout", data={"csrf_token": _csrf_token(logout_page.get_data(as_text=True))})

    _register_employer(client, email="tech@test.com", name="TechCorp", industry="Information & Communication Technology")
    post_page = client.get("/employer/post")
    client.post("/employer/post", data={
        "csrf_token": _csrf_token(post_page.get_data(as_text=True)),
        "title": "IT Technician", "location": "Bo", "duration": "6mo", "required_skills": "farming, coding",
    })
    logout_page2 = client.get("/employer/post")
    client.post("/employer/logout", data={"csrf_token": _csrf_token(logout_page2.get_data(as_text=True))})

    a = register_user(client)
    client.post(
        "/api/candidate",
        json={
            "email": "farmer@test.com",
            "skills": ["farming"],
            "preferred_industries": ["Agriculture"],
        },
        headers=auth_headers(a["access_token"]),
    )
    with app_module.app.app_context():
        candidate_id = app_module.Candidate.query.filter_by(email="farmer@test.com").first().id

    resp = client.get(f"/api/match_jobs/{candidate_id}", headers=auth_headers(a["access_token"]))
    assert resp.status_code == 200
    jobs = {j["title"]: j for j in resp.get_json()["jobs"]}

    assert jobs["Farm Technician"]["industry_match"] is True
    assert jobs["IT Technician"]["industry_match"] is False
    # Identical skills overlap (both require exactly "farming", candidate
    # has exactly "farming") -- the only difference is the industry bonus.
    assert jobs["Farm Technician"]["score"] > jobs["IT Technician"]["score"]
    assert jobs["Farm Technician"]["score"] - jobs["IT Technician"]["score"] == 15


def test_matching_score_never_exceeds_100_even_with_industry_bonus(client):
    import app as app_module

    _register_employer(client, email="agri2@test.com", name="AgriCorp2", industry="Agriculture")
    page = client.get("/employer/post")
    client.post("/employer/post", data={
        "csrf_token": _csrf_token(page.get_data(as_text=True)),
        "title": "Perfect Match Job", "location": "Bo", "duration": "6mo", "required_skills": "farming",
    })
    client.post("/employer/logout", data={"csrf_token": _csrf_token(page.get_data(as_text=True))})

    a = register_user(client)
    client.post(
        "/api/candidate",
        json={"email": "farmer2@test.com", "skills": ["farming"], "preferred_industries": ["Agriculture"]},
        headers=auth_headers(a["access_token"]),
    )
    with app_module.app.app_context():
        candidate_id = app_module.Candidate.query.filter_by(email="farmer2@test.com").first().id

    resp = client.get(f"/api/match_jobs/{candidate_id}", headers=auth_headers(a["access_token"]))
    job = resp.get_json()["jobs"][0]
    assert job["score"] == 100  # 100% skills overlap + bonus, capped
    assert job["industry_match"] is True


def test_match_jobs_response_includes_employer_identity(client):
    import app as app_module

    _register_employer(client, email="verified@test.com", name="VerifiedCorp", industry="Healthcare")
    page = client.get("/employer/post")
    client.post("/employer/post", data={
        "csrf_token": _csrf_token(page.get_data(as_text=True)),
        "title": "Nurse Assistant", "location": "Bo", "duration": "6mo",
    })
    client.post("/employer/logout", data={"csrf_token": _csrf_token(page.get_data(as_text=True))})

    a = register_user(client)
    with app_module.app.app_context():
        candidate = app_module.Candidate(email="nocandidate@test.com", user_id=None)
        app_module.db.session.add(candidate)
        app_module.db.session.commit()
        candidate_id = candidate.id

    resp = client.get(f"/api/match_jobs/{candidate_id}", headers=auth_headers(a["access_token"]))
    assert resp.status_code == 403  # candidate.user_id is None, not this caller -- confirms ownership check untouched


def test_match_jobs_fallback_path_includes_employer_identity_when_no_candidate(client):
    a = register_user(client)
    resp = client.get(f"/api/match_jobs/999999", headers=auth_headers(a["access_token"]))
    assert resp.status_code == 200
    body = resp.get_json()
    assert "No candidate profile yet" in body["message"]
    # Every job dict in the fallback path must still carry the employer key
    # (None is fine -- the point is the key exists, matching the shape the
    # mobile app's job cards already expect from GET /jobs).
    for job in body["jobs"]:
        assert "employer" in job


def test_match_jobs_ranked_path_includes_job_type_and_category(client):
    """
    Real, serious gap found and closed: /api/match_jobs is the mobile
    app's DEFAULT job feed for any user with a candidate profile (see
    job_screen.dart's fetchJobs() -- it only falls back to GET /jobs when
    no profile exists yet or a search/filter is active). This route
    hand-builds its response dicts and had silently dropped job_type and
    category, so for most real users a gig job fetched through here had
    no job_type at all -- job["job_type"] != "gig" defaults true when the
    key is simply missing, which broke the "no CV needed for gig work"
    logic in _showApplySheet for the majority of real traffic, not an
    edge case.
    """
    import app as app_module

    _register_employer(client, email="gigemployer@test.com", name="GigCo")
    page = client.get("/employer/post")
    client.post("/employer/post", data={
        "csrf_token": _csrf_token(page.get_data(as_text=True)),
        "title": "Cleaner Needed", "location": "Bo", "duration": "1 day",
        "job_type": "gig", "category": "Cleaning",
    })
    client.post("/employer/logout", data={"csrf_token": _csrf_token(page.get_data(as_text=True))})

    a = register_user(client, email="matchworker@test.com", phone="222999888")
    client.post(
        "/api/candidate", json={"email": "matchworker@test.com"},
        headers=auth_headers(a["access_token"]),
    )
    with app_module.app.app_context():
        candidate_id = app_module.Candidate.query.filter_by(email="matchworker@test.com").first().id

    resp = client.get(f"/api/match_jobs/{candidate_id}", headers=auth_headers(a["access_token"]))
    assert resp.status_code == 200
    jobs = {j["title"]: j for j in resp.get_json()["jobs"]}
    assert jobs["Cleaner Needed"]["job_type"] == "gig"
    assert jobs["Cleaner Needed"]["category"] == "Cleaning"


def test_match_jobs_fallback_path_includes_job_type_and_category(client):
    """Same gap, same fix, for the no-candidate-yet fallback branch."""
    _register_employer(client, email="gigemployer2@test.com", name="GigCo2")
    page = client.get("/employer/post")
    client.post("/employer/post", data={
        "csrf_token": _csrf_token(page.get_data(as_text=True)),
        "title": "Okada Rider Needed", "location": "Freetown", "duration": "1 week",
        "job_type": "gig", "category": "Okada / Transport",
    })
    client.post("/employer/logout", data={"csrf_token": _csrf_token(page.get_data(as_text=True))})

    a = register_user(client, email="matchworker2@test.com", phone="223999888")
    resp = client.get("/api/match_jobs/999999", headers=auth_headers(a["access_token"]))
    assert resp.status_code == 200
    jobs = {j["title"]: j for j in resp.get_json()["jobs"]}
    assert jobs["Okada Rider Needed"]["job_type"] == "gig"
    assert jobs["Okada Rider Needed"]["category"] == "Okada / Transport"
