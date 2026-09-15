"""
Coverage for the /admin/scanner* routes: RBAC (admin-only, not verifier —
these actions spend real API credits), CSRF enforcement, and that
triggering a scan queues a scan_run without calling Firecrawl inline.
"""
import re

import app as app_module


def _csrf_token(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "csrf token not found in page"
    return match.group(1)


def _create_admin(email="ops@youthchain.test", password="adminpass123", role="admin"):
    with app_module.app.app_context():
        admin = app_module.Admin(
            name="Ops", email=email, role=role, active=True,
            password_hash=app_module.generate_password_hash(password),
        )
        app_module.db.session.add(admin)
        app_module.db.session.commit()
    return email, password


def _login_admin(client, email, password):
    page = client.get("/admin/login")
    token = _csrf_token(page.get_data(as_text=True))
    return client.post("/admin/login", data={"csrf_token": token, "email": email, "password": password})


def _make_source(name="Careers.sl", url="https://careers.sl/jobs"):
    with app_module.app.app_context():
        source = app_module.JobSource(name=name, base_url=url)
        app_module.db.session.add(source)
        app_module.db.session.commit()
        return source.id


def test_scanner_page_requires_admin_role_not_verifier(client):
    email, password = _create_admin(role="verifier")
    _login_admin(client, email, password)
    resp = client.get("/admin/scanner")
    assert resp.status_code == 403


def test_scanner_page_reachable_by_admin_role(client):
    email, password = _create_admin(role="admin")
    _login_admin(client, email, password)
    resp = client.get("/admin/scanner")
    assert resp.status_code == 200


def test_scanner_page_shows_empty_state_with_no_sources_configured(client):
    """The very first thing an admin sees before adding any source at
    all -- must not render a broken/empty table or a stray Jinja error,
    and must actually say something, not just silently show nothing."""
    email, password = _create_admin(role="admin")
    _login_admin(client, email, password)
    resp = client.get("/admin/scanner")
    html = resp.get_data(as_text=True)
    assert "No sources configured yet" in html
    assert "No scans have run yet" in html


def test_scanner_page_shows_never_scanned_for_a_source_with_no_runs(client):
    """A source can exist (an admin added one) before the scanner has
    ever actually run against it -- the exact "hasn't produced listings
    yet" state. Must read as "Never", not a blank cell or "None"."""
    email, password = _create_admin(role="admin")
    _login_admin(client, email, password)
    _make_source(name="Careers.sl", url="https://careers.sl/jobs")

    resp = client.get("/admin/scanner")
    html = resp.get_data(as_text=True)
    assert "Careers.sl" in html
    assert "Never" in html
    # Still no runs at all yet, even though a source now exists.
    assert "No scans have run yet" in html


def test_scanner_page_shows_backfill_count_for_a_completed_run(client):
    """Real gap found on review: the panel showed found/created/updated
    looking perfectly healthy for a scan with no way to tell an admin
    whether the jobs underneath actually got real bodies. This is the
    number that answers that question, and it must actually render."""
    email, password = _create_admin(role="admin")
    _login_admin(client, email, password)
    source_id = _make_source()
    with app_module.app.app_context():
        run = app_module.ScanRun(
            source_id=source_id, status="success", trigger="manual",
            jobs_found=5, jobs_created=5, jobs_updated=0, jobs_backfilled=3,
        )
        app_module.db.session.add(run)
        app_module.db.session.commit()

    resp = client.get("/admin/scanner")
    html = resp.get_data(as_text=True)
    assert "3 got a full description from their own page" in html


def test_scanner_page_omits_backfill_line_for_pre_existing_runs(client):
    """scan_run rows from before this column existed have
    jobs_backfilled=None, not 0 -- must render as if the line simply
    isn't there, not as "0 got a full description", which would read as
    a real (and wrong) claim that backfill ran and found nothing."""
    email, password = _create_admin(role="admin")
    _login_admin(client, email, password)
    source_id = _make_source()
    with app_module.app.app_context():
        run = app_module.ScanRun(
            source_id=source_id, status="success", trigger="manual",
            jobs_found=5, jobs_created=5, jobs_updated=0, jobs_backfilled=None,
        )
        app_module.db.session.add(run)
        app_module.db.session.commit()

    resp = client.get("/admin/scanner")
    html = resp.get_data(as_text=True)
    assert "got a full description from their own page" not in html


def test_scanner_page_shows_poller_disabled_by_default(client, monkeypatch):
    """Local dev / most test environments never set
    ENABLE_JOB_SCANNER_POLLER=1 -- must read as an informational
    "Disabled", not silently omitted or shown as an error."""
    monkeypatch.delenv("ENABLE_JOB_SCANNER_POLLER", raising=False)
    email, password = _create_admin(role="admin")
    _login_admin(client, email, password)
    resp = client.get("/admin/scanner")
    html = resp.get_data(as_text=True)
    assert "Disabled" in html
    assert "Scheduled scanning" in html


def test_scanner_page_shows_poller_enabled_when_set(client, monkeypatch):
    monkeypatch.setenv("ENABLE_JOB_SCANNER_POLLER", "1")
    email, password = _create_admin(role="admin")
    _login_admin(client, email, password)
    resp = client.get("/admin/scanner")
    html = resp.get_data(as_text=True)
    assert "Enabled" in html


def test_scanner_page_shows_description_coverage_stat(client):
    """The exact number this whole feature's own development kept
    needing to check by hand -- how many real scraped jobs actually got
    a full description vs. a short listing-page stub."""
    email, password = _create_admin(role="admin")
    _login_admin(client, email, password)
    source_id = _make_source()
    with app_module.app.app_context():
        long_description = "A real, full job description. " * 5  # well over the 100-char floor
        app_module.db.session.add(app_module.Job(
            title="Has a real description", location="Freetown", duration="Full-time",
            source="scraped", source_id=source_id, description=long_description,
            external_id="job-with-description",
        ))
        app_module.db.session.add(app_module.Job(
            title="Only a stub", location="Freetown", duration="Full-time",
            source="scraped", source_id=source_id, description="Closing Date: Ongoing",
            external_id="job-with-stub",
        ))
        app_module.db.session.commit()

    resp = client.get("/admin/scanner")
    html = resp.get_data(as_text=True)
    assert "1 / 2" in html  # 1 of 2 scraped jobs has a real description
    assert "2" in html  # total scraped jobs count


def test_create_source_requires_csrf_token(client):
    email, password = _create_admin(role="admin")
    _login_admin(client, email, password)
    # No csrf_token field at all -- CSRFProtect should reject this.
    resp = client.post("/admin/scanner/sources", data={"name": "X", "base_url": "https://x.sl"})
    assert resp.status_code == 400


def test_create_source_rejects_malformed_url(client):
    email, password = _create_admin(role="admin")
    _login_admin(client, email, password)
    page = client.get("/admin/scanner")
    token = _csrf_token(page.get_data(as_text=True))
    resp = client.post(
        "/admin/scanner/sources",
        data={"csrf_token": token, "name": "Bad Source", "base_url": "not-a-url"},
    )
    assert resp.status_code == 200
    assert "valid http" in resp.get_data(as_text=True)
    with app_module.app.app_context():
        assert app_module.JobSource.query.filter_by(name="Bad Source").count() == 0


def test_create_source_clamps_frequency_to_valid_range(client):
    email, password = _create_admin(role="admin")
    _login_admin(client, email, password)
    page = client.get("/admin/scanner")
    token = _csrf_token(page.get_data(as_text=True))
    client.post(
        "/admin/scanner/sources",
        data={
            "csrf_token": token, "name": "Too Frequent", "base_url": "https://x.sl",
            "scan_frequency_minutes": "5",  # below the 30-minute floor
        },
    )
    with app_module.app.app_context():
        source = app_module.JobSource.query.filter_by(name="Too Frequent").first()
        assert source is not None
        assert source.scan_frequency_minutes == 30


def test_trigger_scan_queues_a_run_without_calling_firecrawl(client, monkeypatch):
    """The whole point of the queue-then-poll design: an admin clicking
    "Scan now" must never make the request itself wait on a live
    Firecrawl/Claude round trip."""
    import scanner.firecrawl_client as firecrawl_client

    def _fail_if_called(*args, **kwargs):
        raise AssertionError("scrape_url should not be called synchronously from the trigger route")

    monkeypatch.setattr(firecrawl_client, "scrape_url", _fail_if_called)

    source_id = _make_source()
    email, password = _create_admin(role="admin")
    _login_admin(client, email, password)
    page = client.get("/admin/scanner")
    token = _csrf_token(page.get_data(as_text=True))

    resp = client.post(f"/admin/scanner/sources/{source_id}/trigger", data={"csrf_token": token})
    assert resp.status_code == 302

    with app_module.app.app_context():
        run = app_module.ScanRun.query.filter_by(source_id=source_id).first()
        assert run is not None
        assert run.status == "queued"
        assert run.trigger == "manual"


def test_trigger_scan_twice_does_not_queue_a_duplicate(client):
    """Real gap found on review: clicking "Scan now" twice (or an
    impatient admin clicking again while a scan is still queued/running)
    used to queue a second scan_run with no guard at all -- each one is a
    real Firecrawl+Claude API spend, not a free retry."""
    source_id = _make_source()
    email, password = _create_admin(role="admin")
    _login_admin(client, email, password)
    page = client.get("/admin/scanner")
    token = _csrf_token(page.get_data(as_text=True))

    client.post(f"/admin/scanner/sources/{source_id}/trigger", data={"csrf_token": token})
    client.post(f"/admin/scanner/sources/{source_id}/trigger", data={"csrf_token": token})

    with app_module.app.app_context():
        runs = app_module.ScanRun.query.filter_by(source_id=source_id).all()
        assert len(runs) == 1


def test_verify_company_sets_verified_fields(client):
    with app_module.app.app_context():
        company = app_module.ScrapedCompany(name="Acme SL", verified=False)
        app_module.db.session.add(company)
        app_module.db.session.commit()
        company_id = company.id

    email, password = _create_admin(role="admin")
    _login_admin(client, email, password)
    page = client.get("/admin/scanner/companies")
    token = _csrf_token(page.get_data(as_text=True))

    resp = client.post(f"/admin/scanner/companies/{company_id}/verify", data={"csrf_token": token})
    assert resp.status_code == 302

    with app_module.app.app_context():
        company = app_module.db.session.get(app_module.ScrapedCompany, company_id)
        assert company.verified is True
        assert company.verified_at is not None
        assert company.verified_by_admin_id is not None


def test_admin_pending_counts_includes_unverified_companies(client):
    with app_module.app.app_context():
        app_module.db.session.add(app_module.ScrapedCompany(name="Unverified Co", verified=False))
        app_module.db.session.commit()
        counts = app_module._admin_pending_counts()
    assert counts["unverified_companies"] == 1
