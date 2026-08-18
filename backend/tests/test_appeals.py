"""
Regression coverage for the suspension-appeal feature: the employer-facing
counterpart to admin_suspend_employer/admin_reinstate_employer (see
test_employer.py for the admin-facing half, test_reports.py for how a
suspension gets triggered in the first place). Without this, a suspended
employer has no real path back except an out-of-band channel an admin
happens to notice.
"""
import re

from conftest import register_user


def _csrf_token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "csrf token not found in page"
    return match.group(1)


def _register_employer(client, email="acme@test.com", name="Acme", password="password123"):
    page = client.get("/employer/register")
    token = _csrf_token(page.get_data(as_text=True))
    client.post(
        "/employer/register",
        data={"csrf_token": token, "name": name, "email": email, "password": password},
    )
    return email, password


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


def _suspend(client, employer_id, admin_email="admin_suspend@youthchain.test"):
    admin_email, admin_password = _create_admin(admin_email)
    _login_admin(client, admin_email, admin_password)
    page = client.get("/admin/employer_verifications")
    token = _csrf_token(page.get_data(as_text=True))
    client.post(f"/admin/employers/{employer_id}/suspend", data={"csrf_token": token})
    client.post("/admin/logout", data={"csrf_token": token})


def _submit_appeal(client, email, password, message="I believe this is a mistake."):
    page = client.get("/employer/appeal")
    token = _csrf_token(page.get_data(as_text=True))
    return client.post(
        "/employer/appeal",
        data={"csrf_token": token, "email": email, "password": password, "message": message},
    )


def test_suspended_employer_can_submit_an_appeal(client):
    import app as app_module

    email, password = _register_employer(client)
    with app_module.app.app_context():
        employer_id = app_module.Employer.query.filter_by(email=email).first().id
    _suspend(client, employer_id)

    resp = _submit_appeal(client, email, password)
    assert resp.status_code == 200
    assert "Your appeal has been submitted" in resp.get_data(as_text=True)

    with app_module.app.app_context():
        appeal = app_module.EmployerAppeal.query.filter_by(employer_id=employer_id).first()
        assert appeal is not None
        assert appeal.status == "open"
        assert appeal.message == "I believe this is a mistake."


def test_appeal_rejects_wrong_password(client):
    import app as app_module

    email, _password = _register_employer(client)
    with app_module.app.app_context():
        employer_id = app_module.Employer.query.filter_by(email=email).first().id
    _suspend(client, employer_id)

    resp = _submit_appeal(client, email, "totally-wrong-password")
    assert "Incorrect email or password" in resp.get_data(as_text=True)

    with app_module.app.app_context():
        assert app_module.EmployerAppeal.query.filter_by(employer_id=employer_id).count() == 0


def test_appeal_password_guessing_is_rate_limited_before_any_success(client, monkeypatch):
    """
    Real gap found via a self-audit, not anticipated in advance:
    _appeal_rate_limited (see test_appeal_rate_limited above) only ever
    fires AFTER a correct password + confirmed-suspended state -- it
    throttles spamming legitimate appeals, but does nothing against an
    attacker guessing this account's password against this same endpoint,
    unlike every real login route. Fixed by checking _login_rate_limited
    (keyed appeal:{email}, same mechanism /login already uses) before the
    password is ever checked, regardless of whether it turns out correct.
    """
    import app as app_module

    monkeypatch.setattr(app_module, "_LOGIN_MAX_ATTEMPTS", 2)

    email, password = _register_employer(client)
    with app_module.app.app_context():
        employer_id = app_module.Employer.query.filter_by(email=email).first().id
    _suspend(client, employer_id)

    for _ in range(2):
        resp = _submit_appeal(client, email, "wrong-guess")
        assert "Incorrect email or password" in resp.get_data(as_text=True)

    # Third attempt is throttled -- even though this one uses the REAL
    # password, proving the limiter is keyed on the attempted identity and
    # checked before credentials are verified, not just after a failure streak.
    resp = _submit_appeal(client, email, password)
    assert resp.status_code == 429
    assert "Too many attempts" in resp.get_data(as_text=True)
    with app_module.app.app_context():
        assert app_module.EmployerAppeal.query.filter_by(employer_id=employer_id).count() == 0


def test_active_employer_cannot_appeal(client):
    """An employer who was never suspended has nothing to contest -- appealing
    should be refused, not silently accepted as a no-op action."""
    email, password = _register_employer(client)

    resp = _submit_appeal(client, email, password)
    assert "This account is not currently suspended" in resp.get_data(as_text=True)


def test_appeal_requires_a_message(client):
    import app as app_module

    email, password = _register_employer(client)
    with app_module.app.app_context():
        employer_id = app_module.Employer.query.filter_by(email=email).first().id
    _suspend(client, employer_id)

    resp = _submit_appeal(client, email, password, message="")
    assert "Please describe why" in resp.get_data(as_text=True)


def test_duplicate_open_appeal_is_rejected(client):
    import app as app_module

    email, password = _register_employer(client)
    with app_module.app.app_context():
        employer_id = app_module.Employer.query.filter_by(email=email).first().id
    _suspend(client, employer_id)

    first = _submit_appeal(client, email, password)
    assert "Your appeal has been submitted" in first.get_data(as_text=True)

    second = _submit_appeal(client, email, password, message="Trying again.")
    assert "already have an appeal under review" in second.get_data(as_text=True)

    with app_module.app.app_context():
        assert app_module.EmployerAppeal.query.filter_by(employer_id=employer_id).count() == 1


