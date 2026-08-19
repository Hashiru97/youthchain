"""
The single most important regression test in the job-scanner feature:
GET /jobs and both branches of GET /api/match_jobs/<id> must never
return a source="scraped" Job, and GET /api/discover_jobs must never
return a source="employer" one. Home and Discover are deliberately
separate feeds (see app.py's own comments on _search_jobs/api_match_jobs)
-- this is what actually protects that contract from silently drifting.
"""
from datetime import date, timedelta

from conftest import register_user, auth_headers


def _make_job(app_module, source="employer", title="Job", **kwargs):
    with app_module.app.app_context():
        job = app_module.Job(title=title, location="Freetown", duration="Full-time", source=source, **kwargs)
        app_module.db.session.add(job)
        app_module.db.session.commit()
        return job.id


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


def test_get_jobs_never_returns_scraped_rows(client):
    import app as app_module

    _make_job(app_module, source="employer", title="Employer Job")
    _make_scraped_job(app_module, title="Scraped Job")

    resp = client.get("/jobs")
    titles = [j["title"] for j in resp.get_json()]
    assert "Employer Job" in titles
    assert "Scraped Job" not in titles


def test_discover_jobs_never_returns_employer_rows(client):
    import app as app_module

    _make_job(app_module, source="employer", title="Employer Job")
    _make_scraped_job(app_module, title="Scraped Job")

    user = register_user(client)
    resp = client.get("/api/discover_jobs", headers=auth_headers(user["access_token"]))
    assert resp.status_code == 200
    titles = [j["title"] for j in resp.get_json()]
    assert "Scraped Job" in titles
    assert "Employer Job" not in titles


def test_discover_jobs_returns_empty_array_not_an_error_when_scanner_hasnt_run_yet(client):
    """The exact "scanner hasn't produced listings yet" state -- a fresh
    install, or a deployment where no source has completed a scan yet.
    Must be a plain 200 with an empty JSON array, not a 404/500 -- the
    mobile app's empty-state UI (see discover_screen_test.dart) depends
    on being able to json.decode() this response the same as any other."""
    user = register_user(client)
    resp = client.get("/api/discover_jobs", headers=auth_headers(user["access_token"]))
    assert resp.status_code == 200
    assert resp.get_json() == []


def test_discover_jobs_requires_auth(client):
    resp = client.get("/api/discover_jobs")
    assert resp.status_code == 401


def test_discover_job_includes_scanner_fields(client):
    import app as app_module

    job_id = _make_scraped_job(
        app_module, title="Scraped Job",
        company_name="Acme SL", salary="Negotiable",
        description="Do things.", apply_url="https://careers.sl/x",
        employment_type="Full-time",
    )
    user = register_user(client)
    resp = client.get("/api/discover_jobs", headers=auth_headers(user["access_token"]))
    job = next(j for j in resp.get_json() if j["id"] == job_id)
    assert job["source"] == "scraped"
    assert job["company_name"] == "Acme SL"
    assert job["salary"] == "Negotiable"
    assert job["description"] == "Do things."
    assert job["apply_url"] == "https://careers.sl/x"
    assert job["employment_type"] == "Full-time"
    assert job["source_name"] == "Careers.sl"


def test_match_jobs_no_candidate_profile_never_returns_scraped_rows(client):
    """The api_match_jobs() fallback branch (no candidate profile yet) —
    bypasses _search_jobs() entirely, so this is the one call site that
    could silently leak a scraped job into Home if the explicit
    source="employer" filter were ever removed."""
    import app as app_module

    _make_job(app_module, source="employer", title="Employer Job")
    _make_scraped_job(app_module, title="Scraped Job")

    user = register_user(client)
    resp = client.get(
        f"/api/match_jobs/{user['user']['id']}",
        headers=auth_headers(user["access_token"]),
    )
    assert resp.status_code == 200
    titles = [j["title"] for j in resp.get_json()["jobs"]]
    assert "Employer Job" in titles
    assert "Scraped Job" not in titles


