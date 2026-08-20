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
#
# Two stages, not one (real gap found in the infra audit): a single-stage
# build left build-essential (a full C/C++ toolchain — gcc, g++, make,
# several hundred MB and its own real CVE surface) sitting in the actual
# runtime image forever, even though it's only ever needed once, to
# compile the handful of pip packages here that don't ship a prebuilt
# wheel for this platform. The builder stage below has it; the runtime
# stage never installs it at all — only the already-built /install prefix
# and compiled blockchain/ artifacts cross the stage boundary via COPY
# --from=builder. curl+gnupg get the same treatment in the runtime stage
# itself (installed just long enough to add the NodeSource apt repo and
# pull in Node, then purged in that same RUN layer) — see the comment on
# that RUN block below for why they can't be dropped from this stage
# entirely the way build-essential can from the builder's output.
FROM python:3.11-slim AS builder

WORKDIR /app

# build-essential for common wheels (filetype's libmagic-less detection
# needs none, but psycopg2/Pillow-style packages do); curl+gnupg to add
# the NodeSource repo (Debian's own default repos ship a Node too old for
# this Hardhat 3 toolchain — see blockchain/package.json). Node 22, not
# 20: found via a real `docker build` failure, not assumed -- Hardhat
# 3.12.0 hard-requires Node >= 22 ("ERROR: You are using Node.js 20.20.2
# which is not supported by Hardhat"). None of this needs to survive into
# the runtime stage — only its OUTPUT does (the installed pip packages,
# the compiled blockchain/ artifacts), copied across below.
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential curl gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/*

COPY backend/requirements.txt .
# --prefix=/install, not a bare `pip install` into the interpreter's own
# site-packages -- an isolated, self-contained tree the runtime stage can
# COPY --from=builder in one shot (onto the identical Python 3.11 install
# both stages share, so this is a straight file copy, not a rebuild).
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# The blockchain project the Hardhat CLI subprocess calls at RUNTIME
# actually needs on disk — see _run_hardhat_script()'s blockchain_dir,
# which resolves to one level up from the runtime stage's WORKDIR (/app),
# i.e. /blockchain. Compiled once here (as root, in the builder); the
# runtime stage copies the result rather than re-running `npm ci` itself.
# node_modules/artifacts/cache from the HOST are excluded by the root
# .dockerignore regardless -- built fresh here instead, which may not
# even match this image's platform/arch if it came from the host.
COPY blockchain/ /blockchain/
WORKDIR /blockchain
RUN npm ci && npx hardhat compile
WORKDIR /app


FROM python:3.11-slim

WORKDIR /app

# Only what the RUNNING app actually needs, not what building it needed:
# postgresql-client for pg_dump/psql (scripts/backup.py and
# scripts/restore.py's Postgres path — see docker-compose.backup.yml);
# age (FiloSottile/age, packaged for whatever Debian release this image's
# python:3.11-slim base currently tracks — that base has since rolled from
# bookworm to trixie upstream since this line was first written, and
# installed as `age` 1.2.1 there; confirmed by actually building this
# image and running `age --version` inside it rather than assumed, twice
# now, precisely because it already drifted once) encrypts backup archives
# before off-site upload (scripts/backup.py's encrypt_backup(),
# BACKUP_ENCRYPTION_RECIPIENT in .env.example); Node.js -- unlike
# build-essential (builder-stage only, see above), Node is a genuine
# RUNTIME dependency, not just a build one: app.py's
# _run_hardhat_script() shells out to `npx hardhat run scripts/*.js` live,
# on every credential issuance/verification/revocation, not only when
# this image is built. curl+gnupg are needed here too, transiently, for
# the exact same NodeSource-repo reason as the builder stage -- but
# purged via apt-get purge in this same RUN layer once Node is installed,
# so (unlike build-essential) they don't persist into the final image
# despite being genuinely needed mid-build.
RUN apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client curl gnupg age \
    && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && apt-get purge -y --auto-remove curl gnupg \
    && rm -rf /var/lib/apt/lists/*

# The builder's pip --prefix=/install tree lands on top of this stage's
# own /usr/local, same layout pip would have used directly -- both stages
# are the identical python:3.11-slim base, so this is a plain file copy
# of already-built packages, not a second install.
COPY --from=builder /install /usr/local

COPY backend/ .
COPY --from=builder /blockchain/ /blockchain/

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
# compile` in the builder stage ran as root, which cached the downloaded
# solc binary under root's home directory -- a different filesystem
# location in THIS stage entirely (the builder's root home never crosses
# the COPY --from=builder boundary above). appuser has its own, separate
# home, so without this the very first credential issuance in a fresh
# container's lifetime paid for a redundant solc re-download inline with
# the (already-backgrounded, non-blocking) on-chain write. Artifacts
# copied from the builder are already correct and unchanged; this only
# warms appuser's own cache.
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