def test_appeal_rate_limited(client, monkeypatch):
    import app as app_module

    monkeypatch.setattr(app_module, "_APPEAL_MAX_ATTEMPTS", 1)

    email, password = _register_employer(client)
    with app_module.app.app_context():
        employer_id = app_module.Employer.query.filter_by(email=email).first().id
    _suspend(client, employer_id)

    first = _submit_appeal(client, email, password)
    assert "Your appeal has been submitted" in first.get_data(as_text=True)

    # A second appeal attempt is already blocked by the duplicate-open-appeal
    # check above the rate limiter, so exercise the limiter directly by
    # denying the first appeal (clearing the duplicate-block) before retrying.
    with app_module.app.app_context():
        appeal = app_module.EmployerAppeal.query.filter_by(employer_id=employer_id).first()
        appeal.status = "denied"
        app_module.db.session.commit()

    second = _submit_appeal(client, email, password, message="Second try.")
    assert second.status_code == 200
    assert "Too many appeal attempts" in second.get_data(as_text=True)


def test_admin_can_deny_an_appeal_leaving_employer_suspended(client):
    import app as app_module

    email, password = _register_employer(client)
    with app_module.app.app_context():
        employer_id = app_module.Employer.query.filter_by(email=email).first().id
    _suspend(client, employer_id)
    _submit_appeal(client, email, password)

    admin_email, admin_password = _create_admin("appeal_admin1@youthchain.test")
    _login_admin(client, admin_email, admin_password)

    page = client.get("/admin/appeals")
    assert page.status_code == 200
    assert "Acme" in page.get_data(as_text=True)

    with app_module.app.app_context():
        appeal_id = app_module.EmployerAppeal.query.filter_by(employer_id=employer_id).first().id

    token = _csrf_token(page.get_data(as_text=True))
    resp = client.post(
        f"/admin/appeals/{appeal_id}/resolve",
        data={"csrf_token": token, "decision": "deny"},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    with app_module.app.app_context():
        appeal = app_module.EmployerAppeal.query.get(appeal_id)
        assert appeal.status == "denied"
        assert appeal.reviewed_by_admin_id is not None
        employer = app_module.Employer.query.get(employer_id)
        assert employer.active is False


def test_admin_can_reinstate_employer_from_an_appeal(client):
    import app as app_module

    email, password = _register_employer(client)
    with app_module.app.app_context():
        employer_id = app_module.Employer.query.filter_by(email=email).first().id
    _suspend(client, employer_id)
    _submit_appeal(client, email, password)

    admin_email, admin_password = _create_admin("appeal_admin2@youthchain.test")
    _login_admin(client, admin_email, admin_password)

    with app_module.app.app_context():
        appeal_id = app_module.EmployerAppeal.query.filter_by(employer_id=employer_id).first().id

    page = client.get("/admin/appeals")
    token = _csrf_token(page.get_data(as_text=True))
    client.post(
        f"/admin/appeals/{appeal_id}/resolve",
        data={"csrf_token": token, "decision": "reinstate"},
    )

    with app_module.app.app_context():
        appeal = app_module.EmployerAppeal.query.get(appeal_id)
        assert appeal.status == "reinstated"
        employer = app_module.Employer.query.get(employer_id)
        assert employer.active is True

    # The employer can now actually log back in -- proving this isn't just a
    # flag flip that the login path ignores.
    login_page = client.get("/employer/login")
    login_token = _csrf_token(login_page.get_data(as_text=True))
    login_resp = client.post(
        "/employer/login",
        data={"csrf_token": login_token, "email": email, "password": password},
    )
    assert login_resp.status_code == 302


def test_verifier_role_admin_cannot_reach_appeals(client):
    verifier_email, verifier_password = _create_admin("appeal_verifier@youthchain.test", role="verifier")
    _login_admin(client, verifier_email, verifier_password)

    resp = client.get("/admin/appeals")
    assert resp.status_code == 403


def test_suspending_an_employer_notifies_them_by_email_with_an_appeal_link(client, monkeypatch):
    import app as app_module

    sent = []
    monkeypatch.setattr(
        app_module,
        "_send_email",
        lambda to_email, subject, body: sent.append((to_email, subject, body)) or True,
    )

    email, _password = _register_employer(client)
    with app_module.app.app_context():
        employer_id = app_module.Employer.query.filter_by(email=email).first().id
    _suspend(client, employer_id)

    assert len(sent) == 1
    to_email, subject, body = sent[0]
    assert to_email == email
    assert "suspended" in subject.lower()
    assert "/employer/appeal" in body


def test_suspended_login_page_links_to_the_appeal_form(client):
    import app as app_module

    email, password = _register_employer(client)
    with app_module.app.app_context():
        employer_id = app_module.Employer.query.filter_by(email=email).first().id
    _suspend(client, employer_id)

    login_page = client.get("/employer/login")
    token = _csrf_token(login_page.get_data(as_text=True))
    resp = client.post(
        "/employer/login",
        data={"csrf_token": token, "email": email, "password": password},
    )
    body = resp.get_data(as_text=True)
    assert "suspended" in body.lower()
    assert '/employer/appeal' in body
