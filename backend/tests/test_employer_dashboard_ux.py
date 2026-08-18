"""
Regression coverage for the employer portal UX pass: the shared
employer_shell.html nav (unread-message badge, verification banner) and
the first-time onboarding checklist on employer_dashboard.html. Real gap
found via a full employer-portal review: a brand-new employer landed on a
near-empty dashboard with no explanation of what to do first, and an
employer with unread applicant messages had zero indication anywhere in
the UI that a reply was waiting -- messages were only reachable by
drilling into a specific job's specific applicant row.
"""
import app as app_module
from test_employer import register_employer, _csrf_token


def _seed_job(employer_email, title="Junior Dev", location="Freetown", duration="3 months"):
    with app_module.app.app_context():
        employer = app_module.Employer.query.filter_by(email=employer_email).first()
        job = app_module.Job(title=title, location=location, duration=duration, employer_id=employer.id)
        app_module.db.session.add(job)
        app_module.db.session.commit()
        return job.id


def _seed_application_with_message(job_id, applicant_email="applicant@test.com", unread_from_user=True):
    with app_module.app.app_context():
        phone = "7" + str(abs(hash(applicant_email)))[:8]
        applicant = app_module.User(
            name="Applicant One", phone=phone, email=applicant_email, password_hash="x",
            consent_accepted_at=app_module.datetime.utcnow(),
        )
        app_module.db.session.add(applicant)
        app_module.db.session.commit()

        application = app_module.Application(user_id=applicant.id, job_id=job_id, cv_file="cv.pdf")
        app_module.db.session.add(application)
        app_module.db.session.commit()

        message = app_module.Message(
            application_id=application.id,
            sender_type="user",
            sender_user_id=applicant.id,
            body="Hi, is this role still open?",
            read=not unread_from_user,
        )
        app_module.db.session.add(message)
        app_module.db.session.commit()
        return application.id


def test_new_employer_sees_onboarding_checklist_with_both_steps_incomplete(client):
    register_employer(client, email="fresh@test.com")

    dash = client.get("/employer").get_data(as_text=True)
    assert "employer-onboarding" in dash
    assert "Verify your organization" in dash
    assert "Post your first job" in dash


def test_individual_employer_sees_identity_language_not_organization_language(client):
    """
    Real inconsistency found via re-checking this page after adding
    Employer.account_type: the onboarding step and the persistent
    verify-banner both hardcoded "organization" regardless of account
    type, so an individual hiring informally (a household needing a
    cleaner, no organization at all) saw copy that didn't describe them.
    """
    register_employer(client, email="individual@test.com", account_type="individual")

    dash = client.get("/employer").get_data(as_text=True)
    assert "Verify your identity" in dash
    assert "Verify your organization" not in dash
    assert "national ID, voter's card, or driver's license" in dash

    # The persistent verify-banner (shown on every non-verification page,
    # not just the dashboard) needs the same fix. Jinja HTML-escapes the
    # apostrophe (' -> &#39;) by default -- matching the raw escaped form
    # here rather than the literal apostrophe, which never appears in the
    # actual response.
    post_page = client.get("/employer/post").get_data(as_text=True)
    assert "You&#39;re not verified yet." in post_page
    assert "Your organization isn&#39;t verified yet." not in post_page


def test_business_employer_still_sees_organization_language(client):
    """The business track's copy must stay exactly as it was -- this is
    the regression guard for the fix above."""
    register_employer(client, email="business@test.com", account_type="business")

    dash = client.get("/employer").get_data(as_text=True)
    assert "Verify your organization" in dash
    assert "Verify your identity" not in dash


def test_onboarding_checklist_hidden_once_verified_and_job_posted(client):
    register_employer(client, email="graduated@test.com")
    _seed_job("graduated@test.com")

    with app_module.app.app_context():
        employer = app_module.Employer.query.filter_by(email="graduated@test.com").first()
        employer.verification_status = "verified"
        app_module.db.session.commit()

    dash = client.get("/employer").get_data(as_text=True)
    assert "employer-onboarding" not in dash


def test_verify_banner_shown_for_unverified_employer_but_not_on_verification_page(client):
    register_employer(client, email="unverified@test.com")

    dash = client.get("/employer").get_data(as_text=True)
    assert "employer-verify-banner" in dash

    verification_page = client.get("/employer/verification").get_data(as_text=True)
    assert "employer-verify-banner" not in verification_page


def test_employer_nav_shows_unread_message_badge(client):
    register_employer(client, email="hasmessages@test.com")
    job_id = _seed_job("hasmessages@test.com")
    _seed_application_with_message(job_id, unread_from_user=True)

    dash = client.get("/employer").get_data(as_text=True)
    assert 'class="shell-nav-badge"' in dash
    assert ">1<" in dash.split('class="shell-nav-badge"')[1][:20]


def test_employer_nav_has_no_badge_when_all_messages_read(client):
    register_employer(client, email="allread@test.com")
    job_id = _seed_job("allread@test.com")
    _seed_application_with_message(job_id, unread_from_user=False)

    dash = client.get("/employer").get_data(as_text=True)
    assert 'class="shell-nav-badge"' not in dash


def test_messages_inbox_lists_thread_with_unread_count(client):
    register_employer(client, email="inbox@test.com")
    job_id = _seed_job("inbox@test.com")
    _seed_application_with_message(job_id, applicant_email="threadperson@test.com", unread_from_user=True)

    inbox = client.get("/employer/messages").get_data(as_text=True)
    assert "Applicant One" in inbox
    assert "Junior Dev" in inbox
    assert "Hi, is this role still open?" in inbox
    assert "employer-thread-row unread" in inbox


def test_messages_inbox_empty_state_when_no_applications_exist(client):
    register_employer(client, email="noinbox@test.com")
    _seed_job("noinbox@test.com")

    inbox = client.get("/employer/messages").get_data(as_text=True)
    assert "No conversations yet" in inbox


def test_messages_inbox_fixed_query_count_regardless_of_thread_count(client):
    """
    Pins the N+1 fix in employer_messages_inbox(): query count must stay
    flat as thread count grows, not scale linearly the way a per-thread
    lookup loop would.
    """
    register_employer(client, email="scale@test.com")
    job_id = _seed_job("scale@test.com")
    for i in range(5):
        _seed_application_with_message(job_id, applicant_email=f"scale{i}@test.com", unread_from_user=True)

    from sqlalchemy import event

    query_count = {"n": 0}

    def _count(*args, **kwargs):
        query_count["n"] += 1

    with app_module.app.app_context():
        engine = app_module.db.engine
        event.listen(engine, "before_cursor_execute", _count)
        try:
            client.get("/employer/messages")
        finally:
            event.remove(engine, "before_cursor_execute", _count)

    # 5 fixed queries in the route itself (latest_rows, unread_by_app,
    # last_message_by_app, applications, jobs, users) plus session/auth
    # lookups -- well under a per-thread N+1 blowup, which would exceed 20+
    # for 5 threads.
    assert query_count["n"] < 20