def test_match_jobs_with_candidate_profile_never_returns_scraped_rows(client):
    """The api_match_jobs() ranked-matching branch (candidate profile
    exists) — the second of the two call sites the Home-feed guard had
    to be added to."""
    import app as app_module

    _make_job(app_module, source="employer", title="Employer Job")
    _make_scraped_job(app_module, title="Scraped Job")

    user = register_user(client)
    # Creating a Candidate row is what routes api_match_jobs into its
    # ranked-matching branch instead of the no-candidate fallback.
    with app_module.app.app_context():
        candidate = app_module.Candidate(email=user["user"]["email"], user_id=user["user"]["id"], skills="python")
        app_module.db.session.add(candidate)
        app_module.db.session.commit()
        candidate_id = candidate.id

    resp = client.get(
        f"/api/match_jobs/{candidate_id}",
        headers=auth_headers(user["access_token"]),
    )
    assert resp.status_code == 200
    titles = [j["title"] for j in resp.get_json()["jobs"]]
    assert "Employer Job" in titles
    assert "Scraped Job" not in titles


def test_jobs_full_text_search_does_not_cross_source_boundary(client):
    """The FTS/tsvector branch of _search_jobs runs a raw SQL query
    against the whole job table before source is re-applied -- this is
    the regression test for that specific fix."""
    import app as app_module

    _make_job(app_module, source="employer", title="Solar Panel Installer")
    _make_scraped_job(app_module, title="Solar Panel Installer")

    resp = client.get("/jobs?q=solar")
    results = resp.get_json()
    assert len(results) == 1
    assert results[0]["source"] == "employer"


def test_discover_jobs_excludes_expired_listings_by_default(client):
    import app as app_module

    _make_scraped_job(app_module, title="Still Open", application_deadline=date.today() + timedelta(days=5))
    _make_scraped_job(app_module, title="No Deadline Stated", application_deadline=None)
    _make_scraped_job(app_module, title="Past Deadline", application_deadline=date.today() - timedelta(days=1))

    user = register_user(client)
    resp = client.get("/api/discover_jobs", headers=auth_headers(user["access_token"]))
    titles = {j["title"] for j in resp.get_json()}
    assert titles == {"Still Open", "No Deadline Stated"}


def test_discover_jobs_old_returns_only_expired_listings(client):
    import app as app_module

    _make_scraped_job(app_module, title="Still Open", application_deadline=date.today() + timedelta(days=5))
    _make_scraped_job(app_module, title="Past Deadline", application_deadline=date.today() - timedelta(days=1))

    user = register_user(client)
    resp = client.get("/api/discover_jobs/old", headers=auth_headers(user["access_token"]))
    titles = {j["title"] for j in resp.get_json()}
    assert titles == {"Past Deadline"}


def test_discover_jobs_old_requires_auth(client):
    resp = client.get("/api/discover_jobs/old")
    assert resp.status_code == 401


def test_a_job_expiring_today_is_not_yet_expired(client):
    """The boundary case: a deadline of today is still a valid day to
    apply, not already past it -- >= today, not > today."""
    import app as app_module

    _make_scraped_job(app_module, title="Due Today", application_deadline=date.today())

    user = register_user(client)
    resp = client.get("/api/discover_jobs", headers=auth_headers(user["access_token"]))
    titles = {j["title"] for j in resp.get_json()}
    assert titles == {"Due Today"}

    resp_old = client.get("/api/discover_jobs/old", headers=auth_headers(user["access_token"]))
    assert resp_old.get_json() == []


def test_job_to_dict_includes_deadline_and_is_expired(client):
    import app as app_module

    job_id = _make_scraped_job(app_module, title="Past Deadline", application_deadline=date.today() - timedelta(days=1))
    user = register_user(client)
    resp = client.get("/api/discover_jobs/old", headers=auth_headers(user["access_token"]))
    job = next(j for j in resp.get_json() if j["id"] == job_id)
    assert job["application_deadline"] == (date.today() - timedelta(days=1)).isoformat()
    assert job["is_expired"] is True


def test_employer_jobs_are_unaffected_by_the_expiry_filter(client):
    """Employer-posted jobs never set application_deadline (post_job()
    has no such field) -- the default expired_only=False filter must be
    a pure no-op for GET /jobs, not accidentally start excluding
    employer jobs that simply have no deadline."""
    import app as app_module

    _make_job(app_module, source="employer", title="Employer Job")
    resp = client.get("/jobs")
    titles = {j["title"] for j in resp.get_json()}
    assert titles == {"Employer Job"}


# ---------------------------------------------------------------------
# Discover-feed candidate ranking (extends Home's own skills-overlap
# scoring, see api_match_jobs, to scraped listings once they have a real
# required_skills value -- see scanner/claude_extractor.py's own field).

