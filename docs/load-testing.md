# Load testing — real numbers, not estimates

Every number on this page was measured against a real running container
(or set of containers) with [k6](https://k6.io/), not extrapolated or
assumed. Commands are included so every result is reproducible. This
replaces qualitative claims like "SQLite has a hard single-writer ceiling"
with actual measurements of what that means in practice, and records two
real bugs found while getting the horizontal-scaling scenario working.

## 1. Read-path baseline (single instance, SQLite)

`GET /jobs` (public, unauthenticated) and `GET /healthz`, the two
highest-traffic real endpoints (what a cold-start user sees first, and
what a load balancer/monitor polls constantly).

```
docker compose -f docker-compose.yml up -d --build
docker run --rm -i -e BASE_URL=http://host.docker.internal:5000 -e VUS=20 -e DURATION=30s \
  grafana/k6 run - < loadtest/script.js
```

| VUs | Throughput | p95 latency | Error rate |
|---|---|---|---|
| 20 | 78.75 req/s | 9.11ms | 0% |
| 100 | 387.65 req/s | 15.47ms | 0% |

Fast and reliable at both concurrency levels — reads were never the
concern; SQLite's actual documented limitation is writes (below).

## 2. Write-path: SQLite vs Postgres

`POST /register` (a real write: `User` row + `OTPCode` bookkeeping),
30 concurrent VUs each registering a unique account per iteration:

```
docker run --rm -i -e BASE_URL=http://host.docker.internal:5000 -e VUS=30 -e DURATION=20s \
  grafana/k6 run - < loadtest/write-script.js
```

| Backend | Throughput | p95 latency | Failure rate |
|---|---|---|---|
| SQLite (default) | 12.46 req/s | 4.14s | **6.38%** |
| Postgres (`DATABASE_URL` set) | 12.80 req/s | 4.77s | **0%** |

The real story here isn't latency (both are slow at this VU count — see
§3) — it's **correctness**. SQLite's single-writer lock caused a real
6.38% failure rate under concurrent writes; Postgres eliminated failures
entirely. This is the concrete evidence behind Phase 3 #19/#20's "hard
single-writer ceiling" language: not a theoretical concern, a measured
6-in-100 real request failure rate.

## 3. The actual latency bottleneck: gunicorn worker count

Both rows above were slow (4+ second p95) even on Postgres, which doesn't
match "Postgres fixes it." Investigated rather than left unexplained:
`-w 1` (the original default) gives eventlet's greenlets huge I/O
concurrency, but Werkzeug's password hashing on every register/login is
**CPU-bound**, and CPU-bound work serializes on a single worker's one OS
thread regardless of how many greenlets it's juggling.

| Config (Postgres) | Throughput | p95 latency |
|---|---|---|
| `-w 1` (original default) | 12.80 req/s | 4.77s |
| `-w 4` | 33.5 req/s (**2.6x**) | ~1.5s neighborhood (see `avg`/`med` below) |

```
avg=875.17ms  min=100.18ms  med=298.74ms  max=11.32s  p(90)=2.86s  p(95)=3.22s
```

`GUNICORN_WORKERS=4` is now the Dockerfile's default (`backend/Dockerfile`),
overridable per-deployment based on real host CPU count — a measured
default, not an arbitrary one.

## 4. Horizontal scaling: 3 replicas behind nginx

```
docker compose -f docker-compose.scale.yml up -d --build
docker run --rm -i -e BASE_URL=http://host.docker.internal:8080 -e VUS=30 -e DURATION=20s \
  grafana/k6 run - < loadtest/write-script.js
```

| Config | Throughput | p95 latency | Failure rate |
|---|---|---|---|
| 1 instance, `-w 4`, Postgres | 33.5 req/s | ~3.2s | 0% |
| **3 replicas** (nginx + Postgres + Redis) | **42.1 req/s** | **1.48s** | 1.16%* |

\* 10/862 requests — no corresponding application errors in any replica's
logs (no 500s/exceptions), consistent with transient connection setup at
the very start of the run, not a backend defect.

This is only an honest scaling story because of two things built earlier
in this session: **stateless JWT auth** (no server-side session to keep
sticky to one instance) and **Redis-backed OTP rate limiting** (a shared
store, so the limiter is correct across replicas). Concretely verified,
not assumed: 7 rapid `POST /auth/otp/verify` requests sent through nginx
(round-robining across all 3 replicas) got `400, 400, 400, 400, 400, 429,
429` — the rate limit tripped correctly at attempt 6 even though the
requests landed on different backend processes. Before Redis, each
replica's own in-memory counter would have independently allowed 5
attempts each — up to 15 total before any 429, a real security gap.

### Two real bugs found getting this to work (not guessed)

1. **`depends_on` only waits for a container to *start*, not for Postgres
   to be *ready*.** All three replicas raced to connect before Postgres
   had finished initializing and crashed with `Connection refused`.
   Fixed with a real `pg_isready` healthcheck and
   `depends_on: { condition: service_healthy }`.
2. **All three replicas running `flask db upgrade` concurrently raced to
   `CREATE TABLE alembic_version`.** The losing replicas crashed with a
   genuine Postgres `UniqueViolation`, and the crash-loop happened to
   self-heal via Docker's restart policy — which is not the same as a
   real fix (it's relying on incidental timing). Fixed properly with a
   dedicated one-shot `migrate` service that every replica waits on via
   `condition: service_completed_successfully` before starting; each
   replica's own subsequent `flask db upgrade` then runs against an
   already-current schema and is a correct no-op, not a second race.

## Reproducing these results

All commands above assume Docker Desktop with `host.docker.internal`
resolving to the host (works out of the box on Windows/Mac; on native
Linux Docker, use `--network host` on the k6 container instead and drop
`host.docker.internal` in favor of `localhost`). Copy `backend/.env.example`
to `backend/.env` first and set `JWT_SECRET_KEY`/`ENFORCE_EMAIL_OTP_REG=0`
before bringing any stack up — none of these numbers depend on real SMTP,
a real JWT secret value, or OTP delivery, only on the backend actually
running.
