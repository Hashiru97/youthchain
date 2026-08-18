# Monitoring, metrics, logs & alerting setup

This repo now ships a real, self-hosted observability stack (Prometheus +
Grafana + Loki, via `docker-compose.observability.yml`) — that part is
genuinely closed, not just documented. What remains open, and cannot be
closed by code in this repository (Phase 3 #11 of the engineering review):
external uptime/alerting that actually pages a human. That requires
choosing a provider and a subscription/on-call decision belonging to
whoever operates this deployment, not to this codebase. This doc covers
both: what's built and verified, and the concrete remaining steps for the
part that isn't.

## What's built in and verified

- **`GET /healthz`** — returns `{"ok": true, "time": "<ISO8601>"}` with a
  `200`. No auth, does not touch the database — a fast liveness probe
  (safe for a load balancer to poll frequently).
- **`GET /healthz/deep`** — a separate, deeper check: verifies real DB
  connectivity (`SELECT 1`) and, if `REDIS_URL` is configured, that Redis
  is reachable too. Returns `503` if either check fails. Kept as its own
  route rather than changing what `/healthz` means, since a load balancer
  polling `/healthz` for readiness would otherwise mark an instance
  unhealthy on a transient DB blip — a real behavior change that deserved
  its own endpoint, not a drive-by redefinition.
- **`GET /metrics`** — Prometheus text-format metrics (request counts,
  latency histograms, in-flight requests, by endpoint/method/status),
  via `prometheus-flask-exporter`. Unauthenticated, matching Prometheus
  convention — restrict access at the network level (the observability
  compose file below does not publish it beyond `127.0.0.1`). Also
  includes `youthchain_backup_last_attempt_success` (1/0) and
  `youthchain_backup_last_success_timestamp_seconds` — real backup-health
  gauges, computed fresh on every scrape from a status file
  `scripts/backup.py` writes after each run (see
  `docs/disaster-recovery.md`). This is what a real alert (section below)
  would fire *on* once you've wired one up — a broken backup pipeline
  previously had no signal anywhere outside backup-cron's own logs.
- **Structured logs** (Python's `logging` module, BL-20) go to stdout.
- **CI already exercises `/healthz` on every build** (`.github/workflows/ci.yml`'s
  `docker` job).

## Self-hosted observability stack (Prometheus + Grafana + Loki)

```
docker compose -f docker-compose.yml -f docker-compose.observability.yml up -d
```

This is additive to the base compose file (only adds `prometheus`,
`grafana`, `loki`, `promtail` — the `backend` service definition lives in
`docker-compose.yml` and is shared, not duplicated).

- **Prometheus** (`http://localhost:9090`) scrapes `backend:5000/metrics`
  every 15s — live-verified: `curl http://localhost:9090/api/v1/targets`
  shows the `youthchain-backend` job with `"health": "up"`.
- **Grafana** (`http://localhost:3000`, default `admin`/`admin` — change
  this on first login, since `docker-compose.observability.yml` is
  checked into source control) has Prometheus and Loki auto-provisioned
  as datasources — no manual "add datasource" step. No dashboards are
  pre-built; build one against the `flask_http_request_*` metrics
  `prometheus-flask-exporter` exposes by default.
- **Loki + Promtail** ship every container's stdout (via Docker service
  discovery over the mounted Docker socket — no per-container config as
  new services are added) — live-verified: querying Loki directly for
  `{container="...backend..."}` returns real gunicorn/application log
  lines.
- All four ports are bound to `127.0.0.1` only — reachable from the host
  machine, not published to the network. Use an SSH tunnel or your VPN to
  reach Grafana on a remote server, rather than publishing `3000` publicly.

**What this stack deliberately does NOT do**: page anyone. There is no
Alertmanager here — adding one without a real destination (Slack webhook,
PagerDuty key, etc.) configured would mean alerts fire into a void, which
is worse than not having them, since it looks like coverage that isn't
real. Wiring real alerting is the next section.

## Wiring real alerting (the part this repo genuinely can't do for you)

Two honest paths, both requiring a real decision and account:

**A. Extend the self-hosted stack** — add Prometheus Alertmanager
(another self-hostable container) and configure it with a real
destination (Slack incoming webhook, PagerDuty integration key, email).
More setup, but everything stays self-hosted and free of a third-party
uptime SaaS.

**B. An external uptime service polling `/healthz`** — simpler, and
independently verifies the whole stack is reachable (Prometheus can't
tell you the network path to your server is down; an external poller
can). Any of these work with zero further code changes:

1. Pick a provider — UptimeRobot, Better Uptime, Pingdom, Checkly, etc.
   Real cost/on-call implications, not this review's decision to make.
2. Point it at `https://<your-domain>/healthz` (through your real TLS
   termination, per `docker-compose.tls.yml` — not the bare IP).
3. Alert on non-200 or timeout.
4. Set alert routing — the actual "on-call decision." A check with nobody
   subscribed to its alerts is equivalent to not having one.

Most real deployments want both: (A) for internal dashboards/debugging,
(B) as an independent outside-in check that (A) itself is reachable.
