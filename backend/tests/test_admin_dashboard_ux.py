"""
Regression coverage for the admin console UX pass: real-time "needs
attention" counts (_admin_pending_counts in app.py), the dashboard's
attention panel, and the sidebar's per-queue nav badges. Real gap found
via a full admin-console review: an admin previously had no way to tell
what needed them without clicking into five separate pages one at a time.
"""
import re

import app as app_module
from conftest import register_user


def _csrf_token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "csrf token not found in page"
    return match.group(1)


def _create_admin(email="ops@youthchain.test", password="adminpass123", role="admin"):
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
    return client.post("/admin/login", data={"csrf_token": token, "email": email, "password": password})


def test_admin_pending_counts_reflects_every_queue(client):
    email, password = _create_admin()
    _login_admin(client, email, password)

    # Create one pending item in each of the four queues.
    with app_module.app.app_context():
        employer = app_module.Employer(
            name="Pending Co", email="pendingco@test.com", password_hash="x",
            active=True, verification_status="pending",
        )
        db = app_module.db
        db.session.add(employer)
        db.session.commit()

        report_employer = app_module.Employer(name="Reported Co", email="reported@test.com", password_hash="x", active=True)
        reporter = app_module.User(
            name="Reporter", phone="7001", email="reporter@test.com", password_hash="x",
            consent_accepted_at=app_module.datetime.utcnow(),
        )
        db.session.add_all([report_employer, reporter])
        db.session.commit()
        db.session.add(app_module.EmployerReport(
            reporter_user_id=reporter.id, employer_id=report_employer.id, category="scam", status="open",
        ))

        appeal_employer = app_module.Employer(name="Appealing Co", email="appealing@test.com", password_hash="x", active=False)
        db.session.add(appeal_employer)
        db.session.commit()
        db.session.add(app_module.EmployerAppeal(employer_id=appeal_employer.id, message="please reinstate", status="open"))

        user_a = app_module.User(
            name="Dupe A", phone="7002", email="dupea@test.com", password_hash="x",
            consent_accepted_at=app_module.datetime.utcnow(),
        )
        user_b = app_module.User(
            name="Dupe B", phone="7003", email="dupeb@test.com", password_hash="x",
            consent_accepted_at=app_module.datetime.utcnow(),
        )
        db.session.add_all([user_a, user_b])
        db.session.commit()
        db.session.add(app_module.DuplicateFlag(
            user_id=user_a.id, matched_user_id=user_b.id, reason="similar name", resolved=False,
        ))
        db.session.commit()

    with app_module.app.app_context():
        counts = app_module._admin_pending_counts()

    assert counts["employer_verifications"] == 1
    assert counts["reports"] == 1
    assert counts["appeals"] == 1
    assert counts["duplicate_flags"] == 1


def test_dashboard_shows_attention_panel_with_correct_counts_for_admin_role(client):
    email, password = _create_admin(role="admin")
    _login_admin(client, email, password)

    with app_module.app.app_context():
        employer = app_module.Employer(
            name="Needs Review Co", email="needsreview@test.com", password_hash="x",
            active=True, verification_status="pending",
        )
        app_module.db.session.add(employer)
        app_module.db.session.commit()

    page = client.get("/admin/analytics")
    body = page.get_data(as_text=True)
    assert "Needs your attention" in body
    assert "Employer Verifications" in body
    assert "has-pending" in body  # at least one card is flagged


def test_dashboard_attention_panel_hidden_for_verifier_role(client):
    email, password = _create_admin(role="verifier")
    _login_admin(client, email, password)

    page = client.get("/admin/analytics")
    body = page.get_data(as_text=True)
    assert "Needs your attention" not in body


def test_sidebar_shows_nav_badge_for_a_pending_verification(client):
    email, password = _create_admin()
    _login_admin(client, email, password)

    with app_module.app.app_context():
        employer = app_module.Employer(
            name="Badge Test Co", email="badgetest@test.com", password_hash="x",
            active=True, verification_status="pending",
        )
        app_module.db.session.add(employer)
        app_module.db.session.commit()

    # Any admin-shell page renders the sidebar, not just the dashboard.
    page = client.get("/admin/reports")
    body = page.get_data(as_text=True)
    assert 'class="shell-nav-badge"' in body


def test_sidebar_shows_no_nav_badges_when_all_queues_are_clear(client):
    email, password = _create_admin()
    _login_admin(client, email, password)

    page = client.get("/admin/reports")
    body = page.get_data(as_text=True)
    assert 'class="shell-nav-badge"' not in body


def test_admin_pending_counts_are_not_computed_for_anonymous_visitors(client, monkeypatch):
    """
    The context processor fires on every template render site-wide, not
    just admin pages -- confirms the four extra COUNT queries are
    genuinely skipped (not just visually hidden) when there's no
    admin-role session, so anonymous/youth/employer traffic never pays
    for them.
    """
    called = {"count": 0}
    real_fn = app_module._admin_pending_counts

    def spy():
        called["count"] += 1
        return real_fn()

    monkeypatch.setattr(app_module, "_admin_pending_counts", spy)

    client.get("/admin/login")

    assert called["count"] == 0
