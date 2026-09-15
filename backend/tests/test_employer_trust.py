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


# ----------------- Composite trust_score / trust_tier -----------------
# Real gap found reviewing this feature rather than assuming it was
# done: the score was advertised as based on verification, reports, and
# application outcomes, but only ever actually used ratings -- the other
# three were separate, unconnected badges/admin-only data. These pin the
# new composite scoring in _employer_trust_summary().

def test_cold_start_employer_gets_a_neutral_not_punitive_trust_score(client):
    """No ratings, no reports, unverified, no applications -- must not
    be 0 (that would look identical to a genuinely bad actor) or a
    misleadingly high number either."""
    import app as app_module

    _register_employer(client, email="blank@test.com", name="Blank Co")

    with app_module.app.app_context():
        employer = app_module.Employer.query.filter_by(email="blank@test.com").first()
        summary = app_module._employer_trust_summary(employer.id)
        # ratings(24, shrunk to neutral 3/5) + verification(10, unverified)
        # + reports(20, none) + outcomes(10, insufficient data) = 64.
        assert summary["trust_score"] == 64
        assert summary["trust_tier"] == "fair"


def test_verified_employer_scores_higher_than_unverified_otherwise_identical(client):
    import app as app_module

    _register_employer(client, email="verified@test.com", name="Verified Co")
    _register_employer(client, email="unverified@test.com", name="Unverified Co")

    with app_module.app.app_context():
        verified = app_module.Employer.query.filter_by(email="verified@test.com").first()
        unverified = app_module.Employer.query.filter_by(email="unverified@test.com").first()
        verified.verification_status = "verified"
        app_module.db.session.commit()

        verified_score = app_module._employer_trust_summary(verified.id)["trust_score"]
        unverified_score = app_module._employer_trust_summary(unverified.id)["trust_score"]
        assert verified_score == unverified_score + 10


def test_rejected_verification_scores_lower_than_unverified(client):
    import app as app_module

    _register_employer(client, email="rejected@test.com", name="Rejected Co")

    with app_module.app.app_context():
        employer = app_module.Employer.query.filter_by(email="rejected@test.com").first()
        employer.verification_status = "rejected"
        app_module.db.session.commit()
        assert app_module._employer_trust_summary(employer.id)["trust_score"] == 54  # 64 - 10


def test_open_report_lowers_trust_score(client):
    import app as app_module

    _register_employer(client, email="reported@test.com", name="Reported Co")
    reporter = register_user(client, email="reporterx@test.com", phone="288888881")

    with app_module.app.app_context():
        employer = app_module.Employer.query.filter_by(email="reported@test.com").first()
        baseline = app_module._employer_trust_summary(employer.id)["trust_score"]

        app_module.db.session.add(app_module.EmployerReport(
            employer_id=employer.id, reporter_user_id=reporter["user"]["id"],
            category="scam", status="open",
        ))
        app_module.db.session.commit()

        after = app_module._employer_trust_summary(employer.id)
        assert after["trust_score"] == baseline - 4
        assert after["trust_tier"] in ("fair", "caution")


def test_dismissed_report_does_not_lower_trust_score(client):
    """A report an admin already determined was unfounded must not keep
    counting against the employer forever."""
    import app as app_module

    _register_employer(client, email="cleared@test.com", name="Cleared Co")
    reporter = register_user(client, email="reportery@test.com", phone="288888882")

    with app_module.app.app_context():
        employer = app_module.Employer.query.filter_by(email="cleared@test.com").first()
        baseline = app_module._employer_trust_summary(employer.id)["trust_score"]

        app_module.db.session.add(app_module.EmployerReport(
            employer_id=employer.id, reporter_user_id=reporter["user"]["id"],
            category="other", status="dismissed",
        ))
        app_module.db.session.commit()

        after = app_module._employer_trust_summary(employer.id)["trust_score"]
        assert after == baseline


def test_multiple_open_reports_cap_the_penalty_at_the_reports_component_max(client):
    import app as app_module

    _register_employer(client, email="verybad@test.com", name="Very Bad Co")
    reporter = register_user(client, email="reporterz@test.com", phone="288888883")

    with app_module.app.app_context():
        employer = app_module.Employer.query.filter_by(email="verybad@test.com").first()
        for _ in range(10):
            app_module.db.session.add(app_module.EmployerReport(
                employer_id=employer.id, reporter_user_id=reporter["user"]["id"],
                category="scam", status="open",
            ))
        app_module.db.session.commit()

        # 10 open reports at -4 each (-40) is more than the 20-point reports
        # component itself -- must floor at losing exactly those 20 points,
        # not go further negative and drag other components down with it.
        after = app_module._employer_trust_summary(employer.id)
        assert after["trust_score"] == 64 - 20


def _apply_as_new_worker(client, job_id, email):
    import app as app_module

    phone = str(abs(hash(email)) % 900000000 + 100000000)
    worker = register_user(client, email=email, phone=phone)
    client.post(
        "/apply", data={"job_id": str(job_id)},
        headers=auth_headers(worker["access_token"]), content_type="multipart/form-data",
    )
    with app_module.app.app_context():
        return app_module.Application.query.filter_by(job_id=job_id, user_id=worker["user"]["id"]).first().id


