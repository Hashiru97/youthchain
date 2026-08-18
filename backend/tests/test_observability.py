"""
Regression coverage for the observability additions: /healthz/deep (a
separate, deeper check from /healthz — see the comment on health_deep() in
app.py for why it's a new route rather than a change to /healthz) and
GET /metrics (Prometheus text-format scrape endpoint, self-hosted via
docker-compose.observability.yml).
"""


def test_healthz_unchanged(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True


def test_healthz_deep_reports_database_ok(client):
    resp = client.get("/healthz/deep")
    assert resp.status_code == 200
    body = resp.get_json()
    assert body["ok"] is True
    assert body["checks"]["database"] == "ok"


def test_healthz_deep_reports_redis_not_configured_when_unset(client):
    import app as app_module

    # conftest's client fixture doesn't set REDIS_URL for the default test
    # run — this asserts the honest "not configured" state rather than a
    # false "ok".
    if app_module._redis_client is None:
        resp = client.get("/healthz/deep")
        body = resp.get_json()
        assert body["checks"]["redis"] == "not configured"


def test_metrics_endpoint_serves_prometheus_text_format(client):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    # Prometheus text format always includes HELP/TYPE lines for exposed
    # metrics — prometheus_flask_exporter's default request-count metric is
    # a reliable one to assert on without depending on exact label values.
    assert "# HELP" in body
    assert "# TYPE" in body


def test_backup_gauges_report_zero_when_no_backup_has_ever_run(client, monkeypatch):
    """
    Real gap found and closed alongside the backup-retry-backoff change
    in docker-compose.backup.yml: a failed scheduled backup previously had
    no signal anywhere outside backup-cron's own container logs. These
    gauges read scripts/backup.py's status file fresh on every /metrics
    scrape (set_function(), not a value cached at import time) -- pointed
    at a path that doesn't exist here, the honest state before any backup
    has ever run.
    """
    import app as app_module

    monkeypatch.setattr(app_module, "_BACKUP_STATUS_PATH", "/nonexistent/.backup_status.json")

    resp = client.get("/metrics")
    body = resp.get_data(as_text=True)
    assert "youthchain_backup_last_attempt_success 0.0" in body
    assert "youthchain_backup_last_success_timestamp_seconds 0.0" in body


def test_backup_gauges_reflect_a_real_successful_status_file(client, monkeypatch, tmp_path):
    import json

    from datetime import datetime, timezone
    import app as app_module

    status_path = tmp_path / ".backup_status.json"
    now = datetime.now(timezone.utc)
    status_path.write_text(json.dumps({"timestamp": now.isoformat(), "success": True, "error": None}))
    monkeypatch.setattr(app_module, "_BACKUP_STATUS_PATH", str(status_path))

    resp = client.get("/metrics")
    body = resp.get_data(as_text=True)
    assert "youthchain_backup_last_attempt_success 1.0" in body
    # The success timestamp gauge should be close to `now`, not zero.
    for line in body.splitlines():
        if line.startswith("youthchain_backup_last_success_timestamp_seconds "):
            value = float(line.split()[-1])
            assert abs(value - now.timestamp()) < 5
            break
    else:
        raise AssertionError("youthchain_backup_last_success_timestamp_seconds not found in /metrics output")


def test_backup_gauges_reflect_a_real_failed_status_file(client, monkeypatch, tmp_path):
    import json

    from datetime import datetime, timezone
    import app as app_module

    status_path = tmp_path / ".backup_status.json"
    status_path.write_text(json.dumps({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "success": False,
        "error": "pg_dump failed: connection refused",
    }))
    monkeypatch.setattr(app_module, "_BACKUP_STATUS_PATH", str(status_path))

    resp = client.get("/metrics")
    body = resp.get_data(as_text=True)
    assert "youthchain_backup_last_attempt_success 0.0" in body
    # A never-succeeded (or previously-failed) backup must report 0 here,
    # not the timestamp of the failed attempt -- this gauge specifically
    # answers "when did a backup last actually succeed."
    assert "youthchain_backup_last_success_timestamp_seconds 0.0" in body


def test_backup_gauges_tolerate_a_corrupt_status_file(client, monkeypatch, tmp_path):
    status_path = tmp_path / ".backup_status.json"
    status_path.write_text("not valid json{{{")

    import app as app_module

    monkeypatch.setattr(app_module, "_BACKUP_STATUS_PATH", str(status_path))

    resp = client.get("/metrics")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "youthchain_backup_last_attempt_success 0.0" in body
