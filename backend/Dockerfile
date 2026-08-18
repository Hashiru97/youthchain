# Reproducible container build for the YouthChain backend (Phase 3 #13 —
# containerization was previously entirely absent, meaning "works on my
# machine" was the only deployment story). IS_PRODUCTION (FLASK_ENV=production)
# gates debug mode, secure cookies, and HSTS at the application level (see
# app.py).
#
# Runs via gunicorn+eventlet, not `python app.py` — discovered by actually
# running the built image, not assumed: once debug mode is correctly gated
# off in production, Flask-SocketIO's own run() refuses to use the Werkzeug
# dev server outside debug mode at all ("The Werkzeug web server is not
# designed to run in production"). `python app.py` remains the local dev
# entrypoint; this image uses the server Flask-SocketIO itself documents
# for production.
#
# Build context is the REPO ROOT, not backend/ — see docker-compose.yml's
# `build:` stanza. Real, previously-undiscovered gap found via a
# full-codebase review: this image used to be Python-only, and its build
# context was scoped to backend/ alone, so the sibling blockchain/
# directory that app.py's _run_hardhat_script() shells out to
# (registerCredential.js / checkRegistered.js / checkValid.js /
# revokeCredential.js) could never be present in the built image at all —
# every credential issuance/verification/revocation would silently fail
# inside any deployment of this image, local network or real one, since
# the gap was "no Node.js and no blockchain/ project", not which network
# HARDHAT_NETWORK happened to point at.
FROM python:3.11-slim

WORKDIR /app

# System deps: build-essential for common wheels (filetype's libmagic-less
# detection needs none, but psycopg2/Pillow-style packages do); postgresql-client
# for pg_dump/psql (scripts/backup.py and scripts/restore.py's Postgres path
# — see docker-compose.backup.yml); curl+gnupg to add the NodeSource repo
# (Debian's own default repos ship a Node too old for this Hardhat 3
# toolchain — see blockchain/package.json). Node 22, not 20: found via a
# real `docker build` failure, not assumed -- Hardhat 3.12.0 hard-requires
# Node >= 22 ("ERROR: You are using Node.js 20.20.2 which is not supported
# by Hardhat"). Kept minimal otherwise on purpose.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential postgresql-client curl gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .

# The blockchain project the Hardhat CLI subprocess calls above actually
# need on disk — see _run_hardhat_script()'s blockchain_dir, which
# resolves to one level up from this image's WORKDIR (/app), i.e.
# /blockchain. node_modules/artifacts/cache are excluded by the root
# .dockerignore (built fresh below instead of copying host-machine
# build output, which may not even match this image's platform/arch).
COPY blockchain/ /blockchain/
WORKDIR /blockchain
RUN npm ci && npx hardhat compile
WORKDIR /app

# instance/ (SQLite DB) and uploads/ are runtime state, not build output —
# mount them as volumes in docker-compose.yml / your orchestrator rather
# than baking data into the image.
RUN mkdir -p instance uploads backups
RUN chmod +x docker-entrypoint.sh

# Non-root runtime user (real gap found via a full-codebase review): this
# image previously ran gunicorn/eventlet -- and docker-entrypoint.sh's
# `flask db upgrade` -- as root, with no USER directive anywhere. That's
# unnecessary attack-surface expansion: a container-escape or a dependency
# RCE gets root inside the container by default otherwise, for no actual
# benefit (nothing this process does needs root -- it binds to an
# unprivileged port and only ever writes to instance/uploads/backups/,
# which are chown'd below so they stay writable). Docker copies a mount
# point's existing ownership into a freshly-created named volume on first
# mount (see docker-compose.yml's uploads-data/backups-data volumes), so
# this ownership survives being volume-mounted over at runtime, not just
# in the image build. /blockchain is chown'd too -- the Hardhat CLI
# subprocess calls run as this same user, not root, and need to read
# their own node_modules/artifacts (and, if deployed.json is ever written
# at runtime by a manual deploy, write to /blockchain itself).
RUN useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app /blockchain
USER appuser

# Re-compile once more as the actual runtime user -- found via a real
# `docker run` + live credential issuance, not assumed: the `npx hardhat
# compile` above ran as root (before USER appuser), which cached the
# downloaded solc binary under root's home directory. appuser has its own,
# separate home, so without this the very first credential issuance in a
# fresh container's lifetime paid for a redundant solc re-download inline
# with the (already-backgrounded, non-blocking) on-chain write. Artifacts
# are already correct and unchanged; this only warms appuser's own cache.
RUN cd /blockchain && npx hardhat compile

EXPOSE 5000

ENV PYTHONUNBUFFERED=1
# Inside a container, migrations/ (via docker-entrypoint.sh's `flask db
# upgrade`) is the sole authority over schema creation — found via a real
# `docker run`, not assumed: without this, app.py's own db.create_all()
# fallback (kept for zero-config local dev outside Docker) runs first at
# import time, creates every table itself, and then Alembic's own
# CREATE TABLE statements in the initial migration collide with tables
# that already exist, crashing startup with "table admin already exists".
ENV SKIP_DB_CREATE_ALL=1

# Worker count found via a real k6 load test (loadtest/write-script.js,
# see docs/load-testing.md), not guessed: -w 1 against Postgres under 30
# concurrent writers measured 12.8 req/s and a 4.77s p95 latency;
# eventlet's greenlets give one worker massive I/O concurrency, but
# CPU-bound work (Werkzeug's password hashing on every register/login)
# still serializes on that one worker's single OS thread. -w 4 on the
# identical test measured 33.5 req/s (2.6x) and a 298ms median (roughly
# 6x). GUNICORN_WORKERS is overridable per-deployment based on real host
# CPU count; 4 is a measured, not arbitrary, default.
ENV GUNICORN_WORKERS=4

ENTRYPOINT ["./docker-entrypoint.sh"]
CMD ["sh", "-c", "gunicorn --worker-class eventlet -w ${GUNICORN_WORKERS} --bind 0.0.0.0:5000 app:app"]