def test_ghosting_every_applicant_lowers_trust_score_once_theres_a_real_sample(client):
    import app as app_module

    _register_employer(client, email="ghoster@test.com", name="Ghoster Co")
    job_id, employer_id = _post_gig_job(client, title="Ghosted Gig")

    baseline = None
    with app_module.app.app_context():
        baseline = app_module._employer_trust_summary(employer_id)["trust_score"]

    for i in range(3):
        _apply_as_new_worker(client, job_id, f"ghosted{i}@test.com")
    # All 3 left at the default "Pending" -- never responded to.

    with app_module.app.app_context():
        after = app_module._employer_trust_summary(employer_id)
        # outcomes component drops from the neutral 10 (insufficient data)
        # to 0 (a real 0% response rate) once the minimum sample is met.
        assert after["trust_score"] == baseline - 10


def test_responding_to_applicants_keeps_the_outcomes_component_full(client):
    import app as app_module

    _register_employer(client, email="responsive@test.com", name="Responsive Co")
    job_id, employer_id = _post_gig_job(client, title="Responsive Gig")

    app_ids = [_apply_as_new_worker(client, job_id, f"responded{i}@test.com") for i in range(3)]

    with app_module.app.app_context():
        for app_id in app_ids:
            app_module.db.session.get(app_module.Application, app_id).status = "Rejected"
        app_module.db.session.commit()

        # outcomes component rises from the neutral 10 (insufficient data)
        # to the full 20 (a real 100% response rate) once the minimum
        # sample is met -- score rises above the 64 cold-start baseline,
        # it does not just stay flat.
        after = app_module._employer_trust_summary(employer_id)
        assert after["trust_score"] == 74


def test_a_couple_of_pending_applications_do_not_yet_affect_the_score(client):
    """Below _MIN_APPLICATIONS_FOR_RESPONSE_RATE -- must stay at the
    neutral default rather than being punished by a tiny, noisy sample."""
    import app as app_module

    _register_employer(client, email="smallbatch@test.com", name="Small Batch Co")
    job_id, employer_id = _post_gig_job(client, title="Small Batch Gig")

    _apply_as_new_worker(client, job_id, "onlyone@test.com")

    with app_module.app.app_context():
        after = app_module._employer_trust_summary(employer_id)
        assert after["trust_score"] == 64


def test_older_ratings_are_weighted_less_than_recent_ones(client):
    import app as app_module
    from datetime import timedelta

    _register_employer(client, email="recency@test.com", name="Recency Co")
    job_id, employer_id = _post_gig_job(client, title="Recency Gig")
    app_id = _complete_gig_and_rate_employer(client, job_id, 1, "recencyworker@test.com")

    with app_module.app.app_context():
        recent_score = app_module._employer_trust_summary(employer_id)["trust_score"]

        rating = app_module.Rating.query.filter_by(application_id=app_id, direction="worker_to_employer").first()
        rating.created_at = app_module.datetime.utcnow() - timedelta(days=_two_years_in_days())
        app_module.db.session.commit()

        aged_score = app_module._employer_trust_summary(employer_id)["trust_score"]
        # A very old, bad (1/5) rating has decayed toward negligible
        # weight -- the score should recover back up toward the neutral
        # cold-start baseline (64) rather than staying dragged down by a
        # single 1-star rating from years ago.
        assert aged_score > recent_score


def _two_years_in_days():
    return 365 * 2


def test_portal_job_listing_shows_the_trust_badge_when_not_good(client):
    _register_employer(client, email="badgeco@test.com", name="Badge Co")
    job_id, employer_id = _post_gig_job(client, title="Badge Gig")
    reporter = register_user(client, email="badgereporter@test.com", phone="288888884")
    import app as app_module
    with app_module.app.app_context():
        employer = app_module.db.session.get(app_module.Employer, employer_id)
        employer.verification_status = "rejected"
        for _ in range(5):
            app_module.db.session.add(app_module.EmployerReport(
                employer_id=employer_id, reporter_user_id=reporter["user"]["id"],
                category="scam", status="open",
            ))
        app_module.db.session.commit()

    resp = client.get("/portal?job_type=gig")
    assert "Use caution" in resp.get_data(as_text=True)


def test_portal_job_listing_hides_the_trust_badge_for_a_good_tier_employer(client):
    """A brand-new, unreported, unverified employer lands in "fair", not
    "good" -- must NOT show a badge here at all (see the "only when not
    good" comment in job_screen.dart's own mirror of this decision) until
    genuinely earning "good"."""
    _register_employer(client, email="freshbadge@test.com", name="Fresh Badge Co")
    _post_gig_job(client, title="Fresh Badge Gig")

    resp = client.get("/portal?job_type=gig")
    body = resp.get_data(as_text=True)
    assert "Fresh Badge Gig" in body
    assert "Use caution" not in body


def test_portal_job_detail_shows_the_trust_badge(client):
    _register_employer(client, email="detailbadge@test.com", name="Detail Badge Co")
    job_id, _employer_id = _post_gig_job(client, title="Detail Badge Gig")

    resp = client.get(f"/portal/jobs/{job_id}")
    assert "Fair standing" in resp.get_data(as_text=True)


def test_employer_dashboard_shows_own_trust_badge(client):
    _register_employer(client, email="ownbadge@test.com", name="Own Badge Co")
    resp = client.get("/employer")
    body = resp.get_data(as_text=True)
    assert "Fair standing" in body
    assert "Your trust score" in body