def _make_candidate(app_module, user_id, skills=""):
    with app_module.app.app_context():
        candidate = app_module.Candidate(email=f"candidate{user_id}@test.com", user_id=user_id, skills=skills)
        app_module.db.session.add(candidate)
        app_module.db.session.commit()
        return candidate.id


def test_discover_jobs_ranked_by_skill_overlap_when_candidate_exists(client):
    import app as app_module

    _make_scraped_job(app_module, title="Great Match", required_skills="Python, Excel")
    _make_scraped_job(app_module, title="No Match", required_skills="Welding, Carpentry")
    _make_scraped_job(app_module, title="No Skills Stated", required_skills=None)

    user = register_user(client)
    _make_candidate(app_module, user["user"]["id"], skills="python, excel")

    resp = client.get("/api/discover_jobs", headers=auth_headers(user["access_token"]))
    jobs = resp.get_json()
    titles_in_order = [j["title"] for j in jobs]
    assert titles_in_order[0] == "Great Match"
    scores = {j["title"]: j["score"] for j in jobs}
    assert scores["Great Match"] == 100
    assert scores["No Match"] == 0
    assert scores["No Skills Stated"] == 0


def test_discover_jobs_unranked_when_no_candidate_profile(client):
    """No profile yet means the "score" key is absent entirely, not
    present-but-zero -- same convention job_screen.dart's own
    `hasScore = job.containsKey("score")` already relies on for Home, so
    a card doesn't show a misleading "0% match" badge before ranking is
    even meaningful."""
    import app as app_module

    _make_scraped_job(app_module, title="Some Job", required_skills="Python")
    user = register_user(client)

    resp = client.get("/api/discover_jobs", headers=auth_headers(user["access_token"]))
    jobs = resp.get_json()
    assert all("score" not in j for j in jobs)


def test_discover_jobs_search_disables_ranking(client):
    """Same rule Home's own /api/match_jobs follows (BL-45): ranking and
    free-text search don't mix. An active q= search must omit "score"
    entirely, not return a skills-ranked subset of the search results."""
    import app as app_module

    _make_scraped_job(app_module, title="Python Developer", required_skills="Python, Excel")
    user = register_user(client)
    _make_candidate(app_module, user["user"]["id"], skills="python, excel")

    resp = client.get("/api/discover_jobs?q=python", headers=auth_headers(user["access_token"]))
    jobs = resp.get_json()
    assert len(jobs) == 1
    assert "score" not in jobs[0]


def test_discover_jobs_ranking_does_not_include_expired_listings(client):
    """Ranking bypasses _search_jobs()'s own filtering, so the expired-
    listings exclusion has to be re-applied independently -- confirms it
    actually was."""
    import app as app_module

    _make_scraped_job(
        app_module, title="Expired Great Match", required_skills="Python",
        application_deadline=date.today() - timedelta(days=1),
    )
    _make_scraped_job(app_module, title="Active Great Match", required_skills="Python")

    user = register_user(client)
    _make_candidate(app_module, user["user"]["id"], skills="python")

    resp = client.get("/api/discover_jobs", headers=auth_headers(user["access_token"]))
    titles = {j["title"] for j in resp.get_json()}
    assert titles == {"Active Great Match"}


def test_discover_jobs_ranking_respects_pagination_across_the_full_set(client):
    """Real correctness requirement, not just a nice-to-have: ranking
    must sort the FULL matching set before paginating, not just re-order
    within whatever _search_jobs()'s own SQL-level LIMIT/OFFSET happened
    to fetch first -- otherwise a great match sitting past the first
    page's cutoff (by recency) could never surface at all."""
    import app as app_module

    # Ten decoys, all more "recent" (higher id) than the real match below,
    # none of which the candidate's skills overlap with at all.
    for i in range(10):
        _make_scraped_job(app_module, title=f"Decoy {i}", required_skills="Welding")
    _make_scraped_job(app_module, title="Buried Great Match", required_skills="Python")

    user = register_user(client)
    _make_candidate(app_module, user["user"]["id"], skills="python")

    resp = client.get("/api/discover_jobs?limit=3", headers=auth_headers(user["access_token"]))
    jobs = resp.get_json()
    assert len(jobs) == 3
    assert jobs[0]["title"] == "Buried Great Match"
    assert jobs[0]["score"] == 100
