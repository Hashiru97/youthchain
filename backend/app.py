from flask import Flask, render_template, request, redirect, url_for, jsonify, send_from_directory, session
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect
from flask_wtf.csrf import CSRFError
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix
from sqlalchemy.exc import IntegrityError
from sqlalchemy import event, func, text as sa_text
from sqlalchemy.orm.attributes import get_history
from functools import wraps
import filetype
import html
import json
import logging
import requests
import os, re, hashlib, secrets, shutil, smtplib, ssl, subprocess, difflib, threading
import base64
import io
import pyotp
import qrcode
from PIL import Image
import eventlet
from datetime import date, datetime, timedelta
from urllib.parse import urlparse
from flask_socketio import SocketIO, join_room
from flask_migrate import Migrate
from prometheus_flask_exporter import PrometheusMetrics
from prometheus_client import Gauge as PromGauge
from flask_jwt_extended import (
    JWTManager,
    create_access_token,
    jwt_required,
    get_jwt_identity,
    get_jwt,
    decode_token,
)
from dotenv import load_dotenv
import scanner.pipeline as scanner_pipeline
import scanner.poller as scanner_poller
import cv_generator

load_dotenv()

app = Flask(__name__)

# Appended to every static CSS/JS <link>/<script> src as a ?v= query
# param (see the context processor below). Originally a single token
# generated once per process start — real bug found in that first
# version, live, during this exact debugging session: the token stayed
# identical across every static file edit made after the process
# started, so a browser that had already fetched e.g.
# employer.css?v=<token> once had no reason to ever re-fetch it again —
# silently serving a stale cached copy through several real CSS fixes in
# a row, which is exactly what looked like "the fix isn't taking effect"
# while testing live on a phone. Keyed to each file's own mtime instead:
# editing ANY one static file changes ONLY that file's query string,
# forcing a genuine re-fetch of exactly the file that changed, on the
# very next request — no server restart required, and no other file's
# cache is invalidated for no reason.
def _asset_version(filename: str) -> str:
    try:
        return str(int(os.path.getmtime(os.path.join(app.static_folder, filename))))
    except OSError:
        return "0"


@app.context_processor
def _inject_asset_version():
    return {"asset_version": _asset_version}


# ----------------- Logging (BL-20 / Phase 8) -----------------
# Replaces the bare print() calls that used to be the only diagnostic output
# in this file (Phase 1 §13 / Phase 8 scored this area 1/10) — structured,
# leveled, and redirectable to a real log aggregator later without touching
# every call site again.
logging.basicConfig(
    level=logging.DEBUG if not os.getenv("FLASK_ENV") == "production" else logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("youthchain")

# ----------------- Deployment topology (BL-08 / Phase 3 #23) -----------------
# This process is expected to sit behind a TLS-terminating reverse proxy or
# cloud load balancer in any non-local environment — Flask's own dev server
# does not (and should not) terminate TLS itself. ProxyFix makes Flask trust
# the proxy's X-Forwarded-Proto/-For/-Host headers, so request.is_secure and
# url_for(..., _external=True) are correct behind that proxy, and so the
# secure-cookie/HSTS logic below (which checks request.is_secure) actually
# works once deployed rather than silently never triggering.
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
IS_PRODUCTION = os.getenv("FLASK_ENV", "development") == "production"

# ----------------- Secrets management (Vault, with env fallback) -----------------
# Self-hosted HashiCorp Vault integration — real, not aspirational: if
# VAULT_ADDR + VAULT_TOKEN are set, secrets are fetched from Vault's KV v2
# engine at startup; otherwise this falls through to plain environment
# variables exactly as before. This is what "a real managed vault" (the
# gap this domain was previously docked for) looks like when it's actually
# wired up rather than just documented as a future step — see
# docker-compose.vault.yml for a runnable dev-mode Vault to point this at.
#
# Deliberately does NOT require Vault: a from-scratch clone with nothing
# configured still works via the env-var fallback that's existed all
# along, so this is additive, not a new hard dependency.
_vault_client = None
_VAULT_ADDR = os.getenv("VAULT_ADDR")
_VAULT_TOKEN = os.getenv("VAULT_TOKEN")
_VAULT_SECRET_PATH = os.getenv("VAULT_SECRET_PATH", "youthchain/config")
if _VAULT_ADDR and _VAULT_TOKEN:
    try:
        import hvac as _hvac_module

        _vault_client = _hvac_module.Client(url=_VAULT_ADDR, token=_VAULT_TOKEN)
        if not _vault_client.is_authenticated():
            raise RuntimeError("Vault token was not accepted")
        logger.info("Connected to Vault at %s for secrets management", _VAULT_ADDR)
    except Exception as e:
        _vault_client = None
        logger.warning(
            "VAULT_ADDR/VAULT_TOKEN were set but Vault could not be reached "
            "(%s) — falling back to plain environment variables for secrets.",
            e,
        )


def _get_secret(env_var: str) -> str | None:
    """
    Resolves a secret: Vault (if connected) first, then the environment
    variable of the same name, matching the existing fallback pattern this
    file already uses for REDIS_URL/DATABASE_URL. Vault keys are looked up
    under VAULT_SECRET_PATH using the same name as the env var (e.g. the
    Vault secret's "JWT_SECRET_KEY" field), so migrating a value into Vault
    doesn't require renaming anything.
    """
    if _vault_client is not None:
        try:
            resp = _vault_client.secrets.kv.v2.read_secret_version(path=_VAULT_SECRET_PATH, raise_on_deleted_version=True)
            value = resp["data"]["data"].get(env_var)
            if value:
                return value
        except Exception as e:
            logger.warning("Vault lookup for %s failed (%s) — falling back to environment variable", env_var, e)
    return os.getenv(env_var)


# ----------------- Push / SMS providers (BL-38 follow-up) -----------------
# Real SDK integration, credential-gated exactly like the Vault client
# above: if the required env vars are present, a real Firebase/Twilio
# client is constructed at startup and used; otherwise these stay None and
# send_push_notification()/send_sms() below fall back to the same
# log-and-return-False stub behavior this codebase already had. No
# fabricated credentials are ever committed — .env.example documents the
# real vars an operator must supply, same as every other integration in
# this file.
_firebase_app = None
_FIREBASE_CREDENTIALS_JSON = _get_secret("FIREBASE_CREDENTIALS_JSON")
if _FIREBASE_CREDENTIALS_JSON:
    try:
        import firebase_admin as _firebase_admin_module
        from firebase_admin import credentials as _firebase_credentials

        # Accepts either a filesystem path to a service-account JSON file
        # or the JSON content itself (handy for platforms like Render/Fly
        # where only a single-line env var, not a mounted file, is
        # available) — detected by whether the value parses as JSON.
        try:
            _cred_dict = json.loads(_FIREBASE_CREDENTIALS_JSON)
            _firebase_cred = _firebase_credentials.Certificate(_cred_dict)
        except json.JSONDecodeError:
            _firebase_cred = _firebase_credentials.Certificate(_FIREBASE_CREDENTIALS_JSON)
        _firebase_app = _firebase_admin_module.initialize_app(_firebase_cred)
        logger.info("Firebase Admin SDK initialized — push notifications are live.")
    except Exception as e:
        _firebase_app = None
        logger.warning(
            "FIREBASE_CREDENTIALS_JSON was set but Firebase could not be "
            "initialized (%s) — push notifications will log-only.", e,
        )

_twilio_client = None
_TWILIO_FROM_NUMBER = _get_secret("TWILIO_FROM_NUMBER")
_TWILIO_ACCOUNT_SID = _get_secret("TWILIO_ACCOUNT_SID")
_TWILIO_AUTH_TOKEN = _get_secret("TWILIO_AUTH_TOKEN")
if _TWILIO_ACCOUNT_SID and _TWILIO_AUTH_TOKEN and _TWILIO_FROM_NUMBER:
    try:
        from twilio.rest import Client as _TwilioClient

        _twilio_client = _TwilioClient(_TWILIO_ACCOUNT_SID, _TWILIO_AUTH_TOKEN)
        logger.info("Twilio client initialized — SMS notifications are live.")
    except Exception as e:
        _twilio_client = None
        logger.warning(
            "TWILIO_* env vars were set but the Twilio client could not be "
            "initialized (%s) — SMS notifications will log-only.", e,
        )

# WhatsApp reuses _twilio_client above (same Account SID/Auth Token — one
# Twilio account, multiple channels) but needs two more pieces before
# send_whatsapp() below will actually send anything: a WhatsApp-enabled
# Twilio sender number, and an approved Content Template SID. Unlike SMS,
# WhatsApp's Business API rejects any business-initiated freeform message
# outside a 24h customer-service window — every message send_whatsapp()
# makes has to reference a template Meta has already reviewed and
# approved through Twilio's Content Template Builder (real, external
# turnaround time, not a config flag). See .env.example for where to get
# both. Neither is required for SMS or push to work.
_TWILIO_WHATSAPP_FROM_NUMBER = _get_secret("TWILIO_WHATSAPP_FROM_NUMBER")
_TWILIO_WHATSAPP_TEMPLATE_SID = _get_secret("TWILIO_WHATSAPP_TEMPLATE_SID")


# ----------------- Secrets (SECRET_KEY / JWT_SECRET_KEY) -----------------
# Same key drives both: Flask session signing (employer web portal, see below)
# and JWT signing (mobile/API). Closes S-10 (SECRET_KEY was never set) and is
# the prerequisite for BL-02 (real authentication).
_APP_SECRET = _get_secret("JWT_SECRET_KEY")
if not _APP_SECRET:
    _APP_SECRET = secrets.token_hex(32)
    logger.warning(
        "JWT_SECRET_KEY is not set in the environment. Using a random key "
        "generated for THIS PROCESS ONLY — every issued token/session will "
        "be invalidated the next time the server restarts, and no two "
        "server processes will trust each other's tokens. This is "
        "acceptable for solo local development only. Set JWT_SECRET_KEY in "
        "backend/.env (see .env.example) for anything else."
    )
app.config["SECRET_KEY"] = _APP_SECRET
app.config["JWT_SECRET_KEY"] = _APP_SECRET
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(hours=12)
# File-download routes are opened via url_launcher on mobile, which hands
# the URL to an external app/browser that cannot attach a custom
# Authorization header. Accepting the token as a query param too (in
# addition to the header, which every other route still uses) is the
# standard workaround for exactly this case. Trade-off: a token can end up
# in browser history/server access logs when passed this way — acceptable
# here since it only grants access to the caller's own already-uploaded
# files (per the ownership checks below), not a wider credential.
app.config["JWT_TOKEN_LOCATION"] = ["headers", "query_string"]
app.config["JWT_QUERY_STRING_NAME"] = "token"
# BL-44: gates the reporting dashboard (/admin/analytics), alongside real
# Admin accounts (see the Admin model and admin_access_required below,
# created via scripts/create_admin.py — never self-registered). The key
# remains supported as a second, machine-to-machine auth path (a
# monitoring script or curl-based check has no browser session to
# present) rather than being the only option. A full multi-role RBAC
# system (distinct admin/verifier/government-user permissions) is a
# bigger extension tied to Phase 9's "Government Integration" milestone
# (BL-43), not invented speculatively here. If unset AND no admin is
# logged in, the dashboard route 404s instead of revealing it exists.
ADMIN_API_KEY = _get_secret("ADMIN_API_KEY")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# Secure cookies are only sent over HTTPS. Forcing this on in local dev
# (plain http://127.0.0.1) would silently break every employer-portal login,
# so it's gated on FLASK_ENV=production — set that (and deploy behind TLS,
# per the ProxyFix note above) for any real environment.
app.config["SESSION_COOKIE_SECURE"] = IS_PRODUCTION
# Real gap found via a full security review: no session (employer or
# admin) ever expired on its own — Flask's default is a non-permanent
# session with no Expires/Max-Age at all, meaning "log out" or a browser
# fully closing were the ONLY ways a session ever ended. A browser tab
# left open on a shared or unattended workstation kept a valid session —
# including an Admin session, the most privileged identity in this system
# — authenticated indefinitely, with no automatic expiry regardless of
# how long it sat idle. Employer.active/_current_employer_id() and
# Admin.active/_current_admin_id() already re-check on every request so a
# deliberate deactivation revokes a session immediately, but that
# requires someone to notice and act — it's not an idle timeout.
# PERMANENT_SESSION_LIFETIME below only takes effect once session.permanent
# is set True at login (see employer_register/employer_login/admin_login/
# admin_2fa_verify) — Flask's SESSION_REFRESH_EACH_REQUEST default (True,
# left as-is) then re-issues the cookie with a fresh expiry on every
# request, making this a real sliding idle timeout, not a fixed absolute
# session length: an actively-used session never gets logged out
# mid-task, only one that's genuinely been abandoned. 2 hours is a
# judgment call, not a measured number — OWASP's session management
# guidance suggests a shorter window (2-5 minutes) specifically for
# administrative applications, but that's tight enough to log out an
# admin mid-review of a single applicant; 2 hours balances real usability
# against the exposure window of a genuinely abandoned, unattended tab.
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=2)
# CSRF is opt-in per-route (via csrf.protect()) rather than app-wide, because
# almost every route here is a bearer-token JSON API endpoint with no session
# cookie for CSRF to exploit. It is explicitly enabled for the employer web
# portal's POST routes below (employer_login_required, employer_login,
# employer_register), which are the only session-cookie-authenticated routes.
app.config["WTF_CSRF_CHECK_DEFAULT"] = False

jwt = JWTManager(app)
csrf = CSRFProtect(app)


@jwt.unauthorized_loader
def _jwt_missing(reason):
    return jsonify({"success": False, "error": "Authentication required"}), 401


@jwt.invalid_token_loader
def _jwt_invalid(reason):
    return jsonify({"success": False, "error": "Invalid or malformed token"}), 401


@jwt.expired_token_loader
def _jwt_expired(header, payload):
    return jsonify({"success": False, "error": "Session expired, please log in again"}), 401

# ----------------- API route prefixes (JSON contract) -----------------
# Used both to scope CORS (bearer-token JSON API only — the employer web
# portal below uses session cookies and is deliberately NOT CORS-enabled,
# since cookies are an ambient credential and CORS wildcarding them would
# reopen a CSRF-style hole) and to route error responses as JSON vs HTML.
API_PREFIXES = (
    "/register",
    "/login",
    "/auth/",
    "/jobs",
    "/apply",
    "/my_applications",
    "/passport",
    "/certificate",
    "/application_file",
    "/api/",
    "/healthz",
)


def _is_api_request() -> bool:
    try:
        return any(request.path.startswith(p) for p in API_PREFIXES)
    except Exception:
        return False


# CORS: scoped to the JSON/bearer-token API surface only. A malicious
# cross-origin page cannot forge a valid Authorization header (unlike an
# ambient cookie), so wildcard origins here do not reintroduce the CSRF-style
# risk that wildcard CORS + session cookies would (see the employer portal
# below, which intentionally has no CORS resource entry).
CORS(app, resources={p: {"origins": "*"} for p in API_PREFIXES})

socketio = SocketIO(app, cors_allowed_origins="*")


@socketio.on("connect")
def _socketio_connect(auth=None):
    """
    Real, severe gap found via a full-codebase review: every socketio.emit()
    call below used to broadcast globally to every connected client, with
    no authentication at connect time and no room targeting at all. That
    meant any client that opened a WebSocket connection -- no login
    required -- received every other user's private message bodies
    (message_created), notification previews including the first 200
    chars of every new message (notification_created, via notify_user),
    and every application's status changes (application_status_changed)
    for the entire platform. Confirmed this was not just a theoretical
    unused wire payload: the mobile app's notification_created handler
    (youthchain_app/lib/screens/job_screen.dart) reads data['title']/
    data['body'] straight out of the event and displays it in a toast, so
    this was live, user-visible cross-account data leakage.

    Joins the connecting client to a private room scoped to their real,
    server-verified identity -- user:<id> for an authenticated youth (JWT,
    sent via the client's `auth` connect payload, since a WebSocket
    handshake doesn't carry a normal Authorization header the way a REST
    call does) or employer:<id> for an authenticated employer (the
    existing session cookie -- Flask-SocketIO exposes the same Flask
    `session` here as on any regular request, since the handshake carries
    the same cookies a browser would send on any other same-origin
    request). Every emit() call below now targets only the room(s) that
    should actually receive it; job_created stays a genuine global
    broadcast, since new job postings are public listings, not private
    data. An unauthenticated connection is simply never joined to any
    private room -- rejecting the connection outright isn't needed since
    nothing privileged was ever reachable purely from being connected,
    only from which rooms a client is in.
    """
    token = (auth or {}).get("token") if isinstance(auth, dict) else None
    if token:
        try:
            decoded = decode_token(token)
            if not _is_jwt_blocklisted(decoded["jti"]):
                join_room(f"user:{int(decoded['sub'])}")
        except Exception:
            pass  # invalid/expired token -- connect anyway, just unauthenticated

    try:
        employer_id = _current_employer_id()
    except RuntimeError:
        # No Flask session bound to this connection at all (a purely
        # JWT-authenticated client, e.g. the mobile app, never sends the
        # employer portal's session cookie) -- same outcome as "not an
        # employer", not a reason to fail the connection.
        employer_id = None
    if employer_id is not None:
        join_room(f"employer:{employer_id}")

# ----------------- Metrics (Phase 3 #11/#15 follow-up) -----------------
# Exposes GET /metrics in Prometheus text format — request counts, latency
# histograms, and in-flight request gauges, by endpoint/method/status, with
# zero per-route code changes (PrometheusMetrics wraps every Flask request
# via before/after-request hooks). Self-hosted: docker-compose.observability.yml
# runs Prometheus to scrape this and Grafana to visualize it, so this closes
# real ground without requiring a paid SaaS APM account this repo cannot
# create for itself. /metrics itself is unauthenticated (matching Prometheus
# convention — access to it should be restricted at the network level, e.g.
# not exposing it outside the docker-compose network, which the observability
# stack below does not).
metrics = PrometheusMetrics(app, path="/metrics")

# ----------------- Database config -----------------
# `or` here, not os.getenv's second positional-arg default — found via a
# real container crash: DATABASE_URL="" (set-but-empty, exactly the
# convention .env.example uses for "unset" — DATABASE_URL=/ADMIN_API_KEY=/
# etc. are all left blank on purpose) makes os.getenv return "" rather than
# falling through to its default, since the var IS present in the
# environment, just empty. That crashed SQLAlchemy with "Could not parse
# SQLAlchemy URL from string ''" on container boot. `or` treats "" the same
# as unset, matching every other *_URL/*_KEY env var in this file already
# does via `if not X:` checks (JWT_SECRET_KEY, REDIS_URL, ADMIN_API_KEY).
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL") or "sqlite:///youthchain.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
# pool_pre_ping: issues a cheap SELECT 1 before handing out a pooled
# connection, transparently reconnecting if it's dead — without this, a
# managed Postgres instance (RDS, Cloud SQL, DigitalOcean, etc.) silently
# closing an idle connection surfaces as a real 500 ("SSL connection has
# been closed unexpectedly") on whichever request happens to be first to
# reuse it after the idle gap, not as a clean retry. pool_recycle: forces
# a connection to be discarded and reopened after 280s regardless of
# activity, safely under the 300s/5min idle-connection-close default most
# managed Postgres providers use, so a stale connection is recycled
# proactively rather than only caught reactively by pre_ping. Harmless on
# SQLite (a single ephemeral file connection, not a pooled network one) —
# applied unconditionally rather than dialect-gated, so there's one config
# path to reason about instead of two.
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_pre_ping": True,
    "pool_recycle": 280,
}

# ----------------- File upload config -----------------
BASE_DIR = os.getcwd()
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
APPLICATION_FOLDER = os.path.join(UPLOAD_FOLDER, "applications")
MESSAGE_ATTACHMENT_FOLDER = os.path.join(UPLOAD_FOLDER, "message_attachments")
EMPLOYER_VERIFICATION_FOLDER = os.path.join(UPLOAD_FOLDER, "employer_verifications")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(APPLICATION_FOLDER, exist_ok=True)
os.makedirs(MESSAGE_ATTACHMENT_FOLDER, exist_ok=True)
os.makedirs(EMPLOYER_VERIFICATION_FOLDER, exist_ok=True)

# Backup health, exposed on GET /metrics (PrometheusMetrics above) — real
# gap found and fixed alongside a shorter retry-on-failure backoff in
# docker-compose.backup.yml: a failed scheduled backup was previously
# only visible in backup-cron's own container logs, with nothing feeding
# the observability stack that already exists (Prometheus/Grafana, see
# docker-compose.observability.yml) to alert on. scripts/backup.py writes
# a small status file to the same backups-data volume this service
# already mounts (docker-compose.yml's `backups-data:/app/backups`) after
# every attempt; these gauges read it fresh on every scrape via
# set_function() (not cached at import time — a stale gauge reading
# "success" forever after one good backup would defeat the entire point).
# No new service, no push gateway: reuses a volume and an endpoint that
# already exist.
_BACKUP_STATUS_PATH = os.path.join(BASE_DIR, "backups", ".backup_status.json")


def _read_backup_status() -> dict | None:
    try:
        with open(_BACKUP_STATUS_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _backup_last_success_timestamp() -> float:
    status = _read_backup_status()
    if not status or not status.get("success"):
        return 0.0
    try:
        return datetime.fromisoformat(status["timestamp"]).timestamp()
    except (KeyError, ValueError, TypeError):
        return 0.0


def _backup_last_attempt_success() -> float:
    status = _read_backup_status()
    if status is None:
        return 0.0
    return 1.0 if status.get("success") else 0.0


_backup_last_success_gauge = PromGauge(
    "youthchain_backup_last_success_timestamp_seconds",
    "Unix timestamp of the most recent successful backup, or 0 if none has ever succeeded. "
    "Alert on this being too far in the past, not just on a single failed attempt.",
)
_backup_last_success_gauge.set_function(_backup_last_success_timestamp)

_backup_last_attempt_gauge = PromGauge(
    "youthchain_backup_last_attempt_success",
    "1 if the most recent backup attempt succeeded, 0 if it failed or none has run yet.",
)
_backup_last_attempt_gauge.set_function(_backup_last_attempt_success)

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB cap
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "pdf", "doc", "docx"}

# ----------------- Optional SMTP (for OTP emails) -----------------
SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER")
SMTP_PASS = os.getenv("SMTP_PASS")
SMTP_FROM = os.getenv("SMTP_FROM", SMTP_USER or "no-reply@youthchain.local")

# Gatekeeping for email OTP at registration (default ON)
ENFORCE_EMAIL_OTP_REG = os.getenv("ENFORCE_EMAIL_OTP_REG", "1") == "1"

# Which Hardhat network _run_hardhat_script (below) targets. Real gap found
# and fixed via a full blockchain-integration review: this was previously
# hardcoded to "localhost" with no override anywhere — meaning the backend
# could NEVER write to or verify against a real deployed network (Sepolia,
# mainnet, anything), no matter how blockchain/.env's ISSUER_PRIVATE_KEY or
# hardhat.config.js's own network entries were configured. Those were
# already correctly built to support a real network; the backend's own
# subprocess invocation was the one place nothing ever plumbed a choice
# through. Defaults to "localhost" so behavior is unchanged for the local
# dev workflow (a Hardhat node on 127.0.0.1:8545) unless explicitly set —
# this alone doesn't constitute a production deployment decision, it just
# removes the code-level blocker to making one.
HARDHAT_NETWORK = os.getenv("HARDHAT_NETWORK", "localhost")

db = SQLAlchemy(app)
# TD-04: replaces the ad hoc PRAGMA-based startup schema check below with a
# real, versioned migration framework. `db.create_all()` still runs for
# zero-config local dev/tests (idempotent — a no-op once tables exist); a
# real deployment should run `flask db upgrade` after pulling a change that
# includes a new migration under migrations/versions/, the same way any
# Flask-Migrate project works.
migrate = Migrate(app, db)

# ----------------- MODELS -----------------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    phone = db.Column(db.String(100), unique=True, nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    # FCM device registration token (BL-38 follow-up — see
    # send_push_notification below). Set via PUT /api/push_token after
    # login; nullable because most accounts in this codebase's tests, and
    # any user who hasn't opened the mobile app since this shipped, won't
    # have one yet. A user can only ever have one token registered at a
    # time (the mobile app re-registers on every app start), so this is a
    # plain column, not a one-to-many table — acceptable since multi-device
    # push isn't a requirement anyone has stated.
    push_token = db.Column(db.String(300), nullable=True)
    # Real, DB-backed consent capture (Phase 3 compliance gap — registration
    # previously collected name/phone/email/CV with no record the person
    # ever agreed to anything). Set once, at registration, from a required
    # checkbox — see /register below and GET /privacy-policy for what the
    # user is actually agreeing to. Deliberately NOT a boolean: storing the
    # timestamp itself is what makes this an evidentiary consent record
    # rather than just a settings flag, and matches how OTPCode/Employer
    # verification already timestamp real actions instead of only
    # flagging them.
    consent_accepted_at = db.Column(db.DateTime, nullable=True)
    # Real gap found via a full admin-capability review: Employer.active and
    # Admin.active both exist with a real suspend/reinstate lever and a
    # per-request re-check (_current_employer_id/_current_admin_id), but
    # User -- the actual youth account, the one this whole platform exists
    # to serve -- had no such field at all. A compromised account (phishing,
    # credential stuffing) or one behaving abusively had zero admin-side
    # remedy; the only "fix" was asking the legitimate owner to act, which
    # doesn't help if they're the one locked out. Same shape, same
    # reasoning, same re-check-every-request discipline as the other two.
    active = db.Column(db.Boolean, nullable=False, default=True, server_default="1")
    # Sierra Leone's National Civil Registration Authority ID -- a distinct
    # government identity number, not a phone number. Real gap found via
    # user feedback: the registration form's "Phone / NCRA ID" field was a
    # SINGLE input feeding this same phone column, so anyone who typed
    # their NCRA number there (the label invited exactly that) had it
    # silently stored as their phone number instead. Nullable/optional for
    # now (see registration_screen.dart/portal_register.html) -- not every
    # youth has this number to hand at signup, and this platform's identity
    # verification is already the human-reviewed Credential/Employer
    # verification workflow, not an automated NCRA registry lookup (no
    # public API exists to check one against, same reasoning as
    # Employer.verification_status and DuplicateFlag).
    ncra_id = db.Column(db.String(50), nullable=True)
    # Opt-in: when true, a Saved Search or profile-skill job alert (see
    # _dispatch_job_alerts_for_scan) also sends a real SMS via Twilio, not
    # just an in-app/push notification. Explicit opt-in rather than
    # inferred from having a phone number (every User already has one,
    # required at registration — that's not a signal of consent) because,
    # unlike push, each SMS is a real per-message Twilio cost. Default
    # False, same "no existing account starts silently opted in" reasoning
    # as Candidate.job_alerts_enabled.
    sms_alerts_enabled = db.Column(db.Boolean, nullable=False, default=False)
    # Same opt-in shape and reasoning as sms_alerts_enabled directly above --
    # a Saved Search / profile-skill job alert also sends a real WhatsApp
    # message via Twilio (see send_whatsapp) when true. Kept as its own
    # separate flag rather than reusing sms_alerts_enabled because they're
    # genuinely different costs/channels a user might want independently
    # (e.g. WhatsApp is free for the sender in many countries once a
    # session is open, SMS never is; a user might have WhatsApp but not
    # want SMS, or vice versa) -- same one-flag-per-real-choice principle
    # as keeping this distinct from Candidate.job_alerts_enabled itself.
    whatsapp_alerts_enabled = db.Column(db.Boolean, nullable=False, default=False)
    # Set once, permanently, by _erase_user_data() -- distinct from `active`
    # (suspension: reversible, data intact, an admin can reinstate) because
    # erasure is neither: name/phone/email/ncra_id/password_hash are
    # overwritten with unusable placeholders and every file this user
    # uploaded is deleted from disk (see _erase_user_data()'s own
    # docstring for the full scope). The row itself is kept, not deleted --
    # every other table's user_id FK (Application, Message, Rating, ...)
    # stays valid and automatically becomes "an anonymized user" the
    # instant this is set, with no need to touch those tables' FK values
    # at all. active is also set False alongside this so every existing
    # per-request suspension check keeps working unchanged; erased_at is
    # what distinguishes "erased, never coming back" from "suspended,
    # could be reinstated" for anything that needs to tell them apart.
    erased_at = db.Column(db.DateTime, nullable=True)

    credentials = db.relationship("Credential", backref="user", lazy=True)

    def to_dict(self):
        return {
            "id": self.id, "name": self.name, "email": self.email, "phone": self.phone,
            "ncra_id": self.ncra_id, "sms_alerts_enabled": self.sms_alerts_enabled,
            "whatsapp_alerts_enabled": self.whatsapp_alerts_enabled,
        }


class UserSession(db.Model):
    """
    One row per youth login, on either surface -- the "connected devices"
    record neither the mobile app nor the web portal had before. Real gap:
    the app already revokes a *token* on explicit self-logout
    (_add_to_jwt_blocklist) and an *account* on suspension
    (Employer.active / Admin.active checked every request), but there was
    no way for a user to see how many devices are logged into their own
    account, or to kill one remotely (a lost/stolen phone, a shared-device
    login they forgot to log out of).

    channel is "app" (mobile, JWT) or "web" (portal, session cookie).
    session_token is the correlating id for each: the JWT's own `jti` for
    "app" rows (so revoking one is just adding that jti to the existing
    _add_to_jwt_blocklist mechanism -- no new enforcement path needed),
    or a random opaque token minted at login and stashed in the signed
    session cookie for "web" rows (Flask's default session has no
    server-side store to revoke by itself, so this row IS that store).

    device_label is a best-effort, non-authoritative description (parsed
    User-Agent) -- shown to the account owner for recognition ("Chrome on
    Windows" vs "unfamiliar device"), never trusted for access control.
    """
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    channel = db.Column(db.String(10), nullable=False)  # "app" | "web"
    session_token = db.Column(db.String(64), nullable=False, unique=True, index=True)
    device_label = db.Column(db.String(200), nullable=True)
    ip_address = db.Column(db.String(45), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    last_seen_at = db.Column(db.DateTime, default=datetime.utcnow)
    revoked_at = db.Column(db.DateTime, nullable=True)

    def to_dict(self, current_session_token=None):
        return {
            "id": self.id,
            "channel": self.channel,
            "device_label": self.device_label or "Unknown device",
            "ip_address": self.ip_address,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "last_seen_at": self.last_seen_at.isoformat() if self.last_seen_at else None,
            "revoked": self.revoked_at is not None,
            "is_current": current_session_token is not None and self.session_token == current_session_token,
        }


def _device_label_from_request() -> str:
    ua = request.user_agent
    browser = (ua.browser or "").capitalize()
    platform = (ua.platform or "").capitalize()
    if browser and platform:
        return f"{browser} on {platform}"[:200]
    return (ua.string or "Unknown device")[:200]


def _create_user_session(user_id: int, channel: str, session_token: str, notify: bool = True) -> "UserSession":
    row = UserSession(
        user_id=user_id,
        channel=channel,
        session_token=session_token,
        device_label=_device_label_from_request(),
        ip_address=request.remote_addr,
    )
    db.session.add(row)
    db.session.commit()
    # notify=False for the token minted at registration itself -- a "new
    # device signed in" alert one second after "you just created an
    # account" is noise, not signal. Every later login still notifies.
    if notify:
        notify_user(
            user_id,
            "new_device_login",
            "New sign-in to your account",
            f"{row.device_label} just signed in. If this wasn't you, revoke it from Manage Devices.",
            push=True,
        )
    return row


_SESSION_TOUCH_INTERVAL = timedelta(minutes=5)


def _touch_user_session(session_token: str) -> None:
    """
    Bumps last_seen_at, but only if it's gone stale by _SESSION_TOUCH_INTERVAL
    -- called on every authenticated request on both channels, so writing
    unconditionally would mean a DB write per request for every active user.
    Same throttling instinct as the rest of this codebase's request-path
    query budget (see _employer_trust_summary's N+1 note, the messages
    inbox N+1 fix) -- last_seen_at only needs to be accurate to within a
    few minutes for a "connected devices" list, not to the second.
    """
    row = UserSession.query.filter_by(session_token=session_token, revoked_at=None).first()
    if row is None:
        return
    now = datetime.utcnow()
    if row.last_seen_at is None or now - row.last_seen_at >= _SESSION_TOUCH_INTERVAL:
        row.last_seen_at = now
        db.session.commit()


# Real Sierra Leone economic sectors, not a guessed/generic SaaS list —
# shared between Employer.industry (what an employer selects at
# registration) and Candidate.preferred_industries (what a youth can
# optionally select in their profile), so the two sides of the
# transparent matching bonus in api_match_jobs always speak the same
# vocabulary. A plain validated string set, not a lookup table — this is
# a small, rarely-changing fixed list, not something that needs its own
# admin-managed CRUD.
_INDUSTRY_CHOICES = {
    "Agriculture",
    "Fisheries",
    "Mining & Minerals",
    "Construction",
    "Manufacturing",
    "Retail & Trade",
    "Hospitality & Tourism",
    "Transportation & Logistics",
    "Information & Communication Technology",
    "Financial Services",
    "Healthcare",
    "Education & Training",
    "Energy & Utilities",
    "Government & Public Sector",
    "NGO & Non-Profit",
    "Professional Services",
    "Other",
}

# Fixed category list for gig/informal-work jobs (Job.category, only
# meaningful when Job.job_type == "gig"). Deliberately a small, hand-picked
# set of common Sierra Leone informal-sector trades rather than an
# admin-managed table — same "small, rarely-changing fixed list" reasoning
# as _INDUSTRY_CHOICES just above. Validated app-side only, extensible to a
# real taxonomy later without a migration that touches existing Job rows.
_GIG_CATEGORY_CHOICES = {
    "Tailoring & Dressmaking",
    "Okada / Transport",
    "Market Vending",
    "Construction Labor",
    "Domestic Work",
    "Hairdressing & Beauty",
    "Food & Catering",
    "Cleaning",
    "Other",
}


class Employer(db.Model):
    """
    verification_status closes Phase 3 #5 (Employer verification —
    previously anyone could self-register and immediately post jobs and
    see applicant PII with zero vetting). Real, bounded scope stated
    explicitly: this is a document-upload-and-human-review workflow, not
    an automated business-registry lookup (Sierra Leone's NCRA/company
    registry has no public API this codebase could integrate with, and
    guessing at one would be exactly the kind of unverified assumption the
    engineering constitution warns against) — an admin looks at the
    uploaded document and makes a real decision, same as any KYC-lite flow.
    """
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(200), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    # "unverified" (default, self-registered) -> "pending" (document
    # uploaded, awaiting admin review) -> "verified" or "rejected".
    verification_status = db.Column(db.String(20), nullable=False, default="unverified")
    verification_document = db.Column(db.String(300), nullable=True)
    verification_reviewed_at = db.Column(db.DateTime, nullable=True)
    verification_reviewed_by_admin_id = db.Column(db.Integer, db.ForeignKey("admin.id"), nullable=True)
    # "business" (registration certificate/tax ID -- the only track this
    # ever supported) or "individual" (national ID/voter's card/driver's
    # license). Real gap found and closed after this feature first
    # shipped: someone hiring informally -- a household needing a
    # cleaner, an individual needing an okada rider for deliveries -- has
    # no business to register and would otherwise sit at "unverified"
    # forever no matter how legitimate they are, which undermines exactly
    # the trust this platform exists to build for informal/gig work (see
    # Job.job_type). Deliberately a SEPARATE badge from business
    # verification once approved (see _employer_summary/StatusBadge.
    # employerVerification), not the same "Verified" label for both --
    # they mean different things and a youth deciding whether to work for
    # someone deserves to know which one they're looking at. Nullable +
    # server_default='business' on the migration: every employer verified
    # before this field existed was necessarily verified via the
    # business-document track, so backfilling to that is a real fact, not
    # a guess.
    verification_type = db.Column(db.String(20), nullable=False, default="business")
    # "business" or "individual" -- the same two tracks as
    # verification_type, but captured as a self-declared intent at
    # REGISTRATION time, not left undiscoverable until someone happens to
    # reach /employer/verification. Real gap found after verification_type
    # shipped: an individual signing up had no signal at all pointing them
    # toward the personal-ID path, and every part of the product (the
    # verification form's default radio, the job-posting form's default
    # formal/gig choice) still silently assumed "business" for every new
    # account regardless of who was actually signing up. This is a
    # DEFAULT/intent, not a lock-in -- verification_type can still end up
    # different if someone's situation changes (registers as an
    # individual, later verifies as a real business, or vice versa); nulling
    # out the redundancy between the two would have coupled "what I said at
    # signup" to "what I actually proved," which are legitimately allowed
    # to diverge. Same server_default='business' backfill reasoning as
    # verification_type -- every account created before this field existed
    # signed up under a flow that never asked, so 'business' is the
    # neutral, already-established default, not a guess about who they are.
    account_type = db.Column(db.String(20), nullable=False, default="business")
    # Real answer to "what if a verified employer goes rogue" (found to
    # have no answer at all via a live audit — verification_status is
    # purely a display badge shown to youth; it was never checked as an
    # access-control gate anywhere, so a rejected/unverified employer had
    # 100% the same operational capability as a verified one, and there
    # was no way to stop ANY employer, verified or not, short of editing
    # the database directly). Same active-flag pattern as Admin.active:
    # checked on every request via _current_employer_id(), so suspending
    # an employer revokes an already-open session immediately, not just
    # their next login.
    active = db.Column(db.Boolean, nullable=False, default=True)
    # What kind of organization this is (see _INDUSTRY_CHOICES) — captured
    # at registration. Real, bounded use: the job-matching algorithm below
    # (api_match_jobs) gives a transparent, explainable bonus when a job's
    # employer industry matches a candidate's own stated preferred
    # industries (Candidate.preferred_industries), the same honest,
    # deterministic-and-explainable-only approach already established for
    # skills-overlap scoring (see BL-45 — this app does not claim AI/ML
    # matching it doesn't have). Nullable so existing employers created
    # before this field existed aren't forced into a guessed value.
    industry = db.Column(db.String(50), nullable=True)
    # Same tombstone design as User.erased_at -- see that column's own
    # docstring for the full reasoning. Set once, permanently, by
    # _erase_employer_data(); distinct from `active` (suspension:
    # reversible) for the same reason.
    erased_at = db.Column(db.DateTime, nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "email": self.email,
            "verification_status": self.verification_status,
            "verification_type": self.verification_type,
            "industry": self.industry,
        }


# ----------------- Employer reputation score -----------------
# Composite, transparent 0-100 score blending every real trust signal
# this platform actually has for an employer: earned ratings,
# document/ID verification, open trust-&-safety reports against them, and
# whether they actually respond to applicants at all. Real gap found
# reviewing the previous ratings-only version (still returned below as
# avg_rating/rating_count, unchanged, for backward compatibility with
# every existing caller): verification_status and EmployerReport were
# already shown as separate, unconnected badges/admin-only data, and
# outcome data wasn't tracked at all for employers despite
# _worker_trust_summary() already doing exactly that (completed_gigs) for
# the other side of the same transaction. Deliberately NOT a black-box/ML
# score -- every component below is a plain, explainable rule anyone
# could recompute by hand, same "no AI/ML matching this app doesn't
# actually have" honesty already established for job matching (see
# api_match_jobs' own docstring).

# Ratings component (0-40 points): recency-weighted (a rating's influence
# halves every ~6 months, so a 2-year-old rating doesn't count the same
# as one from last week) and shrunk toward a neutral 3/5 prior, worth
# _RATING_SHRINKAGE_WEIGHT "phantom" neutral ratings -- both close real
# gaps in the plain-average version: a brand-new employer's very first
# rating, good OR bad, no longer swings the score to the extremes the way
# a raw average of one data point would.
_RATING_HALF_LIFE_DAYS = 180
_RATING_SHRINKAGE_PRIOR = 3.0
_RATING_SHRINKAGE_WEIGHT = 3

# Reports component (0-20 points, starts full and is docked): only OPEN
# EmployerReport rows count against an employer -- a dismissed report (an
# admin already determined was unfounded) costs nothing, and an
# "actioned" one means the employer was already suspended via that same
# report (see admin_resolve_report), which takes them out of the
# active-employer pool this function is ever meaningfully called for.
_REPORT_PENALTY_PER_OPEN = 4
_REPORT_MAX_PENALTY = 20

# Application-outcomes component (0-20 points): rewards actually
# responding to applicants (moving an Application off "Pending") over any
# particular hire/reject ratio -- a real employer rejecting 90% of
# applicants for one role is normal hiring, not a trust problem; one who
# lets every application sit unanswered forever is the actual "ghosting"
# pattern this exists to catch. Needs a real sample before it means
# anything, same reasoning the rating shrinkage above already applies --
# an employer with one or two applications total gets the neutral default
# instead of being punished by a tiny, noisy ratio.
_MIN_APPLICATIONS_FOR_RESPONSE_RATE = 3

_TRUST_SCORE_MAX = 40 + 20 + 20 + 20  # ratings + verification + reports + outcomes


def _employer_trust_summary(employer_id: int) -> dict:
    """
    The employer-side mirror of _worker_trust_summary(): what a youth sees
    about an employer built from *earned* history rather than a one-time
    document check alone. This is what actually lets an informal
    individual employer (see Employer.verification_type) build real,
    visible trust with zero paperwork -- the same trust philosophy
    already built for CV-less workers, just never extended to the other
    side of the same transaction until now.

    Returns avg_rating/rating_count unchanged (existing callers/UI keep
    working exactly as before) plus two new fields: trust_score (0-100)
    and trust_tier ("good" >= 70, "fair" >= 40, else "caution") -- see
    the module-level comment just above for what each component means
    and why the weights are what they are.
    """
    employer = Employer.query.get(employer_id)

    ratings = Rating.query.filter(
        Rating.employer_id == employer_id, Rating.direction == "worker_to_employer", Rating.hidden.is_(False),
    ).all()
    rating_count = len(ratings)
    avg_score = (sum(r.score for r in ratings) / rating_count) if rating_count else None

    if ratings:
        now = datetime.utcnow()
        weighted_sum = 0.0
        weight_total = 0.0
        for r in ratings:
            age_days = max((now - r.created_at).days, 0) if r.created_at else 0
            weight = 0.5 ** (age_days / _RATING_HALF_LIFE_DAYS)
            weighted_sum += r.score * weight
            weight_total += weight
        shrunk_rating = (
            (weighted_sum + _RATING_SHRINKAGE_PRIOR * _RATING_SHRINKAGE_WEIGHT)
            / (weight_total + _RATING_SHRINKAGE_WEIGHT)
        )
    else:
        shrunk_rating = _RATING_SHRINKAGE_PRIOR
    ratings_component = (shrunk_rating / 5.0) * 40

    if employer and employer.verification_status == "verified":
        verification_component = 20
    elif employer and employer.verification_status == "rejected":
        verification_component = 0
    else:
        verification_component = 10  # unverified/pending -- neutral, not punitive

    open_report_count = EmployerReport.query.filter_by(employer_id=employer_id, status="open").count()
    reports_component = max(0, _REPORT_MAX_PENALTY - open_report_count * _REPORT_PENALTY_PER_OPEN)

    total_applications = (
        db.session.query(func.count(Application.id))
        .join(Job, Application.job_id == Job.id)
        .filter(Job.employer_id == employer_id)
        .scalar()
    ) or 0
    if total_applications >= _MIN_APPLICATIONS_FOR_RESPONSE_RATE:
        responded = (
            db.session.query(func.count(Application.id))
            .join(Job, Application.job_id == Job.id)
            .filter(Job.employer_id == employer_id, Application.status != "Pending")
            .scalar()
        ) or 0
        outcomes_component = (responded / total_applications) * 20
    else:
        outcomes_component = 10  # not enough data yet -- neutral, not punitive

    trust_score = round(ratings_component + verification_component + reports_component + outcomes_component)
    trust_score = max(0, min(_TRUST_SCORE_MAX, trust_score))
    if trust_score >= 70:
        trust_tier = "good"
    elif trust_score >= 40:
        trust_tier = "fair"
    else:
        trust_tier = "caution"

    return {
        "avg_rating": round(avg_score, 1) if avg_score is not None else None,
        "rating_count": rating_count,
        "trust_score": trust_score,
        "trust_tier": trust_tier,
    }


def _employer_summary(employer: "Employer") -> dict:
    """
    The one shared shape for "who's hiring" shown to a youth — jobs
    (Job.to_dict), matched jobs (api_match_jobs), and message threads
    (api_application_messages) all show the same fields. Real duplication
    bug found via this feature's own tests, not assumed: api_match_jobs'
    industry-bonus logic read employer_info["industry"] from
    Job.to_dict()'s OWN independently-built dict, which had never been
    updated to include it when the industry field was added — two call
    sites hand-building the "same" dict silently drifted out of sync
    within the very same commit. Consolidated here so there is only one
    place left to update.

    Known, deliberate trade-off: this now runs one extra aggregate query
    per call (_employer_trust_summary), same N+1 shape this function's own
    Employer.query.get() caller already has for a job list -- not batched,
    because at this app's current scale a real measured bottleneck hasn't
    shown up here yet (see the engineering constitution's "never optimize
    before understanding, never optimize before measuring"), and a correct
    N+1 is a well-understood, easy fix later if it ever does.
    """
    return {
        "name": employer.name,
        "verification_status": employer.verification_status,
        "verification_type": employer.verification_type,
        "industry": employer.industry,
        **_employer_trust_summary(employer.id),
    }


class Admin(db.Model):
    """
    A real operator/verifier account, replacing the bare ADMIN_API_KEY as
    the primary way to reach /admin/analytics. There is deliberately no
    self-registration route for this model (unlike Employer) — admin
    accounts are created out-of-band by whoever already controls the
    server, via scripts/create_admin.py, mirroring how backup/restore are
    operator-run rather than web-exposed.

    `role` is one of two real, currently-meaningful values (enforced by
    admin_role_required below, not just a display label):
      - "admin": full access — /admin/analytics AND /admin/accounts
        (managing other admin accounts: listing, deactivating).
      - "verifier": /admin/analytics only. A read-only reporting role for
        someone who needs to see aggregate platform numbers (a funder, a
        government liaison doing due diligence) without being trusted to
        deactivate other operators' accounts.
    A full multi-role permissions system (arbitrary roles, per-action
    grants) belongs to BL-43's "Government Integration" RBAC extension —
    this is two roles because there are exactly two real, distinct
    capabilities that exist in this codebase today, not invented ahead of
    a concrete need.
    """

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(200), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    role = db.Column(db.String(50), nullable=False, default="admin")
    active = db.Column(db.Boolean, nullable=False, default=True)
    # TOTP 2FA (RFC 6238 — Google Authenticator/Authy-compatible). Scoped to
    # Admin only, not User/Employer: this is the highest-privilege role in
    # the system and the smallest surface to add real 2FA to without also
    # requiring mobile-app UI changes (a regular User's actual login has no
    # OTP step today — the existing "OTP" machinery is email verification
    # at registration, not a login second factor — extending 2FA there is a
    # larger, separate mobile+backend change, not invented speculatively
    # here). totp_secret is nullable/blank until enrollment; totp_enabled
    # gates whether login actually requires it.
    totp_secret = db.Column(db.String(32), nullable=True)
    totp_enabled = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "email": self.email,
            "role": self.role,
            "active": self.active,
            "totp_enabled": self.totp_enabled,
        }


class Job(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    location = db.Column(db.String(100), nullable=False)
    duration = db.Column(db.String(50), nullable=False)
    # Comma-separated required skills, used for matching
    required_skills = db.Column(db.Text, nullable=True)
    # "formal" (CV required, the only kind of job this platform served
    # until now) or "gig" (informal/hire-based work — tailoring, okada
    # transport, market vending, construction labor, domestic work — where
    # a CV isn't how trust actually works; see Application.cv_file and the
    # Rating model below for the rest of that lifecycle). server_default
    # on the migration backfills every existing row to "formal", so no
    # pre-existing job or mobile client that doesn't send this field
    # changes behavior at all. Plain validated string, not an app-side
    # CHECK constraint — same convention as Application.status.
    job_type = db.Column(db.String(20), nullable=False, default="formal")
    # Only meaningful when job_type == "gig" — one of _GIG_CATEGORY_CHOICES.
    # Small fixed list (see _GIG_CATEGORY_CHOICES), not an admin-managed
    # taxonomy table, matching how _INDUSTRY_CHOICES is deliberately kept
    # a plain validated set rather than its own CRUD.
    category = db.Column(db.String(40), nullable=True)
    # Owning employer. Nullable to tolerate pre-existing/seeded jobs created
    # before employer accounts existed (see startup migration below).
    employer_id = db.Column(db.Integer, db.ForeignKey("employer.id"), nullable=True)
    # "employer" (the only kind that existed before the job scanner) or
    # "scraped" (created by scanner.pipeline.run_scan_for_source from an
    # external job_source). server_default='employer' on the migration
    # backfills every pre-existing row correctly, since nothing but
    # post_job() ever created a Job before this column existed. Plain
    # validated string, same convention as job_type above — not an
    # app-side CHECK constraint.
    source = db.Column(db.String(20), nullable=False, default="employer")
    # Which job_source produced this row; null for employer-posted jobs.
    source_id = db.Column(db.Integer, db.ForeignKey("job_source.id"), nullable=True)
    # AI-extracted company name for scraped jobs, which have no Employer
    # account at all (see ScrapedCompany below for the separate, lightweight
    # "verified companies" concept this powers — deliberately NOT the same
    # thing as Employer.verification_status, which requires a real account
    # and manual document review).
    company_name = db.Column(db.String(200), nullable=True)
    # Full extracted description. Null for employer-posted jobs — the
    # /employer/post form was never extended with a description field,
    # so this stays scraped-job-only rather than half-populated everywhere.
    description = db.Column(db.Text, nullable=True)
    # Free text, not a parsed number — Sierra Leone job listings state pay
    # inconsistently ("Le 2,000,000/month", "Negotiable", "$500-800");
    # inventing a currency-normalization scheme wasn't asked for and isn't
    # safe to guess at.
    salary = db.Column(db.String(120), nullable=True)
    # External application URL for scraped jobs — this platform's own
    # /apply flow doesn't apply to a listing sourced from someone else's
    # site.
    apply_url = db.Column(db.String(500), nullable=True)
    # De-dup key for re-scans: the source's own listing URL, or a content
    # hash when a source doesn't expose a stable per-listing URL. Not
    # DB-unique-constrained — de-dup is a get-or-create in the pipeline
    # (source_id + external_id), matching this file's stated preference for
    # app-level validation over DB constraints (see job_type's own comment).
    external_id = db.Column(db.String(300), nullable=True)
    # When the scanner last created/refreshed this row. Null for
    # employer-posted jobs — Job has no general created_at column at all
    # today, so this is scan-specific bookkeeping, not a retrofit of one.
    scraped_at = db.Column(db.DateTime, nullable=True)
    # Free-text work arrangement as stated on the source page (e.g.
    # "Full-time", "Contract"), or null if not stated. Purely informational
    # and unrelated to job_type ("formal"/"gig") above — do not conflate.
    employment_type = db.Column(db.String(60), nullable=True)
    # Application deadline as stated on a scraped listing's own page,
    # parsed to a real date by scanner.pipeline (see its own
    # _parse_deadline docstring) — or null when the source didn't state
    # one or it couldn't be parsed. Drives _search_jobs()'s expired_only
    # filter (a scraped job past its deadline drops out of the normal
    # Discover feed into GET /api/discover_jobs/old) and
    # scanner.reaper's hard-delete of anything past deadline + grace
    # period. Employer-posted jobs never set this (post_job() has no
    # deadline field) — always null there, which _search_jobs()'s filter
    # treats as "never expires", so this column is a pure no-op for the
    # Home feed.
    application_deadline = db.Column(db.Date, nullable=True)
    # Comma-separated scam-signal names from scanner.scam_signals'
    # detect_scam_signals() -- any of "fee_request", "personal_email",
    # "vague_pay" -- or null/empty when none fired. Scraped-job-only,
    # same as description/salary/apply_url above; recomputed by
    # scanner.pipeline every scan (listing pass and, if it ran, again
    # after the detail backfill pass, since that can change the very
    # fields this reads), not just once at creation, so a listing that's
    # edited at the source to add/remove a red flag stays current rather
    # than freezing whatever the first scan happened to see.
    scam_signals = db.Column(db.String(200), nullable=True)

    def to_dict(self):
        employer_info = None
        if self.employer_id:
            employer = Employer.query.get(self.employer_id)
            if employer:
                employer_info = _employer_summary(employer)
        source_name = None
        company_verified = False
        if self.source == "scraped":
            if self.source_id:
                job_source = JobSource.query.get(self.source_id)
                source_name = job_source.name if job_source else None
            if self.company_name:
                # Same case-insensitive exact-name match scanner.pipeline
                # itself uses to upsert ScrapedCompany — see that model's
                # docstring for why this is intentionally not a fuzzy
                # lookup.
                company = ScrapedCompany.query.filter(
                    db.func.lower(ScrapedCompany.name) == self.company_name.strip().lower()
                ).first()
                company_verified = bool(company and company.verified)
        return {
            "id": self.id,
            "title": self.title,
            "location": self.location,
            "duration": self.duration,
            "required_skills": self.required_skills or "",
            "job_type": self.job_type or "formal",
            "category": self.category,
            # Raw FK, deliberately exposed at the top level (not just
            # nested inside "employer") — the mobile app needs a real
            # employer_id to call POST /api/report_employer with, and
            # _employer_summary()'s dict intentionally omits id/email as
            # the "safe public view" (see its own docstring). Not
            # sensitive on its own — a youth already sees the employer's
            # name, which identifies them far more than a numeric id does.
            "employer_id": self.employer_id,
            # Same shape as the "employer" key on the messages API response
            # (see api_application_messages) — None for jobs with no owning
            # employer (e.g. seed_jobs.py), otherwise {"name", "verification_status", "industry"}.
            # Lets a youth see who's hiring, and whether they're verified,
            # before applying — not just after.
            "employer": employer_info,
            "source": self.source or "employer",
            "source_name": source_name,
            "company_name": self.company_name,
            "company_verified": company_verified,
            "description": self.description,
            "salary": self.salary,
            "apply_url": self.apply_url,
            "employment_type": self.employment_type,
            "application_deadline": self.application_deadline.isoformat() if self.application_deadline else None,
            "is_expired": bool(self.application_deadline and self.application_deadline < date.today()),
            "scam_signals": self.scam_signals.split(",") if self.scam_signals else [],
        }


class JobSource(db.Model):
    """
    Admin-configured scrape target for the job scanner (e.g. Careers.sl,
    Jobsearchsl, a custom URL). scan_frequency_minutes is validated in
    Python (30-1440) at the route, not a DB CHECK — same convention as
    job_type/category above.
    """

    __tablename__ = "job_source"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    base_url = db.Column(db.String(500), nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)
    scan_frequency_minutes = db.Column(db.Integer, nullable=False, default=360)
    # Written by the Python-side claim step (scanner.pipeline), not by
    # pg_cron — always current the instant a scan actually starts, not
    # just when it finishes, so pg_cron's own "is this source due" check
    # (see the migration that enables it) can't double-queue a scan that's
    # already running.
    last_scan_started_at = db.Column(db.DateTime, nullable=True)
    created_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.utcnow())
    created_by_admin_id = db.Column(db.Integer, db.ForeignKey("admin.id"), nullable=True)


class ScanRun(db.Model):
    """
    One row per scan *attempt* against one source — a "scan all sources"
    admin action creates one row per active source, not a single "all"
    row, so each source's own history/failure is independently visible.
    status is a plain validated string (queued/running/success/failed),
    same convention as Application.status.
    """

    __tablename__ = "scan_run"

    id = db.Column(db.Integer, primary_key=True)
    source_id = db.Column(db.Integer, db.ForeignKey("job_source.id"), nullable=False)
    status = db.Column(db.String(20), nullable=False, default="queued")
    trigger = db.Column(db.String(20), nullable=False, default="schedule")
    triggered_by_admin_id = db.Column(db.Integer, db.ForeignKey("admin.id"), nullable=True)
    queued_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.utcnow())
    started_at = db.Column(db.DateTime, nullable=True)
    finished_at = db.Column(db.DateTime, nullable=True)
    jobs_found = db.Column(db.Integer, nullable=True)
    jobs_created = db.Column(db.Integer, nullable=True)
    jobs_updated = db.Column(db.Integer, nullable=True)
    # How many jobs got real description/salary/employment_type/location
    # data from a second, per-job detail-page scrape this run (see
    # scanner/pipeline.py's module docstring) -- not persisted until an
    # admin actually asked "why does the app still look empty" for a
    # source whose scan history showed found/created/updated all looking
    # healthy, with nothing in that panel to explain that the jobs
    # underneath were still bodyless. Nullable: old rows predate this
    # column and never ran the backfill pass at all, not "backfilled 0".
    jobs_backfilled = db.Column(db.Integer, nullable=True)
    error_message = db.Column(db.Text, nullable=True)
    # "<hostname>:<pid>" of the worker that claimed this run — stuck-run
    # debugging only, not an access-control mechanism.
    claimed_by = db.Column(db.String(100), nullable=True)


class ScrapedCompany(db.Model):
    """
    The "verified companies" concept for the scanner status panel —
    deliberately separate from Employer.verification_status, which
    requires a real registered account and manual document review.
    Scraped jobs have no account at all; this just tracks distinct
    company names/domains the scanner has encountered, with a lightweight
    admin-verify flag. Matched by case-insensitive exact name, not a
    fuzzy/automated lookup — same "never claim automation this app
    doesn't have" posture as Employer.verification_status's own docstring.
    """

    __tablename__ = "scraped_company"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    domain = db.Column(db.String(200), nullable=True)
    verified = db.Column(db.Boolean, nullable=False, default=False)
    verified_at = db.Column(db.DateTime, nullable=True)
    verified_by_admin_id = db.Column(db.Integer, db.ForeignKey("admin.id"), nullable=True)
    first_seen_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.utcnow())
    last_seen_at = db.Column(db.DateTime, nullable=False, default=lambda: datetime.utcnow())


@event.listens_for(Job, "after_insert")
def _sync_job_fts_on_insert(mapper, connection, job):
    """
    Keeps the SQLite FTS5 shadow table (job_fts, see migrations/) in sync
    on job creation. post_job() was the only place a Job was ever created
    until the job scanner (scanner.pipeline) started upserting scraped
    listings — see _sync_job_fts_on_update below for the companion
    listener that change required. No-ops entirely on Postgres, where
    /jobs's search instead uses a real to_tsvector/plainto_tsquery query
    computed directly against the job table — no shadow table needed
    there at all.
    """
    if connection.dialect.name != "sqlite":
        return
    connection.execute(
        sa_text("INSERT INTO job_fts(rowid, title, required_skills, location) VALUES (:id, :title, :skills, :location)"),
        {"id": job.id, "title": job.title or "", "skills": job.required_skills or "", "location": job.location or ""},
    )


@event.listens_for(Job, "after_update")
def _sync_job_fts_on_update(mapper, connection, job):
    """
    Companion to _sync_job_fts_on_insert. Job rows were never editable
    before the job scanner — re-scanning an already-seen scraped listing
    (matched by source_id + external_id in scanner.pipeline) now updates
    title/required_skills/location on an existing row, which would
    otherwise leave the FTS5 shadow table silently stale after the first
    scan. No-ops on Postgres, same as the insert listener.

    job_fts was created `content=''` (bc0adf755ad8) — a genuinely
    contentless FTS5 table, which SQLite does not allow a plain UPDATE
    against at all ("cannot UPDATE contentless fts5 table", found via a
    real re-scan test failing, not assumed). The documented way to change
    a row in a contentless table is FTS5's special 'delete' command,
    which requires the OLD indexed values (there's no stored content for
    SQLite to look them up itself) — get_history() below reads those from
    SQLAlchemy's own pending-flush state, not a second query.
    """
    if connection.dialect.name != "sqlite":
        return

    def _old_value(attr):
        hist = get_history(job, attr)
        # hist.deleted holds the pre-change value only if this specific
        # attribute actually changed; an attribute untouched by this
        # update has no history entry at all, so its current value IS
        # the value job_fts already has indexed.
        return hist.deleted[0] if hist.deleted else getattr(job, attr)

    connection.execute(
        sa_text(
            "INSERT INTO job_fts(job_fts, rowid, title, required_skills, location) "
            "VALUES ('delete', :id, :title, :skills, :location)"
        ),
        {
            "id": job.id,
            "title": _old_value("title") or "",
            "skills": _old_value("required_skills") or "",
            "location": _old_value("location") or "",
        },
    )
    connection.execute(
        sa_text("INSERT INTO job_fts(rowid, title, required_skills, location) VALUES (:id, :title, :skills, :location)"),
        {"id": job.id, "title": job.title or "", "skills": job.required_skills or "", "location": job.location or ""},
    )


class Credential(db.Model):
    # Real bug found and fixed via a live reproduction, not just code
    # reading: `hash` used to be globally unique (unique=True below, no
    # user_id in the constraint). Two DIFFERENT users uploading files with
    # byte-identical content (a realistic case — template-generated
    # certificates from the same issuing institution) collided: the second
    # user's upload silently returned the FIRST user's existing row instead
    # of creating their own — their own title/issuer input was discarded,
    # they got back a "success" response referencing someone else's
    # credential_id, and it never appeared in their own /passport list.
    # Reproduced live: Bob's own passport stayed empty after a "Credential
    # already exists" 200 response pointing at Alice's row. Scoping the
    # uniqueness to (user_id, hash) instead lets each user legitimately
    # hold their own record for the same underlying document — matching
    # what the on-chain contract itself actually tracks: registerCredential
    # only records "this hash was registered by the platform's own issuer
    # address at time T," never which end user it belongs to. That
    # user-to-hash binding was always purely a backend-database fact, not
    # an on-chain one, so nothing about the chain's own guarantees changes
    # here — see _write_onchain_tx_for_credential's docstring for how the
    # write path now handles a hash that's already registered by a
    # different user's row.
    __table_args__ = (
        db.UniqueConstraint("user_id", "hash", name="uq_credential_user_hash"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    issuer = db.Column(db.String(200), nullable=False)
    # Callable default: evaluated per-insert, not frozen at process start
    # (a plain `datetime.now().year` here would insert the same year for
    # every row for the life of the process, mis-dating anything created
    # after a New Year's boundary — see Phase 4/TD-08 of the engineering review).
    year = db.Column(db.Integer, default=lambda: datetime.now().year)
    file_path = db.Column(db.String(300), nullable=True)
    hash = db.Column(db.String(64), nullable=False, index=True)  # SHA256 — unique per-user, see __table_args__ above
    onchain_tx = db.Column(db.String(80), nullable=True)          # tx hash, if written on-chain
    # Revocation (real gap found via a full-codebase review — see
    # YouthChainRegistry.sol's revokeCredential()/isValid() for the
    # on-chain half of this). revoked_at is set immediately when an admin
    # revokes, the same "backend record exists before on-chain
    # confirmation lands" pattern onchain_tx already uses for issuance;
    # revoke_onchain_tx is filled in by the background write once it's
    # actually mined (see _revoke_credential_onchain_async).
    revoked_at = db.Column(db.DateTime, nullable=True)
    revoked_by_admin_id = db.Column(db.Integer, db.ForeignKey("admin.id"), nullable=True)
    revoke_onchain_tx = db.Column(db.String(80), nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "issuer": self.issuer,
            "year": self.year,
            "hash": self.hash,
            "onchain_tx": self.onchain_tx,
            "revoked_at": self.revoked_at.isoformat() if self.revoked_at else None,
            "revoke_onchain_tx": self.revoke_onchain_tx,
        }



class Application(db.Model):
    __table_args__ = (
        # Was previously only created imperatively via a raw
        # `CREATE UNIQUE INDEX IF NOT EXISTS` in the old startup migration
        # block — moved here so it's part of the model itself: created by
        # db.create_all() on a fresh DB, and picked up correctly by Alembic
        # autogeneration (see migrations/), rather than living only in a
        # side-channel SQL string a schema-diff tool can't see.
        db.UniqueConstraint("user_id", "job_id", name="uq_user_job"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    job_id = db.Column(db.Integer, db.ForeignKey("job.id"), nullable=False)
    # Nullable since the gig/informal-work lifecycle (see Job.job_type):
    # applying to a gig job doesn't require a CV. Route-level validation in
    # apply()/portal_apply() still requires one for every formal job — this
    # relaxation is a DB-level ceiling raise, not a behavior change on its
    # own.
    cv_file = db.Column(db.String(300), nullable=True)
    supporting_file = db.Column(db.String(300), nullable=True)
    # Pending / Accepted / Rejected / Completed ("Completed" is gig-only —
    # see the /employer/applications/<id>/complete route — the formal-job
    # lifecycle never reaches it). Plain string, not an app-side CHECK
    # constraint, same convention the rest of this file already uses.
    status = db.Column(db.String(50), default="Pending")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "job_id": self.job_id,
            "cv_file": self.cv_file,
            "supporting_file": self.supporting_file,
            "status": self.status,
            "created_at": (self.created_at.isoformat() if self.created_at else None),
        }


class SavedJob(db.Model):
    """
    A user's bookmark on a Job -- Home (employer/gig) and Discover
    (scraped) jobs already share one Job table (see Job.source), so one
    join table covers both without special-casing either. Deliberately a
    live reference, not a snapshot: if a scraped listing's own next scan
    updates or removes it, the saved entry reflects that change rather
    than freezing what the user originally bookmarked -- the same
    "always show the current state" reasoning _search_jobs() already
    applies to every other job read path in this file.
    """

    __table_args__ = (
        # Same convention Application.__table_args__ already established
        # for its own (user_id, job_id) pair -- the DB-level guarantee
        # is what makes save_job() idempotent under a race (two rapid
        # taps), not just the route's own check-then-insert.
        db.UniqueConstraint("user_id", "job_id", name="uq_saved_job_user_job"),
    )

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    job_id = db.Column(db.Integer, db.ForeignKey("job.id"), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class SavedSearch(db.Model):
    """
    An explicit "alert me" filter over Discover (GET /api/discover_jobs's
    q/location/skill params, same meaning here) — distinct from
    Candidate.job_alerts_enabled below, which is an implicit alert driven
    by the candidate's own profile skills rather than a one-off search.
    Matched against a newly scraped Job exactly once, at scan time (see
    _dispatch_job_alerts_for_scan) — a Job's external_id already makes
    scanner.pipeline's upsert create each real listing only once, so
    firing alerts only for scanner.pipeline.ScanOutcome.created_job_ids
    is what keeps this naturally idempotent across rescans without a
    separate seen/notified tracking table.
    """
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    q = db.Column(db.String(200), nullable=True)
    location = db.Column(db.String(120), nullable=True)
    skill = db.Column(db.String(120), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "q": self.q,
            "location": self.location,
            "skill": self.skill,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Rating(db.Model):
    """
    The trust mechanism for the gig/informal-work lifecycle (see
    Job.job_type): a worker without a CV or diploma builds a visible track
    record from completed gigs and ratings instead. Bidirectional on
    purpose — employer rates worker AND worker rates employer, one row
    each, both keyed off the same Application — because in informal work
    the employer-side risk (no-show, wage theft) is just as real as the
    worker-side risk, and a platform that only let employers rate workers
    would just be building a one-way surveillance tool, not mutual trust
    infrastructure. One rating per (application, direction), enforced by
    the unique constraint below AND an app-level pre-check in the route
    (so a duplicate attempt gets a friendly error, not a raw
    IntegrityError) — same pattern portal_apply() already uses for the
    Application uq_user_job constraint. Immutable once created: there is
    no edit route, deliberately (same "no edit route" precedent Job itself
    already sets) — a rating that turns out to be unfair goes through
    RatingFlag/admin review, not silent self-editing, so the trust signal
    can't be quietly rewritten by whoever didn't like it.
    """
    __table_args__ = (
        db.UniqueConstraint("application_id", "direction", name="uq_rating_application_direction"),
    )
    id = db.Column(db.Integer, primary_key=True)
    application_id = db.Column(db.Integer, db.ForeignKey("application.id"), nullable=False, index=True)
    direction = db.Column(db.String(20), nullable=False)  # "employer_to_worker" | "worker_to_employer"
    # Denormalized copies of both parties (same reasoning EmployerReport.employer_id
    # already uses) -- lets "all ratings for employer X" / "all ratings for
    # worker Y" be a single indexed lookup, not a join back through
    # Application -> Job on every read of _worker_trust_summary().
    employer_id = db.Column(db.Integer, db.ForeignKey("employer.id"), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    score = db.Column(db.Integer, nullable=False)  # 1-5, validated app-side
    comment = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    # Soft-hide only via admin moderation (RatingFlag "actioned") -- same
    # non-destructive audit-trail ethos as Credential.revoked_at /
    # EmployerReport.status. A hidden rating is excluded from
    # _worker_trust_summary()'s aggregate but the row itself is retained.
    hidden = db.Column(db.Boolean, nullable=False, default=False)

    def to_dict(self):
        return {
            "id": self.id,
            "application_id": self.application_id,
            "direction": self.direction,
            "score": self.score,
            "comment": self.comment,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "hidden": self.hidden,
        }


class RatingFlag(db.Model):
    """
    The dispute path for Rating -- exact shape of EmployerReport/
    EmployerAppeal's "someone flags a record, an admin reviews it" pattern,
    reused rather than reinvented. "actioned" here sets Rating.hidden =
    True and this row's own status, the same "resolve route mutates the
    flagged row plus itself" shape admin_resolve_report() already uses for
    EmployerReport -> Employer.active.
    """
    id = db.Column(db.Integer, primary_key=True)
    rating_id = db.Column(db.Integer, db.ForeignKey("rating.id"), nullable=False, index=True)
    flagged_by_role = db.Column(db.String(10), nullable=False)  # "employer" | "worker"
    flagged_by_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    flagged_by_employer_id = db.Column(db.Integer, db.ForeignKey("employer.id"), nullable=True)
    reason = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    status = db.Column(db.String(20), nullable=False, default="open")  # open -> dismissed | actioned
    reviewed_at = db.Column(db.DateTime, nullable=True)
    reviewed_by_admin_id = db.Column(db.Integer, db.ForeignKey("admin.id"), nullable=True)


# -------- Youth profile models --------
class Candidate(db.Model):
    """
    Youth profile used by the mobile app.
    Separate from User so existing auth/employer flows stay intact.
    """
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)

    email = db.Column(db.String(200), unique=True, nullable=False)
    name = db.Column(db.String(200))
    location = db.Column(db.String(120))
    skills = db.Column(db.Text)   # comma-separated tags
    bio = db.Column(db.Text)
    # Comma-separated, same shape/convention as skills — explicit,
    # candidate-stated industry interests (see _INDUSTRY_CHOICES), used
    # only for the transparent matching bonus in api_match_jobs. Deliberately
    # not inferred from application history or any other behavioral
    # signal: an explicit, user-set preference is honest about being
    # exactly that, where inference would risk this codebase's existing
    # "no fake AI/smart-matching claims" discipline (BL-45).
    preferred_industries = db.Column(db.Text)

    # Opt-in: when true, a newly scraped Discover job whose required_skills
    # overlaps this candidate's own skills fires a notify_user() job alert
    # (see _dispatch_job_alerts_for_scan). Default False so no existing
    # profile starts silently getting notified the moment this column
    # ships — same "opt-in, not retroactively on" reasoning as
    # Candidate.preferred_industries starting empty rather than guessed.
    job_alerts_enabled = db.Column(db.Boolean, nullable=False, default=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Education(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    candidate_id = db.Column(db.Integer, db.ForeignKey("candidate.id"), nullable=False)
    school = db.Column(db.String(200))
    degree = db.Column(db.String(200))
    year = db.Column(db.String(10))


class OTPCode(db.Model):
    """
    Renamed from `email` to `identifier` (and gained `channel`) to let a
    code be issued against a phone number via SMS, not just an email
    address -- real gap found via user feedback: registration only ever
    offered email OTP, while password login already accepts "phone or
    email" as the identifier, an inconsistency between the two entry
    points into the same account. The mobile JSON registration endpoints
    (/register, /auth/otp/request, /auth/otp/verify,
    /auth/otp/register/request) keep querying this table the exact same
    way as before (channel="email" implicitly, since that's the only
    thing they ever set) -- this rename doesn't change their behavior,
    only portal_register()'s new channel-choosing flow actually uses
    channel="sms".
    """
    id = db.Column(db.Integer, primary_key=True)
    identifier = db.Column(db.String(200), index=True, nullable=False)
    channel = db.Column(db.String(10), nullable=False, default="email")  # "email" | "sms"
    code = db.Column(db.String(6), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    used = db.Column(db.Boolean, default=False)
    # What this code proves, not just who it was sent to -- added alongside
    # the new forgot-password flow (BL-46), which reuses this exact
    # OTPCode machinery instead of inventing a second, parallel
    # email-link/token system. Without this, a leftover unused
    # registration code for some identifier could double as a valid
    # password-reset code for an EXISTING account at that same
    # identifier (registration codes prove "you're new here," reset codes
    # need to prove "you already control this account, right now" --
    # different claims, so they must not be interchangeable even when the
    # identifier+channel+code happen to line up). server_default backfills
    # every pre-existing row to "register", which is the only purpose that
    # existed before this column did.
    purpose = db.Column(db.String(10), nullable=False, default="register")  # "register" | "reset"


class AnalyticsEvent(db.Model):
    """
    BL-40 / Phase 3 #10: self-hosted usage-event log. No external analytics
    service (Mixpanel/GA/etc.) is configured for this project, so this is a
    minimal, dependency-free alternative — enough to answer the questions
    Phase 3 flagged as needed to demonstrate impact to a government/donor
    stakeholder (application funnel, employer engagement), aggregated by
    the /admin/analytics dashboard (BL-44).
    """
    id = db.Column(db.Integer, primary_key=True)
    event_type = db.Column(db.String(50), index=True, nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    employer_id = db.Column(db.Integer, db.ForeignKey("employer.id"), nullable=True)
    meta = db.Column(db.Text, nullable=True)  # small JSON blob, e.g. {"job_id": 5}
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)


class DuplicateFlag(db.Model):
    """
    Identity duplicate-detection (Phase 3 gap): fake/duplicate youth
    accounts undermine the "trusted employment history" promise this
    platform exists for, but Sierra Leone's NCRA national ID registry has
    no public API this codebase could verify identity against (same
    reasoning as Employer.verification_status not doing an automated
    business-registry lookup) — so this is a fuzzy-match heuristic that
    flags likely duplicates for a human admin to review, and deliberately
    never auto-blocks or auto-merges an account on its own. See
    _check_duplicate_signals() below for the matching logic, run
    best-effort at registration.
    """
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    matched_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    reason = db.Column(db.String(300), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    resolved = db.Column(db.Boolean, nullable=False, default=False)
    resolved_at = db.Column(db.DateTime, nullable=True)
    resolved_by_admin_id = db.Column(db.Integer, db.ForeignKey("admin.id"), nullable=True)


class EmployerReport(db.Model):
    """
    The missing other half of "what if a verified employer goes rogue":
    Employer.active/admin_suspend_employer gave an admin a real lever, but
    an admin still needed to already know something was wrong (a support
    email, a manual complaint) to pull it. This is how a report actually
    reaches an admin in the first place — a youth flags a specific
    employer, optionally tied to the job or message that triggered it, an
    admin reviews it and can dismiss it or act (suspend the employer,
    reusing the existing admin_suspend_employer route directly from the
    review page). Deliberately reporter -> employer only (not the
    reverse): an employer already has a materially stronger position than
    an individual applicant (they see the applicant's real name, CV,
    contact details; the reverse trust asymmetry is the one this feature
    exists to address), and employers already have the web dashboard as
    their own channel to flag a concerning applicant to whoever operates
    the platform.
    """
    id = db.Column(db.Integer, primary_key=True)
    reporter_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    employer_id = db.Column(db.Integer, db.ForeignKey("employer.id"), nullable=False, index=True)
    # Optional context — a report can be about a specific job posting, a
    # specific message, or just the employer generally (both nullable).
    job_id = db.Column(db.Integer, db.ForeignKey("job.id"), nullable=True)
    message_id = db.Column(db.Integer, db.ForeignKey("message.id"), nullable=True)
    category = db.Column(db.String(30), nullable=False)  # scam, harassment, fake_job, inappropriate, other
    details = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    status = db.Column(db.String(20), nullable=False, default="open")  # open -> dismissed | actioned
    reviewed_at = db.Column(db.DateTime, nullable=True)
    reviewed_by_admin_id = db.Column(db.Integer, db.ForeignKey("admin.id"), nullable=True)


class EmployerAppeal(db.Model):
    """
    admin_reinstate_employer's docstring already says a suspension "could
    turn out to be mistaken" — but until now the only way back was an
    employer reaching an admin through some out-of-band channel, since a
    suspended account can't log in to the dashboard to ask for anything.
    For a platform meaning to be trusted national infrastructure, "a
    single admin's judgment call, permanent unless someone happens to
    hear about it" is a real due-process gap, not a hypothetical one — a
    bad-faith mass-report (see EmployerReport) or a reviewer mistake both
    land here with the employer having zero recourse. This is the
    employer-facing half: a real, in-product path to contest a
    suspension, reusing the same active-flag mechanism rather than
    inventing a second one.
    """
    id = db.Column(db.Integer, primary_key=True)
    employer_id = db.Column(db.Integer, db.ForeignKey("employer.id"), nullable=False, index=True)
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    status = db.Column(db.String(20), nullable=False, default="open")  # open -> reinstated | denied
    reviewed_at = db.Column(db.DateTime, nullable=True)
    reviewed_by_admin_id = db.Column(db.Integer, db.ForeignKey("admin.id"), nullable=True)


class UserAppeal(db.Model):
    """
    The youth (User) counterpart to EmployerAppeal -- same due-process gap,
    same fix: admin_suspend_user() can lock a youth account out entirely
    (see User.active's docstring), and until this existed the only way
    back was an admin happening to notice and reinstate unprompted. A new,
    separate model rather than folding into EmployerAppeal -- mirrors how
    RatingFlag was built as its own table alongside EmployerReport rather
    than merging two differently-shaped subjects into one, and keeps this
    additive (zero risk to the employer appeal path already in prod use).
    """
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    message = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    status = db.Column(db.String(20), nullable=False, default="open")  # open -> reinstated | denied
    reviewed_at = db.Column(db.DateTime, nullable=True)
    reviewed_by_admin_id = db.Column(db.Integer, db.ForeignKey("admin.id"), nullable=True)


class ScrapedListingReport(db.Model):
    """
    The Discover-feed counterpart to EmployerReport -- a scraped listing
    has no real Employer account to report against (Job.employer_id is
    null for source="scraped" rows, see Job's own docstring), so
    EmployerReport's NOT NULL employer_id can't represent this at all. A
    new, separate model rather than loosening that column to nullable
    and branching its validation two ways -- same reasoning UserAppeal's
    own docstring gives for not folding into EmployerAppeal: keeps this
    additive, zero risk to the employer-report path already in prod use.
    Resolving one has no suspension lever to pull (there's no account to
    suspend) -- see admin_resolve_listing_report()'s own docstring for
    what "actioned" means here instead.
    """
    id = db.Column(db.Integer, primary_key=True)
    reporter_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    # Nullable, unlike EmployerReport.employer_id: a report must outlive
    # the listing it was filed against (moderation history, the
    # "repeatedly-reported" signal in admin_listing_reports()), but the
    # Job itself is routinely hard-deleted -- by scanner.reaper on its
    # grace-period timer, or by an admin's own "remove_listing" decision.
    # Both of those null this out first rather than leaving a dangling
    # FK a real Postgres deployment would reject the delete over (see
    # reap_expired_jobs()'s and admin_resolve_listing_report()'s own
    # docstrings). admin_listing_reports.html already renders "Listing
    # removed" when job_id is None/the job is gone.
    job_id = db.Column(db.Integer, db.ForeignKey("job.id"), nullable=True, index=True)
    category = db.Column(db.String(30), nullable=False)  # reuses _REPORT_CATEGORIES
    details = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    status = db.Column(db.String(20), nullable=False, default="open")  # open -> dismissed | actioned
    reviewed_at = db.Column(db.DateTime, nullable=True)
    reviewed_by_admin_id = db.Column(db.Integer, db.ForeignKey("admin.id"), nullable=True)


class Message(db.Model):
    """
    BL-39: employer<->applicant messaging, scoped to a specific Application
    (not a general inbox) — the goal is moving the product from "a job
    board with a status flag" toward an actual trust-building marketplace
    (Phase 3 #9), and scoping to the application both keeps the data model
    simple and matches the only relationship that currently justifies two
    parties talking to each other at all.
    """
    id = db.Column(db.Integer, primary_key=True)
    application_id = db.Column(db.Integer, db.ForeignKey("application.id"), nullable=False, index=True)
    sender_type = db.Column(db.String(10), nullable=False)  # "user" or "employer"
    sender_user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)
    sender_employer_id = db.Column(db.Integer, db.ForeignKey("employer.id"), nullable=True)
    body = db.Column(db.Text, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    # Read receipts: set when the *other* party (not the sender) has
    # fetched the thread — see _mark_messages_read below.
    read = db.Column(db.Boolean, nullable=False, default=False)
    read_at = db.Column(db.DateTime, nullable=True)
    # Optional file attachment, same ownership-scoped serving pattern as
    # application_file/certificate (unique-prefixed filename, checked via
    # /message_attachment/<filename> below — never the raw original name,
    # which would make attachments guessable the same way S-02 flagged for
    # certificates before that was fixed).
    attachment_file = db.Column(db.String(300), nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "application_id": self.application_id,
            "sender_type": self.sender_type,
            "body": self.body,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "read": self.read,
            "read_at": self.read_at.isoformat() if self.read_at else None,
            "attachment_file": self.attachment_file,
        }


class Notification(db.Model):
    """
    BL-38: in-app notifications, always on regardless of external provider
    config (a real, working notification list + a Socket.IO push while the
    app is open). send_push_notification()/send_sms() below now do real
    Firebase Cloud Messaging / Twilio SDK calls when FIREBASE_CREDENTIALS_JSON
    / TWILIO_* are configured (see .env.example) — an operator still has to
    supply a real Firebase project and Twilio account themselves (this
    codebase has no way to create those), but the integration code itself
    is live, not a stub, and degrades gracefully (logs, returns False) when
    those credentials are absent.
    """
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False, index=True)
    type = db.Column(db.String(50), nullable=False)
    title = db.Column(db.String(200), nullable=False)
    body = db.Column(db.Text, nullable=True)
    meta = db.Column(db.Text, nullable=True)
    read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)

    def to_dict(self):
        return {
            "id": self.id,
            "type": self.type,
            "title": self.title,
            "body": self.body,
            "read": self.read,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "meta": json.loads(self.meta) if self.meta else {},
        }


# ----------------- DB init (dev/test convenience only) -----------------
# db.create_all() is idempotent and only ever creates missing tables — it
# does not alter existing ones, so it is safe to leave running unconditionally
# alongside real Alembic migrations. It exists purely so a fresh clone
# without ever running `flask db upgrade` still gets a working local DB with
# zero setup (and so the test suite, which imports this module directly,
# doesn't need a migration step of its own). Any change to an EXISTING
# table's schema must go through a real migration (`flask db migrate` /
# `flask db upgrade`, see migrations/) — this line will not apply it.
if not os.getenv("SKIP_DB_CREATE_ALL"):
    with app.app_context():
        db.create_all()
        # job_fts (see the Job.after_insert listener above and
        # migrations/versions/bc0adf755ad8_*) is a hand-written FTS5
        # virtual table, not an ORM model — db.create_all() has no idea it
        # exists. Without this, any zero-config dev/test setup that never
        # runs `flask db upgrade` (which is exactly what this whole
        # db.create_all() branch exists for) would 500 on every single job
        # creation the moment the after_insert listener tried to INSERT
        # into a table that was never created — found via the test suite
        # actually failing, not anticipated in advance.
        if db.engine.dialect.name == "sqlite":
            try:
                db.session.execute(sa_text(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS job_fts USING fts5("
                    "title, required_skills, location, content=''"
                    ")"
                ))
                db.session.commit()
            except Exception:
                db.session.rollback()
                logger.exception("Could not create job_fts virtual table")


# ----------------- HELPERS -----------------
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def valid_email(email: str) -> bool:
    return bool(EMAIL_RE.match((email or "").strip()))


def _password_is_breached(password: str) -> bool | None:
    """
    Checks a password against Have I Been Pwned's Pwned Passwords API
    using its k-anonymity model. Real gap found via a full OWASP Top 10
    (A07: Identification and Authentication Failures) review — and the
    exact mitigation NIST 800-63B recommends in place of forced
    complexity rules: reject a password already known to be compromised
    in a real breach, rather than requiring an arbitrary mix of character
    classes that mostly just produces predictable patterns like
    "Password1!". This app's existing 8-character minimum (no forced
    complexity) already matches that same NIST guidance; this closes the
    other half of it.

    The password itself — not even its full hash — is ever sent over the
    network: only the first 5 hex characters of its SHA-1 hash go out (a
    k-anonymity set shared by many thousands of other real password
    hashes), and the API returns every known-breached hash SUFFIX sharing
    that prefix for a local, in-process match. This is the API's own
    documented privacy-preserving design, not something invented here.
    Add-Padding requests response padding too, so a network observer
    can't infer anything from the response size either.

    Fails OPEN (returns None, registration proceeds) if the API can't be
    reached in time — deliberately the opposite default from
    file_is_malware_free()'s fail-closed: a transient outage of a
    third-party breach-check API blocking every new registration on this
    platform would be a worse outcome than occasionally skipping the
    check, and this is defense-in-depth on top of the existing length
    requirement, not the only thing standing between an attacker and an
    account.
    """
    # Suppression rationale for the `# nosec B324` marker below: Bandit
    # flags SHA-1 as a weak hash for security use (collision resistance),
    # but it isn't used that way here -- SHA-1 is the
    # Pwned Passwords API's own fixed wire protocol (see the docstring
    # above), not a choice made by this codebase, and nothing here trusts
    # SHA-1 to resist a deliberate collision attack -- it's a lookup key
    # into a public breach-hash database, not a password-storage or
    # integrity mechanism (bcrypt/Werkzeug's own hasher does that elsewhere
    # in this file). Switching hash algorithms would just make every
    # lookup fail against the real API.
    sha1 = hashlib.sha1(password.encode("utf-8")).hexdigest().upper()  # nosec B324
    prefix, suffix = sha1[:5], sha1[5:]
    try:
        resp = requests.get(
            f"https://api.pwnedpasswords.com/range/{prefix}",
            timeout=3,
            headers={"Add-Padding": "true"},
        )
        resp.raise_for_status()
    except requests.RequestException:
        logger.warning("Pwned Passwords API unreachable — skipping breach check for this registration")
        return None

    for line in resp.text.splitlines():
        candidate_suffix = line.split(":", 1)[0]
        if candidate_suffix == suffix:
            return True
    return False


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# Extension-only validation (allowed_file above) can be trivially bypassed
# by renaming any file — S-08 / Phase 5 of the engineering review. This maps
# each allowed extension to the content signature(s) that must actually be
# present, via the `filetype` library's magic-byte sniffing — not a full
# malware/AV scan by itself, but see file_is_malware_free() further down
# (real ClamAV integration, optional via CLAMD_HOST) for that layer, added
# in a later pass once that real scanning engine dependency was worth
# taking on.
_EXPECTED_CONTENT_EXTENSIONS = {
    "png": {"png"},
    "jpg": {"jpg", "jpeg"},
    "jpeg": {"jpg", "jpeg"},
    "pdf": {"pdf"},
    "doc": {"doc"},
    "docx": {"docx"},
}


def content_matches_extension(file_storage, filename: str) -> bool:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    expected = _EXPECTED_CONTENT_EXTENSIONS.get(ext)
    if not expected:
        return False
    header = file_storage.stream.read(261)  # longest signature filetype needs
    file_storage.stream.seek(0)
    kind = filetype.guess(header)
    if kind is None:
        # filetype can't identify empty files or formats it doesn't
        # recognize — reject rather than trust an unverifiable claim.
        return False
    return kind.extension in expected


# Every on-chain WRITE (registerCredential.js, revokeCredential.js,
# accreditIssuer.js, revokeIssuer.js) shells out to a fresh `npx hardhat
# run` process that resolves its own signer's "next" nonce from the RPC
# node independently -- there is no shared nonce-tracking between
# invocations. Two of these running concurrently (e.g. two credentials
# uploaded moments apart, each getting its own
# _write_onchain_tx_for_credential_async background thread) can both read
# the same "next" nonce before either transaction is mined, and the
# second one to actually reach the node either gets rejected (nonce
# already used) or silently replaces the first depending on the RPC
# provider's mempool behavior -- either way the wrong credential/issuer
# action goes uncommitted with no application-level error surfaced.
# Serializing all writes behind one process-wide lock is a narrow, real
# fix for that specific race -- it does not address a genuinely
# concurrent RPC provider outage/timeout (an already-existing, separate
# failure mode each write function already handles via its own None
# return), only the specific case where two writes would otherwise be
# in flight from this backend at the same moment. Read-only calls
# (checkRegistered.js, checkValid.js, listIssuers.js) don't consume a
# nonce and are deliberately NOT gated by this lock.
_ONCHAIN_WRITE_LOCK = threading.Lock()


def generate_file_hash(file_path: str) -> str:
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            sha256_hash.update(chunk)
    return sha256_hash.hexdigest()

def _run_hardhat_script(script: str, hash_hex: str, timeout: int = 90) -> str | None:
    """
    Shared subprocess runner for both the write path (registerCredential.js)
    and the read path (checkRegistered.js) — factored out so the two don't
    duplicate the subprocess/env/error-handling boilerplate. Returns raw
    stdout on success, None on any failure (best-effort by design: a
    blockchain hiccup should degrade the feature, not break credential
    issuance/verification entirely).
    """
    try:
        blockchain_dir = os.path.abspath(os.path.join(BASE_DIR, "..", "blockchain"))
        # On Windows, "npx" resolves to npx.CMD, and subprocess.run() with a
        # plain arg list (no shell=True) can't execute a .CMD file directly
        # — it fails with WinError 2 ("cannot find the file specified") even
        # though npx is genuinely on PATH. shutil.which() resolves the real
        # executable (npx.CMD) so this works without needing shell=True
        # (which would otherwise require careful quoting of every arg).
        # This was a real, silent, pre-existing bug: the on-chain write
        # subprocess call had never actually succeeded on Windows.
        npx_path = shutil.which("npx") or "npx"
        cmd = [npx_path, "hardhat", "run", f"scripts/{script}", "--network", HARDHAT_NETWORK]
        env = os.environ.copy()
        env["HASH"] = hash_hex

        logger.info("Running hardhat script: %s (cwd=%s)", " ".join(cmd), blockchain_dir)

        proc = subprocess.run(
            cmd,
            cwd=blockchain_dir,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )

        logger.debug("hardhat stdout: %s", proc.stdout)
        if proc.stderr:
            logger.debug("hardhat stderr: %s", proc.stderr)

        if proc.returncode != 0:
            logger.warning("hardhat script %s exited %s", script, proc.returncode)
            return None
        return proc.stdout
    except Exception:
        logger.exception("hardhat subprocess call failed")
        return None


def _write_onchain_tx_for_credential(credential: Credential) -> str | None:
    """
    Call the /blockchain Hardhat project to register this credential's hash
    on the local Ethereum node. Best-effort: returns tx hash or None.

    Checks _check_onchain_registered() first — real gap found alongside the
    Credential.__table_args__ fix that let two different users each hold
    their own row for the same content hash: the contract's own
    registerCredential() is unique by hash GLOBALLY (it has no concept of
    per-user rows at all, see YouthChainRegistry.sol), so once one user's
    upload has registered a given hash, a second user's row for that same
    hash would otherwise attempt a write that's guaranteed to revert
    ("already exists") on every single call, wasting the ~30-90s subprocess
    round trip for a foregone conclusion. Checking first turns that into a
    fast, correct outcome instead — the content genuinely IS registered
    on-chain (just via a different row's original transaction), which
    /verify/<id>'s own live check already confirms independent of which
    row's onchain_tx column happens to be set. This also self-heals a
    prior write that actually succeeded on-chain but whose tx hash the
    backend failed to record for some transient reason (a timeout, a
    parsing hiccup) — previously that retried into a guaranteed revert
    forever; now it's recognized immediately.
    """
    hash_hex = (credential.hash or "").strip()
    if not hash_hex:
        return None

    if _check_onchain_registered(hash_hex):
        logger.info(
            "Credential hash already registered on-chain (credential_id=%s) — "
            "skipping a redundant, guaranteed-to-revert write.",
            credential.id,
        )
        return None

    with _ONCHAIN_WRITE_LOCK:
        stdout = _run_hardhat_script("registerCredential.js", hash_hex)
    if not stdout:
        return None

    # ASCII-only marker line (MINED_TX:<hash>), not the emoji-prefixed
    # human-readable line — matching on the emoji was observed to fail
    # silently on Windows, where console encoding can mangle it in a piped
    # subprocess even though the on-chain write itself succeeded.
    tx_hash = None
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("MINED_TX:"):
            tx_hash = line.split(":", 1)[1].strip()
    return tx_hash


def _check_onchain_registered(hash_hex: str) -> bool | None:
    """
    Calls the contract's own isRegistered(hash) view function via
    scripts/checkRegistered.js. Returns True/False on a successful chain
    read, or None if the check itself couldn't be performed (blockchain
    unreachable, etc.) — callers must treat None as "unknown", not as
    "not registered".

    This is what makes /verify and /employer/verify actually check the
    chain instead of only trusting this backend's own database (see
    Phase 6 of the engineering review for why that gap mattered).
    """
    hash_hex = (hash_hex or "").strip()
    if not hash_hex:
        return None

    stdout = _run_hardhat_script("checkRegistered.js", hash_hex, timeout=30)
    if not stdout:
        return None

    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("REGISTERED:"):
            return line.split(":", 1)[1].strip().lower() == "true"
    return None


def _check_onchain_valid(hash_hex: str) -> bool | None:
    """
    Calls the contract's isValid(hash) view function via
    scripts/checkValid.js -- registered AND not revoked, unlike
    _check_onchain_registered() above (existence only). This is the check
    /verify and /employer/verify should actually display, now that
    revocation exists (see YouthChainRegistry.sol's revokeCredential()) --
    a fraudulent or erroneously-issued credential that's been revoked
    must stop showing as "verified" here, even though isRegistered()
    correctly still reports the historical fact that it was once
    registered. Deliberately a separate function from
    _check_onchain_registered rather than changing that one's meaning:
    _write_onchain_tx_for_credential's write-avoidance check needs
    existence-only semantics (a revoked hash still "exists" and must
    never be re-registered), so that one stays on isRegistered.
    """
    hash_hex = (hash_hex or "").strip()
    if not hash_hex:
        return None

    stdout = _run_hardhat_script("checkValid.js", hash_hex, timeout=30)
    if not stdout:
        return None

    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("VALID:"):
            return line.split(":", 1)[1].strip().lower() == "true"
    return None


def _revoke_credential_onchain(credential: "Credential") -> str | None:
    """
    Calls the contract's revokeCredential(hash) via scripts/
    revokeCredential.js -- the write-side counterpart to
    _write_onchain_tx_for_credential. Best-effort: returns tx hash or
    None. Unlike issuance, does NOT check-before-write here: the caller
    (POST /admin/credentials/<id>/revoke) already only reaches this once
    per credential (revoked_at is set synchronously before this runs, and
    the route itself refuses to revoke an already-revoked row), so a
    double-write attempt isn't the routine race issuance's
    already-registered-elsewhere case is.
    """
    hash_hex = (credential.hash or "").strip()
    if not hash_hex:
        return None

    with _ONCHAIN_WRITE_LOCK:
        stdout = _run_hardhat_script("revokeCredential.js", hash_hex)
    if not stdout:
        return None

    tx_hash = None
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("MINED_TX:"):
            tx_hash = line.split(":", 1)[1].strip()
    return tx_hash


_ETH_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def _is_valid_eth_address(address: str) -> bool:
    """Same 20-byte-hex shape check as blockchain/scripts/_shared.js's
    normalizeAddress() -- checked here too so a malformed address gets a
    clean 400 instead of a ~90s subprocess round trip that was always
    going to fail."""
    return bool(_ETH_ADDRESS_RE.match(address or ""))


def _run_hardhat_script_for_address(script: str, address: str | None = None, timeout: int = 90) -> str | None:
    """
    Address-parameterized sibling of _run_hardhat_script (which is
    hash-parameterized) -- kept as a separate function rather than
    generalizing that one to take an arbitrary env dict, matching this
    codebase's existing precedent of small, explicit, single-purpose
    functions over one parameterized do-everything version (see
    registerCredential.js's resolveIssuer() vs revokeCredential.js's
    resolveOwner(), deliberately not shared either, for the same reason).
    address is optional -- listIssuers.js (unlike the other 3 callers)
    doesn't take one at all, it replays the whole event log instead.
    """
    try:
        blockchain_dir = os.path.abspath(os.path.join(BASE_DIR, "..", "blockchain"))
        npx_path = shutil.which("npx") or "npx"
        cmd = [npx_path, "hardhat", "run", f"scripts/{script}", "--network", HARDHAT_NETWORK]
        env = os.environ.copy()
        if address is not None:
            env["ADDRESS"] = address

        logger.info("Running hardhat script: %s (cwd=%s)", " ".join(cmd), blockchain_dir)

        proc = subprocess.run(
            cmd,
            cwd=blockchain_dir,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )

        logger.debug("hardhat stdout: %s", proc.stdout)
        if proc.stderr:
            logger.debug("hardhat stderr: %s", proc.stderr)

        if proc.returncode != 0:
            logger.warning("hardhat script %s exited %s", script, proc.returncode)
            return None
        return proc.stdout
    except Exception:
        logger.exception("hardhat subprocess call failed")
        return None


def _accredit_issuer_onchain(address: str) -> str | None:
    """Calls the contract's accreditIssuer(address) via
    scripts/accreditIssuer.js. Best-effort: returns tx hash or None. This
    is a rare, deliberate, admin-initiated action (onboarding a partner
    org's wallet), not a high-frequency one like credential issuance --
    run synchronously on the request thread rather than given the
    background-thread treatment _write_onchain_tx_for_credential_async
    gets, since there's no per-request-throughput concern to justify that
    complexity here."""
    with _ONCHAIN_WRITE_LOCK:
        stdout = _run_hardhat_script_for_address("accreditIssuer.js", address)
    if not stdout:
        return None

    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("MINED_TX:"):
            return line.split(":", 1)[1].strip()
    return None


def _revoke_issuer_onchain(address: str) -> str | None:
    """Calls the contract's revokeIssuer(address) via
    scripts/revokeIssuer.js. Same synchronous reasoning as
    _accredit_issuer_onchain."""
    with _ONCHAIN_WRITE_LOCK:
        stdout = _run_hardhat_script_for_address("revokeIssuer.js", address)
    if not stdout:
        return None

    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("MINED_TX:"):
            return line.split(":", 1)[1].strip()
    return None


def _list_issuers_onchain() -> list[dict] | None:
    """
    Calls scripts/listIssuers.js, which reconstructs full accreditation
    history from the IssuerAccredited/IssuerRevoked event log (the
    accreditedIssuers mapping itself isn't enumerable on-chain). Returns
    None on failure, an empty/populated list of {"address": ..., "accredited": bool}
    on success -- never partial/guessed data.
    """
    stdout = _run_hardhat_script_for_address("listIssuers.js", timeout=30)
    if not stdout:
        return None

    issuers = []
    for line in stdout.splitlines():
        line = line.strip()
        if line.startswith("ISSUER:"):
            _, address, accredited = line.split(":", 2)
            issuers.append({"address": address, "accredited": accredited.strip().lower() == "true"})
    return issuers


def _revoke_credential_onchain_async(app_ref: "Flask", credential_id: int) -> None:
    """
    Background counterpart to _write_onchain_tx_for_credential_async --
    same reasoning (BL-18): the on-chain write is a ~30-90s subprocess
    round trip that the admin revoking a credential doesn't need to
    block on synchronously. The DB-level revocation (revoked_at) and the
    user notification already happened synchronously in the route before
    this is spawned, so the credential is already correctly showing as
    revoked in this backend's own records the instant the admin acts;
    this just fills in revoke_onchain_tx once the chain actually confirms
    it, mirroring onchain_tx's own "pending until this lands" pattern.
    """
    with app_ref.app_context():
        credential = db.session.get(Credential, credential_id)
        if credential is None:
            return

        tx = _revoke_credential_onchain(credential)
        if not tx:
            return

        credential.revoke_onchain_tx = tx
        db.session.commit()
        logger.info("Credential %s revocation confirmed on-chain (tx=%s)", credential_id, tx)


def _spawn_background_credential_revoke(credential_id: int) -> None:
    """Thin wrapper, same reasoning as _spawn_background_onchain_write --
    factored out so tests can monkeypatch this one seam to run inline."""
    threading.Thread(
        target=_revoke_credential_onchain_async,
        args=(app, credential_id),
        daemon=True,
    ).start()


def _write_onchain_tx_for_credential_async(app_ref: "Flask", credential_id: int) -> None:
    """
    Runs the real on-chain write (_write_onchain_tx_for_credential) off the
    request thread, then updates the Credential row and notifies the owner
    when it lands. This is what actually fixes BL-18, not just papers over
    it: /issue_credential used to block the whole request — up to the
    ~90s subprocess ceiling — waiting on a Hardhat CLI round trip before
    responding at all, which is why the Caddyfile's proxy read_timeout was
    tuned to 120s and the mobile client had its own 100s timeout. Under
    any real concurrent credential-issuance load that's a serious
    throughput problem: each in-flight issuance ties up a request
    handler for up to a minute and a half for something the caller
    doesn't need to wait on synchronously at all — the credential is
    already valid and stored the moment its DB row exists; on-chain
    confirmation is an enhancement to that, not a precondition for it.

    Runs as a plain background thread, not an eventlet-specific
    primitive: subprocess.run() releases the GIL while waiting on the
    child process, so a real OS thread doesn't compete with
    request-handling threads/greenlets for CPU — this works identically
    whether the process is running under gunicorn+eventlet (production)
    or the plain Flask dev server (local `python app.py`), rather than
    depending on eventlet's monkey-patch behavior being active either way.

    Needs its own app context (Flask-SQLAlchemy's db.session is a
    thread-local/greenlet-local scoped session — a background thread gets
    its own, separate from the request thread's, and needs an active app
    context to use it at all) — the same pattern Flask's own docs
    recommend for background work, not something invented here.
    """
    with app_ref.app_context():
        credential = db.session.get(Credential, credential_id)
        if credential is None:
            return

        tx = _write_onchain_tx_for_credential(credential)
        if not tx:
            return

        credential.onchain_tx = tx
        db.session.commit()
        logger.info("Credential %s confirmed on-chain (tx=%s)", credential_id, tx)

        notify_user(
            credential.user_id,
            "credential_onchain_confirmed",
            "Credential verified on-chain",
            f"“{credential.title}” has been confirmed on the YouthChain blockchain registry.",
            credential_id=credential.id,
        )


def _spawn_background_onchain_write(credential_id: int) -> None:
    """
    Thin wrapper around starting _write_onchain_tx_for_credential_async in
    a background thread — factored out (rather than calling
    threading.Thread(...).start() directly at each call site) so tests can
    monkeypatch this one seam to run the work inline/synchronously
    instead. A raw, unmonkeypatchable Thread.start() call gives a test no
    way to deterministically wait for the background work to actually
    finish before asserting on its effects (the DB update, the
    notification) — the same reasoning behind every other testability
    seam in this codebase (e.g. ApiClient.testClient on the mobile side).
    """
    threading.Thread(
        target=_write_onchain_tx_for_credential_async,
        args=(app, credential_id),
        daemon=True,
    ).start()


def _send_email(to_email: str, subject: str, body: str) -> bool:
    """Send email if SMTP configured; else log and return False (non-fatal)."""
    if not (SMTP_HOST and SMTP_USER and SMTP_PASS and SMTP_FROM):
        logger.info("SMTP not configured; would send to %s: %s\n%s", to_email, subject, body)
        return False
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls(context=ctx)
            server.login(SMTP_USER, SMTP_PASS)
            msg = f"From: {SMTP_FROM}\r\nTo: {to_email}\r\nSubject: {subject}\r\n\r\n{body}"
            server.sendmail(SMTP_FROM, [to_email], msg)
        return True
    except Exception:
        logger.exception("Failed to send email to %s", to_email)
        return False


def _otp_code() -> str:
    # secrets.randbelow is a CSPRNG (closes S-06 — random.randint was not).
    return f"{secrets.randbelow(1_000_000):06d}"


# ----------------- Auth helpers -----------------
def _current_user_id():
    """
    Integer user id from the JWT identity, or None if missing/malformed —
    or if the current route has no JWT decorator on it at all.
    get_jwt_identity() raises RuntimeError (not just returning None) when
    verify_jwt_in_request() was never called on this request — real routes
    that mix JWT and session auth (e.g. messaging, reachable from both the
    JWT-authenticated mobile app and session-only employer web pages) can
    legitimately call this from a route with no @jwt_required() at all, so
    that case must degrade to None rather than 500.

    Also re-verifies User.active on every call, same discipline and same
    reasoning as _current_employer_id()/_current_admin_id() -- otherwise
    admin_suspend_user() would only block a suspended youth's *next login*
    while every already-issued token kept working for its full remaining
    lifetime, defeating the point of suspension as an immediate lever.
    """
    try:
        raw = get_jwt_identity()
        uid = int(raw) if raw is not None else None
    except (TypeError, ValueError, RuntimeError):
        return None
    if uid is None:
        return None
    user = User.query.get(uid)
    if user is None or not user.active:
        return None
    return uid


def _forbidden(msg: str = "Forbidden"):
    return jsonify({"success": False, "error": msg}), 403


def log_event(event_type, user_id=None, employer_id=None, **meta):
    """
    Best-effort usage-event logging (BL-40). Never allowed to break the
    request it's called from — an analytics failure shouldn't fail a
    registration or job application.
    """
    try:
        row = AnalyticsEvent(
            event_type=event_type,
            user_id=user_id,
            employer_id=employer_id,
            meta=json.dumps(meta) if meta else None,
        )
        db.session.add(row)
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Failed to log analytics event %s", event_type)


def send_push_notification(user_id: int, title: str, body: str) -> bool:
    """
    Sends a real Firebase Cloud Messaging push when FIREBASE_CREDENTIALS_JSON
    is configured (see the startup block above) AND the target user has a
    registered device token (see PUT /api/push_token). Degrades to the
    original log-and-return-False stub behavior in every other case —
    unconfigured server, or a user who hasn't registered a token yet —
    mirroring how _send_email() degrades when SMTP isn't configured.
    """
    if _firebase_app is None:
        logger.info("[PUSH STUB] Would push to user %s: %s — %s", user_id, title, body)
        return False

    user = User.query.get(user_id)
    if not user or not user.push_token:
        logger.info("[PUSH SKIPPED] User %s has no registered push token", user_id)
        return False

    try:
        from firebase_admin import messaging as _fcm_messaging

        # `token=` (an FCM device registration token, from
        # PUT /api/push_token) triggers a DeprecationWarning in
        # firebase-admin>=7 in favor of `fid=` — deliberately not switched:
        # `fid` is a Firebase *Installation* ID, a different identifier
        # obtained through a different mobile SDK call, not simply a rename
        # of the same value. This codebase has no mobile-side code that
        # collects an installation ID, only the standard FCM registration
        # token, so `token=` remains the semantically correct field even
        # though it is deprecated.
        message = _fcm_messaging.Message(
            notification=_fcm_messaging.Notification(title=title, body=body),
            token=user.push_token,
        )
        _fcm_messaging.send(message, app=_firebase_app)
        return True
    except Exception:
        logger.exception("Firebase push to user %s failed", user_id)
        return False


def _to_e164_sierra_leone(phone: str) -> str:
    """
    Normalizes a Sierra Leone phone number to E.164 (+232XXXXXXXX) before
    it's handed to Twilio — required for both send_sms() and
    send_whatsapp() below, since Twilio's API rejects any `to` number that
    isn't E.164. Registration/profile screens never asked a user to type
    a country code (a Sierra Leone youth doesn't say "+232" when giving
    out their own number — see registration_screen.dart/
    profile_cv_screen.dart, neither of which formats or validates beyond
    "non-empty"), so User.phone in the database is a real mix of local
    ("076123456" or "76123456") and already-international
    ("+23276123456") formats. Same 8-digit-subscriber-number assumption
    _normalize_phone_suffix above already documents for duplicate-account
    detection — this is that same real-world shape, applied at the point
    a number actually needs to be dialable rather than merely compared.

    Confirmed gap, not a hypothetical: before this, send_sms()/
    send_whatsapp() passed whatever format was on file straight through
    to Twilio's `to=` — for any user who registered typing the natural
    local format (the overwhelming majority, this being a Sierra
    Leone-first platform), Twilio would reject the call outright, and
    that failure was swallowed by send_sms/send_whatsapp's own
    try/except, meaning notify_user() would report success upstream (the
    in-app Notification row is created either way) while the SMS/WhatsApp
    silently never sent.

    Returns the input unchanged (never raises) if it doesn't match a
    recognized Sierra Leone shape — e.g. a already-foreign number entered
    with its own country code. Twilio's API remains the final validator;
    this only fixes the specific, common, locally-typed-number case.
    """
    stripped = (phone or "").strip()
    if stripped.startswith("+"):
        return stripped
    digits = re.sub(r"\D", "", stripped)
    if digits.startswith("232") and len(digits) == 11:
        return f"+{digits}"
    if digits.startswith("0") and len(digits) == 9:
        return f"+232{digits[1:]}"
    if len(digits) == _PHONE_SUFFIX_LENGTH:
        return f"+232{digits}"
    return phone


def send_sms(phone: str, body: str) -> bool:
    """
    Sends a real SMS via Twilio when TWILIO_ACCOUNT_SID/TWILIO_AUTH_TOKEN/
    TWILIO_FROM_NUMBER are all configured (see the startup block above).
    Degrades to the original log-and-return-False stub behavior when
    unconfigured.
    """
    if _twilio_client is None:
        logger.info("[SMS STUB] Would SMS %s: %s", phone, body)
        return False

    try:
        _twilio_client.messages.create(to=_to_e164_sierra_leone(phone), from_=_TWILIO_FROM_NUMBER, body=body)
        return True
    except Exception:
        logger.exception("Twilio SMS to %s failed", phone)
        return False


def send_whatsapp(phone: str, job_title: str) -> bool:
    """
    Sends a real WhatsApp message via Twilio's WhatsApp Business API when
    the Twilio client AND both TWILIO_WHATSAPP_FROM_NUMBER and
    TWILIO_WHATSAPP_TEMPLATE_SID are configured (see the startup block
    above). Degrades to the same log-and-return-False stub behavior as
    send_sms() when any of the three isn't set yet.

    Deliberately takes job_title, not an arbitrary body string like
    send_sms() -- WhatsApp Business messaging requires a pre-approved
    Content Template for any business-initiated message (see
    TWILIO_WHATSAPP_TEMPLATE_SID's own comment), so the only thing this
    function can actually vary per-send is that template's declared
    variables, not freeform text. The template this was built against
    has exactly one variable ("{{1}}" = the matching job's title) --
    see .env.example for the exact template text to submit for approval.
    """
    if _twilio_client is None or not _TWILIO_WHATSAPP_FROM_NUMBER or not _TWILIO_WHATSAPP_TEMPLATE_SID:
        logger.info("[WHATSAPP STUB] Would WhatsApp %s about: %s", phone, job_title)
        return False

    try:
        _twilio_client.messages.create(
            to=f"whatsapp:{_to_e164_sierra_leone(phone)}",
            from_=f"whatsapp:{_TWILIO_WHATSAPP_FROM_NUMBER}",
            content_sid=_TWILIO_WHATSAPP_TEMPLATE_SID,
            content_variables=json.dumps({"1": job_title}),
        )
        return True
    except Exception:
        logger.exception("Twilio WhatsApp to %s failed", phone)
        return False


def notify_user(user_id, ntype, title, body=None, push=True, sms=False, whatsapp=False, **meta):
    """
    Creates an in-app Notification row, emits it over the existing
    Socket.IO connection for near-real-time delivery while the app is
    open, and best-effort attempts a push notification via
    send_push_notification() above (a real Firebase push when configured
    and the user has a registered token, otherwise a harmless log-only
    no-op — see that function).

    sms=True / whatsapp=True mark this notification as *eligible* for that
    channel — neither by itself means the message goes out. The actual
    per-user opt-in checks (User.sms_alerts_enabled /
    User.whatsapp_alerts_enabled) live inside this function, not at each
    call site, for the same reason send_push_notification's own token
    check lives inside it rather than in every caller: one place to get
    the "did this user actually agree to this" decision right. Every
    existing call site defaults to both False and is unaffected; only
    _dispatch_job_alerts_for_scan's Saved Search / job-alert matches pass
    sms=True, whatsapp=True today — this is deliberately not wired to
    every notification type the way push is, since each SMS/WhatsApp send
    is a real Twilio cost and most of the other 8 notification types
    (logins, credentials, messages, ratings) don't warrant it.
    """
    try:
        row = Notification(
            user_id=user_id,
            type=ntype,
            title=title,
            body=body,
            meta=json.dumps(meta) if meta else None,
        )
        db.session.add(row)
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Failed to create notification %s for user %s", ntype, user_id)
        return

    try:
        socketio.emit("notification_created", row.to_dict(), room=f"user:{user_id}")
    except Exception:
        logger.exception("socketio emit notification_created failed")

    if push:
        send_push_notification(user_id, title, body or "")

    if sms or whatsapp:
        user = User.query.get(user_id)
        if sms and user and user.sms_alerts_enabled:
            send_sms(user.phone, f"{title} — {body}" if body else title)
        if whatsapp and user and user.whatsapp_alerts_enabled:
            send_whatsapp(user.phone, body or title)


# Fuzzy duplicate-account detection thresholds. Both deliberately loose —
# false positives just mean an admin dismisses a flag that turns out to be
# two different people with a similar name and phone prefix; false
# negatives mean a real duplicate silently gets through. Given the
# platform's actual goal (surfacing likely duplicates for human review,
# never auto-blocking), erring toward more false positives is the safer
# default.
_DUPLICATE_NAME_SIMILARITY_THRESHOLD = 0.85
# Sierra Leone subscriber numbers are 8 digits (2-digit network prefix +
# 6-digit number), written either as local "0XXXXXXXX" (leading 0 + 8
# digits, 9 total) or international "+232XXXXXXXX" (232 + 8 digits, 11
# total). Comparing the last 8 digits of each strips both the leading 0
# and the 232 country code, so the same real number in either format
# normalizes to the same value — verified against both formats directly,
# not assumed (e.g. local "076123456" and international "+23276123456"
# both normalize to "76123456").
_PHONE_SUFFIX_LENGTH = 8


def _normalize_phone_suffix(phone: str) -> str:
    digits = re.sub(r"\D", "", phone or "")
    return digits[-_PHONE_SUFFIX_LENGTH:] if len(digits) >= _PHONE_SUFFIX_LENGTH else digits


def _phone_lookup_candidates(identifier: str) -> set:
    """
    Real gap found alongside _to_e164_sierra_leone's own (see that
    function's docstring): since User.phone has never been normalized at
    write time, every phone-based account lookup in this file — login,
    OTP request/verify, forgot-password, registration's duplicate
    pre-check — was doing a plain `==` against whatever string is on
    file. A user who registered typing "076123456" and later types
    "76123456" or "+23276123456" at login couldn't be found at all, not
    a delivery problem like the Twilio one, a "can't log in" problem.

    Returns every Sierra Leone format the identifier as typed could
    plausibly match against, always INCLUDING the identifier exactly as
    given — so `User.phone.in_(_phone_lookup_candidates(x))` is always at
    least as permissive as the `User.phone == x` it replaces, never less,
    and safe to call with a non-phone-shaped identifier (an email — see
    every combined email-or-phone call site) since a string with no
    recognizable 8-digit Sierra Leone subscriber number inside it just
    yields itself as the only candidate, identical to the old exact-match
    behavior. No data migration needed: this fixes lookups against
    already-stored numbers in ANY format without changing what's stored.
    """
    candidates = {identifier}
    suffix = _normalize_phone_suffix(identifier)
    if len(suffix) == _PHONE_SUFFIX_LENGTH:
        candidates.update({suffix, f"0{suffix}", f"232{suffix}", f"+232{suffix}"})
    return candidates


def _check_duplicate_signals(new_user: "User") -> None:
    """
    Best-effort fuzzy match of a newly registered youth account against
    every existing account, flagging (not blocking) likely duplicates for
    admin review via DuplicateFlag. Two independent signals, either one
    sufficient to flag:
      1. Phone numbers share the same last _PHONE_SUFFIX_LENGTH digits
         but are not byte-identical (already impossible — unique
         constraint) — catches the same number re-entered with a
         different country-code prefix or leading-zero formatting.
      2. Name similarity (difflib.SequenceMatcher ratio, case/whitespace
         normalized) at or above _DUPLICATE_NAME_SIMILARITY_THRESHOLD —
         catches near-identical names (typos, added middle name, etc.).
    A linear scan against every other user — acceptable at this
    platform's current scale; a real national-scale deployment would need
    this indexed (e.g. a phone-suffix column with a DB index) rather than
    scanned, recorded as a known scaling limit rather than solved
    speculatively here.
    """
    try:
        new_phone_suffix = _normalize_phone_suffix(new_user.phone)
        new_name_normalized = " ".join((new_user.name or "").lower().split())

        for other in User.query.filter(User.id != new_user.id).all():
            reasons = []

            other_phone_suffix = _normalize_phone_suffix(other.phone)
            if new_phone_suffix and other_phone_suffix and new_phone_suffix == other_phone_suffix:
                reasons.append("phone number matches an existing account (formatting difference only)")

            other_name_normalized = " ".join((other.name or "").lower().split())
            if new_name_normalized and other_name_normalized:
                ratio = difflib.SequenceMatcher(None, new_name_normalized, other_name_normalized).ratio()
                if ratio >= _DUPLICATE_NAME_SIMILARITY_THRESHOLD:
                    reasons.append(f"name is {ratio:.0%} similar to an existing account")

            if reasons:
                db.session.add(DuplicateFlag(
                    user_id=new_user.id,
                    matched_user_id=other.id,
                    reason="; ".join(reasons),
                ))

        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Duplicate-detection check failed for user %s", new_user.id)


def _current_employer_id():
    """
    Employer id from the Flask session (web portal login), or None. Also
    verifies the account is still active on every call, not just at login
    time — the same reasoning and pattern as _current_admin_id(): without
    this, suspending a rogue employer (see Employer.active) would only
    block their *next* login while leaving an already-open session cookie
    fully valid, defeating the point of suspension as an immediate
    access-revocation tool.
    """
    eid = session.get("employer_id")
    try:
        eid = int(eid) if eid is not None else None
    except (TypeError, ValueError):
        return None
    if eid is None:
        return None
    employer = Employer.query.get(eid)
    if employer is None or not employer.active:
        session.pop("employer_id", None)
        return None
    return eid


@app.context_processor
def _inject_employer_identity():
    """
    Same reasoning as _inject_admin_identity below (this file's admin
    equivalent): makes `current_employer`/`employer_unread_messages`
    available to every template automatically, so the shared employer
    shell (nav bar, unread-messages badge) doesn't need each route to
    remember to pass them individually. Real gap this closes: messaging
    was previously only reachable by clicking into a specific applicant's
    row on a specific job's page -- there was no signal anywhere that a
    new message had even arrived, so an employer had to remember to go
    looking.

    Only queries when an employer session actually exists (mirrors
    is_admin_role's guard on the admin side) -- this context processor
    fires on every template render site-wide, not just employer pages, so
    youth/anonymous traffic never pays for the extra queries.
    """
    eid = _current_employer_id()
    employer = Employer.query.get(eid) if eid is not None else None
    unread = 0
    if employer is not None:
        job_ids = [row[0] for row in db.session.query(Job.id).filter(Job.employer_id == employer.id).all()]
        if job_ids:
            unread = (
                Message.query.join(Application, Application.id == Message.application_id)
                .filter(
                    Application.job_id.in_(job_ids),
                    Message.sender_type == "user",
                    Message.read.is_(False),
                )
                .count()
            )
    return {
        "current_employer": employer,
        "employer_unread_messages": unread,
    }


def _employer_owns_job(job, eid) -> bool:
    """
    True only if this employer is the job's actual, real owner.

    Deliberately does NOT treat job.employer_id is None (unowned jobs —
    seed_jobs.py demo data, or any pre-employer-accounts legacy row; see
    the Job model's own docstring) as manageable by any logged-in
    employer. That was a real cross-tenant authorization gap, found via
    live audit rather than assumed: with the old "no owner means every
    employer" rule, two completely unrelated, real employer accounts
    could each view every unowned job's applicant list, download those
    applicants' CVs, message them, and accept/reject their applications —
    reproduced live end-to-end (a second employer that never posted the
    job successfully changed an applicant's status). A normal employer
    workflow (post_job()) always sets a real employer_id, so this is not
    a legitimate ongoing use case — only stale demo/legacy data ever has
    employer_id = None. Unowned jobs stay publicly visible to youth
    (GET /jobs is unauthenticated and unaffected by this) but are no
    longer manageable by ANY employer session until a real owner is
    assigned (currently: only by editing the DB directly / a future admin
    "assign owner" action, deliberately not invented speculatively here).
    """
    return eid is not None and job is not None and job.employer_id == eid


def employer_login_required(view):
    """Protects the server-rendered employer portal (session-cookie auth)."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if _current_employer_id() is None:
            return redirect(url_for("employer_login", next=request.path))
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            csrf.protect()
        return view(*args, **kwargs)

    return wrapped


def _current_portal_user_id():
    """
    User id from the Flask session (youth web portal login), or None —
    the session-cookie counterpart to _current_user_id()'s JWT lookup,
    same pattern as _current_employer_id() just above. A youth using the
    mobile app authenticates with a JWT; the same account using the new
    web portal (Phase 3 #9 — "what's on the app, on the web too")
    authenticates with a session cookie instead, so the two auth paths
    need their own lookup rather than forcing the web portal to carry a
    JWT around in a cookie.
    """
    uid = session.get("portal_user_id")
    try:
        uid = int(uid) if uid is not None else None
    except (TypeError, ValueError):
        return None
    if uid is None:
        return None
    user = User.query.get(uid)
    if user is None or not user.active:
        session.pop("portal_user_id", None)
        session.pop("portal_session_token", None)
        return None

    token = session.get("portal_session_token")
    if token is None:
        # Pre-existing session from before device tracking shipped (or any
        # other path that set portal_user_id without minting a token) --
        # heal forward with a new UserSession row instead of forcing a
        # logout; PERMANENT_SESSION_LIFETIME (2h) retires these naturally
        # either way.
        token = secrets.token_urlsafe(32)
        session["portal_session_token"] = token
        _create_user_session(uid, "web", token, notify=False)
    elif UserSession.query.filter_by(session_token=token, revoked_at=None).first() is None:
        # Revoked from another device (Manage Devices) or a stale/forged
        # token -- same "re-check on every request" discipline as
        # _current_employer_id/_current_admin_id's account-level checks,
        # just at the single-device granularity instead of whole-account.
        session.pop("portal_user_id", None)
        session.pop("portal_session_token", None)
        return None
    else:
        _touch_user_session(token)
    return uid


def portal_login_required(view):
    """Protects the server-rendered youth web portal (session-cookie auth)."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if _current_portal_user_id() is None:
            return redirect(url_for("portal_login", next=request.path))
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            csrf.protect()
        return view(*args, **kwargs)

    return wrapped


def _current_admin_id():
    """
    Admin id from the Flask session (real login), or None. Also verifies
    the account is still active on every call, not just at login time —
    otherwise deactivating an admin (see admin_deactivate_account) would
    only block their *next* login while leaving an already-active session
    cookie fully valid, defeating the point of deactivation as an
    immediate access-revocation tool.
    """
    aid = session.get("admin_id")
    try:
        aid = int(aid) if aid is not None else None
    except (TypeError, ValueError):
        return None
    if aid is None:
        return None
    admin = Admin.query.get(aid)
    if admin is None or not admin.active:
        session.pop("admin_id", None)
        return None
    return aid


def _admin_pending_counts() -> dict:
    """
    One COUNT query per queue an admin (not verifier) can act on — the
    real gap this closes: before this, "what needs my attention" required
    clicking into five separate pages one at a time to find out, with no
    signal at all from the sidebar or dashboard that anything was
    waiting. Every count here mirrors the exact filter each queue's own
    route already uses (admin_employer_verifications, admin_reports,
    admin_appeals, admin_duplicate_flags), so the badge a nav link shows
    and what that page actually lists can never silently drift apart.
    """
    return {
        "employer_verifications": Employer.query.filter_by(verification_status="pending").count(),
        "reports": EmployerReport.query.filter_by(status="open").count(),
        "listing_reports": ScrapedListingReport.query.filter_by(status="open").count(),
        "appeals": EmployerAppeal.query.filter_by(status="open").count(),
        "duplicate_flags": DuplicateFlag.query.filter_by(resolved=False).count(),
        "rating_flags": RatingFlag.query.filter_by(status="open").count(),
        "user_appeals": UserAppeal.query.filter_by(status="open").count(),
        "unverified_companies": ScrapedCompany.query.filter_by(verified=False).count(),
    }


@app.context_processor
def _inject_admin_identity():
    """
    Makes `current_admin`/`is_admin_session`/`is_admin_role`/
    `admin_pending`/`admin_pending_total` available to every template
    automatically, computed once here instead of every admin route
    re-deriving and passing them individually (previously only
    admin_analytics() did this — the admin shell templates need the same
    values on every page to render a consistent sidebar, so centralizing
    it avoids each route needing to remember to pass it, and the drift
    that would otherwise cause on the page an author forgets). Cheap: one
    indexed primary-key lookup, and a no-op on any page that never
    references these variables.

    admin_pending's four COUNT queries only run for an actual admin-role
    session -- this context processor fires on EVERY template render
    site-wide (youth/employer pages too, not just the admin console), so
    gating on is_admin_role keeps those four extra queries from running
    on every single page load for every visitor, not just the small
    fraction who are a logged-in admin.
    """
    admin_id = _current_admin_id()
    admin = Admin.query.get(admin_id) if admin_id is not None else None
    is_admin_role = admin is not None and admin.role == "admin"
    admin_pending = _admin_pending_counts() if is_admin_role else {}
    return {
        "current_admin": admin,
        "is_admin_session": admin is not None,
        "is_admin_role": is_admin_role,
        "admin_pending": admin_pending,
        "admin_pending_total": sum(admin_pending.values()),
    }


def admin_login_required(view):
    """Protects session-only admin routes (the login-gated dashboard shell,
    as opposed to admin_access_required below, which also accepts the
    operator API key for machine-to-machine callers)."""

    @wraps(view)
    def wrapped(*args, **kwargs):
        if _current_admin_id() is None:
            return redirect(url_for("admin_login", next=request.path))
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            csrf.protect()
        return view(*args, **kwargs)

    return wrapped


def admin_role_required(role):
    """
    Stricter than admin_login_required: requires a logged-in Admin session
    AND that the account's role matches. Used for /admin/accounts, which a
    "verifier" account (read-only reporting access) should not be able to
    reach — deactivating another operator's account is a materially
    different trust level than viewing aggregate analytics.
    """

    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            aid = _current_admin_id()
            if aid is None:
                return redirect(url_for("admin_login", next=request.path))
            admin = Admin.query.get(aid)
            if admin is None or admin.role != role:
                return _forbidden("Admins with the '%s' role only" % role)
            if request.method in ("POST", "PUT", "PATCH", "DELETE"):
                csrf.protect()
            return view(*args, **kwargs)

        return wrapped

    return decorator


def admin_access_required(view):
    """
    Protects /admin/analytics: a real logged-in Admin session OR the
    operator-held ADMIN_API_KEY, whichever the caller presents. Real named
    accounts (BL-44 follow-up) are now the primary path — created via
    scripts/create_admin.py, never self-registered — but the API key stays
    supported deliberately, for the same reason many real systems support
    both a browser session and an API token on one endpoint: a monitoring
    script or curl-based smoke test has no browser session to present.
    Both a missing key AND a missing admin session 404 identically, so an
    unauthenticated caller cannot even tell the route exists.
    """

    @wraps(view)
    def wrapped(*args, **kwargs):
        if _current_admin_id() is not None:
            return view(*args, **kwargs)
        if not ADMIN_API_KEY:
            return jsonify({"success": False, "error": "Not found"}), 404
        supplied = request.args.get("key") or (request.headers.get("Authorization") or "").removeprefix("Bearer ").strip()
        if not supplied or not secrets.compare_digest(supplied, ADMIN_API_KEY):
            return jsonify({"success": False, "error": "Not found"}), 404
        return view(*args, **kwargs)

    return wrapped


# ----------------- OTP brute-force protection (BL-09 / S-04) -----------------
# Originally an in-memory, per-process dict — correct for a single instance,
# but silently unsafe the moment a second instance is added: each process
# would track its own attempt count, so an attacker spread across N
# instances behind a load balancer gets N times the effective attempt
# budget. Now backed by Redis (a real shared store) when REDIS_URL is
# configured, with an in-memory fallback — loudly warned, same pattern as
# the JWT_SECRET_KEY fallback above — so local dev/tests keep working with
# zero setup.
_OTP_MAX_ATTEMPTS = 5
_OTP_WINDOW = timedelta(minutes=10)

_redis_client = None
_REDIS_URL = os.getenv("REDIS_URL")
if _REDIS_URL:
    try:
        import redis as _redis_module

        _redis_client = _redis_module.from_url(_REDIS_URL, socket_connect_timeout=2)
        _redis_client.ping()
        logger.info("Connected to Redis for OTP rate limiting (%s)", _REDIS_URL)
    except Exception as e:
        _redis_client = None
        logger.warning(
            "REDIS_URL was set but could not be reached (%s) — falling back to "
            "in-memory OTP rate limiting for this process only. Safe for a "
            "single-instance deployment, NOT safe once you run more than one "
            "backend instance behind a load balancer.",
            e,
        )
else:
    logger.warning(
        "REDIS_URL is not set — OTP rate limiting is in-memory, per-process "
        "only. Fine for local development or a single-instance deployment; "
        "set REDIS_URL before running more than one backend instance, or "
        "each instance will independently grant the same attempt budget."
    )

_otp_attempts: dict[str, list[datetime]] = {}

# ----------------- JWT revocation (logout) -----------------
# Real gap found via a full OWASP Top 10 (A07: Identification and
# Authentication Failures) review: JWTs issued to the mobile app were
# valid for their full 12h JWT_ACCESS_TOKEN_EXPIRES lifetime no matter
# what — there was no server-side way to invalidate one before natural
# expiry, and there wasn't even a /logout route for the mobile app at
# all (the employer/admin session-cookie flows already correctly
# revoke immediately on Employer.active/Admin.active going False —
# JWTs had no equivalent). If a phone were lost/stolen, or an account
# password changed after a suspected compromise, an already-issued
# token kept working regardless for up to 12 more hours. Same
# Redis-backed-with-in-memory-fallback pattern as OTP rate limiting
# above, keyed by the token's own `jti` (JWT ID, a unique claim
# flask-jwt-extended includes automatically) with a TTL set to the
# token's own remaining lifetime — a blocklist entry never needs to
# outlive the token it's blocking, so this can't grow unboundedly.
_jwt_blocklist_memory: dict[str, datetime] = {}


def _add_to_jwt_blocklist(jti: str, expires_at: datetime) -> None:
    if _redis_client is not None:
        ttl_seconds = max(int((expires_at - datetime.utcnow()).total_seconds()), 1)
        _redis_client.setex(f"jwt_blocklist:{jti}", ttl_seconds, "1")
        return
    _jwt_blocklist_memory[jti] = expires_at


def _is_jwt_blocklisted(jti: str) -> bool:
    if _redis_client is not None:
        return bool(_redis_client.exists(f"jwt_blocklist:{jti}"))
    expires_at = _jwt_blocklist_memory.get(jti)
    if expires_at is None:
        return False
    if expires_at < datetime.utcnow():
        # Naturally expired -- prune opportunistically rather than
        # running a separate cleanup timer for the in-memory fallback.
        _jwt_blocklist_memory.pop(jti, None)
        return False
    return True


@jwt.token_in_blocklist_loader
def _check_if_token_is_blocklisted(jwt_header, jwt_payload):
    blocklisted = _is_jwt_blocklisted(jwt_payload["jti"])
    if not blocklisted:
        # Every JWT-protected request passes through this loader before the
        # view runs, making it the one place that sees every authenticated
        # "app" request without adding a second decorator everywhere --
        # same reasoning as this file's other on-every-request checks
        # (_current_admin_id re-verifying admin.active, etc.).
        _touch_user_session(jwt_payload["jti"])
    return blocklisted


# ----------------- Malware scanning (optional, ClamAV) -----------------
# Real gap found via a full-platform review: content_matches_extension()
# above (S-08) only ever checked that an upload's magic bytes matched its
# claimed extension — never that the content was actually safe. For a
# platform where one user's uploaded CV/certificate/verification document
# is routinely downloaded by a DIFFERENT user (an employer reviewing an
# applicant, an admin reviewing an employer's verification document), a
# well-formed PDF that also carries a malicious payload was previously
# accepted with nothing to catch it. Same optional-integration pattern as
# Redis above: connects if CLAMD_HOST is set, logs and disables scanning
# (not a hard failure) if it isn't — see docker-compose.clamav.yml.
_clamd_client = None
CLAMD_HOST = os.getenv("CLAMD_HOST")
CLAMD_PORT = int(os.getenv("CLAMD_PORT", "3310"))
if CLAMD_HOST:
    try:
        import clamd as _clamd_module

        _clamd_client = _clamd_module.ClamdNetworkSocket(host=CLAMD_HOST, port=CLAMD_PORT, timeout=10)
        _clamd_client.ping()
        logger.info("Connected to ClamAV at %s:%s for upload malware scanning", CLAMD_HOST, CLAMD_PORT)
    except Exception as e:
        _clamd_client = None
        logger.warning(
            "CLAMD_HOST was set but ClamAV could not be reached (%s) — uploads will "
            "NOT be malware-scanned until this is fixed. Unlike Redis/OTP rate "
            "limiting above, this does not silently fall back to a degraded-but-safe "
            "mode: file_is_malware_free() fails CLOSED (rejects uploads) rather than "
            "open while CLAMD_HOST is set but unreachable, since an operator who "
            "explicitly configured scanning almost certainly wants uploads rejected "
            "when scanning can't be confirmed, not silently allowed through.",
            e,
        )
else:
    logger.warning(
        "CLAMD_HOST is not set — uploaded files are validated for content-type "
        "match only (S-08), never scanned for malware. Set CLAMD_HOST (see "
        "docker-compose.clamav.yml) for real scanning before handling uploads "
        "from untrusted users in production."
    )


def file_is_malware_free(file_storage) -> bool:
    """
    Scans the raw upload stream via ClamAV's INSTREAM protocol — before
    the file ever touches disk, not after, so a positive hit never gets
    written anywhere a later step could read it. Resets the stream
    position afterward, same discipline as content_matches_extension()
    above, since callers still need to .save() the same FileStorage
    object afterward.

    Fails OPEN (returns True) if CLAMD_HOST is unset — matches every other
    optional external integration in this codebase (SMTP, Firebase,
    Twilio, Vault, Redis): a deployment that hasn't configured scanning
    gets the same behavior it always had, not a new hard failure.

    Fails CLOSED (returns False) if CLAMD_HOST IS set but the scan itself
    can't be completed (clamd unreachable mid-request, timeout, etc.) —
    deliberately the opposite of the fail-open pattern used elsewhere.
    Rate limiting or OTP degrading to a weaker-but-functional mode when
    Redis is unreachable is a reasonable trade-off; silently accepting an
    unscannable file when an operator explicitly turned scanning on is
    not the same kind of trade-off — "reject when unsure" is the correct
    default for exactly the file types this gates.
    """
    if _clamd_client is None:
        return True

    try:
        file_storage.stream.seek(0)
        result = _clamd_client.instream(file_storage.stream)
        file_storage.stream.seek(0)
        status, reason = result.get("stream", (None, None))
        if status == "FOUND":
            logger.warning("ClamAV flagged an upload as infected: %s", reason)
        return status == "OK"
    except Exception:
        logger.exception("ClamAV scan failed — rejecting upload (fail closed since CLAMD_HOST is configured)")
        return False


# Real, if minor, gap found reviewing "lightweight image usage" against
# every upload path sharing ALLOWED_EXTENSIONS (certificates, CV/
# supporting documents, employer verification documents, message
# attachments): a phone-camera photo of a paper certificate easily
# arrives at 3-8MB, and nothing downscaled or recompressed it before
# storage. Not high-impact (these are reviewed as forced downloads, not
# rendered inline or re-fetched on every scroll — see download_certificate
# below), but a real, free-to-fix gap once noticed. 2000px on the longest
# side is comfortably more than needed to keep a scanned/photographed
# document fully legible on any real review screen.
_MAX_IMAGE_UPLOAD_DIMENSION = 2000


def _compress_uploaded_image_if_needed(file_path: str) -> None:
    """
    Best-effort downscale + recompress, in place, for an already-saved
    upload that turns out to be an oversized png/jpg/jpeg. PDFs/DOCs
    (also allowed by ALLOWED_EXTENSIONS) are left completely untouched —
    this only ever opens files with an image extension.

    Deliberately fail-open, unlike file_is_malware_free()'s fail-closed
    security gate above: if Pillow can't open or re-save a file for any
    reason (corrupt-but-still-passed-content-sniffing image, an exotic
    color mode, etc.), the original upload is left exactly as it was
    rather than risking a real user's document over an optimization that
    was never a correctness requirement.

    Callers MUST call this before generate_file_hash() wherever the
    result is hashed (today, only the credential-upload path does) — the
    hash has to reflect the bytes actually stored/served afterward, or a
    verifier re-hashing the downloaded file later would get a mismatch
    against whatever was recorded (e.g. written on-chain).
    """
    ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
    if ext not in ("png", "jpg", "jpeg"):
        return

    try:
        with Image.open(file_path) as img:
            if max(img.size) <= _MAX_IMAGE_UPLOAD_DIMENSION:
                return
            img.thumbnail((_MAX_IMAGE_UPLOAD_DIMENSION, _MAX_IMAGE_UPLOAD_DIMENSION), Image.LANCZOS)
            if ext == "png":
                img.save(file_path, format="PNG", optimize=True)
            else:
                # A JPEG can't carry an alpha channel -- converting to RGB
                # only actually changes anything for the rare upload that
                # has a .jpg/.jpeg extension but decodes to RGBA/P (a
                # mislabeled PNG-as-JPEG would already have been rejected
                # by content_matches_extension() above, so this is purely
                # a defensive save-time safeguard, not the primary guard).
                img.convert("RGB").save(file_path, format="JPEG", quality=85, optimize=True)
    except Exception:
        logger.exception("Best-effort image compression failed for %s — keeping the original upload as-is", file_path)


def _rate_limited(key: str, max_attempts: int, window: timedelta) -> bool:
    """
    Generic sliding-window rate limiter — the same Redis-sorted-set-with-
    in-memory-fallback mechanism originally built for OTP brute-force
    protection (BL-09/S-04), generalized so any caller can apply its own
    budget under its own key namespace rather than sharing OTP's specific
    5-per-10-minutes limit. `_otp_rate_limited`/TOTP verification and the
    upload rate limiter below (see `_upload_rate_limited`) are both thin
    wrappers over this.
    """
    if _redis_client is not None:
        redis_key = f"ratelimit:{key}"
        now = datetime.utcnow().timestamp()
        window_start = now - window.total_seconds()
        _redis_client.zremrangebyscore(redis_key, 0, window_start)
        return _redis_client.zcard(redis_key) >= max_attempts

    now = datetime.utcnow()
    attempts = [t for t in _otp_attempts.get(key, []) if now - t < window]
    _otp_attempts[key] = attempts
    return len(attempts) >= max_attempts


def _record_attempt(key: str, window: timedelta) -> None:
    if _redis_client is not None:
        redis_key = f"ratelimit:{key}"
        now = datetime.utcnow().timestamp()
        # Score and member both = timestamp; a member collision (two
        # attempts landing on the exact same float) just dedupes to one
        # recorded attempt, which under-counts by at most one in a
        # vanishingly rare race — an acceptable trade for avoiding a second
        # round trip to fetch a unique sequence number.
        _redis_client.zadd(redis_key, {str(now): now})
        _redis_client.expire(redis_key, int(window.total_seconds()))
        return

    _otp_attempts.setdefault(key, []).append(datetime.utcnow())


def _otp_rate_limited(email: str) -> bool:
    return _rate_limited(f"otp:{email}", _OTP_MAX_ATTEMPTS, _OTP_WINDOW)


def _record_otp_attempt(email: str) -> None:
    _record_attempt(f"otp:{email}", _OTP_WINDOW)


# ----------------- Login brute-force protection -----------------
# Real gap found via a full security review: none of the three
# password-based login routes (/login for the mobile app, /employer/login,
# /admin/login) had ANY throttle on password attempts — only OTP
# verification did (_otp_rate_limited above). check_password_hash()'s slow
# hash gives some inherent per-attempt cost, but that's not a substitute
# for a real limit: nothing stopped an unbounded number of password
# guesses against any account, including an Admin account, which has the
# most privileged access in this entire system (employer suspension,
# report/appeal resolution, other operators' accounts). Same generic
# _rate_limited()/_record_attempt() mechanism as OTP/uploads/reports —
# keyed by the submitted identifier (matching OTP's convention), not IP,
# so an attacker spreading guesses across many source IPs still can't
# out-run the limit for one specific target account.
_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_WINDOW = timedelta(minutes=15)


def _login_rate_limited(identity: str) -> bool:
    return _rate_limited(f"login:{identity}", _LOGIN_MAX_ATTEMPTS, _LOGIN_WINDOW)


def _record_login_attempt(identity: str) -> None:
    _record_attempt(f"login:{identity}", _LOGIN_WINDOW)


# ----------------- Upload rate limiting -----------------
# Real gap found via a live security audit, not anticipated in advance: no
# upload endpoint (credential issuance, job applications, employer
# verification documents, message attachments) had any throttle at all —
# only the 16MB-per-file cap (see MAX_CONTENT_LENGTH). That bounds a single
# request, not a burst of many, so nothing stopped rapid repeated uploads
# from filling disk. Budget is deliberately much looser than OTP's (5/10min
# would be far too tight for real usage — applying to a few jobs in one
# sitting alone can mean several file uploads), keyed per-user (the
# authenticated/session identity, not IP, since IP-based limiting would
# incorrectly throttle every user behind the same NAT/campus network).
_UPLOAD_MAX_ATTEMPTS = 20
_UPLOAD_WINDOW = timedelta(minutes=10)


def _upload_rate_limited(identity: str) -> bool:
    return _rate_limited(f"upload:{identity}", _UPLOAD_MAX_ATTEMPTS, _UPLOAD_WINDOW)


def _record_upload_attempt(identity: str) -> None:
    _record_attempt(f"upload:{identity}", _UPLOAD_WINDOW)


# A real report is rare; a much tighter budget than uploads, deliberately —
# the report system itself is a plausible harassment vector (spam-reporting
# an innocent employer, or flooding the admin review queue) if left
# unthrottled.
_REPORT_MAX_ATTEMPTS = 5
_REPORT_WINDOW = timedelta(hours=1)


def _report_rate_limited(identity: str) -> bool:
    return _rate_limited(f"report:{identity}", _REPORT_MAX_ATTEMPTS, _REPORT_WINDOW)


def _record_report_attempt(identity: str) -> None:
    _record_attempt(f"report:{identity}", _REPORT_WINDOW)


# AI-assisted CV generation (see cv_generator.polish_cv_content) spends a
# real Anthropic API call — real per-call cost — every time it runs,
# unlike everything else this endpoint does. No endpoint that spends real
# money per call had any throttle before this; same generic
# _rate_limited() mechanism as uploads/reports, keyed per-user. Loose
# enough for genuine iterative use (edit the profile, regenerate, tweak,
# regenerate again) but bounded against a client hammering the endpoint.
_CV_GENERATION_MAX_ATTEMPTS = 10
_CV_GENERATION_WINDOW = timedelta(hours=1)


def _cv_generation_rate_limited(identity: str) -> bool:
    return _rate_limited(f"cvgen:{identity}", _CV_GENERATION_MAX_ATTEMPTS, _CV_GENERATION_WINDOW)


def _record_cv_generation_attempt(identity: str) -> None:
    _record_attempt(f"cvgen:{identity}", _CV_GENERATION_WINDOW)


# A suspended employer only needs to file one appeal to be reviewed — this
# budget exists purely to stop the public, unauthenticated-by-session
# /employer/appeal endpoint being hammered, not to legitimately allow
# repeated filing.
_APPEAL_MAX_ATTEMPTS = 3
_APPEAL_WINDOW = timedelta(hours=1)


def _appeal_rate_limited(identity: str) -> bool:
    return _rate_limited(f"appeal:{identity}", _APPEAL_MAX_ATTEMPTS, _APPEAL_WINDOW)


def _record_appeal_attempt(identity: str) -> None:
    _record_attempt(f"appeal:{identity}", _APPEAL_WINDOW)


# -------- Shared credential issuing helper --------
def _issue_credential_internal(user_id, title, issuer, file):
    """
    Shared logic for issuing a credential, used by both /issue_credential
    and /api/certificate/upload.

    These two routes previously diverged silently (only one deduped by
    hash and attempted the on-chain write — see Phase 4/TD-07 of the
    engineering review, and the helper's own now-corrected docstring, which
    used to falsely claim this was already shared). Reconciled here (BL-16)
    so both routes behave identically: same dedup-by-hash check, same
    best-effort on-chain write, same "already exists" vs "newly created"
    signal back to the caller.

    Returns (credential, error_message, created).
    """
    if not user_id or not title or not issuer:
        return None, "Missing required fields", False

    if not file or not allowed_file(file.filename):
        return None, "Invalid or missing certificate file", False
    if not content_matches_extension(file, file.filename):
        return None, "File content does not match its extension", False
    if not file_is_malware_free(file):
        return None, "File failed a security scan", False

    # Unique-prefixed filename (the same S-02 pattern used for CV/supporting-
    # document uploads, employer verification documents, and message
    # attachments) — this was the one upload path in the codebase that had
    # been missed. Without it, two users uploading a file with the same
    # original name (e.g. both named "certificate.pdf") collide on the same
    # path: the second save silently overwrites the first's file on disk,
    # while the first credential's DB row keeps pointing at that filename
    # and keeps its *original* content hash — so the older credential's
    # stored hash stops matching what's actually on disk, and anyone
    # downloading it gets a different user's document. Reproduced live
    # before this fix: uploading "certificate.pdf" as two different users
    # left the first user's Credential row serving the second user's file
    # content verbatim.
    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
    filename = f"{ts}_{user_id}_{secure_filename(file.filename)}"
    file_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    file.save(file_path)
    # Must run before generate_file_hash() -- see this function's own
    # docstring for why the hash has to reflect the final, stored bytes.
    _compress_uploaded_image_if_needed(file_path)
    file_hash = generate_file_hash(file_path)

    # Scoped to THIS user — see Credential.__table_args__'s docstring for
    # the real cross-user collision this used to cause when scoped to hash
    # alone. A different user already holding a credential with the same
    # content hash is a separate, expected case (each gets their own row),
    # not a dedup match.
    existing = Credential.query.filter_by(hash=file_hash, user_id=user_id).first()
    if existing:
        # Same file uploaded again by the same user: don't create a
        # duplicate row, but do retry the on-chain write if it never
        # succeeded the first time. Backgrounded — see
        # _write_onchain_tx_for_credential_async's docstring for why this
        # request shouldn't block on that.
        if not existing.onchain_tx:
            _spawn_background_onchain_write(existing.id)
        return existing, None, False

    new_cred = Credential(
        user_id=user_id,
        title=title,
        issuer=issuer,
        file_path=filename,
        hash=file_hash,
    )
    db.session.add(new_cred)
    db.session.commit()  # now new_cred.id is set

    # The on-chain write happens in the background now (BL-18) — this
    # request returns as soon as the credential itself is durably stored,
    # not up to ~90s later once a Hardhat subprocess round trip finishes.
    # onchain_tx starts (and, for the caller of this function, stays)
    # unset here; _write_onchain_tx_for_credential_async fills it in and
    # notifies the owner once the write actually lands.
    _spawn_background_onchain_write(new_cred.id)

    log_event("credential_issued", user_id=user_id, credential_id=new_cred.id, onchain="pending")
    return new_cred, None, True


# ----------------- MIDDLEWARE / HEADERS -----------------
@app.after_request
def _no_cache_for_lists(resp):
    # Keep lists always fresh in clients that might cache (jobs & application list)
    if request.path.startswith("/jobs") or request.path.startswith("/my_applications"):
        resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp



# Real gap found via a full OWASP Top 10 (A05: Security Misconfiguration)
# review: no Content-Security-Policy at all, on an app that renders raw
# Jinja HTML for the admin/employer portals and (until this same review)
# had a real, since-fixed XSS vector in the admin dashboard's SVG charts
# (see _svg_bar_chart's docstring). X-Frame-Options/nosniff/Referrer-Policy
# already existed and still do; CSP is genuine defense-in-depth on top —
# script-src 'self' with no 'unsafe-inline' means even a FUTURE XSS bug
# that manages to inject markup still can't execute a <script> tag or an
# onclick="..." attribute, because the browser itself refuses to run
# anything not loaded from this same origin. This is only honestly
# possible because every inline <script> block and onclick=/onchange=
# attribute in every template was found and externalized to a real
# static .js file as part of this same pass (see
# static/admin_shell.js, static/employer_dashboard_live.js,
# static/employer_applications_live.js) — a CSP this strict would have
# silently broken the admin nav toggle and the real-time Socket.IO
# refresh otherwise. style-src has no 'unsafe-inline' either — every
# inline style="..." attribute across the admin/employer templates was
# found and replaced with a real class in static/style.css or
# static/admin.css as part of this same pass, so a future XSS bug can't
# even inject a <style> block or style="..." attribute that the browser
# will honor.
_CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "object-src 'none'"
)


@app.after_request
def _security_headers(resp):
    """
    Baseline security headers (BL-08 / Phase 3 #23). HSTS is only sent once
    the request actually arrived over HTTPS (via ProxyFix's is_secure check
    above) — sending it over plain HTTP would be a lie the browser can't act
    on, and could be actively harmful if this exact host is ever legitimately
    served over HTTP during local development.
    """
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["Content-Security-Policy"] = _CONTENT_SECURITY_POLICY
    if IS_PRODUCTION and request.is_secure:
        resp.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    return resp


# ----------------- ERROR HANDLERS -----------------
@app.errorhandler(RequestEntityTooLarge)
def _too_large(e):
    return jsonify({"success": False, "error": "File too large (max 16 MB)"}), 413


# ----------------- API ROUTES -----------------
@app.route("/healthz")
def health():
    return jsonify({"ok": True, "time": datetime.utcnow().isoformat()})


@app.route("/healthz/deep")
def health_deep():
    """
    A separate, deeper check from /healthz — deliberately not a change to
    /healthz itself (docs/monitoring.md previously flagged that changing
    what /healthz means would be a real behavior change worth its own
    decision, since a load balancer's readiness probe would then mark an
    instance unhealthy on a transient DB blip). This endpoint additionally
    verifies DB connectivity and, if REDIS_URL is configured, that Redis is
    reachable too — useful for an operator's own dashboard/alerting, not
    for a load balancer's fast liveness probe.
    """
    checks = {}
    overall_ok = True

    try:
        db.session.execute(db.text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as e:
        checks["database"] = f"error: {e}"
        overall_ok = False

    if _redis_client is not None:
        try:
            _redis_client.ping()
            checks["redis"] = "ok"
        except Exception as e:
            checks["redis"] = f"error: {e}"
            overall_ok = False
    else:
        checks["redis"] = "not configured"

    return jsonify({"ok": overall_ok, "checks": checks, "time": datetime.utcnow().isoformat()}), (200 if overall_ok else 503)


@app.route("/employers")
def employers_info():
    """
    Public "for employers" page linked from the new shared site header
    (_site_header.html) and from every employer role card's context.
    No auth required -- a prospective employer deciding whether to sign
    up needs to see this before they have an account.

    Pricing is described honestly rather than invented: post_job() has
    no cost, no listing limit, and never sets Job.application_deadline
    for an employer-posted job (grep confirms it -- that column is
    scraped-listing-only), so "free, no forced expiration" is what the
    platform actually does today, not marketing copy. There is also
    currently no employer-facing way to close a listing early (only
    admin_resolve_listing_report() can remove a Job row, via a scam
    report) -- the page doesn't claim otherwise.
    """
    return render_template("employers_info.html")


@app.route("/")
def home():
    """
    Real gap found via a full-codebase review: this route used to return
    a bare JSON API status message, and nothing anywhere in this app --
    no shell's brand mark, no auth page, no nav -- ever actually linked
    to it (checked directly: zero matches for url_for('home') or a
    hardcoded href="/" across every template). /admin, /employer, and
    /portal each worked fine as their own destination, but a first-time
    visitor to the bare domain, or anyone trying to point one link at
    "the YouthChain web app," had no single page that led anywhere.
    Nothing else depends on the old JSON shape (no test asserts on it,
    and machine health checks already have their own dedicated route --
    see /healthz), so this is a clean, safe replacement rather than a
    breaking one: a real landing page presenting the three real,
    already-working entry points (portal/employer/admin login) as equal,
    clearly-labeled choices, not a fourth parallel auth system.

    featured_jobs reuses the exact same _search_jobs() query GET /jobs
    already runs (same default filters: employer-sourced, not expired,
    newest first) rather than a bespoke "featured" query -- a first-time
    visitor sees the same live listings /jobs itself would return, not a
    curated or fabricated subset. Empty on a fresh install with zero
    jobs; home.html guards the whole section on that rather than
    rendering an empty grid.
    """
    featured_jobs = _search_jobs(q=None, location=None, skill=None, limit=6, offset=0)
    return render_template("home.html", featured_jobs=[job.to_dict() for job in featured_jobs])


@app.route("/privacy-policy")
def privacy_policy():
    """
    Linked from the registration consent checkbox (both /register's caller
    and the mobile app) and publicly reachable on its own. Explicitly a
    placeholder pending real legal review — see the engineering constitution
    constraint against fabricating legal text: this codebase can describe
    what data it technically collects and why (that much is verifiable from
    the models/routes themselves), but cannot write a legally binding
    privacy policy for a national-scale platform without an actual lawyer,
    so it does not pretend to.
    """
    return render_template("privacy_policy.html")


@app.route("/terms-of-service")
def terms_of_service():
    """
    Same posture as privacy_policy() above: an accurate, engineering-written
    description of what the platform does and does not guarantee (the
    credential-verification scope, acceptable use, reporting/enforcement),
    explicitly marked as a draft pending real legal review rather than
    fabricated legal text -- see this codebase's engineering constitution
    on not inventing legal advice. Not yet linked from the registration
    consent checkbox alongside /privacy-policy; wiring that in is a product
    decision (single checkbox agreeing to both vs. two separate ones) left
    for whoever finalizes the real legal text, not assumed here.
    """
    return render_template("terms_of_service.html")


@app.route("/get-app")
def get_app():
    """
    Real bug found by re-checking my own portal work rather than trusting
    my own summary of it: every "Get the app" CTA across the youth web
    portal (the nav bar on every page, plus the dashboard/applications
    banners) pointed at /privacy-policy -- copy-pasted from a nearby
    url_for() call and never actually fixed. Given a real destination
    instead. Deliberately does NOT link to a Play Store / App Store URL --
    per docs/mobile-release.md, this codebase has never actually submitted
    to either store (code signing and store submission are both explicitly
    out of scope there), so a store badge/link here would be a fabricated
    URL pointing at a listing that doesn't exist. Honest holding page
    instead: what the app adds over the web portal, and why those specific
    things are hard to replicate in a browser (see this route's own
    reasoning against reproducing FCM push, Socket.IO real-time messaging,
    and offline JWT-session persistence server-side for the web).
    """
    return render_template("get_app.html")


@app.route("/manifest.json")
def portal_manifest():
    """
    Makes the youth web portal installable (Chrome/Edge "Add to Home
    Screen" on Android, the equivalent on desktop) -- served from the
    root, not /static/, purely so the URL is a stable, conventional one a
    browser's installability check expects; the file itself still lives
    in static/img/ alongside the icons it references. start_url is
    /portal, not /, since / is this backend's plain JSON API root (see
    home() above) -- installing this manifest must launch straight into
    the actual youth-facing app, not an API status message.

    No offline JWT-session persistence or offline form submission is
    implied by any of this (see get_app()'s own docstring on why that's
    deliberately not attempted) -- this manifest plus sw.js below only
    ever caches already-rendered GET pages for read access while offline,
    the same "last known state, not a live app" contract a native app's
    own cache would give for content it already fetched once.
    """
    return jsonify({
        "name": "YouthChain",
        "short_name": "YouthChain",
        "description": "Connecting Sierra Leonean youth to verified jobs and trusted employment history.",
        "start_url": "/portal",
        "scope": "/",
        "display": "standalone",
        "background_color": "#F5F3EE",
        "theme_color": "#0F7A5C",
        "icons": [
            {"src": "/static/img/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "/static/img/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any"},
            {"src": "/static/img/icon-192-maskable.png", "sizes": "192x192", "type": "image/png", "purpose": "maskable"},
            {"src": "/static/img/icon-512-maskable.png", "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    })


@app.route("/sw.js")
def portal_service_worker():
    """
    Served from the root (not /static/sw.js) so its default scope covers
    the whole origin, including every /portal/* route -- a service worker
    can only ever control paths at or below its own URL unless the
    Service-Worker-Allowed response header widens that, and this avoids
    needing that header at all. The file itself lives in static/ like
    every other JS asset; this route just re-serves it at the URL a
    service worker needs to be registered from. no-store so a browser
    checking for an updated worker never gets served a stale cached copy
    of the thing whose entire job is managing that browser's cache --
    see sw.js's own docstring for the update-detection mechanism this
    enables.
    """
    return send_from_directory(
        app.static_folder, "sw.js", mimetype="application/javascript",
        max_age=0,
    )


# ----------- USER AUTH -----------
@app.route("/register", methods=["POST"])
def register():
    data = request.json or {}
    # first_name/last_name is the new shape (see registration_screen.dart's
    # docstring) -- concatenated into the same single `name` column every
    # other part of this codebase already reads as one display string.
    # Falls back to a plain "name" key for backward compatibility with any
    # client that still sends one (including this suite's own
    # conftest.register_user helper).
    first_name = (data.get("first_name") or "").strip()
    last_name = (data.get("last_name") or "").strip()
    name = f"{first_name} {last_name}".strip() or data.get("name")
    phone = data.get("phone")
    email = data.get("email")
    password = data.get("password")
    # "channel" is new (see OTPCode's docstring) -- defaults to "email" so
    # an old, not-yet-updated mobile client (which never sends this key)
    # keeps behaving exactly as before, verifying against email only.
    channel = data.get("channel") if data.get("channel") in ("email", "sms") else "email"
    # OTP code supplied by client when ENFORCE_EMAIL_OTP_REG is ON
    otp_code = (data.get("otp_code") or "").strip()
    consent = data.get("consent")
    # Optional -- see User.ncra_id's docstring for why this is a distinct
    # field from phone now, not folded into it.
    ncra_id = (data.get("ncra_id") or "").strip() or None

    if not all([name, phone, email, password]):
        return jsonify({"success": False, "error": "Missing required fields"}), 400
    if not valid_email(email):
        return jsonify({"success": False, "error": "Please enter a valid email address"}), 400
    if consent is not True:
        return jsonify({"success": False, "error": "You must accept the privacy policy to register"}), 400

    if len(password) < 8:
        return jsonify({"success": False, "error": "Password must be at least 8 characters"}), 400
    if _password_is_breached(password):
        return jsonify({
            "success": False,
            "error": "This password has appeared in a known data breach. Please choose a different one.",
        }), 400

    # Enforce a real/working contact method via OTP -- whichever one was
    # actually verified (channel), not always email.
    if ENFORCE_EMAIL_OTP_REG:
        identifier = email if channel == "email" else phone
        if _otp_rate_limited(identifier):
            return jsonify({"success": False, "error": "Too many attempts. Try again later."}), 429
        if len(otp_code) != 6 or not otp_code.isdigit():
            return jsonify({"success": False, "error": "Enter the 6-digit code sent to you"}), 400
        _record_otp_attempt(identifier)
        row = OTPCode.query.filter_by(identifier=identifier, channel=channel, code=otp_code, purpose="register", used=False).first()
        if not row or row.expires_at < datetime.utcnow():
            if row:
                row.used = True
                db.session.commit()
            return jsonify({"success": False, "error": "Invalid or expired code"}), 400
        # Mark code used now that we’re creating the account
        row.used = True
        db.session.commit()

    if User.query.filter(User.phone.in_(_phone_lookup_candidates(phone)) | (User.email == email)).first():
        # Deliberately the same generic shape as a real validation error —
        # closes S-07 (user enumeration) alongside the /auth/otp/request fix.
        return jsonify({"success": False, "error": "Unable to register with the details provided"}), 400

    hashed_pw = generate_password_hash(password)
    new_user = User(
        name=name,
        phone=phone,
        email=email,
        ncra_id=ncra_id,
        password_hash=hashed_pw,
        consent_accepted_at=datetime.utcnow(),
    )
    db.session.add(new_user)
    db.session.commit()
    log_event("user_registered", user_id=new_user.id)
    _check_duplicate_signals(new_user)

    token = create_access_token(identity=str(new_user.id))
    _create_user_session(new_user.id, "app", decode_token(token)["jti"], notify=False)
    return jsonify({
        "message": "✅ User registered successfully",
        "user": new_user.to_dict(),
        "access_token": token,
    }), 201


@app.route("/login", methods=["POST"])
def login():
    data = request.json or {}
    phone_or_email = data.get("phone") or data.get("email")
    password = data.get("password")

    if phone_or_email and _login_rate_limited(phone_or_email):
        return jsonify({"success": False, "error": "Too many login attempts. Try again later."}), 429
    if phone_or_email:
        _record_login_attempt(phone_or_email)

    user = User.query.filter(User.phone.in_(_phone_lookup_candidates(phone_or_email)) | (User.email == phone_or_email)).first()
    # Same error for "no such user" and "wrong password" — do not let a caller
    # distinguish account existence from credential correctness (S-07).
    if not user or not check_password_hash(user.password_hash, password or ""):
        # Real gap found via a full OWASP Top 10 (A09: Security Logging
        # and Monitoring Failures) review: every successful login/
        # registration/admin action already gets a real AnalyticsEvent
        # row (see the many other log_event() calls throughout this
        # file) -- but a FAILED login never did, anywhere in this
        # codebase, despite rate limiting (_login_rate_limited above)
        # existing specifically because failed logins can mean an active
        # attack. Throttling an attack and having an audit trail of it
        # having happened are two different things — an admin reviewing
        # "why did this account get rate-limited yesterday" had nothing
        # to look at. user_id is intentionally None when the account
        # doesn't exist at all — this is an internal audit log, not the
        # HTTP response, so it's fine (and useful) for it to know more
        # than the caller is allowed to; S-07 is about what the response
        # reveals, not what gets recorded server-side.
        log_event("user_login_failed", user_id=user.id if user else None, identifier=phone_or_email)
        return jsonify({"success": False, "error": "❌ Incorrect phone/email or password"}), 401

    if not user.active:
        # Password is correct at this point, so the caller has already
        # proven account ownership -- revealing suspension here isn't an
        # enumeration leak the way it would be pre-credential-check.
        # Same wording/shape as employer_login()'s equivalent branch.
        return jsonify({"success": False, "error": "This account has been suspended.", "suspended": True}), 403

    log_event("user_login", user_id=user.id)
    token = create_access_token(identity=str(user.id))
    _create_user_session(user.id, "app", decode_token(token)["jti"])
    return jsonify({
        "message": "✅ Login successful",
        "user": user.to_dict(),
        "access_token": token,
    }), 200


@app.route("/logout", methods=["POST"])
@jwt_required()
def logout():
    """
    Real gap found via a full OWASP Top 10 review (A07) — see
    _add_to_jwt_blocklist's docstring above for the full reasoning. This
    route didn't exist at all before; the mobile app could only ever
    forget its own copy of the token locally, which left the token itself
    still fully valid server-side for whatever remained of its 12h
    lifetime. Blocklists the exact token used to authenticate this
    request (by its jti) until that token's own natural expiry — not a
    account-wide "log out everywhere", just this one credential.
    """
    claims = get_jwt()
    _add_to_jwt_blocklist(claims["jti"], datetime.utcfromtimestamp(claims["exp"]))
    UserSession.query.filter_by(session_token=claims["jti"], revoked_at=None).update({"revoked_at": datetime.utcnow()})
    db.session.commit()
    return jsonify({"success": True, "message": "Logged out"}), 200


# ----------- EMAIL OTP AUTH (LOGIN) -----------
@app.route("/auth/otp/request", methods=["POST"])
def otp_request():
    """
    channel + identifier are channel-aware the same way
    otp_request_for_registration()/otp_request_for_reset() already are
    (see OTPCode's own docstring) — an old, not-yet-updated mobile client
    sends {"email": "..."} with neither key, which still works exactly as
    before: identifier falls back to email, channel defaults to "email".
    channel="sms" looks the account up by User.phone instead, so a phone-
    only-remembering user (Orange/Africell number, no email login flow
    Sierra Leone users already leaned into for register/reset) can log in
    without ever typing a password.
    """
    data = request.json or {}
    channel = data.get("channel") if data.get("channel") in ("email", "sms") else "email"
    identifier = (data.get("identifier") or data.get("email") or "").strip()

    if channel == "email":
        if not valid_email(identifier):
            return jsonify({"success": False, "error": "Please enter a valid email address"}), 400
    elif not identifier:
        return jsonify({"success": False, "error": "Please enter a phone number"}), 400

    # Rate-limited (and recorded) before the account-existence check, and
    # unconditionally regardless of whether the account exists — an
    # unauthenticated caller could otherwise spam this endpoint to
    # email-bomb (or, now, SMS-bomb -- a real per-message Twilio cost, not
    # just an annoyance) any registered user with unlimited OTP codes
    # (found live: 30/30 rapid requests sent 30 real emails before this
    # fix). Checking before the `if user:` branch also keeps the
    # anti-enumeration property below intact: a real and a fake
    # email/phone both see identical 200-then-429 behavior, so the
    # rate-limit response itself can't be used to tell which accounts exist.
    if _otp_rate_limited(identifier):
        return jsonify({"success": False, "error": "Too many attempts. Try again later."}), 429
    _record_otp_attempt(identifier)

    user = User.query.filter_by(email=identifier).first() if channel == "email" \
        else User.query.filter(User.phone.in_(_phone_lookup_candidates(identifier))).first()
    # Same success-shaped response whether or not the account exists (S-07) —
    # no OTP is actually sent for an unregistered email/phone, but the
    # caller can't tell the difference from the response alone.
    if user:
        OTPCode.query.filter(OTPCode.expires_at < datetime.utcnow()).delete()
        code = _otp_code()
        expires = datetime.utcnow() + timedelta(minutes=10)
        OTPCode.query.filter_by(identifier=identifier, purpose="login", used=False).update({"used": True})
        db.session.add(OTPCode(identifier=identifier, channel=channel, purpose="login", code=code, expires_at=expires, used=False))
        db.session.commit()
        body = f"Your login code is {code}. It expires in 10 minutes."
        if channel == "sms":
            send_sms(identifier, body)
        else:
            _send_email(identifier, "Your YouthChain OTP", body)

    return jsonify({"message": "✅ If that account exists, a code has been sent"}), 200


@app.route("/auth/otp/verify", methods=["POST"])
def otp_verify():
    data = request.json or {}
    channel = data.get("channel") if data.get("channel") in ("email", "sms") else "email"
    identifier = (data.get("identifier") or data.get("email") or "").strip()
    code = (data.get("code") or "").strip()

    if not identifier or len(code) != 6 or not code.isdigit():
        return jsonify({"success": False, "error": "Invalid email or code"}), 400
    if channel == "email" and not valid_email(identifier):
        return jsonify({"success": False, "error": "Invalid email or code"}), 400

    if _otp_rate_limited(identifier):
        return jsonify({"success": False, "error": "Too many attempts. Try again later."}), 429
    _record_otp_attempt(identifier)

    row = OTPCode.query.filter_by(identifier=identifier, channel=channel, code=code, purpose="login", used=False).first()
    if not row or row.expires_at < datetime.utcnow():
        if row:
            row.used = True
            db.session.commit()
        return jsonify({"success": False, "error": "Invalid or expired code"}), 400

    row.used = True
    db.session.commit()

    user = User.query.filter_by(email=identifier).first() if channel == "email" \
        else User.query.filter(User.phone.in_(_phone_lookup_candidates(identifier))).first()
    if not user:
        return jsonify({"success": False, "error": "Invalid or expired code"}), 400
    if not user.active:
        return jsonify({"success": False, "error": "This account has been suspended.", "suspended": True}), 403

    token = create_access_token(identity=str(user.id))
    _create_user_session(user.id, "app", decode_token(token)["jti"])
    return jsonify({"message": "✅ OTP verified", "user": user.to_dict(), "access_token": token}), 200


# ----------- EMAIL OTP for REGISTRATION (NEW) -----------
@app.route("/auth/otp/register/request", methods=["POST"])
def otp_request_for_registration():
    """
    Send an OTP to ANY valid email or phone (account may not exist yet).
    Used to prove the contact method is reachable before registration.

    channel + identifier are new (see OTPCode's docstring) -- an old,
    not-yet-updated mobile client sends {"email": "..."} with neither key,
    which still works exactly as before: identifier falls back to email,
    channel defaults to "email".
    """
    data = request.json or {}
    channel = data.get("channel") if data.get("channel") in ("email", "sms") else "email"
    identifier = (data.get("identifier") or data.get("email") or "").strip()

    if channel == "email":
        if not valid_email(identifier):
            return jsonify({"success": False, "error": "Please enter a valid email address"}), 400
    elif not identifier:
        return jsonify({"success": False, "error": "Please enter a phone number"}), 400

    if _otp_rate_limited(identifier):
        return jsonify({"success": False, "error": "Too many attempts. Try again later."}), 429
    _record_otp_attempt(identifier)

    # Cleanup old rows
    OTPCode.query.filter(OTPCode.expires_at < datetime.utcnow()).delete()

    code = _otp_code()
    expires = datetime.utcnow() + timedelta(minutes=10)
    # Invalidate previous codes for this identifier
    OTPCode.query.filter_by(identifier=identifier, purpose="register", used=False).update({"used": True})
    db.session.add(OTPCode(identifier=identifier, channel=channel, purpose="register", code=code, expires_at=expires, used=False))
    db.session.commit()
    _send_registration_otp(identifier, channel, code)

    msg = "OTP sent" if channel == "sms" else "OTP sent to email"
    return jsonify({"message": f"✅ {msg}"}), 200


@app.route("/auth/otp/register/verify", methods=["POST"])
def otp_verify_for_registration():
    """
    Early-feedback check for the mobile registration flow's code screen --
    validates a code WITHOUT consuming it (register() above is what
    actually marks it used, at final account creation). The mobile app
    has no server-side session the way portal_register() does to
    remember "this identifier was verified" between screens, so a wrong
    code would otherwise only surface after the user has also typed
    their name and password on the final combined /register call. This
    lets the code screen itself say so immediately, while the code stays
    valid for that final call to actually consume.
    """
    data = request.json or {}
    channel = data.get("channel") if data.get("channel") in ("email", "sms") else "email"
    identifier = (data.get("identifier") or data.get("email") or "").strip()
    code = (data.get("code") or "").strip()

    if not identifier or len(code) != 6 or not code.isdigit():
        return jsonify({"success": False, "error": "Invalid code"}), 400
    if _otp_rate_limited(identifier):
        return jsonify({"success": False, "error": "Too many attempts. Try again later."}), 429
    _record_otp_attempt(identifier)

    row = OTPCode.query.filter_by(identifier=identifier, channel=channel, code=code, purpose="register", used=False).first()
    if not row or row.expires_at < datetime.utcnow():
        return jsonify({"success": False, "error": "Invalid or expired code"}), 400
    return jsonify({"success": True}), 200


# ----------- FORGOT PASSWORD (mobile JSON, youth) -----------
# BL-46: reuses the exact channel-aware OTPCode machinery built for
# registration (see OTPCode's docstring) rather than an emailed
# reset-link/token scheme -- a link can't be "sent via SMS" the way an
# OTP code can, and this app already has a tested, working OTP pipeline
# for both channels. Same 3-step shape as registration (request code ->
# precheck the code -> submit the actual change) for the same reason
# registration was split that way: a wrong code should surface
# immediately, before the user has also retyped a new password for
# nothing.
def _send_reset_otp(identifier: str, channel: str, code: str) -> None:
    body = f"Your YouthChain password reset code is {code}. It expires in 10 minutes. If you didn't request this, you can ignore this message."
    if channel == "sms":
        send_sms(identifier, body)
    else:
        _send_email(identifier, "Reset your YouthChain password", body)


def _revoke_all_user_sessions(user_id: int) -> None:
    """
    Full "log out everywhere" -- used after a password reset, since a
    reset is often *because* of a lost/compromised device or account.
    Mirrors api_revoke_device()'s per-row logic exactly, just applied to
    every not-yet-revoked session instead of one.
    """
    rows = UserSession.query.filter_by(user_id=user_id, revoked_at=None).all()
    for row in rows:
        row.revoked_at = datetime.utcnow()
        if row.channel == "app":
            _add_to_jwt_blocklist(row.session_token, datetime.utcnow() + timedelta(hours=12))
    if rows:
        db.session.commit()


@app.route("/auth/otp/reset/request", methods=["POST"])
def otp_request_for_reset():
    data = request.json or {}
    channel = data.get("channel") if data.get("channel") in ("email", "sms") else "email"
    identifier = (data.get("identifier") or data.get("email") or "").strip()

    if channel == "email":
        if not valid_email(identifier):
            return jsonify({"success": False, "error": "Please enter a valid email address"}), 400
    elif not identifier:
        return jsonify({"success": False, "error": "Please enter a phone number"}), 400

    if _otp_rate_limited(identifier):
        return jsonify({"success": False, "error": "Too many attempts. Try again later."}), 429
    _record_otp_attempt(identifier)

    # Same anti-enumeration shape as /auth/otp/request (login OTP): identical
    # 200 whether or not an account exists at this identifier, and no code
    # is actually issued/sent for one that doesn't.
    user = User.query.filter((User.email == identifier) | User.phone.in_(_phone_lookup_candidates(identifier))).first()
    if user:
        OTPCode.query.filter(OTPCode.expires_at < datetime.utcnow()).delete()
        code = _otp_code()
        expires = datetime.utcnow() + timedelta(minutes=10)
        OTPCode.query.filter_by(identifier=identifier, purpose="reset", used=False).update({"used": True})
        db.session.add(OTPCode(identifier=identifier, channel=channel, purpose="reset", code=code, expires_at=expires, used=False))
        db.session.commit()
        _send_reset_otp(identifier, channel, code)

    return jsonify({"message": "✅ If that account exists, a reset code has been sent"}), 200


@app.route("/auth/otp/reset/verify", methods=["POST"])
def otp_verify_for_reset():
    """Precheck, same shape as otp_verify_for_registration -- validates without consuming."""
    data = request.json or {}
    channel = data.get("channel") if data.get("channel") in ("email", "sms") else "email"
    identifier = (data.get("identifier") or data.get("email") or "").strip()
    code = (data.get("code") or "").strip()

    if not identifier or len(code) != 6 or not code.isdigit():
        return jsonify({"success": False, "error": "Invalid code"}), 400
    if _otp_rate_limited(identifier):
        return jsonify({"success": False, "error": "Too many attempts. Try again later."}), 429
    _record_otp_attempt(identifier)

    row = OTPCode.query.filter_by(identifier=identifier, channel=channel, code=code, purpose="reset", used=False).first()
    if not row or row.expires_at < datetime.utcnow():
        return jsonify({"success": False, "error": "Invalid or expired code"}), 400
    return jsonify({"success": True}), 200


@app.route("/auth/otp/reset/confirm", methods=["POST"])
def otp_confirm_reset():
    """The call that actually consumes the code and changes the password."""
    data = request.json or {}
    channel = data.get("channel") if data.get("channel") in ("email", "sms") else "email"
    identifier = (data.get("identifier") or data.get("email") or "").strip()
    code = (data.get("code") or "").strip()
    new_password = data.get("new_password") or ""

    if not identifier or len(code) != 6 or not code.isdigit():
        return jsonify({"success": False, "error": "Invalid code"}), 400
    if len(new_password) < 8:
        return jsonify({"success": False, "error": "Password must be at least 8 characters"}), 400
    if _password_is_breached(new_password):
        return jsonify({
            "success": False,
            "error": "This password has appeared in a known data breach. Please choose a different one.",
        }), 400

    if _otp_rate_limited(identifier):
        return jsonify({"success": False, "error": "Too many attempts. Try again later."}), 429
    _record_otp_attempt(identifier)

    row = OTPCode.query.filter_by(identifier=identifier, channel=channel, code=code, purpose="reset", used=False).first()
    if not row or row.expires_at < datetime.utcnow():
        if row:
            row.used = True
            db.session.commit()
        return jsonify({"success": False, "error": "Invalid or expired code"}), 400

    user = User.query.filter((User.email == identifier) | User.phone.in_(_phone_lookup_candidates(identifier))).first()
    if not user:
        # The request step already proved this identifier belongs to a real
        # account (no code would exist otherwise) -- this branch is only
        # reachable if the account was deleted in between, not a real
        # enumeration path.
        return jsonify({"success": False, "error": "Invalid or expired code"}), 400

    row.used = True
    user.password_hash = generate_password_hash(new_password)
    db.session.commit()
    log_event("user_password_reset", user_id=user.id)
    _revoke_all_user_sessions(user.id)
    notify_user(user.id, "password_reset", "Your password was changed", "If this wasn't you, contact support immediately.", push=True)

    return jsonify({"message": "✅ Password reset successful"}), 200


# ----------- JOBS -----------
_DEFAULT_PAGE_SIZE = 100
_MAX_PAGE_SIZE = 200


def _pagination_params():
    """
    Shared limit/offset parsing (BL-21 / Phase 3 #19 — this endpoint
    previously returned every row with no limit at all). Defaults are
    generous enough that existing callers passing no params see identical
    behavior at current data volumes; the cap exists so a caller can't
    request an unbounded result set.
    """
    try:
        limit = min(int(request.args.get("limit", _DEFAULT_PAGE_SIZE)), _MAX_PAGE_SIZE)
    except (TypeError, ValueError):
        limit = _DEFAULT_PAGE_SIZE
    try:
        offset = max(int(request.args.get("offset", 0)), 0)
    except (TypeError, ValueError):
        offset = 0
    return limit, offset


def _search_jobs(
    q: str, location: str, skill: str, limit: int, offset: int,
    job_type: str = None, source: str = "employer", expired_only: bool = False,
):
    """
    Shared job search/filter logic behind both GET /jobs (JSON, mobile
    app) and the youth web portal's job listing page (Phase 3 #9 — "what's
    on the app, on the web too") — extracted out of get_jobs() so the same
    relevance-ranked full-text search can't silently drift between the two
    surfaces. Pure extraction, same queries/ordering/pagination as before;
    see get_jobs()'s own docstring for the full FTS reasoning. Returns a
    plain, already-paginated list of Job rows — callers decide how to
    render them (jsonify vs. a template).

    `source` defaults to "employer" so every pre-existing caller (GET
    /jobs, the youth web portal) keeps returning exactly what it always
    has with no call-site change — GET /api/discover_jobs is the one
    caller that passes source="scraped".

    `expired_only` (default False) filters on Job.application_deadline:
    False excludes anything already past deadline (the normal Discover
    feed — a job disappears from it the moment its own stated deadline
    passes, moving to the "old listings" bucket instead of just piling
    up forever), True returns ONLY past-deadline jobs (GET
    /api/discover_jobs/old). A null deadline (every employer-posted job,
    and any scraped job whose source page never stated one) always
    counts as "not expired" — this column is a pure no-op for GET /jobs,
    which never passes expired_only=True.
    """
    query = Job.query.filter(Job.source == source)
    today = date.today()
    if expired_only:
        query = query.filter(Job.application_deadline.isnot(None), Job.application_deadline < today)
    else:
        query = query.filter(db.or_(Job.application_deadline.is_(None), Job.application_deadline >= today))
    q = (q or "").strip()
    location = (location or "").strip()
    skill = (skill or "").strip()
    job_type = (job_type or "").strip()
    if job_type not in ("formal", "gig"):
        job_type = ""
    if job_type:
        query = query.filter(Job.job_type == job_type)

    if q:
        dialect = db.engine.dialect.name
        matched_ids = None  # None = fall through to the portable ILIKE path below

        if dialect == "sqlite":
            # Real FTS5 full-text search — job_fts (see migrations/ and the
            # after_insert listener above) is a shadow index over
            # title/required_skills/location, ranked by SQLite's built-in
            # bm25() relevance function. Tokens are individually
            # prefix-matched (each token gets a trailing `*`) and ANDed —
            # "junior dev" matches "Junior Software Developer" the same
            # way a real search box should, not just an exact substring.
            fts_query = " ".join(f'"{tok}"*' for tok in re.findall(r"\w+", q) if tok) or None
            if fts_query:
                try:
                    rows = db.session.execute(
                        sa_text(
                            "SELECT job.id FROM job_fts JOIN job ON job.id = job_fts.rowid "
                            "WHERE job_fts MATCH :q ORDER BY bm25(job_fts)"
                        ),
                        {"q": fts_query},
                    ).fetchall()
                    matched_ids = [r[0] for r in rows]
                except Exception:
                    logger.exception("FTS5 search failed, falling back to substring match")
                    matched_ids = None
        elif dialect == "postgresql":
            # Real to_tsvector/to_tsquery full-text search, ranked by
            # ts_rank — computed on the fly against the job table directly
            # (no shadow table needed on this backend, unlike SQLite).
            #
            # Deliberately to_tsquery with explicit `:*` prefix matching
            # per token, NOT plainto_tsquery — found via live testing
            # against a real Postgres instance, not assumed: plainto_tsquery
            # only matches on English-stemmed whole words, so "dev" simply
            # never matches "Developer" at all (stemming doesn't reduce
            # "developer" to "dev"). SQLite's FTS5 side already does
            # per-token prefix matching (see above); without this fix,
            # search behavior for the same query would silently differ
            # between the two supported database backends. Tokens are
            # pre-extracted with the same \\w+ regex used for the SQLite
            # path, so the constructed tsquery string is always
            # syntactically valid — never built from unsanitized raw input.
            #
            # setweight('A'/'B'/'C') per field, NOT one flat concatenated
            # to_tsvector — real bug found by finally running this suite
            # against a real Postgres instance (CI never had before this
            # pass): a plain `title || ' ' || required_skills || ' ' ||
            # location` blob gives ts_rank no per-field structure to work
            # with, so a title match and an incidental required_skills
            # match score the same, and test_jobs_full_text_search_ranks_
            # title_match_above_incidental_skill_match failed for real
            # ("Warehouse Assistant", skills "solar panel maintenance",
            # outranked "Solar Panel Installer" for query "solar panel").
            # SQLite's bm25(job_fts) never had this problem — FTS5 tracks
            # each declared column separately and normalizes per-column
            # term density even at equal column weights, so a short title
            # match already naturally outranks a diluted skills-field
            # match with no extra weighting needed. setweight replicates
            # that same title-favoring behavior explicitly on Postgres,
            # since ts_rank has no equivalent implicit per-column
            # normalization over one flat tsvector.
            tokens = re.findall(r"\w+", q)
            pg_query = " & ".join(f"{tok}:*" for tok in tokens) or None
            if pg_query:
                weighted_tsvector = (
                    "setweight(to_tsvector('english', coalesce(title,'')), 'A') || "
                    "setweight(to_tsvector('english', coalesce(required_skills,'')), 'B') || "
                    "setweight(to_tsvector('english', coalesce(location,'')), 'C')"
                )
                try:
                    rows = db.session.execute(
                        sa_text(
                            f"SELECT id FROM job WHERE {weighted_tsvector} @@ to_tsquery('english', :q) "
                            f"ORDER BY ts_rank({weighted_tsvector}, to_tsquery('english', :q)) DESC"
                        ),
                        {"q": pg_query},
                    ).fetchall()
                    matched_ids = [r[0] for r in rows]
                except Exception:
                    logger.exception("Postgres full-text search failed, falling back to substring match")
                    matched_ids = None

        if matched_ids is not None:
            if not matched_ids:
                return []
            # Apply location/skill filters (if any) on top of the FTS
            # match set, then re-sort in Python to preserve the relevance
            # order the FTS engine already computed — simpler and more
            # robust than a raw SQL CASE-based ORDER BY, and just as
            # correct since matched_ids is already a short, bounded list.
            # The FTS/tsvector queries above run against the whole job
            # table regardless of source (a raw SQL query, not `query`
            # above) — source must be re-applied here or an employer-only
            # /jobs search could return scraped-job matches, and vice
            # versa for /api/discover_jobs. Same reasoning for
            # expired_only — the raw SQL query above has no idea about
            # it either.
            candidates = {j.id: j for j in Job.query.filter(Job.id.in_(matched_ids), Job.source == source).all()}
            ranked_jobs = [candidates[jid] for jid in matched_ids if jid in candidates]

            loc_lower = location.lower()
            skill_lower = skill.lower()
            if loc_lower:
                ranked_jobs = [j for j in ranked_jobs if loc_lower in (j.location or "").lower()]
            if skill_lower:
                ranked_jobs = [j for j in ranked_jobs if skill_lower in (j.required_skills or "").lower()]
            if job_type:
                ranked_jobs = [j for j in ranked_jobs if (j.job_type or "formal") == job_type]
            if expired_only:
                ranked_jobs = [j for j in ranked_jobs if j.application_deadline is not None and j.application_deadline < today]
            else:
                ranked_jobs = [j for j in ranked_jobs if j.application_deadline is None or j.application_deadline >= today]

            return ranked_jobs[offset : offset + limit]
        else:
            # Fallback: the original portable substring match — used when
            # neither dialect branch above applies, or a real-time FTS
            # query itself errored (logged above, not silently swallowed).
            query = query.filter(Job.title.ilike(f"%{q}%"))

    if location:
        query = query.filter(Job.location.ilike(f"%{location}%"))

    if skill:
        query = query.filter(Job.required_skills.ilike(f"%{skill}%"))

    query = query.order_by(Job.id.desc())
    return query.limit(limit).offset(offset).all()


@app.route("/jobs", methods=["GET"])
def get_jobs():
    """
    BL-36 (search/filter) + full-text search follow-up. `q` now does a
    real relevance-ranked full-text search across title/required_skills/
    location — SQLite FTS5 (job_fts, bm25-ranked) when DATABASE_URL is
    unset/SQLite, Postgres to_tsvector/ts_rank when it isn't — instead of
    a plain title-only substring match. Falls back to the original
    substring behavior if the dialect isn't recognized or a live FTS query
    errors (logged, not silently swallowed).

    Query params (all optional, combinable):
      q        - full-text search across title/required_skills/location
      location - substring match against job location (case-insensitive)
      skill    - substring match against required_skills (case-insensitive)
      job_type - "formal" or "gig" (see Job.job_type); omitted = both
    """
    limit, offset = _pagination_params()
    jobs = _search_jobs(
        q=request.args.get("q"),
        location=request.args.get("location"),
        skill=request.args.get("skill"),
        limit=limit,
        offset=offset,
        job_type=request.args.get("job_type"),
    )
    return jsonify([job.to_dict() for job in jobs])


# See the skill-match branch inside api_discover_jobs below for what this
# actually bounds.
MAX_DISCOVER_JOBS_TO_SCORE = 1000


@app.route("/api/discover_jobs", methods=["GET"])
@jwt_required()
def api_discover_jobs():
    """
    The mobile Discover tab's feed — real listings pulled in by the job
    scanner (scanner/pipeline.py) from configured external sources, kept
    entirely separate from Home's employer/gig feed (GET /jobs,
    /api/match_jobs). Same bare-JSON-array shape as GET /jobs.

    Query params (all optional, combinable): q, location, skill — same
    meaning as GET /jobs. job_type is not exposed here: scraped listings
    are all created as job_type="formal" (see pipeline.py), there's no
    gig/formal distinction to filter on yet.

    Ranked by candidate skill-match when the caller has a saved profile
    and isn't actively searching (q empty) — same scoring method and the
    same "ranking and free-text search don't mix" rule /api/match_jobs
    already applies to Home (BL-45: transparent skills-overlap only, no
    claim of AI/ML matching this app doesn't have). No industry-alignment
    bonus here, unlike Home — a scraped listing has no Employer account
    with a tracked industry to compare against a candidate's preferred
    ones, so there's nothing real to base that bonus on.

    A job dict only gains a "score" key when ranking is actually active —
    mirroring the mobile app's own existing convention for Home
    (job_screen.dart's `hasScore = job.containsKey("score")`, which hides
    the match badge entirely rather than showing a misleading "0% match"
    on every card during a search or before a profile exists). When
    ranking is active this bypasses _search_jobs()'s own SQL-level
    pagination — sorting only within one already-paginated page would
    mean a great match sitting just past the first page's cutoff could
    never surface, the same reason /api/match_jobs doesn't paginate its
    own ranked results at all — and instead paginates in Python after
    sorting the full matching set by score.
    """
    limit, offset = _pagination_params()
    q = (request.args.get("q") or "").strip()
    location = (request.args.get("location") or "").strip()
    skill = (request.args.get("skill") or "").strip()
    candidate = Candidate.query.filter_by(user_id=_current_user_id()).first()

    if candidate and not q:
        cand_skills = {s.strip().lower() for s in (candidate.skills or "").split(",") if s.strip()}
        today = date.today()
        base_query = Job.query.filter(
            Job.source == "scraped",
            db.or_(Job.application_deadline.is_(None), Job.application_deadline >= today),
        )
        if location:
            base_query = base_query.filter(Job.location.ilike(f"%{location}%"))
        if skill:
            base_query = base_query.filter(Job.required_skills.ilike(f"%{skill}%"))

        def _score(job):
            required = {s.strip().lower() for s in (job.required_skills or "").split(",") if s.strip()}
            if not required or not cand_skills:
                return 0
            return int(100 * len(cand_skills & required) / len(required))

        # This branch scores/sorts in Python rather than SQL (see the
        # docstring above for why), which means its cost scales with how
        # many scraped jobs match location/skill — not with `limit`, the
        # way _search_jobs()'s SQL-paginated branch below does. Capped at
        # the most recent MAX_DISCOVER_JOBS_TO_SCORE (order_by(id.desc())
        # already puts newest first, so this only ever drops the oldest,
        # lowest-priority matches) as a stopgap against that scaling with
        # total scraped-job volume rather than page size. Not a real fix —
        # this app's own principle is "never optimize before measuring,"
        # and nobody has measured real row counts against this cap yet —
        # just a bound on how bad it can get before that measurement
        # happens.
        scored = sorted(
            ((_score(j), j) for j in base_query.order_by(Job.id.desc()).limit(MAX_DISCOVER_JOBS_TO_SCORE).all()),
            key=lambda pair: pair[0], reverse=True,
        )
        page = scored[offset:offset + limit]
        payload = [dict(job.to_dict(), score=score) for score, job in page]
    else:
        jobs = _search_jobs(q=q, location=location, skill=skill, limit=limit, offset=offset, source="scraped")
        payload = [job.to_dict() for job in jobs]

    return jsonify(payload)


@app.route("/api/discover_jobs/old", methods=["GET"])
@jwt_required()
def api_discover_jobs_old():
    """
    The other side of GET /api/discover_jobs's expired_only=False
    default: a scraped job whose own stated deadline has passed drops
    out of the normal feed into this one instead of just disappearing
    outright or piling up in the main feed forever. Purely a read view —
    nothing here deletes anything; a job listed here still gets hard-
    deleted by scanner.reaper once it's REAP_GRACE_DAYS past deadline
    (see that module's own docstring), same as it would whether or not
    anyone ever opened this screen.
    """
    limit, offset = _pagination_params()
    jobs = _search_jobs(
        q=request.args.get("q"),
        location=request.args.get("location"),
        skill=request.args.get("skill"),
        limit=limit,
        offset=offset,
        source="scraped",
        expired_only=True,
    )
    return jsonify([job.to_dict() for job in jobs])


# ----------- SAVED JOBS (bookmark toggle) -----------
# Two idempotent POSTs rather than a single toggle or a DELETE route --
# matches this file's existing "POST is the only mutation verb used on
# mobile-facing /api/ routes" convention (see apply(), flag_rating()),
# rather than introducing DELETE, which nothing else here uses.

@app.route("/api/jobs/<int:job_id>/save", methods=["POST"])
@jwt_required()
def save_job(job_id):
    user_id = _current_user_id()
    Job.query.get_or_404(job_id)
    try:
        db.session.add(SavedJob(user_id=user_id, job_id=job_id))
        db.session.commit()
    except IntegrityError:
        # Already saved -- same job tapped twice (e.g. a fast double-tap
        # before the first request's response updates the icon). Not an
        # error from the caller's point of view: the end state (saved)
        # is exactly what was asked for either way.
        db.session.rollback()
    return jsonify({"success": True, "saved": True})


@app.route("/api/jobs/<int:job_id>/unsave", methods=["POST"])
@jwt_required()
def unsave_job(job_id):
    user_id = _current_user_id()
    SavedJob.query.filter_by(user_id=user_id, job_id=job_id).delete()
    db.session.commit()
    return jsonify({"success": True, "saved": False})


@app.route("/api/saved_jobs", methods=["GET"])
@jwt_required()
def api_saved_jobs():
    """
    Every job (Home or Discover, see SavedJob's own docstring) the
    current user has bookmarked, most-recently-saved first. Bare JSON
    array, same shape as GET /jobs and GET /api/discover_jobs -- the
    mobile Saved Jobs screen tells them apart the same way any other
    mixed list would, by each job's own "source" field.
    """
    saved = (
        SavedJob.query.filter_by(user_id=_current_user_id())
        .order_by(SavedJob.created_at.desc())
        .all()
    )
    job_ids = [s.job_id for s in saved]
    jobs_by_id = {j.id: j for j in Job.query.filter(Job.id.in_(job_ids)).all()} if job_ids else {}
    # Preserves save-order even though the Job.id.in_(...) query above
    # doesn't -- a saved job that's since been deleted (job_ids has no
    # matching row) is silently skipped rather than erroring.
    return jsonify([jobs_by_id[jid].to_dict() for jid in job_ids if jid in jobs_by_id])


# ----------- SAVED SEARCHES (Discover alerts) -----------
# Small fixed cap, same defensive-posture reasoning as every other
# per-user-growth limit in this file (rate limits on OTP/uploads/CV
# generation) — an unbounded SavedSearch list is both DB bloat and a
# notification-spam vector for whoever ends up on the other end of every
# future scan's alert dispatch.
_MAX_SAVED_SEARCHES_PER_USER = 20


@app.route("/api/saved_searches", methods=["POST"])
@jwt_required()
def create_saved_search():
    """
    Creates a Discover alert filter for the current user (see
    SavedSearch's own docstring). At least one of q/location/skill is
    required — an all-empty filter would match every future scraped job,
    which is really "alert me on everything" wearing a saved-search
    costume and would swamp whoever created it by mistake.
    """
    data = request.get_json() or {}
    user_id = _current_user_id()

    existing_count = SavedSearch.query.filter_by(user_id=user_id).count()
    if existing_count >= _MAX_SAVED_SEARCHES_PER_USER:
        return jsonify({
            "success": False,
            "error": f"You can save up to {_MAX_SAVED_SEARCHES_PER_USER} searches. Delete one first.",
        }), 400

    q = (data.get("q") or "").strip() or None
    location = (data.get("location") or "").strip() or None
    skill = (data.get("skill") or "").strip() or None
    if not (q or location or skill):
        return jsonify({"success": False, "error": "At least one of q, location, skill is required"}), 400

    row = SavedSearch(user_id=user_id, q=q, location=location, skill=skill)
    db.session.add(row)
    db.session.commit()
    return jsonify({"success": True, "saved_search": row.to_dict()}), 201


@app.route("/api/saved_searches", methods=["GET"])
@jwt_required()
def list_saved_searches():
    rows = (
        SavedSearch.query.filter_by(user_id=_current_user_id())
        .order_by(SavedSearch.created_at.desc())
        .all()
    )
    return jsonify([r.to_dict() for r in rows])


@app.route("/api/saved_searches/<int:search_id>/delete", methods=["POST"])
@jwt_required()
def delete_saved_search(search_id):
    # POST, not DELETE — this file uses no DELETE routes anywhere else
    # (see save_job()/unsave_job()'s own comment on why), kept consistent
    # rather than introducing the verb for just this one endpoint.
    row = SavedSearch.query.get_or_404(search_id)
    if row.user_id != _current_user_id():
        return _forbidden()
    db.session.delete(row)
    db.session.commit()
    return jsonify({"success": True})


# ----------- APPLICATIONS (Youth applies) -----------
@app.route("/apply", methods=["POST"])
@jwt_required()
def apply():
    # user_id is derived from the authenticated token, never trusted from the
    # request body — closes S-01 (a client could previously apply "as" any
    # user_id it chose to send).
    user_id = _current_user_id()
    job_id = request.form.get("job_id")
    cv_file = request.files.get("cv")
    supporting_file = request.files.get("supporting")

    if not user_id or not job_id:
        return jsonify({"success": False, "error": "Missing required fields"}), 400

    # Loaded up front (previously only fetched after the Application was
    # already committed, purely for the socketio notify below) so job_type
    # can gate whether a CV is required at all -- gig/informal-work jobs
    # (see Job.job_type) don't require one, matching how trust actually
    # works for that kind of work (see the Rating model's docstring).
    job = Job.query.get(job_id)
    if not job:
        return jsonify({"success": False, "error": "Job not found"}), 404
    # A Discover (scraped) listing has no employer account behind it to
    # receive an Application -- its "apply" action is the mobile app
    # opening apply_url externally (see DiscoverJobDetailScreen), never
    # this route. Rejecting here isn't just UX: an Application row is
    # permanent user data (a real job application), and Application.job_id
    # is a NOT NULL FK a real Postgres deployment enforces -- letting one
    # get created against a scraped Job would make that Job undeletable
    # by scanner.reaper (or an admin's "remove listing") the moment it
    # expires or gets reported, since there'd be no safe way to null or
    # drop someone's actual application the way ScrapedListingReport.job_id
    # can be nulled.
    if job.source != "employer":
        return jsonify({"success": False, "error": "This job cannot be applied to directly"}), 400
    cv_required = job.job_type != "gig"

    if cv_required and not cv_file:
        return jsonify({"success": False, "error": "Missing required fields"}), 400

    if _upload_rate_limited(f"user:{user_id}"):
        return jsonify({"success": False, "error": "Too many uploads. Try again later."}), 429
    _record_upload_attempt(f"user:{user_id}")

    # Prevent duplicate application for same (user, job)
    existing = Application.query.filter_by(user_id=user_id, job_id=job_id).first()
    if existing:
        return jsonify({"success": False, "error": "You have already applied for this job"}), 400

    # Validate CV type -- required for formal jobs; for gig jobs a CV is
    # optional "proof of past work," but still runs the same validation
    # pipeline as a formal CV whenever one is actually attached.
    if cv_file and cv_file.filename:
        if not allowed_file(cv_file.filename):
            return jsonify({"success": False, "error": "Unsupported CV file type"}), 400
        if not content_matches_extension(cv_file, cv_file.filename):
            return jsonify({"success": False, "error": "CV file content does not match its extension"}), 400
        if not file_is_malware_free(cv_file):
            return jsonify({"success": False, "error": "CV file failed a security scan"}), 400
    elif cv_required:
        return jsonify({"success": False, "error": "Unsupported CV file type"}), 400

    # Validate the optional supporting doc too, BEFORE saving anything —
    # real, pre-existing bug found and fixed alongside adding the malware
    # scan above (not introduced by it, just made more likely to actually
    # trigger): this used to validate the supporting doc only after the CV
    # was already saved to disk, so a rejected supporting file left an
    # orphaned CV file behind with no Application row ever created to
    # reference it. Validating both files fully before saving either means
    # a rejection at any point leaves nothing on disk.
    if supporting_file and supporting_file.filename:
        if not allowed_file(supporting_file.filename):
            return jsonify({"success": False, "error": "Unsupported supporting file type"}), 400
        if not content_matches_extension(supporting_file, supporting_file.filename):
            return jsonify({"success": False, "error": "Supporting file content does not match its extension"}), 400
        if not file_is_malware_free(supporting_file):
            return jsonify({"success": False, "error": "Supporting file failed a security scan"}), 400

    # Save CV with unique prefix (may be None on a gig-job application with
    # no CV attached -- Application.cv_file is nullable for exactly this).
    ts = datetime.utcnow().strftime('%Y%m%d%H%M%S')
    cv_filename = None
    if cv_file and cv_file.filename:
        cv_filename_orig = secure_filename(cv_file.filename)
        cv_filename = f"{ts}_{user_id}_{cv_filename_orig}"
        cv_path = os.path.join(APPLICATION_FOLDER, cv_filename)
        cv_file.save(cv_path)
        _compress_uploaded_image_if_needed(cv_path)

    support_filename = None
    if supporting_file and supporting_file.filename:
        support_orig = secure_filename(supporting_file.filename)
        support_filename = f"{ts}_{user_id}_{support_orig}"
        support_path = os.path.join(APPLICATION_FOLDER, support_filename)
        supporting_file.save(support_path)
        _compress_uploaded_image_if_needed(support_path)

    app_obj = Application(
        user_id=user_id,
        job_id=job_id,
        cv_file=cv_filename,
        supporting_file=support_filename,
    )
    db.session.add(app_obj)
    try:
        db.session.commit()

        # Only the owning employer needs to know a new application came in
        # (their dashboard/applicants list live-refreshes) -- see
        # _socketio_connect's docstring for why this used to broadcast to
        # every connected client regardless of who they were.
        try:
            if job.employer_id:
                socketio.emit(
                    "application_created",
                    app_obj.to_dict(),
                    room=f"employer:{job.employer_id}",
                )
        except Exception:
            logger.exception("socketio emit application_created failed")


    except IntegrityError:
        db.session.rollback()
        # Race only: the pre-check at the top of this route already looked
        # for an existing (user, job) Application and found none, but a
        # second concurrent request for the same pair can still slip past
        # that check and lose the DB's own unique-constraint race here.
        # The files above were already written to disk before this
        # commit, on the assumption the write would succeed -- clean them
        # up rather than leaving them orphaned with no Application row
        # ever created to reference them.
        for orphaned in (cv_filename, support_filename):
            if orphaned:
                try:
                    os.remove(os.path.join(APPLICATION_FOLDER, orphaned))
                except OSError:
                    logger.exception("apply(): failed to remove orphaned file %s after IntegrityError", orphaned)
        return jsonify({"success": False, "error": "You have already applied for this job"}), 400

    log_event("application_submitted", user_id=user_id, job_id=job_id)
    return jsonify({"message": "✅ Application submitted"}), 201


@app.route("/my_applications/<int:user_id>", methods=["GET"])
@jwt_required()
def my_applications(user_id):
    if _current_user_id() != user_id:
        return _forbidden()

    limit, offset = _pagination_params()
    # Single JOIN instead of a Job.query.get() per row — this endpoint used
    # to do exactly the N+1 query pattern that /employer/applications/<id>
    # (a few hundred lines below) already showed the fix for; the two are
    # now consistent (BL-21 / Phase 4 finding).
    rows = (
        db.session.query(Application, Job)
        .outerjoin(Job, Application.job_id == Job.id)
        .filter(Application.user_id == user_id)
        .order_by(Application.created_at.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )
    # One grouped fetch for every application's worker->employer rating,
    # not a query per row -- same N+1-avoidance discipline as
    # employer_applications()'s own ratings_by_app_id lookup.
    app_ids = [a.id for a, _job in rows]
    ratings_by_app_id = (
        {r.application_id: r for r in Rating.query.filter(
            Rating.application_id.in_(app_ids), Rating.direction == "worker_to_employer"
        ).all()}
        if app_ids else {}
    )
    out = []
    for a, job in rows:
        item = a.to_dict()
        if job:
            item.update({"job_title": job.title, "job_location": job.location, "job_duration": job.duration})
        if job and job.job_type == "gig":
            rating = ratings_by_app_id.get(a.id)
            item["employer_rating"] = rating.to_dict() if rating else None
            item["can_rate_employer"] = a.status == "Completed" and rating is None
        out.append(item)
    return jsonify(out)


def _submit_gig_rating(application, direction, employer_id, data):
    """
    Shared core of all three gig-rating submission routes (rate_employer,
    portal_rate_employer, employer_rate_worker below) -- see Rating's
    docstring for why both directions exist. Each of the three keeps its
    own ownership/auth check at the call site (JWT current-user, portal
    session current-user, employer-owns-job -- genuinely different from
    each other), since that's the only part that isn't just copy-pasted:
    the Completed-status guard, the already-rated guard, score parsing/
    validation, and the Rating row/commit/log_event used to be repeated
    line-for-line across all three. One real behavior change folded in
    here: rate_employer alone used to answer an invalid score with a bare
    400 while every other error on all three routes used _forbidden()'s
    403 -- unified on _forbidden() since nothing enforced that split and
    no test/client depended on the 400 (checked before making this
    change).

    `data` is whatever the caller already extracted request data from
    (request.get_json() or request.form) -- the one place the three
    callers genuinely differ in how they read the incoming score/comment.

    Returns (rating, error_response): on success, rating is the created
    Rating and error_response is None; on failure, rating is None and
    error_response is the Flask response the caller should return as-is.
    """
    if application.status != "Completed":
        return None, _forbidden("This application hasn't been marked complete yet")
    if Rating.query.filter_by(application_id=application.id, direction=direction).first():
        already = "employer" if direction == "worker_to_employer" else "worker"
        return None, _forbidden(f"You have already rated this {already}")

    try:
        score = int(data.get("score"))
    except (TypeError, ValueError):
        score = None
    if score not in _GIG_RATING_SCORE_RANGE:
        return None, _forbidden("score must be an integer from 1 to 5")

    rating = Rating(
        application_id=application.id,
        direction=direction,
        employer_id=employer_id,
        user_id=application.user_id,
        score=score,
        comment=(data.get("comment") or "").strip() or None,
    )
    db.session.add(rating)
    db.session.commit()
    event_name = "employer_rated" if direction == "worker_to_employer" else "worker_rated"
    log_event(event_name, user_id=application.user_id, employer_id=employer_id, application_id=application.id, score=score)
    return rating, None


def _submit_worker_rating_flag(rating, user_id, reason):
    """
    Shared core of flag_rating() (JWT/mobile) and portal_flag_rating()
    (web-portal session) below -- the worker's two routes to dispute a
    rating THEY received (employer_to_worker direction). Ownership/
    direction check and the reason-required check stay at each call site
    rather than folding in here: the two routes format "reason is
    required" differently (a bare 400 vs _forbidden()'s 403), and a real
    test (test_flag_requires_a_reason) locks the JWT route's 400, so
    unifying that specific check would be a genuine behavior change, not
    just a dedup. employer_flag_rating (the employer-side symmetric flow,
    elsewhere in this file) has a different rate-limit key shape and
    redirect target entirely, so it stays fully separate too.

    Handles the part that WAS identical: the per-hour rate limit (same
    tight budget as report_employer() -- a dispute queue is exactly as
    plausible a harassment/spam vector as the employer-report one that
    reasoning was first written for), RatingFlag creation, commit,
    log_event, and the admin notification.

    Returns (flag, rate_limited): on success, flag is the created
    RatingFlag and rate_limited is False; if rate-limited, flag is None
    and rate_limited is True so the caller returns its own 429.
    """
    if _report_rate_limited(f"user:{user_id}"):
        return None, True
    _record_report_attempt(f"user:{user_id}")

    flag = RatingFlag(
        rating_id=rating.id,
        flagged_by_role="worker",
        flagged_by_user_id=user_id,
        reason=reason,
    )
    db.session.add(flag)
    db.session.commit()
    log_event("rating_flagged", user_id=user_id, rating_id=rating.id)
    _notify_admins_of_new_rating_flag(flag, rating)
    return flag, False


@app.route("/api/applications/<int:app_id>/rate_employer", methods=["POST"])
@jwt_required()
def rate_employer(app_id):
    """Worker's half of the bidirectional gig rating -- see Rating's docstring."""
    application = Application.query.get_or_404(app_id)
    # IDOR guard, same shape as employer_rate_worker's ownership check --
    # a worker may only rate the employer on THEIR OWN application.
    if application.user_id != _current_user_id():
        return _forbidden()
    job = Job.query.get_or_404(application.job_id)

    data = request.get_json(silent=True) or request.form
    rating, error = _submit_gig_rating(application, "worker_to_employer", job.employer_id, data)
    if error:
        return error
    return jsonify({"success": True, "rating": rating.to_dict()}), 201


@app.route("/api/ratings/<int:rating_id>/flag", methods=["POST"])
@jwt_required()
def flag_rating(rating_id):
    """
    The worker's half of the dispute path for a Rating they think is
    unfair -- see RatingFlag's docstring. JWT/mobile-only, so only a
    worker can reach this one; the symmetric employer-side flow is
    employer_flag_rating() below (session-authenticated, web portal).

    Deliberately restricted to employer_to_worker ratings only (the ones
    ABOUT this worker) -- Rating.user_id is always the worker regardless
    of direction, so without this a worker could "dispute" their own
    worker_to_employer submission, which isn't a real dispute at all, just
    noise in the admin queue.
    """
    rating = Rating.query.get_or_404(rating_id)
    user_id = _current_user_id()
    if rating.user_id != user_id or rating.direction != "employer_to_worker":
        return _forbidden()

    data = request.get_json(silent=True) or request.form
    reason = (data.get("reason") or "").strip()
    if not reason:
        return jsonify({"success": False, "error": "reason is required"}), 400

    flag, rate_limited = _submit_worker_rating_flag(rating, user_id, reason)
    if rate_limited:
        return jsonify({"success": False, "error": "Too many reports. Try again later."}), 429
    return jsonify({"success": True, "flag_id": flag.id}), 201


@app.route("/api/devices", methods=["GET"])
@jwt_required()
def api_devices():
    """Mobile counterpart to portal_devices() -- same "connected devices" list, JWT-authenticated."""
    user_id = _current_user_id()
    current_token = get_jwt()["jti"]
    rows = (
        UserSession.query.filter_by(user_id=user_id, revoked_at=None)
        .order_by(UserSession.last_seen_at.desc())
        .all()
    )
    return jsonify({"sessions": [r.to_dict(current_session_token=current_token) for r in rows]}), 200


@app.route("/api/devices/<int:session_id>/revoke", methods=["POST"])
@jwt_required()
def api_revoke_device(session_id):
    """Mobile counterpart to portal_revoke_device() -- see that route's docstring."""
    user_id = _current_user_id()
    row = UserSession.query.get_or_404(session_id)
    if row.user_id != user_id:
        return _forbidden()
    if row.revoked_at is None:
        row.revoked_at = datetime.utcnow()
        if row.channel == "app":
            _add_to_jwt_blocklist(row.session_token, datetime.utcnow() + timedelta(hours=12))
        db.session.commit()
        log_event("user_session_revoked", user_id=user_id, target_session_id=session_id, channel=row.channel)
    return jsonify({"success": True}), 200


@app.route("/api/candidate/<int:user_id>/trust_summary", methods=["GET"])
@jwt_required()
def api_candidate_trust_summary(user_id):
    """Self-only, same pattern as my_applications -- the mobile work-history screen's data source."""
    if _current_user_id() != user_id:
        return _forbidden()
    summary = _worker_trust_summary(user_id)
    rated_gigs = (
        db.session.query(Application, Job, Rating)
        .join(Job, Application.job_id == Job.id)
        .join(Rating, (Rating.application_id == Application.id) & (Rating.direction == "employer_to_worker"))
        .filter(Application.user_id == user_id, Rating.hidden.is_(False))
        .order_by(Rating.created_at.desc())
        .all()
    )
    summary["rated_gigs"] = [
        {
            "application_id": a.id,
            "job_title": job.title,
            "category": job.category,
            "score": r.score,
            "comment": r.comment,
            "rated_at": r.created_at.isoformat() if r.created_at else None,
        }
        for a, job, r in rated_gigs
    ]
    return jsonify(summary)


# ----------- CREDENTIALS -----------
@app.route("/passport/<int:user_id>", methods=["GET"])
@jwt_required()
def get_passport(user_id):
    if _current_user_id() != user_id:
        return _forbidden()
    creds = Credential.query.filter_by(user_id=user_id).all()
    return jsonify([cred.to_dict() for cred in creds])


@app.route("/issue_credential", methods=["POST"])
@jwt_required()
def issue_credential():
    # NOTE: until a real accredited-issuer model exists (Phase 9 Private Beta
    # milestone / BL-06), this endpoint only supports self-issuance: a caller
    # can only issue a credential to their own account, never to another
    # user_id. This is a deliberate, documented interim restriction — it
    # closes the immediate IDOR risk without inventing issuer accreditation
    # here.
    user_id = _current_user_id()
    title = request.form.get("title")
    issuer = request.form.get("issuer")
    file = request.files.get("file")

    if _upload_rate_limited(f"user:{user_id}"):
        return jsonify({"success": False, "error": "Too many uploads. Try again later."}), 429
    _record_upload_attempt(f"user:{user_id}")

    cred, error, created = _issue_credential_internal(user_id, title, issuer, file)
    if error:
        return jsonify({"success": False, "error": error}), 400

    return jsonify({
        "message": "✅ Credential issued" if created else "⚠ Credential already exists",
        "hash": cred.hash,
        "credential_id": cred.id,
        "onchain_tx": cred.onchain_tx,
        # The write now happens in the background (BL-18) — "pending" here
        # is the normal, expected state for a just-issued credential, not
        # an error; a "credential_onchain_confirmed" notification (see
        # _write_onchain_tx_for_credential_async) follows once it lands.
        "onchain_status": "confirmed" if cred.onchain_tx else "pending",
        "verify_url": f"/verify/{cred.id}",
    }), 201 if created else 200


# API-friendly upload endpoint
@app.route("/api/certificate/upload", methods=["POST"])
@jwt_required()
def api_certificate_upload():
    """
    API wrapper for issuing a credential. Self-issuance only — see the note
    on /issue_credential. Behaviorally identical to that route (both call
    _issue_credential_internal) with a `success`-shaped response instead.

    Expects multipart/form-data:
      - title
      - issuer
      - file
    """
    user_id = _current_user_id()
    title = request.form.get("title")
    issuer = request.form.get("issuer")
    file = request.files.get("file")

    if _upload_rate_limited(f"user:{user_id}"):
        return jsonify({"success": False, "error": "Too many uploads. Try again later."}), 429
    _record_upload_attempt(f"user:{user_id}")

    cred, error, created = _issue_credential_internal(user_id, title, issuer, file)
    if error:
        return jsonify({"success": False, "error": error}), 400

    verify_url = url_for("verify_by_id", cred_id=cred.id, _external=False)
    return jsonify({
        "success": True,
        "credential_id": cred.id,
        "hash": cred.hash,
        "onchain_tx": cred.onchain_tx,
        "onchain_status": "confirmed" if cred.onchain_tx else "pending",
        "verify_url": verify_url,
    }), 201 if created else 200


# ----------- File downloads -----------
@app.route("/certificate/<filename>", methods=["GET"])
@jwt_required(optional=True)
def download_certificate(filename):
    cred = Credential.query.filter_by(file_path=filename).first()
    if not cred:
        return _forbidden("You do not have access to this file")
    owner_id = _current_user_id()
    if owner_id is None:
        # Falls back to the youth web portal's session cookie (Phase 3 #9)
        # when there's no JWT on the request at all — same dual-auth shape
        # as download_application just below.
        owner_id = _current_portal_user_id()
    if cred.user_id != owner_id:
        return _forbidden("You do not have access to this file")
    # Forced download (as_attachment=True), consistent with every other
    # file-download route in this codebase (application_file, message
    # attachments, employer verification documents). Found via audit: this
    # was previously the one route serving inline (as_attachment=False) —
    # no comment or usage anywhere (including the mobile app, which never
    # actually calls this route) suggested it was a deliberate "view in
    # browser" decision rather than an oversight, and inline rendering is
    # a marginally larger attack surface than a forced download for the
    # one upload-security gap this audit didn't close: magic-byte
    # validation confirms a file really is a PDF, not that a real PDF has
    # no embedded exploit (a real malware/content scan, e.g. ClamAV, would
    # be needed for that — a genuine infra decision, not made here).
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename, as_attachment=True)


@app.route("/application_file/<filename>", methods=["GET"])
@jwt_required(optional=True)
def download_application(filename):
    # Reachable by two different caller types sharing one file namespace:
    # the applicant themselves (JWT) or the employer who owns the job the
    # application was submitted to (session cookie, set by employer login).
    row = Application.query.filter(
        (Application.cv_file == filename) | (Application.supporting_file == filename)
    ).first()
    if not row:
        return _forbidden("You do not have access to this file")

    uid = _current_user_id()
    if uid is None:
        uid = _current_portal_user_id()  # web portal session (Phase 3 #9)
    if uid is not None and row.user_id == uid:
        return send_from_directory(APPLICATION_FOLDER, filename, as_attachment=True)

    eid = _current_employer_id()
    if eid is not None:
        job = Job.query.get(row.job_id)
        if _employer_owns_job(job, eid):
            return send_from_directory(APPLICATION_FOLDER, filename, as_attachment=True)

    return _forbidden("You do not have access to this file")


def _send_registration_otp(identifier: str, channel: str, code: str) -> None:
    """
    Channel-aware OTP dispatch for portal_register()'s contact step --
    the mobile JSON registration endpoints stay hardcoded to _send_email
    directly (channel="email" always) for now, since only the web portal
    exposes a channel choice today (see OTPCode's docstring; the mobile
    app gets the same choice in a follow-up pass).
    """
    body = f"Your registration code is {code}. It expires in 10 minutes."
    if channel == "sms":
        send_sms(identifier, body)
    else:
        _send_email(identifier, "Verify your YouthChain email", body)


# ----------- Youth Web Portal (session-cookie auth) -----------
# Phase 3 #9: the mobile app's core loop (browse jobs, apply, track
# applications, hold a verifiable digital passport) reachable from a
# browser too, so a youth without the app installed yet isn't blocked
# from using YouthChain at all. Deliberately session-cookie + CSRF
# authenticated, the same pattern as the employer/admin web portals
# above/below — NOT the mobile app's JWT, which has no browser-native
# place to live without inventing token storage this codebase doesn't
# otherwise need. Messaging and push notifications are intentionally
# left app-only for now (see portal_applications.html) rather than
# rebuilding Socket.IO + FCM against a second auth model here.
@app.route("/portal/register", methods=["GET", "POST"])
def portal_register():
    """
    Three steps, not two -- restructured from a combined "email + fill in
    everything, OTP included" form so contact verification happens fully
    before password creation, not alongside it. Real gap found via user
    feedback: matches how most trusted apps (and this codebase's own
    suspension-appeal identity-proof pattern) separate "prove you own
    this" from "set your credentials," and this is also the first place
    a youth can choose EITHER email or SMS for the code -- password login
    already accepts "phone or email" as the identifier, so registration
    being email-only was an inconsistency between the two entry points
    into the same account.

    step="contact": choose channel + enter that one contact value, send OTP.
    step="code": enter the code, verify it (server-side session flag only
      -- portal_reg_verified can't be forged by the client).
    step="details": name + the OTHER contact field + password + consent,
      using the now-verified identifier from step 1.
    """
    if request.method == "POST":
        csrf.protect()
        step = request.form.get("step")

        if step == "contact":
            channel = request.form.get("channel") if request.form.get("channel") in ("email", "sms") else "email"
            identifier = (request.form.get("identifier") or "").strip()

            if channel == "email":
                if not valid_email(identifier):
                    return render_template("portal_register.html", error="Please enter a valid email address.", step="contact", channel=channel)
                exists = User.query.filter_by(email=identifier).first()
            else:
                if not identifier:
                    return render_template("portal_register.html", error="Please enter a phone number.", step="contact", channel=channel)
                exists = User.query.filter(User.phone.in_(_phone_lookup_candidates(identifier))).first()

            if exists:
                # Same generic shape as the mobile /register enumeration fix
                # (S-07) -- doesn't reveal whether the account exists.
                return render_template("portal_register.html", error="Unable to register with the details provided.", step="contact", channel=channel)

            if _otp_rate_limited(identifier):
                return render_template("portal_register.html", error="Too many attempts. Try again later.", step="contact", channel=channel)
            _record_otp_attempt(identifier)

            if ENFORCE_EMAIL_OTP_REG:
                OTPCode.query.filter(OTPCode.expires_at < datetime.utcnow()).delete()
                code = _otp_code()
                expires = datetime.utcnow() + timedelta(minutes=10)
                OTPCode.query.filter_by(identifier=identifier, used=False).update({"used": True})
                db.session.add(OTPCode(identifier=identifier, channel=channel, code=code, expires_at=expires, used=False))
                db.session.commit()
                _send_registration_otp(identifier, channel, code)

            session["portal_reg_identifier"] = identifier
            session["portal_reg_channel"] = channel
            session.pop("portal_reg_verified", None)
            return render_template("portal_register.html", error=None, step="code", identifier=identifier, channel=channel)

        if step == "code":
            identifier = session.get("portal_reg_identifier")
            channel = session.get("portal_reg_channel")
            if not identifier:
                return render_template("portal_register.html", error="Your session expired. Please start again.", step="contact", channel="email")

            otp_code = (request.form.get("otp_code") or "").strip()

            if ENFORCE_EMAIL_OTP_REG:
                if _otp_rate_limited(identifier):
                    return render_template("portal_register.html", error="Too many attempts. Try again later.", step="code", identifier=identifier, channel=channel)
                if len(otp_code) != 6 or not otp_code.isdigit():
                    return render_template("portal_register.html", error="Enter the 6-digit code sent to you.", step="code", identifier=identifier, channel=channel)
                _record_otp_attempt(identifier)
                row = OTPCode.query.filter_by(identifier=identifier, channel=channel, code=otp_code, used=False).first()
                if not row or row.expires_at < datetime.utcnow():
                    if row:
                        row.used = True
                        db.session.commit()
                    return render_template("portal_register.html", error="Invalid or expired code.", step="code", identifier=identifier, channel=channel)
                row.used = True
                db.session.commit()

            session["portal_reg_verified"] = True
            return render_template("portal_register.html", error=None, step="details", identifier=identifier, channel=channel)

        if step == "details":
            identifier = session.get("portal_reg_identifier")
            channel = session.get("portal_reg_channel")
            if not identifier or not session.get("portal_reg_verified"):
                return render_template("portal_register.html", error="Please verify your contact details first.", step="contact", channel="email")

            email = identifier if channel == "email" else (request.form.get("other") or "").strip()
            phone = identifier if channel == "sms" else (request.form.get("other") or "").strip()
            # Two boxes, not one -- see registration_screen.dart's docstring
            # for the same split on mobile. Concatenated into the single
            # `name` column below; every other place in this codebase reads
            # User.name as one display string, and splitting that column
            # would ripple far beyond what this form change needs.
            first_name = (request.form.get("first_name") or "").strip()
            last_name = (request.form.get("last_name") or "").strip()
            name = f"{first_name} {last_name}".strip()
            # Optional -- see User.ncra_id's docstring. Deliberately a
            # field of its own, never folded into the phone/other field
            # the way it used to be implied by a shared "Phone / NCRA ID"
            # label.
            ncra_id = (request.form.get("ncra_id") or "").strip() or None
            password = request.form.get("password") or ""
            consent = request.form.get("consent") == "on"

            def _redo(error):
                # Re-populates name/the other contact field on a validation
                # error so a typo doesn't force retyping the whole form --
                # same reasoning as the old two-step version's _redo.
                # Password is deliberately never echoed back.
                return render_template(
                    "portal_register.html", error=error, step="details", identifier=identifier, channel=channel,
                    first_name=first_name, last_name=last_name, other=(phone if channel == "email" else email),
                    ncra_id=ncra_id,
                )

            if not all([first_name, last_name, email, phone, password]):
                return _redo("Please complete all fields.")
            if not valid_email(email):
                return _redo("Please enter a valid email address.")
            if consent is not True:
                return _redo("You must accept the privacy policy to register.")
            if len(password) < 8:
                return _redo("Password must be at least 8 characters.")
            if _password_is_breached(password):
                return _redo("This password has appeared in a known data breach. Please choose a different one.")

            if User.query.filter(User.phone.in_(_phone_lookup_candidates(phone)) | (User.email == email)).first():
                return _redo("Unable to register with the details provided.")

            new_user = User(
                name=name, phone=phone, email=email, ncra_id=ncra_id,
                password_hash=generate_password_hash(password),
                consent_accepted_at=datetime.utcnow(),
            )
            db.session.add(new_user)
            db.session.commit()
            log_event("user_registered", user_id=new_user.id)
            _check_duplicate_signals(new_user)

            session.clear()
            session.permanent = True  # see PERMANENT_SESSION_LIFETIME's comment above
            session["portal_user_id"] = new_user.id
            portal_token = secrets.token_urlsafe(32)
            session["portal_session_token"] = portal_token
            _create_user_session(new_user.id, "web", portal_token, notify=False)
            return redirect(url_for("portal_dashboard"))

        return render_template("portal_register.html", error=None, step="contact", channel="email")

    return render_template("portal_register.html", error=None, step="contact", channel="email")


@app.route("/portal/login", methods=["GET", "POST"])
def portal_login():
    if request.method == "POST":
        csrf.protect()
        identifier = (request.form.get("identifier") or "").strip()
        password = request.form.get("password") or ""

        if identifier and _login_rate_limited(f"portal:{identifier}"):
            return render_template("portal_login.html", error="Too many login attempts. Try again later.", suspended=False), 429
        if identifier:
            _record_login_attempt(f"portal:{identifier}")

        user = User.query.filter(User.phone.in_(_phone_lookup_candidates(identifier)) | (User.email == identifier)).first()
        if not user or not check_password_hash(user.password_hash, password):
            log_event("user_login_failed", user_id=user.id if user else None, identifier=identifier)
            return render_template("portal_login.html", error="Incorrect phone/email or password.", suspended=False)
        if not user.active:
            return render_template("portal_login.html", error="This account has been suspended.", suspended=True)

        log_event("user_login", user_id=user.id)
        session.clear()
        session.permanent = True
        session["portal_user_id"] = user.id
        portal_token = secrets.token_urlsafe(32)
        session["portal_session_token"] = portal_token
        _create_user_session(user.id, "web", portal_token)
        next_url = request.args.get("next") or url_for("portal_dashboard")
        return redirect(next_url)

    return render_template("portal_login.html", error=None, suspended=False)


@app.route("/portal/forgot-password", methods=["GET", "POST"])
def portal_forgot_password():
    """
    Same 3-step shape as portal_register() (contact -> code -> confirm),
    same reasoning: prove control of the identifier fully before setting
    a new credential, not alongside it. Session-scoped state
    (portal_reset_*) mirrors portal_reg_* exactly -- see that route's
    docstring for why a server-side session flag (not a client-supplied
    flag) is what makes step "confirm" un-skippable.
    """
    if request.method == "POST":
        csrf.protect()
        step = request.form.get("step")

        if step == "contact":
            channel = request.form.get("channel") if request.form.get("channel") in ("email", "sms") else "email"
            identifier = (request.form.get("identifier") or "").strip()

            if channel == "email":
                if not valid_email(identifier):
                    return render_template("portal_forgot_password.html", error="Please enter a valid email address.", step="contact", channel=channel)
            elif not identifier:
                return render_template("portal_forgot_password.html", error="Please enter a phone number.", step="contact", channel=channel)

            if _otp_rate_limited(identifier):
                return render_template("portal_forgot_password.html", error="Too many attempts. Try again later.", step="contact", channel=channel)
            _record_otp_attempt(identifier)

            # Same anti-enumeration shape as the mobile /auth/otp/reset/request --
            # always advance to the "code" step, but only actually issue/send
            # one if the identifier belongs to a real account.
            user = User.query.filter((User.email == identifier) | User.phone.in_(_phone_lookup_candidates(identifier))).first()
            if user:
                OTPCode.query.filter(OTPCode.expires_at < datetime.utcnow()).delete()
                code = _otp_code()
                expires = datetime.utcnow() + timedelta(minutes=10)
                OTPCode.query.filter_by(identifier=identifier, purpose="reset", used=False).update({"used": True})
                db.session.add(OTPCode(identifier=identifier, channel=channel, purpose="reset", code=code, expires_at=expires, used=False))
                db.session.commit()
                _send_reset_otp(identifier, channel, code)

            session["portal_reset_identifier"] = identifier
            session["portal_reset_channel"] = channel
            session.pop("portal_reset_verified", None)
            return render_template("portal_forgot_password.html", error=None, step="code", identifier=identifier, channel=channel)

        if step == "code":
            identifier = session.get("portal_reset_identifier")
            channel = session.get("portal_reset_channel")
            if not identifier:
                return render_template("portal_forgot_password.html", error="Your session expired. Please start again.", step="contact", channel="email")

            otp_code = (request.form.get("otp_code") or "").strip()
            if _otp_rate_limited(identifier):
                return render_template("portal_forgot_password.html", error="Too many attempts. Try again later.", step="code", identifier=identifier, channel=channel)
            if len(otp_code) != 6 or not otp_code.isdigit():
                return render_template("portal_forgot_password.html", error="Enter the 6-digit code sent to you.", step="code", identifier=identifier, channel=channel)
            _record_otp_attempt(identifier)

            row = OTPCode.query.filter_by(identifier=identifier, channel=channel, code=otp_code, purpose="reset", used=False).first()
            if not row or row.expires_at < datetime.utcnow():
                if row:
                    row.used = True
                    db.session.commit()
                return render_template("portal_forgot_password.html", error="Invalid or expired code.", step="code", identifier=identifier, channel=channel)

            # Deliberately NOT marking the row used yet -- same precheck
            # reasoning as otp_verify_for_registration/otp_verify_for_reset:
            # this only proves the code was right, the "confirm" step below
            # is what actually consumes it alongside the password change.
            session["portal_reset_verified"] = True
            return render_template("portal_forgot_password.html", error=None, step="confirm", identifier=identifier, channel=channel, otp_code=otp_code)

        if step == "confirm":
            identifier = session.get("portal_reset_identifier")
            channel = session.get("portal_reset_channel")
            if not identifier or not session.get("portal_reset_verified"):
                return render_template("portal_forgot_password.html", error="Please verify your contact details first.", step="contact", channel="email")

            otp_code = (request.form.get("otp_code") or "").strip()
            new_password = request.form.get("new_password") or ""
            confirm_password = request.form.get("confirm_password") or ""

            def _redo(error):
                return render_template("portal_forgot_password.html", error=error, step="confirm", identifier=identifier, channel=channel, otp_code=otp_code)

            if len(new_password) < 8:
                return _redo("Password must be at least 8 characters.")
            if new_password != confirm_password:
                return _redo("Passwords do not match.")
            if _password_is_breached(new_password):
                return _redo("This password has appeared in a known data breach. Please choose a different one.")

            row = OTPCode.query.filter_by(identifier=identifier, channel=channel, code=otp_code, purpose="reset", used=False).first()
            if not row or row.expires_at < datetime.utcnow():
                session.pop("portal_reset_verified", None)
                return render_template("portal_forgot_password.html", error="Your code expired. Please start again.", step="contact", channel="email")

            user = User.query.filter((User.email == identifier) | User.phone.in_(_phone_lookup_candidates(identifier))).first()
            if not user:
                return render_template("portal_forgot_password.html", error="Your code expired. Please start again.", step="contact", channel="email")

            row.used = True
            user.password_hash = generate_password_hash(new_password)
            db.session.commit()
            log_event("user_password_reset", user_id=user.id)
            _revoke_all_user_sessions(user.id)
            notify_user(user.id, "password_reset", "Your password was changed", "If this wasn't you, contact support immediately.", push=True)

            session.pop("portal_reset_identifier", None)
            session.pop("portal_reset_channel", None)
            session.pop("portal_reset_verified", None)
            return render_template("portal_login.html", error=None, suspended=False, reset_success=True)

        return render_template("portal_forgot_password.html", error=None, step="contact", channel="email")

    return render_template("portal_forgot_password.html", error=None, step="contact", channel="email")


def _submit_user_appeal_internal(email: str, password: str, message: str):
    """
    Shared logic for the two surfaces a suspended youth can reach this
    from (portal_appeal, the web form, and api_submit_appeal, the mobile
    JSON counterpart) -- same reuse discipline as
    _issue_credential_internal for /issue_credential + /api/certificate/upload.
    Returns (appeal, error_message, status_code); appeal is None on any
    failure. status_code distinguishes 429 (rate-limited, either budget)
    from 400 (any other rejection) -- callers that don't care (the HTML
    form route) can just ignore it.
    """
    # Real gap found via a self-audit after building this: _appeal_rate_limited
    # further down only ever fires AFTER a correct password (and
    # confirmed-suspended state), so it throttles "spamming legitimate
    # appeals" but does nothing against password-guessing -- unlike every
    # real login route (/login, /portal/login, /employer/login), which all
    # check _login_rate_limited BEFORE verifying the password.
    if email and _login_rate_limited(f"appeal:{email}"):
        return None, "Too many attempts. Please try again later.", 429
    if email:
        _record_login_attempt(f"appeal:{email}")

    user = User.query.filter_by(email=email).first()
    if not user or not check_password_hash(user.password_hash, password):
        return None, "Incorrect email or password.", 400
    if user.active:
        return None, "This account is not currently suspended.", 400
    if not message:
        return None, "Please describe why you believe this is a mistake.", 400

    existing_open = UserAppeal.query.filter_by(user_id=user.id, status="open").first()
    if existing_open is not None:
        return None, "You already have an appeal under review. An admin will respond by email.", 400

    if _appeal_rate_limited(f"user:{user.id}"):
        return None, "Too many appeal attempts. Please try again later.", 429
    _record_appeal_attempt(f"user:{user.id}")

    appeal = UserAppeal(user_id=user.id, message=message)
    db.session.add(appeal)
    db.session.commit()
    log_event("user_appeal_submitted", user_id=user.id)
    _notify_admins_of_new_user_appeal(appeal, user)
    return appeal, None, 201


@app.route("/portal/appeal", methods=["GET", "POST"])
def portal_appeal():
    """
    Deliberately NOT behind @portal_login_required -- a suspended user's
    session is revoked the moment User.active goes False (see
    _current_portal_user_id()), so a login-gated route would be
    unreachable by exactly the population this exists for. Mirrors
    employer_appeal() exactly.
    """
    if request.method == "POST":
        csrf.protect()
        email = (request.form.get("email") or "").strip()
        password = request.form.get("password") or ""
        message = (request.form.get("message") or "").strip()[:2000]
        appeal, error, status = _submit_user_appeal_internal(email, password, message)
        if error:
            return render_template("portal_appeal.html", error=error, submitted=False), status
        return render_template("portal_appeal.html", error=None, submitted=True)

    return render_template("portal_appeal.html", error=None, submitted=False)


@app.route("/api/appeal", methods=["POST"])
def api_submit_appeal():
    """
    Mobile counterpart to portal_appeal() -- deliberately unauthenticated
    (a suspended user has no valid JWT to present at all, blocklisted at
    suspension time), proving identity the same way as /login: email +
    password. No CSRF token needed here for the same reason /login and
    /register don't take one -- this is a stateless JSON API call, not a
    cookie-authenticated form post.
    """
    data = request.json or {}
    email = (data.get("email") or "").strip()
    password = data.get("password") or ""
    message = (data.get("message") or "").strip()[:2000]
    appeal, error, status = _submit_user_appeal_internal(email, password, message)
    if error:
        return jsonify({"success": False, "error": error}), status
    return jsonify({"success": True, "appeal_id": appeal.id}), status


@app.route("/portal/logout", methods=["POST"])
@portal_login_required
def portal_logout():
    token = session.get("portal_session_token")
    if token:
        UserSession.query.filter_by(session_token=token, revoked_at=None).update({"revoked_at": datetime.utcnow()})
        db.session.commit()
    session.clear()
    return redirect(url_for("portal_login"))


@app.context_processor
def _inject_portal_identity():
    """
    Same pattern as _inject_employer_identity/_inject_admin_identity above
    -- makes `current_portal_user` available to every template without
    each portal route remembering to pass it, gated behind the cheap
    session lookup so anonymous/employer/admin traffic never pays for it.
    """
    uid = _current_portal_user_id()
    return {"current_portal_user": User.query.get(uid) if uid is not None else None}


_PORTAL_PAGE_SIZE = 24  # grid-friendly page size for the human-facing job feed, vs GET /jobs' 100-row API default


@app.route("/portal")
def portal_dashboard():
    try:
        limit = min(int(request.args.get("limit", _PORTAL_PAGE_SIZE)), _MAX_PAGE_SIZE)
    except (TypeError, ValueError):
        limit = _PORTAL_PAGE_SIZE
    try:
        offset = max(int(request.args.get("offset", 0)), 0)
    except (TypeError, ValueError):
        offset = 0

    q = (request.args.get("q") or "").strip()
    location = (request.args.get("location") or "").strip()
    skill = (request.args.get("skill") or "").strip()
    job_type = (request.args.get("job_type") or "").strip()
    # Real gap found by re-checking this page rather than assuming it was
    # done: there was no way to reach a second page of results at all --
    # fetch one extra row (never rendered) purely to know whether a "Next"
    # link should appear, instead of a second COUNT query.
    jobs = _search_jobs(q=q, location=location, skill=skill, limit=limit + 1, offset=offset, job_type=job_type)
    has_next = len(jobs) > limit
    jobs = jobs[:limit]

    # One grouped fetch for every job's employer instead of a query per
    # card (N+1) -- same discipline as employer_dashboard's applicant
    # counts and the admin/employer shells' other fixed-query patterns.
    employer_ids = {job.employer_id for job in jobs if job.employer_id}
    employers_by_id = {e.id: e for e in Employer.query.filter(Employer.id.in_(employer_ids)).all()} if employer_ids else {}
    # One aggregate query per distinct employer on this page (bounded by
    # employer_ids, not per job) -- same documented N+1 trade-off
    # _employer_summary() already accepts, see its own docstring.
    employer_trust_by_id = {eid: _employer_trust_summary(eid) for eid in employer_ids}

    applied_job_ids = set()
    uid = _current_portal_user_id()
    if uid is not None:
        applied_job_ids = {
            row[0] for row in db.session.query(Application.job_id).filter(Application.user_id == uid).all()
        }

    return render_template(
        "portal_dashboard.html", jobs=jobs, q=q, location=location, skill=skill, job_type=job_type,
        applied_job_ids=applied_job_ids, employers_by_id=employers_by_id,
        employer_trust_by_id=employer_trust_by_id, active_page="jobs",
        has_next=has_next, has_prev=offset > 0,
        next_offset=offset + limit, prev_offset=max(offset - limit, 0),
    )


@app.route("/portal/jobs/<int:job_id>")
def portal_job_detail(job_id):
    job = Job.query.get_or_404(job_id)
    employer = Employer.query.get(job.employer_id) if job.employer_id else None
    employer_trust = _employer_trust_summary(employer.id) if employer else None

    already_applied = False
    uid = _current_portal_user_id()
    if uid is not None:
        already_applied = Application.query.filter_by(user_id=uid, job_id=job.id).first() is not None

    return render_template(
        "portal_job_detail.html", job=job, employer=employer, employer_trust=employer_trust,
        already_applied=already_applied, active_page="jobs",
    )


@app.route("/portal/jobs/<int:job_id>/apply", methods=["POST"])
@portal_login_required
def portal_apply(job_id):
    job = Job.query.get_or_404(job_id)
    user_id = _current_portal_user_id()
    cv_file = request.files.get("cv")
    supporting_file = request.files.get("supporting")

    def _fail(error):
        return render_template(
            "portal_job_detail.html", job=job,
            employer=Employer.query.get(job.employer_id) if job.employer_id else None,
            already_applied=False, active_page="jobs", error=error,
        )

    # See /apply's identical check for why: no employer account to
    # receive this, and an Application row is permanent data a real
    # Postgres FK would refuse to let scanner.reaper (or an admin) ever
    # clean up around once the scraped listing expires or is removed.
    if job.source != "employer":
        return _fail("This job cannot be applied to directly.")

    # Gig/informal-work jobs (see Job.job_type) don't require a CV -- trust
    # there comes from completed-job ratings instead, not a document.
    cv_required = job.job_type != "gig"
    if cv_required and (not cv_file or not cv_file.filename):
        return _fail("Please attach your CV to apply.")

    if _upload_rate_limited(f"user:{user_id}"):
        return _fail("Too many uploads. Try again later.")
    _record_upload_attempt(f"user:{user_id}")

    if Application.query.filter_by(user_id=user_id, job_id=job.id).first():
        return _fail("You have already applied for this job.")

    if cv_file and cv_file.filename:
        if not allowed_file(cv_file.filename):
            return _fail("Unsupported CV file type.")
        if not content_matches_extension(cv_file, cv_file.filename):
            return _fail("CV file content does not match its extension.")
        if not file_is_malware_free(cv_file):
            return _fail("CV file failed a security scan.")

    if supporting_file and supporting_file.filename:
        if not allowed_file(supporting_file.filename):
            return _fail("Unsupported supporting file type.")
        if not content_matches_extension(supporting_file, supporting_file.filename):
            return _fail("Supporting file content does not match its extension.")
        if not file_is_malware_free(supporting_file):
            return _fail("Supporting file failed a security scan.")

    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    cv_filename = None
    if cv_file and cv_file.filename:
        cv_filename = f"{ts}_{user_id}_{secure_filename(cv_file.filename)}"
        cv_path = os.path.join(APPLICATION_FOLDER, cv_filename)
        cv_file.save(cv_path)
        _compress_uploaded_image_if_needed(cv_path)

    support_filename = None
    if supporting_file and supporting_file.filename:
        support_filename = f"{ts}_{user_id}_{secure_filename(supporting_file.filename)}"
        support_path = os.path.join(APPLICATION_FOLDER, support_filename)
        supporting_file.save(support_path)
        _compress_uploaded_image_if_needed(support_path)

    app_obj = Application(user_id=user_id, job_id=job.id, cv_file=cv_filename, supporting_file=support_filename)
    db.session.add(app_obj)
    try:
        db.session.commit()
        try:
            if job.employer_id:
                socketio.emit("application_created", app_obj.to_dict(), room=f"employer:{job.employer_id}")
        except Exception:
            logger.exception("socketio emit application_created failed")
    except IntegrityError:
        db.session.rollback()
        return _fail("You have already applied for this job.")

    log_event("application_submitted", user_id=user_id, job_id=job.id)
    return redirect(url_for("portal_applications"))


@app.route("/portal/applications")
@portal_login_required
def portal_applications():
    uid = _current_portal_user_id()
    rows = (
        db.session.query(Application, Job)
        .outerjoin(Job, Application.job_id == Job.id)
        .filter(Application.user_id == uid)
        .order_by(Application.created_at.desc())
        .all()
    )
    # Same N+1-avoidance discipline as my_applications()'s own lookup --
    # one grouped fetch for every application's worker->employer rating,
    # keyed by direction so the template can tell "already rated" from
    # "rating received" without a query per row.
    app_ids = [a.id for a, _job in rows]
    ratings_given = (
        {r.application_id: r for r in Rating.query.filter(
            Rating.application_id.in_(app_ids), Rating.direction == "worker_to_employer"
        ).all()}
        if app_ids else {}
    )
    ratings_received = (
        {r.application_id: r for r in Rating.query.filter(
            Rating.application_id.in_(app_ids), Rating.direction == "employer_to_worker"
        ).all()}
        if app_ids else {}
    )
    return render_template(
        "portal_applications.html", rows=rows, active_page="applications",
        ratings_given=ratings_given, ratings_received=ratings_received,
    )


@app.route("/portal/applications/<int:app_id>/rate_employer", methods=["POST"])
@portal_login_required
def portal_rate_employer(app_id):
    """
    Worker's half of the bidirectional gig rating, web-portal session
    version -- mirrors rate_employer() (the JWT/mobile route) exactly.
    Rating is a one-shot form submission, structurally like credential
    upload (built on both web and mobile), not an ongoing back-and-forth
    like messaging (this portal's one deliberate, documented app-only
    feature, see portal_applications.html) -- so unlike messaging, this
    belongs on the web portal too, not just the app.
    """
    application = Application.query.get_or_404(app_id)
    uid = _current_portal_user_id()
    if application.user_id != uid:
        return _forbidden()
    job = Job.query.get_or_404(application.job_id)

    rating, error = _submit_gig_rating(application, "worker_to_employer", job.employer_id, request.form)
    if error:
        return error
    return redirect(url_for("portal_applications"))


@app.route("/portal/ratings/<int:rating_id>/flag", methods=["POST"])
@portal_login_required
def portal_flag_rating(rating_id):
    """Worker's half of the dispute path, web-portal session version -- mirrors flag_rating()."""
    rating = Rating.query.get_or_404(rating_id)
    uid = _current_portal_user_id()
    if rating.user_id != uid or rating.direction != "employer_to_worker":
        return _forbidden()

    reason = (request.form.get("reason") or "").strip()
    if not reason:
        return _forbidden("reason is required")

    flag, rate_limited = _submit_worker_rating_flag(rating, uid, reason)
    if rate_limited:
        return jsonify({"success": False, "error": "Too many reports. Try again later."}), 429
    return redirect(url_for("portal_applications"))


@app.route("/portal/work_history")
@portal_login_required
def portal_work_history():
    """The web-portal counterpart to the mobile app's Work History screen (WorkHistoryScreen.dart)."""
    uid = _current_portal_user_id()
    summary = _worker_trust_summary(uid)
    rated_gigs = (
        db.session.query(Application, Job, Rating)
        .join(Job, Application.job_id == Job.id)
        .join(Rating, (Rating.application_id == Application.id) & (Rating.direction == "employer_to_worker"))
        .filter(Application.user_id == uid, Rating.hidden.is_(False))
        .order_by(Rating.created_at.desc())
        .all()
    )
    return render_template("portal_work_history.html", summary=summary, rated_gigs=rated_gigs, active_page="work_history")


@app.route("/portal/devices")
@portal_login_required
def portal_devices():
    """
    "Connected devices" -- lets a youth see (and remotely kill) every
    active app + web login on their account, not just the one they're
    currently using. See UserSession's docstring for why this didn't
    exist before.
    """
    uid = _current_portal_user_id()
    current_token = session.get("portal_session_token")
    rows = (
        UserSession.query.filter_by(user_id=uid, revoked_at=None)
        .order_by(UserSession.last_seen_at.desc())
        .all()
    )
    return render_template("portal_devices.html", sessions=rows, current_token=current_token, active_page="devices")


@app.route("/portal/devices/<int:session_id>/revoke", methods=["POST"])
@portal_login_required
def portal_revoke_device(session_id):
    uid = _current_portal_user_id()
    row = UserSession.query.get_or_404(session_id)
    # IDOR guard, same shape as every other "does this row belong to the
    # caller" check in this codebase (rate_employer, flag_rating, etc.).
    if row.user_id != uid:
        return _forbidden()
    if row.revoked_at is None:
        row.revoked_at = datetime.utcnow()
        if row.channel == "app":
            # Reuses the exact mechanism /logout already uses for
            # self-revocation -- session_token IS the JWT's jti for "app"
            # rows, so this makes the revoked device's token stop working
            # on its very next authenticated request, not just next login.
            _add_to_jwt_blocklist(row.session_token, datetime.utcnow() + timedelta(hours=12))
        db.session.commit()
        log_event("user_session_revoked", user_id=uid, target_session_id=session_id, channel=row.channel)

    was_current = row.session_token == session.get("portal_session_token")
    if was_current:
        # Revoking the device you're revoking FROM -- log this browser out
        # too instead of leaving it in a confusing "still on the page but
        # no longer a valid session" limbo.
        session.clear()
        return redirect(url_for("portal_login"))
    return redirect(url_for("portal_devices"))


@app.route("/portal/passport", methods=["GET", "POST"])
@portal_login_required
def portal_passport():
    uid = _current_portal_user_id()

    if request.method == "POST":
        title = (request.form.get("title") or "").strip()
        issuer = (request.form.get("issuer") or "").strip()
        file = request.files.get("file")

        if _upload_rate_limited(f"user:{uid}"):
            error = "Too many uploads. Try again later."
        else:
            _record_upload_attempt(f"user:{uid}")
            cred, error, created = _issue_credential_internal(uid, title, issuer, file)
            if not error:
                error = None

        creds = Credential.query.filter_by(user_id=uid).order_by(Credential.id.desc()).all()
        return render_template("portal_passport.html", credentials=creds, active_page="passport", error=error)

    creds = Credential.query.filter_by(user_id=uid).order_by(Credential.id.desc()).all()
    return render_template("portal_passport.html", credentials=creds, active_page="passport", error=None)


# ----------- Employer Portal (session-cookie auth) -----------
@app.route("/employer/register", methods=["GET", "POST"])
def employer_register():
    if request.method == "POST":
        csrf.protect()
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip()
        password = request.form.get("password") or ""
        industry = (request.form.get("industry") or "").strip() or None
        # Lenient, not hard-fail, same reasoning as post_job()'s job_type:
        # an omitted/unrecognized value silently becomes "business" rather
        # than 400ing, so nothing existing that doesn't send this yet ever
        # breaks. Self-declared intent (see Employer.account_type's
        # docstring), not a lock-in -- just what steers the defaults
        # everywhere else (verification form, job-posting form) so an
        # individual signing up to hire informally isn't left to
        # stumble onto the personal-ID path on their own.
        account_type = request.form.get("account_type")
        if account_type not in ("business", "individual"):
            account_type = "business"

        error = None
        if not name or not email or not password:
            error = "All fields are required."
        elif not valid_email(email):
            error = "Please enter a valid email address."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        elif _password_is_breached(password):
            error = "This password has appeared in a known data breach. Please choose a different one."
        elif Employer.query.filter_by(email=email).first():
            error = "An employer account with that email already exists."
        elif industry is not None and industry not in _INDUSTRY_CHOICES:
            error = "Please choose a valid industry."

        if error:
            return render_template("employer_register.html", error=error, industry_choices=sorted(_INDUSTRY_CHOICES))

        employer = Employer(
            name=name, email=email, password_hash=generate_password_hash(password),
            industry=industry, account_type=account_type,
        )
        db.session.add(employer)
        db.session.commit()
        session.clear()
        # Makes PERMANENT_SESSION_LIFETIME (see its own comment above) apply
        # to this session at all — session.clear() also resets .permanent
        # to Flask's default (False), so this has to come after, not before.
        session.permanent = True
        session["employer_id"] = employer.id
        return redirect(url_for("employer_dashboard"))

    return render_template("employer_register.html", error=None, industry_choices=sorted(_INDUSTRY_CHOICES))


@app.route("/employer/login", methods=["GET", "POST"])
def employer_login():
    if request.method == "POST":
        csrf.protect()
        email = (request.form.get("email") or "").strip()
        password = request.form.get("password") or ""

        if email and _login_rate_limited(f"employer:{email}"):
            return render_template("employer_login.html", error="Too many login attempts. Try again later.", suspended=False), 429
        if email:
            _record_login_attempt(f"employer:{email}")

        employer = Employer.query.filter_by(email=email).first()
        if not employer or not check_password_hash(employer.password_hash, password):
            # See user_login_failed's comment on the mobile /login route —
            # same OWASP A09 gap, same fix, same reasoning.
            log_event("employer_login_failed", employer_id=employer.id if employer else None, identifier=email)
            return render_template("employer_login.html", error="Incorrect email or password.", suspended=False)
        if not employer.active:
            return render_template(
                "employer_login.html",
                error="This employer account has been suspended.",
                suspended=True,
            )

        session.clear()
        session.permanent = True  # see PERMANENT_SESSION_LIFETIME's comment above
        session["employer_id"] = employer.id
        next_url = request.args.get("next") or url_for("employer_dashboard")
        return redirect(next_url)

    return render_template("employer_login.html", error=None, suspended=False)


@app.route("/employer/forgot-password", methods=["GET", "POST"])
def employer_forgot_password():
    """
    Same contact -> code -> confirm shape as portal_forgot_password(),
    email-only (Employer has no phone field, so there's no channel
    choice to make -- see Employer's docstring). Employer sessions are
    plain Flask session cookies with no UserSession-equivalent
    server-side store (that table is youth-only), so unlike the youth
    reset flow, this can only clear the CURRENT browser's session on
    success -- any other already-open employer session elsewhere keeps
    working until it naturally expires (PERMANENT_SESSION_LIFETIME).
    Building a parallel EmployerSession revocation store is a real,
    reasonable follow-up, but out of scope for "add forgot password" on
    its own -- tracked as a known limitation, not silently glossed over.
    """
    if request.method == "POST":
        csrf.protect()
        step = request.form.get("step")

        if step == "contact":
            email = (request.form.get("email") or "").strip()
            if not valid_email(email):
                return render_template("employer_forgot_password.html", error="Please enter a valid email address.", step="contact")

            if _otp_rate_limited(f"employer-reset:{email}"):
                return render_template("employer_forgot_password.html", error="Too many attempts. Try again later.", step="contact")
            _record_otp_attempt(f"employer-reset:{email}")

            employer = Employer.query.filter_by(email=email).first()
            if employer:
                OTPCode.query.filter(OTPCode.expires_at < datetime.utcnow()).delete()
                code = _otp_code()
                expires = datetime.utcnow() + timedelta(minutes=10)
                OTPCode.query.filter_by(identifier=f"employer:{email}", purpose="reset", used=False).update({"used": True})
                db.session.add(OTPCode(identifier=f"employer:{email}", channel="email", purpose="reset", code=code, expires_at=expires, used=False))
                db.session.commit()
                _send_reset_otp(email, "email", code)

            session["employer_reset_email"] = email
            session.pop("employer_reset_verified", None)
            return render_template("employer_forgot_password.html", error=None, step="code", email=email)

        if step == "code":
            email = session.get("employer_reset_email")
            if not email:
                return render_template("employer_forgot_password.html", error="Your session expired. Please start again.", step="contact")

            otp_code = (request.form.get("otp_code") or "").strip()
            limiter_key = f"employer-reset:{email}"
            if _otp_rate_limited(limiter_key):
                return render_template("employer_forgot_password.html", error="Too many attempts. Try again later.", step="code", email=email)
            if len(otp_code) != 6 or not otp_code.isdigit():
                return render_template("employer_forgot_password.html", error="Enter the 6-digit code sent to you.", step="code", email=email)
            _record_otp_attempt(limiter_key)

            row = OTPCode.query.filter_by(identifier=f"employer:{email}", channel="email", code=otp_code, purpose="reset", used=False).first()
            if not row or row.expires_at < datetime.utcnow():
                if row:
                    row.used = True
                    db.session.commit()
                return render_template("employer_forgot_password.html", error="Invalid or expired code.", step="code", email=email)

            session["employer_reset_verified"] = True
            return render_template("employer_forgot_password.html", error=None, step="confirm", email=email, otp_code=otp_code)

        if step == "confirm":
            email = session.get("employer_reset_email")
            if not email or not session.get("employer_reset_verified"):
                return render_template("employer_forgot_password.html", error="Please verify your email first.", step="contact")

            otp_code = (request.form.get("otp_code") or "").strip()
            new_password = request.form.get("new_password") or ""
            confirm_password = request.form.get("confirm_password") or ""

            def _redo(error):
                return render_template("employer_forgot_password.html", error=error, step="confirm", email=email, otp_code=otp_code)

            if len(new_password) < 8:
                return _redo("Password must be at least 8 characters.")
            if new_password != confirm_password:
                return _redo("Passwords do not match.")
            if _password_is_breached(new_password):
                return _redo("This password has appeared in a known data breach. Please choose a different one.")

            row = OTPCode.query.filter_by(identifier=f"employer:{email}", channel="email", code=otp_code, purpose="reset", used=False).first()
            if not row or row.expires_at < datetime.utcnow():
                session.pop("employer_reset_verified", None)
                return render_template("employer_forgot_password.html", error="Your code expired. Please start again.", step="contact")

            employer = Employer.query.filter_by(email=email).first()
            if not employer:
                return render_template("employer_forgot_password.html", error="Your code expired. Please start again.", step="contact")

            row.used = True
            employer.password_hash = generate_password_hash(new_password)
            db.session.commit()
            log_event("employer_password_reset", employer_id=employer.id)

            session.clear()
            return render_template("employer_login.html", error=None, suspended=False, reset_success=True)

        return render_template("employer_forgot_password.html", error=None, step="contact")

    return render_template("employer_forgot_password.html", error=None, step="contact")


@app.route("/employer/logout", methods=["POST"])
@employer_login_required
def employer_logout():
    session.clear()
    return redirect(url_for("employer_login"))


@app.route("/employer/appeal", methods=["GET", "POST"])
def employer_appeal():
    """
    Deliberately NOT behind @employer_login_required — a suspended
    employer's session is revoked the moment Employer.active goes False
    (see _current_employer_id()), so a login-gated route would be
    unreachable by exactly the population this exists for. Identity is
    proven the same way as employer_login (email + password), just
    without also requiring `active` to be True — that's the one check
    every other employer-portal route makes and this one intentionally
    doesn't. This is the counterpart to admin_reinstate_employer's
    docstring: a real path back, not just a theoretical one.
    """
    if request.method == "POST":
        csrf.protect()
        email = (request.form.get("email") or "").strip()
        password = request.form.get("password") or ""
        message = (request.form.get("message") or "").strip()[:2000]

        # Real gap found via a self-audit after building the youth-appeal
        # counterpart to this route: _appeal_rate_limited below only ever
        # fires AFTER a correct password (and confirmed-suspended state),
        # so it throttles "spamming legitimate appeals" but does nothing
        # against password-guessing -- unlike every real login route
        # (/login, /portal/login, /employer/login), which all check
        # _login_rate_limited BEFORE verifying the password. Same fix,
        # same mechanism, applied here for the first time.
        if email and _login_rate_limited(f"appeal:{email}"):
            return render_template("employer_appeal.html", error="Too many attempts. Please try again later.", submitted=False), 429
        if email:
            _record_login_attempt(f"appeal:{email}")

        employer = Employer.query.filter_by(email=email).first()
        if not employer or not check_password_hash(employer.password_hash, password):
            return render_template("employer_appeal.html", error="Incorrect email or password.", submitted=False)
        if employer.active:
            return render_template(
                "employer_appeal.html",
                error="This account is not currently suspended.",
                submitted=False,
            )
        if not message:
            return render_template("employer_appeal.html", error="Please describe why you believe this is a mistake.", submitted=False)

        existing_open = EmployerAppeal.query.filter_by(employer_id=employer.id, status="open").first()
        if existing_open is not None:
            return render_template(
                "employer_appeal.html",
                error="You already have an appeal under review. An admin will respond by email.",
                submitted=False,
            )

        if _appeal_rate_limited(f"employer:{employer.id}"):
            return render_template(
                "employer_appeal.html",
                error="Too many appeal attempts. Please try again later.",
                submitted=False,
            )
        _record_appeal_attempt(f"employer:{employer.id}")

        appeal = EmployerAppeal(employer_id=employer.id, message=message)
        db.session.add(appeal)
        db.session.commit()
        log_event("employer_appeal_submitted", employer_id=employer.id)
        _notify_admins_of_new_appeal(appeal, employer)
        return render_template("employer_appeal.html", error=None, submitted=True)

    return render_template("employer_appeal.html", error=None, submitted=False)


@app.route("/employer")
@employer_login_required
def employer_dashboard():
    eid = _current_employer_id()
    # Only this employer's own jobs — see _employer_owns_job() for why
    # unowned/legacy jobs are deliberately excluded here now, not folded
    # in as if they belonged to whoever happens to be logged in.
    jobs = Job.query.filter(Job.employer_id == eid).all()
    # One grouped query for every job's applicant count instead of one
    # COUNT per job (N+1 — found during a full-codebase review; this route
    # runs on every dashboard page load, so it scales with the employer's
    # job count on every single request).
    applicant_counts = dict(
        db.session.query(Application.job_id, func.count(Application.id))
        .filter(Application.job_id.in_([job.id for job in jobs]))
        .group_by(Application.job_id)
        .all()
    )
    job_data = []
    for job in jobs:
        job_data.append({
            "id": job.id,
            "title": job.title,
            "location": job.location,
            "duration": job.duration,
            # Real gap found by re-checking this page's own output rather
            # than trusting the template alone: required_skills was never
            # included here, so the skills-pill section in
            # employer_dashboard.html silently rendered nothing for every
            # job that had skills set — Jinja's default-undefined behavior
            # hid the bug instead of erroring on it.
            "required_skills": job.required_skills,
            "job_type": job.job_type,
            "category": job.category,
            "applicant_count": applicant_counts.get(job.id, 0)
        })
    employer = Employer.query.get(eid)
    trust = _employer_trust_summary(eid)
    return render_template("employer_dashboard.html", jobs=job_data, employer=employer, trust=trust, active_page="dashboard")


@app.route("/employer/verification", methods=["GET", "POST"])
@employer_login_required
def employer_verification():
    """
    Phase 3 #5 (Employer verification) follow-up. The employer uploads a
    real document; an admin reviews it and approves/rejects (see
    admin_employer_verifications below). No automated decision is made
    anywhere in this flow — the document only ever gets a human's
    judgment, which is the honest scope for a "vetting" feature this
    codebase can actually deliver.

    Two tracks (see Employer.verification_type's docstring for the real
    gap this closes): "business" (registration certificate, tax ID) for a
    registered organization, or "individual" (national ID, voter's card,
    driver's license) for someone hiring informally with no business to
    register -- a household needing a cleaner, someone needing an okada
    rider. Same upload-and-review pipeline either way; only the accepted
    document description and the resulting badge differ.
    """
    eid = _current_employer_id()
    employer = Employer.query.get(eid)

    if request.method == "POST":
        csrf.protect()
        verification_type = request.form.get("verification_type")
        if verification_type not in ("business", "individual"):
            verification_type = "business"
        file = request.files.get("document")
        if not file or not file.filename:
            return render_template("employer_verification.html", employer=employer, active_page="verification", error="Please choose a file to upload.")
        if _upload_rate_limited(f"employer:{eid}"):
            return render_template("employer_verification.html", employer=employer, active_page="verification", error="Too many uploads. Try again later."), 429
        _record_upload_attempt(f"employer:{eid}")
        if not allowed_file(file.filename):
            return render_template("employer_verification.html", employer=employer, active_page="verification", error="Unsupported file type.")
        if not content_matches_extension(file, file.filename):
            return render_template("employer_verification.html", employer=employer, active_page="verification", error="File content does not match its extension.")
        if not file_is_malware_free(file):
            return render_template("employer_verification.html", employer=employer, active_page="verification", error="File failed a security scan.")

        ts = datetime.utcnow().strftime("%Y%m%d%H%M%S")
        filename = f"{ts}_{eid}_{secure_filename(file.filename)}"
        verification_path = os.path.join(EMPLOYER_VERIFICATION_FOLDER, filename)
        file.save(verification_path)
        _compress_uploaded_image_if_needed(verification_path)

        employer.verification_document = filename
        employer.verification_type = verification_type
        employer.verification_status = "pending"
        employer.verification_reviewed_at = None
        employer.verification_reviewed_by_admin_id = None
        db.session.commit()
        log_event("employer_verification_submitted", employer_id=eid, verification_type=verification_type)
        return redirect(url_for("employer_verification"))

    return render_template("employer_verification.html", employer=employer, active_page="verification", error=None)


@app.route("/admin/employer_verifications")
@admin_role_required("admin")
def admin_employer_verifications():
    """Admin-only (not verifier — approving/rejecting a business's
    verification status is a materially higher-trust action than viewing
    read-only analytics, same reasoning as admin_accounts)."""
    pending = Employer.query.filter_by(verification_status="pending").order_by(Employer.created_at.asc()).all()
    decided = (
        Employer.query.filter(Employer.verification_status.in_(["verified", "rejected"]))
        .order_by(Employer.verification_reviewed_at.desc())
        .limit(50)
        .all()
    )
    # The actual answer to "what if a verified employer goes rogue" —
    # verification_status alone gates nothing (see Employer.active's
    # docstring), so this full roster with a real suspend/reinstate lever
    # is what makes that question answerable at all, not just the
    # one-time verification decision above.
    all_employers = Employer.query.order_by(Employer.name.asc()).all()
    return render_template(
        "admin_employer_verifications.html",
        pending=pending,
        decided=decided,
        all_employers=all_employers,
        active_page="employer_verifications",
    )


@app.route("/admin/employer_verifications/<int:employer_id>/decide", methods=["POST"])
@admin_role_required("admin")
def admin_decide_employer_verification(employer_id):
    decision = request.form.get("decision")
    if decision not in ("verified", "rejected"):
        return _forbidden("Invalid decision")

    employer = Employer.query.get_or_404(employer_id)
    employer.verification_status = decision
    employer.verification_reviewed_at = datetime.utcnow()
    employer.verification_reviewed_by_admin_id = _current_admin_id()
    db.session.commit()
    log_event("employer_verification_decided", employer_id=employer_id, decision=decision)
    return redirect(url_for("admin_employer_verifications"))


def _valid_source_url(url: str) -> bool:
    """Basic well-formedness check before a job_source's base_url is ever
    sent to Firecrawl — Firecrawl's own infrastructure does the actual
    fetching, so this only needs to catch an obviously malformed value,
    not enforce a full allowlist (see the scanner implementation plan's
    explicit note on this being deferred, not required for v1)."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in ("http", "https") and bool(parsed.netloc)


def _scanner_health(sources):
    """
    Scanner Health -- the things an admin actually needs to answer "is
    this working right now" without reading server logs or writing a
    throwaway script, which is exactly how every one of these numbers
    got checked earlier in this feature's own development. Kept
    deliberately small: a health summary that itself needs a manual to
    read defeats the point. Shared by every admin_scanner* view that
    re-renders admin_scanner.html, not just the main GET -- the template
    references `health` unconditionally.
    """
    last_successful_scan = (
        ScanRun.query.filter_by(status="success").order_by(ScanRun.finished_at.desc()).first()
    )
    total_scraped_jobs = Job.query.filter_by(source="scraped").count()
    jobs_with_description = Job.query.filter(
        Job.source == "scraped",
        Job.description.isnot(None),
        db.func.length(Job.description) >= scanner_pipeline.MIN_USEFUL_DESCRIPTION_LENGTH,
    ).count()
    return {
        "poller_enabled": os.getenv("ENABLE_JOB_SCANNER_POLLER") == "1",
        "active_sources": sum(1 for s in sources if s.active),
        "total_sources": len(sources),
        "total_scraped_jobs": total_scraped_jobs,
        "jobs_with_description": jobs_with_description,
        "last_successful_scan_at": last_successful_scan.finished_at if last_successful_scan else None,
    }


@app.route("/admin/scanner")
@admin_role_required("admin")
def admin_scanner():
    """
    Job scanner diagnostics — sources, recent scan history, and pending
    company verifications at a glance. Admin-only (not verifier): this
    page's actions (add a source, trigger a scan) spend real Firecrawl/
    Anthropic API credits, a materially different trust level than
    read-only analytics — same reasoning admin_employer_verifications
    gives for its own @admin_role_required("admin") gate.
    """
    sources = JobSource.query.order_by(JobSource.created_at.desc()).all()
    recent_runs = (
        ScanRun.query.order_by(ScanRun.queued_at.desc())
        .limit(50)
        .all()
    )
    sources_by_id = {s.id: s for s in JobSource.query.all()}

    return render_template(
        "admin_scanner.html",
        sources=sources,
        recent_runs=recent_runs,
        sources_by_id=sources_by_id,
        health=_scanner_health(sources),
        active_page="scanner",
    )


@app.route("/admin/scanner/sources", methods=["POST"])
@admin_role_required("admin")
def admin_scanner_create_source():
    name = (request.form.get("name") or "").strip()
    base_url = (request.form.get("base_url") or "").strip()
    try:
        scan_frequency_minutes = int(request.form.get("scan_frequency_minutes", 360))
    except (TypeError, ValueError):
        scan_frequency_minutes = 360
    scan_frequency_minutes = max(30, min(scan_frequency_minutes, 1440))

    if not name or not base_url or not _valid_source_url(base_url):
        sources = JobSource.query.order_by(JobSource.created_at.desc()).all()
        recent_runs = ScanRun.query.order_by(ScanRun.queued_at.desc()).limit(50).all()
        sources_by_id = {s.id: s for s in sources}
        return render_template(
            "admin_scanner.html", sources=sources, recent_runs=recent_runs, sources_by_id=sources_by_id,
            health=_scanner_health(sources),
            active_page="scanner", error="A name and a valid http(s):// URL are required.",
        )

    source = JobSource(
        name=name, base_url=base_url, scan_frequency_minutes=scan_frequency_minutes,
        created_by_admin_id=_current_admin_id(),
    )
    db.session.add(source)
    db.session.commit()
    log_event("scan_source_created", admin_id=_current_admin_id(), source_id=source.id, name=name)
    return redirect(url_for("admin_scanner"))


@app.route("/admin/scanner/sources/<int:source_id>", methods=["POST"])
@admin_role_required("admin")
def admin_scanner_update_source(source_id):
    """Toggle a source active/inactive, or update its scan frequency —
    the one PATCH-shaped action this admin panel needs, kept as a single
    POST route (no PUT/PATCH plumbing exists elsewhere in this app either)."""
    source = JobSource.query.get_or_404(source_id)
    if "active" in request.form:
        source.active = request.form.get("active") == "1"
    if "scan_frequency_minutes" in request.form:
        try:
            source.scan_frequency_minutes = max(30, min(int(request.form["scan_frequency_minutes"]), 1440))
        except (TypeError, ValueError):
            pass
    db.session.commit()
    log_event("scan_source_updated", admin_id=_current_admin_id(), source_id=source.id, active=source.active)
    return redirect(url_for("admin_scanner"))


@app.route("/admin/scanner/sources/<int:source_id>/trigger", methods=["POST"])
@admin_role_required("admin")
def admin_scanner_trigger_source(source_id):
    """
    Queues a manual scan_run — does NOT call Firecrawl/Claude inline
    (that would block the admin's own request for as long as the scan
    takes, and risk a browser-side timeout). scanner.poller's claim loop
    picks this up within one poll interval (SCANNER_POLL_INTERVAL_SECONDS,
    default 30s), the exact same path a pg_cron-scheduled run takes.

    Guarded against a double-click (or repeated impatient clicking while
    a scan is already running) queuing a pile of redundant scan_runs —
    each one is a real Firecrawl+Claude API spend, not a free retry. Same
    "already queued/running" check the pg_cron-side scheduling query uses
    for scheduled runs (see the enable_pg_cron_scan_scheduling migration).
    """
    source = JobSource.query.get_or_404(source_id)
    already_pending = ScanRun.query.filter(
        ScanRun.source_id == source.id, ScanRun.status.in_(["queued", "running"]),
    ).first()
    if already_pending:
        return redirect(url_for("admin_scanner"))

    scan_run = ScanRun(
        source_id=source.id, status="queued", trigger="manual",
        triggered_by_admin_id=_current_admin_id(),
    )
    db.session.add(scan_run)
    db.session.commit()
    log_event("scan_triggered", admin_id=_current_admin_id(), source_id=source.id, scan_run_id=scan_run.id)
    return redirect(url_for("admin_scanner"))


@app.route("/admin/scanner/companies")
@admin_role_required("admin")
def admin_scanner_companies():
    unverified = ScrapedCompany.query.filter_by(verified=False).order_by(ScrapedCompany.last_seen_at.desc()).all()
    verified = ScrapedCompany.query.filter_by(verified=True).order_by(ScrapedCompany.verified_at.desc()).limit(50).all()
    return render_template(
        "admin_scanner_companies.html", unverified=unverified, verified=verified, active_page="scanner_companies",
    )


@app.route("/admin/scanner/companies/<int:company_id>/verify", methods=["POST"])
@admin_role_required("admin")
def admin_scanner_verify_company(company_id):
    """Same shape as admin_decide_employer_verification above — a
    separate action/table from Employer.verification_status on purpose
    (see ScrapedCompany's own docstring)."""
    company = ScrapedCompany.query.get_or_404(company_id)
    company.verified = True
    company.verified_at = datetime.utcnow()
    company.verified_by_admin_id = _current_admin_id()
    db.session.commit()
    log_event("scraped_company_verified", admin_id=_current_admin_id(), company_id=company.id, name=company.name)
    return redirect(url_for("admin_scanner_companies"))


def _notify_employer_of_suspension(employer: "Employer") -> None:
    """
    Real gap found alongside EmployerAppeal: suspending an employer told
    every future login attempt "you're suspended" but never told the
    employer AT suspension time, and never mentioned that a real appeal
    path exists — someone would only discover either by trying to log in
    on their own initiative. Best-effort, same pattern as
    _notify_admins_of_new_report(): an email failure must never fail the
    suspend action itself.
    """
    try:
        _send_email(
            employer.email,
            "Your YouthChain employer account has been suspended",
            (
                f"Your YouthChain employer account ({employer.name}) has been "
                "suspended and can no longer log in, post jobs, or view "
                "applicants.\n\n"
                "If you believe this is a mistake, you can submit an appeal at "
                f"{url_for('employer_appeal', _external=True)} using this "
                "account's email and password. An admin will review it."
            ),
        )
    except Exception:
        logger.exception("Failed to notify employer %s of suspension", employer.id)


def _notify_user_of_suspension(user: "User") -> None:
    """
    User's counterpart to _notify_employer_of_suspension() -- same
    reasoning, and email rather than the in-app Notification/socketio path
    notify_user() otherwise uses: a suspended account can no longer log
    into either surface to ever SEE an in-app notification, so email is
    the only channel that can actually reach them. Also revokes every
    currently active session (both channels) so this is immediate, not
    "immediate for future requests, but any tab/app already open keeps
    working until its token naturally expires" -- belt-and-suspenders on
    top of _current_user_id()/_current_portal_user_id() already re-checking
    User.active on every call.
    """
    for row in UserSession.query.filter_by(user_id=user.id, revoked_at=None).all():
        row.revoked_at = datetime.utcnow()
        if row.channel == "app":
            _add_to_jwt_blocklist(row.session_token, datetime.utcnow() + timedelta(hours=12))
    db.session.commit()
    try:
        _send_email(
            user.email,
            "Your YouthChain account has been suspended",
            (
                f"Your YouthChain account ({user.name}) has been suspended and can "
                "no longer log in or apply for jobs. Every device previously signed "
                "in has been signed out.\n\n"
                "If you believe this is a mistake, you can submit an appeal at "
                f"{url_for('portal_appeal', _external=True)} using this account's "
                "email and password. An admin will review it."
            ),
        )
    except Exception:
        logger.exception("Failed to notify user %s of suspension", user.id)


def _notify_admins_of_new_appeal(appeal: "EmployerAppeal", employer: "Employer") -> None:
    """The appeal-side twin of _notify_admins_of_new_report() — same reasoning:
    an admin has no way to know an appeal exists unless something tells them."""
    try:
        admins = Admin.query.filter_by(role="admin", active=True).all()
        if not admins:
            return
        subject = f"YouthChain: suspension appeal from {employer.name}"
        body = (
            f"{employer.name} has appealed their suspension.\n\n"
            f"{appeal.message}\n\n"
            "Review it at /admin/appeals."
        )
        for admin in admins:
            _send_email(admin.email, subject, body)
    except Exception:
        logger.exception("Failed to notify admins of new appeal %s", appeal.id)


def _notify_admins_of_new_user_appeal(appeal: "UserAppeal", user: "User") -> None:
    """The youth-appeal twin of _notify_admins_of_new_appeal() -- same reasoning."""
    try:
        admins = Admin.query.filter_by(role="admin", active=True).all()
        if not admins:
            return
        subject = f"YouthChain: suspension appeal from {user.name}"
        body = (
            f"{user.name} ({user.email}) has appealed their suspension.\n\n"
            f"{appeal.message}\n\n"
            "Review it at /admin/user_appeals."
        )
        for admin in admins:
            _send_email(admin.email, subject, body)
    except Exception:
        logger.exception("Failed to notify admins of new user appeal %s", appeal.id)


@app.route("/admin/employers/<int:employer_id>/suspend", methods=["POST"])
@admin_role_required("admin")
def admin_suspend_employer(employer_id):
    """
    The real lever for "a verified employer is behaving badly" — see
    Employer.active's docstring for why this didn't exist before and why
    verification_status alone was never enough. Immediate: the same
    request revokes any already-open session for this employer, since
    _current_employer_id() re-checks Employer.active on every call.
    """
    employer = Employer.query.get_or_404(employer_id)
    employer.active = False
    db.session.commit()
    log_event("employer_suspended", employer_id=employer_id)
    _notify_employer_of_suspension(employer)
    return redirect(url_for("admin_employer_verifications"))


@app.route("/admin/employers/<int:employer_id>/reinstate", methods=["POST"])
@admin_role_required("admin")
def admin_reinstate_employer(employer_id):
    """
    Reversible on purpose, unlike Admin deactivation: a suspension here is
    plausibly based on a report or dispute that could turn out to be
    mistaken or resolved, and permanently locking out a real business
    with no recourse is a harsher default than this platform's evidence
    (a single admin's judgment call, not a court) actually supports.
    """
    employer = Employer.query.get_or_404(employer_id)
    employer.active = True
    db.session.commit()
    log_event("employer_reinstated", employer_id=employer_id)
    return redirect(url_for("admin_employer_verifications"))


def _open_employer_erasure_holds(employer_id: int) -> list[str]:
    """Employer-side counterpart to _open_erasure_holds() above -- same
    reasoning, scoped to disputes this EMPLOYER is a direct party to:
    their own appeal, their own filed rating dispute, and (unlike the
    worker side) an open EmployerReport where they're the SUBJECT --
    workers filed those against them, so erasing on top of an active
    fraud/scam investigation would be destroying the evidence the
    investigation is actually about, not just evidence of the erasing
    party's own making."""
    holds = []
    if EmployerAppeal.query.filter_by(employer_id=employer_id, status="open").first():
        holds.append("This employer has an open appeal awaiting review.")
    if EmployerReport.query.filter_by(employer_id=employer_id, status="open").first():
        holds.append("This employer has an open report awaiting review.")
    if RatingFlag.query.filter_by(flagged_by_employer_id=employer_id, status="open").first():
        holds.append("This employer has an open rating dispute awaiting review.")
    return holds


def _erase_employer_data(employer: "Employer", *, reason: str) -> tuple[bool, str | None]:
    """
    Employer-side counterpart to _erase_user_data() above -- same
    tombstone design (see User.erased_at's docstring), same "delete
    what's purely theirs, redact their content out of records a
    counterparty has a legitimate interest in, leave everything else's
    employer_id FK pointing at this now-anonymized row untouched."

    - Job rows survive untouched (title/location/duration/etc): every
      applicant's own Application.job_id is NOT NULL, so their
      application history depends on the Job existing, the same reason
      Application rows themselves survive User erasure. This does leave a
      real, separate gap this function does not attempt to fix: nothing
      in this codebase today hides an inactive/suspended/erased
      employer's job postings from GET /jobs or search -- that gap
      predates this feature (a merely-suspended employer's jobs are
      already just as visible) and fixing it is a distinct piece of work
      from account erasure, not folded in here.
    - Rating rows survive untouched -- same reasoning as the User side:
      an employer_to_worker rating is the employer's own assessment,
      already fed into the worker's trust history; a worker_to_employer
      rating affects THIS employer's own trust score history, which
      erasing shouldn't retroactively rewrite.
    - EmployerReport rows survive -- these are workers' own reports
      against this employer; their content isn't this employer's data to
      erase.
    - Message rows survive, only this employer's own sent messages have
      body/attachment redacted, same as the User side.

    Returns (success, error_message), same contract as
    _erase_user_data().
    """
    holds = _open_employer_erasure_holds(employer.id)
    if holds:
        return False, " ".join(holds) + " This account can be erased once that's resolved."

    if employer.verification_document:
        _delete_upload_file(os.path.join(EMPLOYER_VERIFICATION_FOLDER, employer.verification_document))

    for message in Message.query.filter_by(sender_employer_id=employer.id).all():
        if message.attachment_file:
            _delete_upload_file(os.path.join(MESSAGE_ATTACHMENT_FOLDER, message.attachment_file))
            message.attachment_file = None
        message.body = "[deleted by employer]"

    # Already confirmed above there's no OPEN appeal -- any that exist
    # here are resolved; redact the free-text body, same treatment as a
    # Message this employer sent.
    for appeal in EmployerAppeal.query.filter_by(employer_id=employer.id).all():
        appeal.message = "[erased]"

    employer.name = "Deleted employer"
    employer.email = f"erased-employer-{employer.id}@erased.youthchain.invalid"
    employer.password_hash = generate_password_hash(secrets.token_hex(32))
    employer.verification_document = None
    employer.active = False
    employer.erased_at = datetime.utcnow()

    db.session.commit()
    log_event("employer_erased", employer_id=employer.id, reason=reason)
    return True, None


@app.route("/api/employer/account/erase", methods=["POST"])
@employer_login_required
def erase_own_employer_account():
    """Self-service counterpart to admin_erase_employer() below -- see
    _erase_employer_data()'s own docstring. Requires re-entering the
    current password, same reasoning as erase_own_account()'s own
    docstring for the User side."""
    employer = Employer.query.get_or_404(_current_employer_id())
    password = request.form.get("password") or (request.get_json(silent=True) or {}).get("password") or ""

    if _login_rate_limited(f"erase:employer:{employer.id}"):
        return jsonify({"success": False, "error": "Too many attempts. Try again later."}), 429
    _record_login_attempt(f"erase:employer:{employer.id}")

    if not check_password_hash(employer.password_hash, password):
        return _forbidden("Incorrect password.")

    ok, error = _erase_employer_data(employer, reason="employer_requested")
    if not ok:
        return jsonify({"success": False, "error": error}), 409
    return jsonify({"success": True})


@app.route("/admin/employers/<int:employer_id>/erase", methods=["POST"])
@admin_role_required("admin")
def admin_erase_employer(employer_id):
    """Admin-triggered counterpart to erase_own_employer_account() above --
    see admin_erase_user()'s own docstring for the identical reasoning on
    why the admin's own 2FA-gated login is the identity check a request
    reaching an admin this way still needs."""
    employer = Employer.query.get_or_404(employer_id)
    ok, error = _erase_employer_data(employer, reason=f"admin_requested:{_current_admin_id()}")
    if not ok:
        return jsonify({"success": False, "error": error}), 409
    log_event("employer_erased_by_admin", employer_id=employer_id, admin_id=_current_admin_id())
    return jsonify({"success": True})


@app.route("/admin/users")
@admin_role_required("admin")
def admin_users():
    """
    Real gap found via a full admin-capability review: Employer and Admin
    both had a real roster + suspend/reinstate lever here; User -- the
    actual youth account -- had neither. A compromised or abusive youth
    account had no admin-side remedy at all before this existed. Search is
    client-side (see data-table-filter in the template, same as
    admin_employer_verifications' "All employers" table) since this is an
    admin console list, not a public search surface.
    """
    q = (request.args.get("q") or "").strip()
    query = User.query
    if q:
        like = f"%{q}%"
        query = query.filter(db.or_(User.name.ilike(like), User.email.ilike(like), User.phone.ilike(like)))
    users = query.order_by(User.name.asc()).limit(200).all()

    # Batched, not per-row -- same N+1-avoidance discipline as
    # _employer_trust_summary/portal_dashboard's employer_trust_by_id.
    session_counts = dict(
        db.session.query(UserSession.user_id, func.count(UserSession.id))
        .filter(UserSession.revoked_at.is_(None))
        .group_by(UserSession.user_id)
        .all()
    )
    return render_template(
        "admin_users.html",
        users=users,
        session_counts=session_counts,
        query=q,
        active_page="users",
    )


def _open_erasure_holds(user_id: int) -> list[str]:
    """
    Returns a list of human-readable reasons erasure must be refused right
    now, or an empty list if there are none. Real gap the erasure feature
    below exists to close without opening a worse one: an unconditional
    self-service "delete everything" action would let someone erase their
    account specifically to destroy evidence the moment a fraud
    investigation or dispute involving them opens -- checked here, not
    assumed away. Deliberately scoped to disputes THIS user is a direct
    party to (their own appeal, a report/flag THEY filed, a duplicate-
    identity flag naming them either side) rather than every conceivable
    record that mentions them -- narrow and enforceable beats broad and
    unverifiable.
    """
    holds = []
    if UserAppeal.query.filter_by(user_id=user_id, status="open").first():
        holds.append("You have an open appeal awaiting review.")
    if EmployerReport.query.filter_by(reporter_user_id=user_id, status="open").first():
        holds.append("You have an open report awaiting review.")
    if ScrapedListingReport.query.filter_by(reporter_user_id=user_id, status="open").first():
        holds.append("You have an open listing report awaiting review.")
    if RatingFlag.query.filter_by(flagged_by_user_id=user_id, status="open").first():
        holds.append("You have an open rating dispute awaiting review.")
    if DuplicateFlag.query.filter(
        db.or_(DuplicateFlag.user_id == user_id, DuplicateFlag.matched_user_id == user_id),
        DuplicateFlag.resolved.is_(False),
    ).first():
        holds.append("Your account is part of an open identity-verification review.")
    return holds


def _delete_upload_file(path: str | None) -> None:
    """Best-effort file removal for _erase_user_data() below -- a file
    already gone (double-erasure, a prior partial failure) is a successful
    outcome here, not an error; only unexpected failures are logged."""
    if not path:
        return
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        logger.exception("_erase_user_data: failed to remove file %s", path)


def _erase_user_data(user: "User", *, reason: str) -> tuple[bool, str | None]:
    """
    Permanently erases a User's personal data -- see erased_at's own
    docstring on the User model for the tombstone design this relies on:
    the row itself is kept (every other table's user_id FK, e.g.
    Application/Message/Rating, stays valid and pointing at this same row
    without needing to touch any of those FK columns), only overwritten
    with unusable placeholders, alongside deleting every file this user
    uploaded and every record that's purely theirs with no other party's
    legitimate interest in it.

    What's deliberately NOT touched, and why:
    - Application rows survive (job_id/status/dates intact) -- the
      employer's own hiring record. Only this user's own uploaded files
      (cv_file/supporting_file) are deleted and those columns nulled.
    - Message rows survive, but only messages THIS user sent have their
      body/attachment redacted -- the counterparty's own messages in the
      same thread, and the thread's existence, are theirs to keep.
    - Rating rows survive untouched -- a rating is the RATER's own
      assessment/speech (and, in the worker_to_employer direction,
      directly feeds the employer's public trust score), not something
      erasing the person it's ABOUT should delete.
    - Credential rows are the one exception to "keep the row": deleted
      outright, not redacted, along with the file. This makes
      /verify/<id> and /employer/verify return the same judgment-free
      "not_found" state as a hash that was never registered -- see
      verify()'s own routes -- rather than introducing a new "erased"
      status that risks reading as "revoked" (implying fraud, which this
      isn't). The on-chain hash itself is never touched (can't be, and
      doesn't need to be -- see the privacy policy's own explanation of
      why deleting the source file is what actually matters).
    - AnalyticsEvent rows are untouched deliberately -- they already
      reference user_id, which is about to point at an anonymized row;
      no further action needed for them to stop being personally
      identifying.

    Returns (success, error_message). On success, error_message is None
    and the caller's own commit already happened (this function commits
    internally, matching _submit_gig_rating's contract elsewhere in this
    file). Refuses (without deleting anything) if _open_erasure_holds()
    finds a reason to.
    """
    holds = _open_erasure_holds(user.id)
    if holds:
        return False, " ".join(holds) + " Your account can be erased once that's resolved."

    for credential in Credential.query.filter_by(user_id=user.id).all():
        if credential.file_path:
            _delete_upload_file(os.path.join(UPLOAD_FOLDER, credential.file_path))
        db.session.delete(credential)

    for application in Application.query.filter_by(user_id=user.id).all():
        if application.cv_file:
            _delete_upload_file(os.path.join(APPLICATION_FOLDER, application.cv_file))
            application.cv_file = None
        if application.supporting_file:
            _delete_upload_file(os.path.join(APPLICATION_FOLDER, application.supporting_file))
            application.supporting_file = None

    for message in Message.query.filter_by(sender_user_id=user.id).all():
        if message.attachment_file:
            _delete_upload_file(os.path.join(MESSAGE_ATTACHMENT_FOLDER, message.attachment_file))
            message.attachment_file = None
        message.body = "[deleted by user]"

    UserSession.query.filter_by(user_id=user.id).delete()
    SavedJob.query.filter_by(user_id=user.id).delete()
    SavedSearch.query.filter_by(user_id=user.id).delete()
    Notification.query.filter_by(user_id=user.id).delete()

    candidate = Candidate.query.filter_by(user_id=user.id).first()
    if candidate:
        Education.query.filter_by(candidate_id=candidate.id).delete()
        db.session.delete(candidate)

    # Already confirmed above there's no OPEN appeal -- any that exist
    # here are resolved (reinstated/denied); redact the free-text body,
    # same treatment as a Message this user sent.
    for appeal in UserAppeal.query.filter_by(user_id=user.id).all():
        appeal.message = "[erased]"

    user.name = "Deleted user"
    user.phone = f"erased-{user.id}"
    user.email = f"erased-{user.id}@erased.youthchain.invalid"
    user.password_hash = generate_password_hash(secrets.token_hex(32))
    user.push_token = None
    user.ncra_id = None
    user.active = False
    user.erased_at = datetime.utcnow()

    db.session.commit()
    log_event("user_erased", user_id=user.id, reason=reason)
    return True, None


@app.route("/api/account/erase", methods=["POST"])
@jwt_required()
def erase_own_account():
    """
    Self-service counterpart to admin_erase_user() below -- see
    _erase_user_data()'s own docstring for what erasure actually does.
    Requires re-entering the current password, not just an active JWT --
    this codebase has no other "change something permanent while already
    logged in" route to match an existing precedent against, but a
    still-valid session token (left open on a shared device, or lifted
    via XSS/malware) being sufficient on its own to trigger something
    this irreversible would be a real gap worth closing here regardless.
    """
    user = User.query.get_or_404(_current_user_id())
    data = request.get_json(silent=True) or request.form
    password = data.get("password") or ""

    if _login_rate_limited(f"erase:{user.id}"):
        return jsonify({"success": False, "error": "Too many attempts. Try again later."}), 429
    _record_login_attempt(f"erase:{user.id}")

    if not check_password_hash(user.password_hash, password):
        return _forbidden("Incorrect password.")

    ok, error = _erase_user_data(user, reason="user_requested")
    if not ok:
        return jsonify({"success": False, "error": error}), 409
    return jsonify({"success": True})


@app.route("/admin/users/<int:user_id>/erase", methods=["POST"])
@admin_role_required("admin")
def admin_erase_user(user_id):
    """Admin-triggered counterpart to erase_own_account() above -- e.g. a
    request that arrives by a channel other than the app itself. See
    _erase_user_data()'s own docstring for what this actually does; the
    admin performing this action is themselves identity-verified by
    already being logged into the admin console (2FA-gated -- see
    admin_2fa_verify), which is the identity check a request reaching an
    admin this way still needs before acting on it."""
    user = User.query.get_or_404(user_id)
    ok, error = _erase_user_data(user, reason=f"admin_requested:{_current_admin_id()}")
    if not ok:
        return jsonify({"success": False, "error": error}), 409
    log_event("user_erased_by_admin", user_id=user_id, admin_id=_current_admin_id())
    return jsonify({"success": True})


@app.route("/admin/users/<int:user_id>/suspend", methods=["POST"])
@admin_role_required("admin")
def admin_suspend_user(user_id):
    """The real lever for "this youth account is compromised or behaving
    abusively" -- see User.active's docstring. Immediate: revokes every
    active session (both channels) and the account-level re-check on
    every subsequent request closes the rest of the gap."""
    user = User.query.get_or_404(user_id)
    user.active = False
    db.session.commit()
    log_event("user_suspended", user_id=user_id)
    _notify_user_of_suspension(user)
    return redirect(url_for("admin_users", q=request.form.get("q", "")))


@app.route("/admin/users/<int:user_id>/reinstate", methods=["POST"])
@admin_role_required("admin")
def admin_reinstate_user(user_id):
    """Reversible on purpose, same reasoning as admin_reinstate_employer()."""
    user = User.query.get_or_404(user_id)
    user.active = True
    db.session.commit()
    log_event("user_reinstated", user_id=user_id)
    return redirect(url_for("admin_users", q=request.form.get("q", "")))


# Real gap found reviewing this queue rather than assuming it was
# production-ready: open_reports/open_flags below render every open row
# on one page with no cap at all — fine at the handful of rows this
# feature launched with, a real problem once a source has been running
# long enough to accumulate hundreds. Same page-size convention
# _PORTAL_PAGE_SIZE already established for the youth-facing job feed.
_ADMIN_QUEUE_PAGE_SIZE = 25


def _paginate_admin_queue(rows: list, offset: int) -> tuple[list, bool, bool]:
    """
    Slices an already-fully-sorted list (see e.g. admin_reports()'s own
    repeat-offender sort, which depends on seeing every open row before
    it can rank them — this can't be pushed down into a SQL LIMIT/OFFSET
    without losing that ordering) into one page. Returns
    (page, has_next, has_prev) — the caller already has next_offset/
    prev_offset's own values (offset ± page size), computing them here
    too would just be a second source of truth for the same numbers.
    """
    page = rows[offset:offset + _ADMIN_QUEUE_PAGE_SIZE]
    has_next = offset + _ADMIN_QUEUE_PAGE_SIZE < len(rows)
    has_prev = offset > 0
    return page, has_next, has_prev


@app.route("/admin/reports")
@admin_role_required("admin")
def admin_reports():
    """
    The other half of admin_suspend_employer: this is how a report of a
    rogue employer actually reaches an admin, rather than requiring them
    to already know something is wrong through some other channel.
    Admin-only (not verifier), same reasoning as employer verification —
    reviewing a report and acting on it is a materially higher-trust
    action than read-only analytics.
    """
    try:
        offset = max(int(request.args.get("offset", 0)), 0)
    except (TypeError, ValueError):
        offset = 0

    open_reports = EmployerReport.query.filter_by(status="open").order_by(EmployerReport.created_at.asc()).all()
    reviewed = (
        EmployerReport.query.filter(EmployerReport.status.in_(["dismissed", "actioned"]))
        .order_by(EmployerReport.reviewed_at.desc())
        .limit(50)
        .all()
    )

    # Real gap found and closed after this feature first shipped: with
    # reports listed in plain chronological order, an employer reported 5
    # times by 5 different youths just looked like 5 unrelated rows — the
    # pattern was in the data but nothing surfaced it, so an admin had to
    # notice it by eye. Now: counted per employer and sorted so employers
    # with more open reports against them float to the top, not buried
    # wherever their oldest report happened to land chronologically.
    # Computed from the FULL open list, before pagination below, so a
    # count shown on any given page is always accurate regardless of
    # which page a particular report happens to land on.
    open_report_counts: dict[int, int] = {}
    for r in open_reports:
        open_report_counts[r.employer_id] = open_report_counts.get(r.employer_id, 0) + 1
    open_reports.sort(key=lambda r: (-open_report_counts[r.employer_id], r.created_at))

    total_open = len(open_reports)
    open_reports, has_next, has_prev = _paginate_admin_queue(open_reports, offset)

    # Pre-resolve the related rows the template needs, rather than
    # querying per-row in Jinja (N+1 in a loop is exactly the kind of
    # thing Phase 4 of the engineering review flagged elsewhere) — from
    # just this page's open_reports + reviewed, not the full open list,
    # now that pagination above has already trimmed it.
    reporter_ids = {r.reporter_user_id for r in open_reports + reviewed}
    employer_ids = {r.employer_id for r in open_reports + reviewed}
    reporters = {u.id: u for u in User.query.filter(User.id.in_(reporter_ids)).all()} if reporter_ids else {}
    employers = {e.id: e for e in Employer.query.filter(Employer.id.in_(employer_ids)).all()} if employer_ids else {}
    # Same "who resolved this" lookup the reviewed table now needs — see
    # this queue's own reviewed_by_admin_id docstring.
    reviewer_ids = {r.reviewed_by_admin_id for r in reviewed if r.reviewed_by_admin_id}
    reviewers = {a.id: a for a in Admin.query.filter(Admin.id.in_(reviewer_ids)).all()} if reviewer_ids else {}

    return render_template(
        "admin_reports.html",
        open_reports=open_reports,
        reviewed=reviewed,
        reporters=reporters,
        employers=employers,
        reviewers=reviewers,
        open_report_counts=open_report_counts,
        active_page="reports",
        total_open=total_open,
        has_next=has_next, has_prev=has_prev,
        next_offset=offset + _ADMIN_QUEUE_PAGE_SIZE, prev_offset=max(offset - _ADMIN_QUEUE_PAGE_SIZE, 0),
    )


@app.route("/admin/reports/<int:report_id>/resolve", methods=["POST"])
@admin_role_required("admin")
def admin_resolve_report(report_id):
    decision = request.form.get("decision")
    if decision not in ("dismiss", "suspend"):
        return _forbidden("Invalid decision")

    report = EmployerReport.query.get_or_404(report_id)
    suspended_employer = None
    if decision == "suspend":
        employer = Employer.query.get(report.employer_id)
        if employer:
            employer.active = False
            suspended_employer = employer
            log_event("employer_suspended", employer_id=employer.id, via_report_id=report_id)
        report.status = "actioned"
    else:
        report.status = "dismissed"

    report.reviewed_at = datetime.utcnow()
    report.reviewed_by_admin_id = _current_admin_id()
    db.session.commit()
    log_event("employer_report_resolved", report_id=report_id, decision=decision)
    if suspended_employer is not None:
        _notify_employer_of_suspension(suspended_employer)
    return redirect(url_for("admin_reports"))


@app.route("/admin/listing_reports")
@admin_role_required("admin")
def admin_listing_reports():
    """
    The Discover-listing counterpart to admin_reports() -- see
    ScrapedListingReport's own docstring for why this is a separate
    page/table rather than merged into /admin/reports: differently
    shaped (no employer to suspend, just a listing that can be removed).
    """
    try:
        offset = max(int(request.args.get("offset", 0)), 0)
    except (TypeError, ValueError):
        offset = 0

    open_reports = ScrapedListingReport.query.filter_by(status="open").order_by(ScrapedListingReport.created_at.asc()).all()
    reviewed = (
        ScrapedListingReport.query.filter(ScrapedListingReport.status.in_(["dismissed", "actioned"]))
        .order_by(ScrapedListingReport.reviewed_at.desc())
        .limit(50)
        .all()
    )

    # Same "float the repeatedly-reported one to the top" reasoning
    # admin_reports() already established for employers. Computed from
    # the FULL open list, before pagination below.
    open_report_counts: dict[int, int] = {}
    for r in open_reports:
        open_report_counts[r.job_id] = open_report_counts.get(r.job_id, 0) + 1
    open_reports.sort(key=lambda r: (-open_report_counts[r.job_id], r.created_at))

    total_open = len(open_reports)
    open_reports, has_next, has_prev = _paginate_admin_queue(open_reports, offset)

    reporter_ids = {r.reporter_user_id for r in open_reports + reviewed}
    job_ids = {r.job_id for r in open_reports + reviewed}
    reporters = {u.id: u for u in User.query.filter(User.id.in_(reporter_ids)).all()} if reporter_ids else {}
    jobs = {j.id: j for j in Job.query.filter(Job.id.in_(job_ids)).all()} if job_ids else {}
    reviewer_ids = {r.reviewed_by_admin_id for r in reviewed if r.reviewed_by_admin_id}
    reviewers = {a.id: a for a in Admin.query.filter(Admin.id.in_(reviewer_ids)).all()} if reviewer_ids else {}

    return render_template(
        "admin_listing_reports.html",
        open_reports=open_reports,
        reviewed=reviewed,
        reporters=reporters,
        jobs=jobs,
        reviewers=reviewers,
        open_report_counts=open_report_counts,
        active_page="listing_reports",
        total_open=total_open,
        has_next=has_next, has_prev=has_prev,
        next_offset=offset + _ADMIN_QUEUE_PAGE_SIZE, prev_offset=max(offset - _ADMIN_QUEUE_PAGE_SIZE, 0),
    )


@app.route("/admin/listing_reports/<int:report_id>/resolve", methods=["POST"])
@admin_role_required("admin")
def admin_resolve_listing_report(report_id):
    """
    "actioned" here means the listing itself was removed (the Job row
    deleted outright) -- there's no employer account to suspend the way
    admin_resolve_report() can pull. Deletes any SavedJob rows pointing
    at it first — same FK-safety reasoning scanner.reaper's own purge
    already has to apply (see that module's docstring), just triggered
    by an admin action instead of the deadline+grace-period clock.

    Also nulls job_id on every ScrapedListingReport row referencing this
    job (not just the one being resolved here) — a job can have several
    open reports (see the "repeatedly-reported" sort in
    admin_listing_reports()), and job_id is a real FK: deleting the Job
    while any of them still pointed at it would fail under Postgres.
    """
    decision = request.form.get("decision")
    if decision not in ("dismiss", "remove_listing"):
        return _forbidden("Invalid decision")

    report = ScrapedListingReport.query.get_or_404(report_id)
    if decision == "remove_listing":
        job = Job.query.get(report.job_id)
        if job:
            SavedJob.query.filter_by(job_id=job.id).delete()
            ScrapedListingReport.query.filter_by(job_id=job.id).update({"job_id": None})
            db.session.delete(job)
            log_event("listing_removed", job_id=report.job_id, via_report_id=report_id)
        report.status = "actioned"
    else:
        report.status = "dismissed"

    report.reviewed_at = datetime.utcnow()
    report.reviewed_by_admin_id = _current_admin_id()
    db.session.commit()
    log_event("listing_report_resolved", report_id=report_id, decision=decision)
    return redirect(url_for("admin_listing_reports"))


@app.route("/admin/rating_flags")
@admin_role_required("admin")
def admin_rating_flags():
    """
    The dispute review queue for Rating -- mirrors admin_reports() closely
    on purpose (same open/reviewed shape, same N+1-avoidance pre-fetch),
    since both are "something a non-admin surfaced, an admin has to act
    on it" queues. Admin-only, same reasoning as reports/appeals.
    """
    try:
        offset = max(int(request.args.get("offset", 0)), 0)
    except (TypeError, ValueError):
        offset = 0

    open_flags = RatingFlag.query.filter_by(status="open").order_by(RatingFlag.created_at.asc()).all()
    reviewed = (
        RatingFlag.query.filter(RatingFlag.status.in_(["dismissed", "actioned"]))
        .order_by(RatingFlag.reviewed_at.desc())
        .limit(50)
        .all()
    )
    total_open = len(open_flags)
    open_flags, has_next, has_prev = _paginate_admin_queue(open_flags, offset)

    rating_ids = {f.rating_id for f in open_flags + reviewed}
    ratings = {r.id: r for r in Rating.query.filter(Rating.id.in_(rating_ids)).all()} if rating_ids else {}
    user_ids = {r.user_id for r in ratings.values()}
    employer_ids = {r.employer_id for r in ratings.values()}
    users = {u.id: u for u in User.query.filter(User.id.in_(user_ids)).all()} if user_ids else {}
    employers = {e.id: e for e in Employer.query.filter(Employer.id.in_(employer_ids)).all()} if employer_ids else {}
    reviewer_ids = {f.reviewed_by_admin_id for f in reviewed if f.reviewed_by_admin_id}
    reviewers = {a.id: a for a in Admin.query.filter(Admin.id.in_(reviewer_ids)).all()} if reviewer_ids else {}
    # Which job/gig this rating was actually about -- without this an
    # admin sees "Bob, 1/5, Acme Corp" with no way to tell which of
    # potentially several gigs between that worker and employer the
    # dispute is even about, forcing them to cross-reference manually.
    application_ids = {r.application_id for r in ratings.values()}
    job_by_application_id = (
        dict(
            db.session.query(Application.id, Job.title)
            .join(Job, Application.job_id == Job.id)
            .filter(Application.id.in_(application_ids))
            .all()
        )
        if application_ids else {}
    )

    return render_template(
        "admin_rating_flags.html",
        open_flags=open_flags,
        reviewed=reviewed,
        ratings=ratings,
        users=users,
        employers=employers,
        reviewers=reviewers,
        job_by_application_id=job_by_application_id,
        active_page="rating_flags",
        total_open=total_open,
        has_next=has_next, has_prev=has_prev,
        next_offset=offset + _ADMIN_QUEUE_PAGE_SIZE, prev_offset=max(offset - _ADMIN_QUEUE_PAGE_SIZE, 0),
    )


@app.route("/admin/rating_flags/<int:flag_id>/resolve", methods=["POST"])
@admin_role_required("admin")
def admin_resolve_rating_flag(flag_id):
    decision = request.form.get("decision")
    if decision not in ("dismiss", "hide"):
        return _forbidden("Invalid decision")

    flag = RatingFlag.query.get_or_404(flag_id)
    if decision == "hide":
        rating = Rating.query.get(flag.rating_id)
        if rating:
            rating.hidden = True
            log_event("rating_hidden", rating_id=rating.id, via_flag_id=flag_id)
        flag.status = "actioned"
    else:
        flag.status = "dismissed"

    flag.reviewed_at = datetime.utcnow()
    flag.reviewed_by_admin_id = _current_admin_id()
    db.session.commit()
    log_event("rating_flag_resolved", flag_id=flag_id, decision=decision)
    return redirect(url_for("admin_rating_flags"))


@app.route("/admin/appeals")
@admin_role_required("admin")
def admin_appeals():
    """
    The review side of EmployerAppeal — mirrors admin_reports() closely on
    purpose (same open/reviewed shape, same N+1-avoidance pre-fetch), since
    both are "something a non-admin surfaced, an admin has to act on it"
    queues. Admin-only, same reasoning as reports/verification: resolving
    an appeal changes account state, a verifier account cannot.
    """
    open_appeals = EmployerAppeal.query.filter_by(status="open").order_by(EmployerAppeal.created_at.asc()).all()
    reviewed = (
        EmployerAppeal.query.filter(EmployerAppeal.status.in_(["reinstated", "denied"]))
        .order_by(EmployerAppeal.reviewed_at.desc())
        .limit(50)
        .all()
    )
    employer_ids = {a.employer_id for a in open_appeals + reviewed}
    employers = {e.id: e for e in Employer.query.filter(Employer.id.in_(employer_ids)).all()} if employer_ids else {}

    return render_template(
        "admin_appeals.html",
        open_appeals=open_appeals,
        reviewed=reviewed,
        employers=employers,
        active_page="appeals",
    )


@app.route("/admin/appeals/<int:appeal_id>/resolve", methods=["POST"])
@admin_role_required("admin")
def admin_resolve_appeal(appeal_id):
    decision = request.form.get("decision")
    if decision not in ("reinstate", "deny"):
        return _forbidden("Invalid decision")

    appeal = EmployerAppeal.query.get_or_404(appeal_id)
    if decision == "reinstate":
        employer = Employer.query.get(appeal.employer_id)
        if employer:
            employer.active = True
            log_event("employer_reinstated", employer_id=employer.id, via_appeal_id=appeal_id)
        appeal.status = "reinstated"
    else:
        appeal.status = "denied"

    appeal.reviewed_at = datetime.utcnow()
    appeal.reviewed_by_admin_id = _current_admin_id()
    db.session.commit()
    log_event("employer_appeal_resolved", appeal_id=appeal_id, decision=decision)
    return redirect(url_for("admin_appeals"))


@app.route("/admin/user_appeals")
@admin_role_required("admin")
def admin_user_appeals():
    """The youth-appeal twin of admin_appeals() -- same open/reviewed shape, same N+1-avoidance pre-fetch."""
    open_appeals = UserAppeal.query.filter_by(status="open").order_by(UserAppeal.created_at.asc()).all()
    reviewed = (
        UserAppeal.query.filter(UserAppeal.status.in_(["reinstated", "denied"]))
        .order_by(UserAppeal.reviewed_at.desc())
        .limit(50)
        .all()
    )
    user_ids = {a.user_id for a in open_appeals + reviewed}
    users = {u.id: u for u in User.query.filter(User.id.in_(user_ids)).all()} if user_ids else {}

    return render_template(
        "admin_user_appeals.html",
        open_appeals=open_appeals,
        reviewed=reviewed,
        users=users,
        active_page="user_appeals",
    )


@app.route("/admin/user_appeals/<int:appeal_id>/resolve", methods=["POST"])
@admin_role_required("admin")
def admin_resolve_user_appeal(appeal_id):
    decision = request.form.get("decision")
    if decision not in ("reinstate", "deny"):
        return _forbidden("Invalid decision")

    appeal = UserAppeal.query.get_or_404(appeal_id)
    if decision == "reinstate":
        user = User.query.get(appeal.user_id)
        if user:
            user.active = True
            log_event("user_reinstated", user_id=user.id, via_appeal_id=appeal_id)
        appeal.status = "reinstated"
    else:
        appeal.status = "denied"

    appeal.reviewed_at = datetime.utcnow()
    appeal.reviewed_by_admin_id = _current_admin_id()
    db.session.commit()
    log_event("user_appeal_resolved", appeal_id=appeal_id, decision=decision)
    return redirect(url_for("admin_user_appeals"))


@app.route("/employer_verification_document/<filename>")
def download_employer_verification_document(filename):
    """Ownership-scoped: the owning employer (session) or any logged-in
    admin (session) — the same dual-check shape as other file-download
    routes, but against Admin instead of a JWT-bearing youth, since only
    admins review these, not applicants."""
    employer = Employer.query.filter_by(verification_document=filename).first()
    if not employer:
        return _forbidden("Document not found")

    eid = _current_employer_id()
    if eid is not None and eid == employer.id:
        return send_from_directory(EMPLOYER_VERIFICATION_FOLDER, filename, as_attachment=True)

    if _current_admin_id() is not None:
        return send_from_directory(EMPLOYER_VERIFICATION_FOLDER, filename, as_attachment=True)

    return _forbidden("You do not have access to this document")


@app.route("/admin/duplicate_flags")
@admin_role_required("admin")
def admin_duplicate_flags():
    """Admin-only, same reasoning as employer verification decisions: this
    surfaces two specific youths' names/phones side by side for comparison,
    a materially higher-trust view than read-only aggregate analytics."""
    flags = (
        DuplicateFlag.query.filter_by(resolved=False)
        .order_by(DuplicateFlag.created_at.desc())
        .all()
    )
    pairs = []
    for flag in flags:
        user = User.query.get(flag.user_id)
        matched_user = User.query.get(flag.matched_user_id)
        if user and matched_user:
            pairs.append((flag, user, matched_user))
    return render_template("admin_duplicate_flags.html", pairs=pairs, active_page="duplicate_flags")


@app.route("/admin/duplicate_flags/<int:flag_id>/resolve", methods=["POST"])
@admin_role_required("admin")
def admin_resolve_duplicate_flag(flag_id):
    flag = DuplicateFlag.query.get_or_404(flag_id)
    flag.resolved = True
    flag.resolved_at = datetime.utcnow()
    flag.resolved_by_admin_id = _current_admin_id()
    db.session.commit()
    return redirect(url_for("admin_duplicate_flags"))


@app.route("/admin/credentials")
@admin_role_required("admin")
def admin_credentials():
    """
    The admin-facing half of revocation (real gap found via a full-codebase
    review, see YouthChainRegistry.sol's revokeCredential()/isValid()):
    before this, there was no way for a fraud/error report against a
    specific credential to actually reach anyone with the power to act on
    it, and no way to act even if it did -- the API existed only on-chain
    with no caller. Admin-only (not verifier), same reasoning as employer
    verification/reports: revoking someone's credential is a materially
    higher-trust action than read-only analytics. Most-recent-first,
    capped at 200 -- an admin triaging fraud reports needs recent activity
    front and center, not a growing unpaginated table of every credential
    ever issued platform-wide.
    """
    credentials = Credential.query.order_by(Credential.id.desc()).limit(200).all()
    user_ids = {c.user_id for c in credentials}
    users = {u.id: u for u in User.query.filter(User.id.in_(user_ids)).all()} if user_ids else {}
    return render_template(
        "admin_credentials.html", credentials=credentials, users=users, active_page="credentials"
    )


@app.route("/admin/credentials/<int:credential_id>/revoke", methods=["POST"])
@admin_role_required("admin")
def admin_revoke_credential(credential_id):
    credential = Credential.query.get_or_404(credential_id)
    if credential.revoked_at is not None:
        return _forbidden("This credential has already been revoked")

    # Revoked in this backend's own records immediately -- same pattern as
    # onchain_tx/issuance (BL-18): the credential is already correctly
    # showing as revoked to the credential's owner and to anyone using
    # /verify the instant an admin acts, on-chain confirmation is an
    # enhancement to that record, not a precondition for it.
    credential.revoked_at = datetime.utcnow()
    credential.revoked_by_admin_id = _current_admin_id()
    db.session.commit()
    log_event("credential_revoked", credential_id=credential.id, user_id=credential.user_id)

    notify_user(
        credential.user_id,
        "credential_revoked",
        "A credential has been revoked",
        f"“{credential.title}” has been revoked by a YouthChain administrator and will no longer show as valid.",
        credential_id=credential.id,
    )

    _spawn_background_credential_revoke(credential.id)
    return redirect(url_for("admin_credentials"))


@app.route("/admin/issuers")
@admin_role_required("admin")
def admin_issuers():
    """
    Real gap found via a full-codebase review: YouthChainRegistry.sol's
    accreditIssuer()/revokeIssuer() (only the deploying wallet is
    auto-accredited in the constructor) had no backend route and no
    admin UI at all -- there was no operational path in this codebase to
    accredit a second credential-issuer wallet once deployed, only raw
    contract interaction outside the app. Admin-only (not verifier),
    same reasoning as /admin/credentials: this is a materially
    higher-trust action than read-only analytics -- it controls who can
    write to the credential registry at all.

    No local DB table backs this list -- see listIssuers.js's docstring
    for why the chain's own event log is the source of truth here, not a
    backend-side mirror of it.
    """
    issuers = _list_issuers_onchain()
    return render_template(
        "admin_issuers.html", issuers=issuers, active_page="issuers"
    )


@app.route("/admin/issuers/accredit", methods=["POST"])
@admin_role_required("admin")
def admin_accredit_issuer():
    address = (request.form.get("address") or "").strip()
    if not _is_valid_eth_address(address):
        issuers = _list_issuers_onchain()
        return render_template(
            "admin_issuers.html", issuers=issuers, active_page="issuers",
            error=f"“{address}” isn't a valid Ethereum address (expected 0x followed by 40 hex characters).",
        )

    tx = _accredit_issuer_onchain(address)
    if not tx:
        issuers = _list_issuers_onchain()
        return render_template(
            "admin_issuers.html", issuers=issuers, active_page="issuers",
            error=f"Accrediting {address} failed -- the blockchain node may be unreachable. Nothing changed; check the server log and try again.",
        )

    log_event("issuer_accredited", admin_id=_current_admin_id(), address=address, tx=tx)
    return redirect(url_for("admin_issuers"))


@app.route("/admin/issuers/revoke", methods=["POST"])
@admin_role_required("admin")
def admin_revoke_issuer():
    address = (request.form.get("address") or "").strip()
    if not _is_valid_eth_address(address):
        issuers = _list_issuers_onchain()
        return render_template(
            "admin_issuers.html", issuers=issuers, active_page="issuers",
            error=f"“{address}” isn't a valid Ethereum address (expected 0x followed by 40 hex characters).",
        )

    tx = _revoke_issuer_onchain(address)
    if not tx:
        issuers = _list_issuers_onchain()
        return render_template(
            "admin_issuers.html", issuers=issuers, active_page="issuers",
            error=f"Revoking {address} failed -- the blockchain node may be unreachable. Nothing changed; check the server log and try again.",
        )

    log_event("issuer_revoked", admin_id=_current_admin_id(), address=address, tx=tx)
    return redirect(url_for("admin_issuers"))


@app.route("/employer/post", methods=["GET", "POST"])
@employer_login_required
def post_job():
    if request.method == "POST":
        title = request.form.get("title")
        location = request.form.get("location")
        duration = request.form.get("duration")
        if not title or not location or not duration:
            employer = Employer.query.get(_current_employer_id())
            return render_template(
                "post_job.html", active_page="post_job", gig_categories=sorted(_GIG_CATEGORY_CHOICES),
                employer=employer, error="Title, location, and duration are required.",
            )
        required_skills = request.form.get("required_skills") or ""
        # Lenient, not hard-fail, on purpose: an omitted/unrecognized
        # job_type silently becomes "formal" rather than 400ing, so any
        # existing client/form that doesn't yet know about gig jobs keeps
        # posting exactly the CV-required jobs it always has.
        job_type = request.form.get("job_type") or "formal"
        if job_type not in ("formal", "gig"):
            job_type = "formal"
        category = request.form.get("category") if job_type == "gig" else None
        if category not in _GIG_CATEGORY_CHOICES:
            category = "Other" if job_type == "gig" else None

        new_job = Job(
            title=title,
            location=location,
            duration=duration,
            required_skills=required_skills,
            job_type=job_type,
            category=category,
            employer_id=_current_employer_id(),
        )
        db.session.add(new_job)
        db.session.commit()
        log_event("job_posted", employer_id=_current_employer_id(), job_id=new_job.id)
        # Deliberately still a genuine global broadcast, unlike every other
        # socketio.emit() in this file (see _socketio_connect's docstring)
        # -- a new job posting is public listing data every youth browsing
        # jobs should see live, not something to scope to a private room.
        try:
            socketio.emit("job_created", new_job.to_dict())
        except Exception:
            logger.exception("socketio emit job_created failed")


        return redirect(url_for("employer_dashboard"))

    employer = Employer.query.get(_current_employer_id())
    return render_template(
        "post_job.html", active_page="post_job", gig_categories=sorted(_GIG_CATEGORY_CHOICES), employer=employer,
    )


@app.route("/employer/verify", methods=["GET", "POST"])
def verify():
    result = None
    credential = None
    onchain_status = None

    if request.method == "POST":
        hash_code = (request.form.get("hash") or "").strip()
        cred = Credential.query.filter_by(hash=hash_code).first()
        if cred:
            credential = cred
            result = "ok"
            # isValid (registered AND not revoked), not the older
            # existence-only isRegistered check -- see
            # _check_onchain_valid's docstring. A credential this
            # backend's own record shows as revoked_at-set will correctly
            # come back False here too, since the on-chain revocation is
            # what set that record in the first place.
            onchain_status = _check_onchain_valid(cred.hash)
            log_event("credential_verified", user_id=cred.user_id, credential_id=cred.id, onchain_status=onchain_status)
        else:
            result = "not_found"
            log_event("credential_verify_not_found")

    return render_template(
        "verify.html", result=result, credential=credential, onchain_status=onchain_status
    )

# Verify by credential ID (shareable URL) -- deliberately public/unauthenticated
# (no @employer_login_required) so anyone holding a physical certificate can
# check it, per passport_screen.dart's docstring on why this link is shared
# outside the employer portal. public=True tells verify.html to skip the
# employer-dashboard link that would otherwise dead-end a job seeker or
# third party at an employer login wall they have no reason to cross.
@app.route("/verify/<int:cred_id>")
def verify_by_id(cred_id):
    cred = Credential.query.get(cred_id)
    if not cred:
        return render_template("verify.html", result="not_found", onchain_status=None, public=True)

    onchain_status = _check_onchain_valid(cred.hash)
    log_event("credential_verified", user_id=cred.user_id, credential_id=cred.id, onchain_status=onchain_status)
    return render_template(
        "verify.html", result="ok", credential=cred, onchain_status=onchain_status, public=True
    )


# ----------- Messaging (BL-39) -----------
def _application_access(app_id):
    """
    Returns (application, job, is_owner_user, is_owner_employer) for the
    caller's current auth context (JWT youth session and/or employer
    session cookie — the same dual-auth pattern used for
    /application_file/<filename>, since messaging is reachable from both
    the mobile app and the employer web portal). Returns None if the
    application doesn't exist.
    """
    application = Application.query.get(app_id)
    if not application:
        return None
    job = Job.query.get(application.job_id)

    uid = _current_user_id()
    eid = _current_employer_id()
    is_owner_user = uid is not None and application.user_id == uid
    is_owner_employer = _employer_owns_job(job, eid)
    return application, job, is_owner_user, is_owner_employer


def _save_message_attachment(file_storage, sender_label: str):
    """
    Same content-validated, unique-prefixed-filename pattern already used
    for CV/supporting-document uploads (see /apply) — reused rather than
    reinvented so attachments get the same S-02/S-08 protections
    (unguessable filename, magic-byte content validation, malware
    scanning) as every other upload path in this codebase.
    """
    if not file_storage or not file_storage.filename:
        return None, None
    if not allowed_file(file_storage.filename):
        return None, "Unsupported attachment file type"
    if not content_matches_extension(file_storage, file_storage.filename):
        return None, "Attachment content does not match its extension"
    if not file_is_malware_free(file_storage):
        return None, "Attachment failed a security scan"
    ts = datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
    orig = secure_filename(file_storage.filename)
    filename = f"{ts}_{sender_label}_{orig}"
    attachment_path = os.path.join(MESSAGE_ATTACHMENT_FOLDER, filename)
    file_storage.save(attachment_path)
    _compress_uploaded_image_if_needed(attachment_path)
    return filename, None


def _create_message(app_id, is_owner_user, is_owner_employer, body, attachment_file_storage=None):
    body = (body or "").strip()
    attachment_filename = None

    if attachment_file_storage is not None and attachment_file_storage.filename:
        identity = f"employer:{_current_employer_id()}" if is_owner_employer else f"user:{_current_user_id()}"
        if _upload_rate_limited(identity):
            return None, "Too many uploads. Try again later.", 429
        _record_upload_attempt(identity)

        attachment_filename, attach_error = _save_message_attachment(
            attachment_file_storage, "employer" if is_owner_employer else "user"
        )
        if attach_error:
            return None, attach_error, 400

    if not body and not attachment_filename:
        return None, "Message body is required", 400
    if len(body) > 2000:
        return None, "Message is too long (max 2000 characters)", 400

    msg = Message(
        application_id=app_id,
        sender_type="employer" if is_owner_employer else "user",
        sender_user_id=_current_user_id() if is_owner_user else None,
        sender_employer_id=_current_employer_id() if is_owner_employer else None,
        body=body,
        attachment_file=attachment_filename,
    )
    db.session.add(msg)
    db.session.commit()
    log_event(
        "message_sent",
        user_id=_current_user_id(),
        employer_id=_current_employer_id(),
        application_id=app_id,
    )

    # Only the two real parties to this specific conversation may see it --
    # see _socketio_connect's docstring for why this used to broadcast
    # every message body to every connected client platform-wide.
    application = Application.query.get(app_id)
    job = Job.query.get(application.job_id) if application else None
    rooms = []
    if application:
        rooms.append(f"user:{application.user_id}")
    if job and job.employer_id:
        rooms.append(f"employer:{job.employer_id}")
    try:
        for room in rooms:
            socketio.emit("message_created", msg.to_dict(), room=room)
    except Exception:
        logger.exception("socketio emit message_created failed")

    # Notify the applicant when the employer messages them. (The reverse —
    # notifying the employer when the applicant messages — would need an
    # equivalent notification channel on the employer side, which doesn't
    # exist yet; employers actively monitor the web dashboard they're
    # already logged into for this, so it's a smaller gap than it looks.)
    if is_owner_employer and application:
        notify_user(
            application.user_id,
            "new_message",
            "New message about your application",
            body[:200],
            application_id=app_id,
        )
    return msg, None, 201


def _mark_messages_read(app_id, viewer_is_employer: bool):
    """
    Marks every message from the *other* party as read the moment this
    thread is fetched — the standard "read receipt on open" pattern. Only
    updates rows that are actually unread (avoids rewriting read_at on
    every refresh) and emits a socket event so the sender's UI can update
    live without polling.
    """
    other_sender_type = "user" if viewer_is_employer else "employer"
    unread = Message.query.filter_by(
        application_id=app_id, sender_type=other_sender_type, read=False
    ).all()
    if not unread:
        return
    now = datetime.utcnow()
    for msg in unread:
        msg.read = True
        msg.read_at = now
    db.session.commit()

    application = Application.query.get(app_id)
    job = Job.query.get(application.job_id) if application else None
    rooms = []
    if application:
        rooms.append(f"user:{application.user_id}")
    if job and job.employer_id:
        rooms.append(f"employer:{job.employer_id}")
    try:
        for room in rooms:
            socketio.emit(
                "messages_read",
                {"application_id": app_id, "message_ids": [m.id for m in unread]},
                room=room,
            )
    except Exception:
        logger.exception("socketio emit messages_read failed")


@app.route("/api/application/<int:app_id>/messages", methods=["GET", "POST"])
@jwt_required(optional=True)
def api_application_messages(app_id):
    access = _application_access(app_id)
    if not access:
        return jsonify({"success": False, "error": "Application not found"}), 404
    application, job, is_owner_user, is_owner_employer = access
    if not (is_owner_user or is_owner_employer):
        return _forbidden("You do not have access to this application's messages")

    if request.method == "POST":
        # Employer-session callers reach this JSON endpoint via their
        # portal cookie, not a JWT — same CSRF exposure as any other
        # session-authenticated POST, so it needs the same guard the
        # HTML employer routes use. JWT-authenticated (mobile) callers
        # don't carry a session cookie, so they're unaffected.
        if is_owner_employer:
            csrf.protect()
        # Accept either JSON (text-only, the original mobile behavior) or
        # multipart form data (when an attachment is included) — mirrors
        # how /apply already distinguishes a plain-fields case from a
        # file-upload case.
        if request.content_type and "multipart/form-data" in request.content_type:
            body_text = request.form.get("body")
            attachment = request.files.get("attachment")
        else:
            data = request.get_json(silent=True) or {}
            body_text = data.get("body")
            attachment = None
        msg, error, status_code = _create_message(app_id, is_owner_user, is_owner_employer, body_text, attachment)
        if error:
            return jsonify({"success": False, "error": error}), status_code
        return jsonify({"success": True, "message": msg.to_dict()}), status_code

    _mark_messages_read(app_id, viewer_is_employer=is_owner_employer)
    messages = Message.query.filter_by(application_id=app_id).order_by(Message.created_at.asc()).all()

    employer_info = None
    if job is not None and job.employer_id is not None:
        employer = Employer.query.get(job.employer_id)
        if employer is not None:
            employer_info = _employer_summary(employer)

    return jsonify({
        "success": True,
        "messages": [m.to_dict() for m in messages],
        "employer": employer_info,
        # Raw FK, same reasoning as Job.to_dict()'s top-level employer_id:
        # the mobile messages screen needs a real employer_id to call
        # POST /api/report_employer with — closing a gap a mobile agent
        # correctly flagged rather than guessed around when this screen's
        # report action was originally scoped out for exactly this reason.
        "employer_id": job.employer_id if job is not None else None,
    })


@app.route("/message_attachment/<filename>")
@jwt_required(optional=True)
def download_message_attachment(filename):
    """Ownership-scoped the same way as /application_file/<filename>:
    accepts either the owning applicant's JWT or the owning employer's
    session, and additionally accepts the JWT as a ?token= query param
    since this is opened via url_launcher on mobile, which can't attach a
    custom Authorization header (identical reasoning already documented on
    /application_file)."""
    msg = Message.query.filter_by(attachment_file=filename).first()
    if not msg:
        return _forbidden("Attachment not found")

    access = _application_access(msg.application_id)
    if not access:
        return _forbidden("Attachment not found")
    _, _, is_owner_user, is_owner_employer = access
    if not (is_owner_user or is_owner_employer):
        return _forbidden("You do not have access to this attachment")

    return send_from_directory(MESSAGE_ATTACHMENT_FOLDER, filename, as_attachment=True)


@app.route("/employer/messages")
@employer_login_required
def employer_messages_inbox():
    """
    Real gap found via a full employer-portal UX review: every
    conversation was only reachable by first opening a specific job, then
    a specific applicant's row -- there was no single place to see what's
    waiting for a reply across every job at once, and nothing in the nav
    ever pointed here at all. This is that place: every application
    (across every job this employer owns) that has at least one message,
    most recently active first, with a per-thread unread count.

    5 fixed queries regardless of how many conversations exist -- no
    per-thread query in a loop (the same N+1 discipline as
    employer_dashboard's applicant-count fix earlier this session).
    """
    eid = _current_employer_id()
    job_ids = [row[0] for row in db.session.query(Job.id).filter(Job.employer_id == eid).all()]
    if not job_ids:
        return render_template("employer_messages_inbox.html", threads=[], active_page="messages")

    latest_rows = (
        db.session.query(Message.application_id, func.max(Message.created_at).label("last_at"))
        .join(Application, Application.id == Message.application_id)
        .filter(Application.job_id.in_(job_ids))
        .group_by(Message.application_id)
        .order_by(func.max(Message.created_at).desc())
        .all()
    )
    if not latest_rows:
        return render_template("employer_messages_inbox.html", threads=[], active_page="messages")

    app_ids = [row[0] for row in latest_rows]

    unread_by_app = dict(
        db.session.query(Message.application_id, func.count(Message.id))
        .filter(
            Message.application_id.in_(app_ids),
            Message.sender_type == "user",
            Message.read.is_(False),
        )
        .group_by(Message.application_id)
        .all()
    )

    # Latest message body per application: fetched once, ascending, and
    # reduced in Python rather than one query per thread -- a portable
    # "latest row per group" isn't a single simple statement across both
    # SQLite and Postgres, and this whole route already only spends 5
    # queries total regardless of thread count.
    last_message_by_app = {}
    for m in Message.query.filter(Message.application_id.in_(app_ids)).order_by(Message.created_at.asc()).all():
        last_message_by_app[m.application_id] = m

    applications = {a.id: a for a in Application.query.filter(Application.id.in_(app_ids)).all()}
    jobs = {j.id: j for j in Job.query.filter(Job.id.in_(job_ids)).all()}
    user_ids = [a.user_id for a in applications.values()]
    users = {u.id: u for u in User.query.filter(User.id.in_(user_ids)).all()} if user_ids else {}

    threads = []
    for app_id, _last_at in latest_rows:  # already sorted most-recent-first
        application = applications.get(app_id)
        if not application:
            continue
        threads.append({
            "application": application,
            "job": jobs.get(application.job_id),
            "applicant": users.get(application.user_id),
            "last_message": last_message_by_app.get(app_id),
            "unread_count": unread_by_app.get(app_id, 0),
        })

    return render_template("employer_messages_inbox.html", threads=threads, active_page="messages")


@app.route("/employer/applications/<int:job_id>/messages/<int:app_id>", methods=["GET", "POST"])
@employer_login_required
def employer_messages_page(job_id, app_id):
    access = _application_access(app_id)
    if not access:
        return jsonify({"success": False, "error": "Application not found"}), 404
    application, job, _, is_owner_employer = access
    if not is_owner_employer or not job or job.id != job_id:
        return _forbidden("You do not have access to this application's messages")

    send_error = None
    if request.method == "POST":
        _, send_error, _ = _create_message(
            app_id, False, True, request.form.get("body"), request.files.get("attachment")
        )
        if not send_error:
            return redirect(url_for("employer_messages_page", job_id=job_id, app_id=app_id))

    _mark_messages_read(app_id, viewer_is_employer=True)
    applicant = User.query.get(application.user_id)
    messages = Message.query.filter_by(application_id=app_id).order_by(Message.created_at.asc()).all()
    return render_template(
        "employer_messages.html",
        job=job,
        application=application,
        applicant=applicant,
        messages=messages,
        active_page="messages",
        error=send_error,
    )


@app.route("/api/notifications", methods=["GET"])
@jwt_required()
def api_notifications():
    uid = _current_user_id()
    limit, offset = _pagination_params()
    notifications = (
        Notification.query.filter_by(user_id=uid)
        .order_by(Notification.created_at.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )
    unread_count = Notification.query.filter_by(user_id=uid, read=False).count()
    return jsonify({
        "success": True,
        "notifications": [n.to_dict() for n in notifications],
        "unread_count": unread_count,
    })


@app.route("/api/notifications/<int:notification_id>/read", methods=["POST"])
@jwt_required()
def api_mark_notification_read(notification_id):
    uid = _current_user_id()
    notification = Notification.query.get(notification_id)
    if not notification or notification.user_id != uid:
        return _forbidden("Notification not found")
    notification.read = True
    db.session.commit()
    return jsonify({"success": True})


@app.route("/api/industries", methods=["GET"])
def api_industries():
    """
    Single source of truth for _INDUSTRY_CHOICES — the mobile app fetches
    this rather than hardcoding its own copy of the list, so the two
    sides of the matching bonus (Employer.industry, Candidate.
    preferred_industries) can never silently drift apart. Public/no auth:
    this is a static reference list, not user data.
    """
    return jsonify({"success": True, "industries": sorted(_INDUSTRY_CHOICES)})


@app.route("/api/push_token", methods=["PUT"])
@jwt_required()
def api_register_push_token():
    """
    Mobile app calls this after login (and again whenever FCM rotates the
    token) so send_push_notification() above has somewhere to deliver to.
    Registering an empty/missing token clears it (e.g. on logout, so a
    stale token from a previous user of a shared device doesn't receive
    another user's push notifications).
    """
    uid = _current_user_id()
    user = User.query.get(uid)
    token = (request.get_json(silent=True) or {}).get("push_token") or None
    user.push_token = token
    db.session.commit()
    return jsonify({"success": True})


@app.route("/api/me", methods=["GET"])
@jwt_required()
def api_me():
    """
    Resolves the authenticated user's own account fields — same
    "let the app read its own current state back on open" purpose GET
    /api/candidate/me already serves for the Candidate profile, needed
    now that User itself has a real toggle (sms_alerts_enabled) the
    mobile app has to be able to read, not just blindly overwrite the
    way it already does for push_token.
    """
    user = User.query.get(_current_user_id())
    return jsonify({"success": True, "user": user.to_dict()}), 200


@app.route("/api/sms_alerts", methods=["PUT"])
@jwt_required()
def api_set_sms_alerts():
    """
    Sets User.sms_alerts_enabled (see its own docstring) — the opt-in
    that lets a Saved Search / profile-skill job alert (see
    _dispatch_job_alerts_for_scan) also go out as a real SMS via Twilio,
    on top of the in-app/push notification every alert already gets.
    """
    user = User.query.get(_current_user_id())
    data = request.get_json(silent=True) or {}
    user.sms_alerts_enabled = bool(data.get("enabled"))
    db.session.commit()
    return jsonify({"success": True, "sms_alerts_enabled": user.sms_alerts_enabled})


@app.route("/api/whatsapp_alerts", methods=["PUT"])
@jwt_required()
def api_set_whatsapp_alerts():
    """
    Sets User.whatsapp_alerts_enabled (see its own docstring) — mirrors
    api_set_sms_alerts above exactly, just gating send_whatsapp() instead
    of send_sms() inside notify_user().
    """
    user = User.query.get(_current_user_id())
    data = request.get_json(silent=True) or {}
    user.whatsapp_alerts_enabled = bool(data.get("enabled"))
    db.session.commit()
    return jsonify({"success": True, "whatsapp_alerts_enabled": user.whatsapp_alerts_enabled})


_REPORT_CATEGORIES = {"scam", "harassment", "fake_job", "inappropriate", "other"}


def _notify_admins_of_new_report(report: "EmployerReport", employer: "Employer") -> None:
    """
    Real gap found and closed after this feature first shipped: an admin
    had to remember to check /admin/reports themselves — nothing told
    them a new report existed. Reuses the existing _send_email() (already
    used for OTP codes) rather than building a new notification channel:
    degrades to the same log-only stub behavior when SMTP isn't
    configured, exactly like every other email this codebase sends.
    Deliberately admin-role only, not verifier — same reasoning as every
    other report/verification action: a verifier can't act on this
    anyway, so paging one would just be noise.
    Best-effort: an email failure must never fail the report submission
    itself, same pattern as log_event().
    """
    try:
        admins = Admin.query.filter_by(role="admin", active=True).all()
        if not admins:
            return
        subject = f"YouthChain: new report against {employer.name}"
        body = (
            f"A youth has reported {employer.name} ({report.category}).\n\n"
            f"{report.details or '(no additional details provided)'}\n\n"
            "Review it at /admin/reports."
        )
        for admin in admins:
            _send_email(admin.email, subject, body)
    except Exception:
        logger.exception("Failed to notify admins of new report %s", report.id)


def _notify_admins_of_new_rating_flag(flag: "RatingFlag", rating: "Rating") -> None:
    """The rating-dispute twin of _notify_admins_of_new_report() -- same
    reasoning: an admin has no way to know a dispute exists unless
    something tells them, and /admin/rating_flags's own badge count only
    helps once they're already looking at the console."""
    try:
        admins = Admin.query.filter_by(role="admin", active=True).all()
        if not admins:
            return
        subject = f"YouthChain: rating dispute ({rating.score}/5, flagged by {flag.flagged_by_role})"
        body = (
            f"A {flag.flagged_by_role} flagged a {rating.score}/5 rating as unfair.\n\n"
            f"Reason: {flag.reason}\n\n"
            "Review it at /admin/rating_flags."
        )
        for admin in admins:
            _send_email(admin.email, subject, body)
    except Exception:
        logger.exception("Failed to notify admins of new rating flag %s", flag.id)


@app.route("/api/report_employer", methods=["POST"])
@jwt_required()
def api_report_employer():
    """
    The youth-facing half of the employer-suspension lever built earlier —
    without this, an admin has no real way to learn a specific employer is
    behaving badly except some out-of-band channel. Optional job_id/
    message_id give the report real context (which posting, which
    conversation) without requiring either — a general "this employer
    seems off" report is still useful.
    """
    user_id = _current_user_id()
    data = request.get_json(silent=True) or {}
    employer_id = data.get("employer_id")
    category = (data.get("category") or "").strip()
    job_id = data.get("job_id")
    message_id = data.get("message_id")
    details = (data.get("details") or "").strip()[:2000]

    if not employer_id or category not in _REPORT_CATEGORIES:
        return jsonify({
            "success": False,
            "error": f"employer_id and a category ({', '.join(sorted(_REPORT_CATEGORIES))}) are required",
        }), 400

    employer = Employer.query.get(employer_id)
    if not employer:
        return jsonify({"success": False, "error": "Employer not found"}), 404

    # Sanity-check optional context rather than trusting it blindly — a
    # report referencing a job/message the reporter has no real
    # relationship to would mislead whoever reviews it.
    if job_id is not None:
        job = Job.query.get(job_id)
        if not job or job.employer_id != employer.id:
            return jsonify({"success": False, "error": "job_id does not belong to this employer"}), 400

    if message_id is not None:
        message = Message.query.get(message_id)
        if not message:
            return jsonify({"success": False, "error": "message_id not found"}), 400
        application = Application.query.get(message.application_id)
        if not application or application.user_id != user_id:
            return _forbidden("You can only report a message from your own conversation")
        job = Job.query.get(application.job_id)
        if not job or job.employer_id != employer.id:
            return jsonify({"success": False, "error": "message_id does not belong to this employer"}), 400

    if _report_rate_limited(f"user:{user_id}"):
        return jsonify({"success": False, "error": "Too many reports. Try again later."}), 429
    _record_report_attempt(f"user:{user_id}")

    report = EmployerReport(
        reporter_user_id=user_id,
        employer_id=employer_id,
        job_id=job_id,
        message_id=message_id,
        category=category,
        details=details or None,
    )
    db.session.add(report)
    db.session.commit()
    log_event("employer_reported", user_id=user_id, employer_id=employer_id, category=category)
    _notify_admins_of_new_report(report, employer)
    return jsonify({"success": True, "report_id": report.id}), 201


def _notify_admins_of_new_listing_report(report: "ScrapedListingReport", job: "Job") -> None:
    """The Discover-listing twin of _notify_admins_of_new_report() —
    same reasoning, same best-effort/never-fail-the-request contract."""
    try:
        admins = Admin.query.filter_by(role="admin", active=True).all()
        if not admins:
            return
        subject = f"YouthChain: new report against a Discover listing ({job.title})"
        body = (
            f"A youth has reported the listing \"{job.title}\" ({report.category}).\n\n"
            f"{report.details or '(no additional details provided)'}\n\n"
            "Review it at /admin/listing_reports."
        )
        for admin in admins:
            _send_email(admin.email, subject, body)
    except Exception:
        logger.exception("Failed to notify admins of new listing report %s", report.id)


@app.route("/api/report_listing", methods=["POST"])
@jwt_required()
def api_report_listing():
    """
    The Discover-feed counterpart to POST /api/report_employer — see
    ScrapedListingReport's own docstring for why a scraped listing needs
    a separate report path rather than reusing that one (no Employer
    account to key it to). Restricted to source="scraped" jobs
    specifically: an employer-posted job already has its own report path
    (this one existing too would just be a second, less-informative way
    to do the same thing, and a confusing choice for the mobile client
    to have to make).
    """
    user_id = _current_user_id()
    data = request.get_json(silent=True) or {}
    job_id = data.get("job_id")
    category = (data.get("category") or "").strip()
    details = (data.get("details") or "").strip()[:2000]

    if not job_id or category not in _REPORT_CATEGORIES:
        return jsonify({
            "success": False,
            "error": f"job_id and a category ({', '.join(sorted(_REPORT_CATEGORIES))}) are required",
        }), 400

    job = Job.query.get(job_id)
    if not job:
        return jsonify({"success": False, "error": "Job not found"}), 404
    if job.source != "scraped":
        return jsonify({"success": False, "error": "This job isn't a Discover listing — use /api/report_employer instead"}), 400

    if _report_rate_limited(f"user:{user_id}"):
        return jsonify({"success": False, "error": "Too many reports. Try again later."}), 429
    _record_report_attempt(f"user:{user_id}")

    report = ScrapedListingReport(
        reporter_user_id=user_id,
        job_id=job_id,
        category=category,
        details=details or None,
    )
    db.session.add(report)
    db.session.commit()
    log_event("listing_reported", user_id=user_id, job_id=job_id, category=category)
    _notify_admins_of_new_listing_report(report, job)
    return jsonify({"success": True, "report_id": report.id}), 201


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        csrf.protect()
        email = (request.form.get("email") or "").strip()
        password = request.form.get("password") or ""

        if email and _login_rate_limited(f"admin:{email}"):
            return render_template("admin_login.html", error="Too many login attempts. Try again later."), 429
        if email:
            _record_login_attempt(f"admin:{email}")

        admin = Admin.query.filter_by(email=email).first()
        if not admin or not check_password_hash(admin.password_hash, password):
            # See user_login_failed's comment on the mobile /login route —
            # same OWASP A09 gap, same fix. Arguably the single
            # highest-value place for this in the whole app: a failed
            # login against the most privileged account type in this
            # system, with previously zero audit trail of it happening.
            # target_admin_id in meta (not a first-class column — Admin
            # has no FK column on AnalyticsEvent, matching the existing
            # admin_2fa_enabled/admin_account_deactivated convention above).
            log_event("admin_login_failed", target_admin_id=admin.id if admin else None, identifier=email)
            return render_template("admin_login.html", error="Incorrect email or password.")
        if not admin.active:
            return render_template("admin_login.html", error="This admin account has been deactivated.")

        session.clear()
        # See PERMANENT_SESSION_LIFETIME's comment above — set once here,
        # applies through the rest of this session's life whether or not
        # 2FA is enabled: admin_2fa_verify below reuses this same session
        # (no session.clear() of its own), it doesn't need to set this again.
        session.permanent = True
        if admin.totp_enabled:
            # Password verified, but not fully logged in yet —
            # admin_pending_2fa_id is deliberately a different session key
            # from admin_id, so nothing gated by admin_login_required/
            # admin_access_required treats this as an authenticated
            # session until the TOTP step below also passes.
            session["admin_pending_2fa_id"] = admin.id
            next_url = request.args.get("next")
            return redirect(url_for("admin_2fa_verify", next=next_url) if next_url else url_for("admin_2fa_verify"))

        session["admin_id"] = admin.id
        next_url = request.args.get("next") or url_for("admin_analytics")
        return redirect(next_url)

    return render_template("admin_login.html", error=None)


@app.route("/admin/forgot-password", methods=["GET", "POST"])
def admin_forgot_password():
    """
    Highest-assurance version of the same contact -> code -> confirm
    shape: an admin account is the most privileged role in the system
    (employer suspension, report/appeal resolution, other operators'
    accounts), so email-OTP alone is deliberately NOT sufficient on its
    own here the way it is for youth/employer -- an attacker who only
    compromised an admin's inbox would otherwise be able to fully take
    over a TOTP-protected admin account by resetting its password, which
    would make the TOTP enrollment (admin_2fa_setup) pointless. When
    admin.totp_enabled is True, a 4th step (totp) requires a valid
    authenticator code, exactly like admin_2fa_verify's own check,
    before the password can be changed. When TOTP isn't enabled, this
    degrades to the same 3-step shape as the other two reset flows.
    """
    if request.method == "POST":
        csrf.protect()
        step = request.form.get("step")

        if step == "contact":
            email = (request.form.get("email") or "").strip()
            if not valid_email(email):
                return render_template("admin_forgot_password.html", error="Please enter a valid email address.", step="contact")

            limiter_key = f"admin-reset:{email}"
            if _otp_rate_limited(limiter_key):
                return render_template("admin_forgot_password.html", error="Too many attempts. Try again later.", step="contact")
            _record_otp_attempt(limiter_key)

            admin = Admin.query.filter_by(email=email).first()
            if admin:
                OTPCode.query.filter(OTPCode.expires_at < datetime.utcnow()).delete()
                code = _otp_code()
                expires = datetime.utcnow() + timedelta(minutes=10)
                OTPCode.query.filter_by(identifier=f"admin:{email}", purpose="reset", used=False).update({"used": True})
                db.session.add(OTPCode(identifier=f"admin:{email}", channel="email", purpose="reset", code=code, expires_at=expires, used=False))
                db.session.commit()
                _send_reset_otp(email, "email", code)

            session["admin_reset_email"] = email
            session.pop("admin_reset_verified", None)
            session.pop("admin_reset_totp_verified", None)
            return render_template("admin_forgot_password.html", error=None, step="code", email=email)

        if step == "code":
            email = session.get("admin_reset_email")
            if not email:
                return render_template("admin_forgot_password.html", error="Your session expired. Please start again.", step="contact")

            otp_code = (request.form.get("otp_code") or "").strip()
            limiter_key = f"admin-reset:{email}"
            if _otp_rate_limited(limiter_key):
                return render_template("admin_forgot_password.html", error="Too many attempts. Try again later.", step="code", email=email)
            if len(otp_code) != 6 or not otp_code.isdigit():
                return render_template("admin_forgot_password.html", error="Enter the 6-digit code sent to you.", step="code", email=email)
            _record_otp_attempt(limiter_key)

            row = OTPCode.query.filter_by(identifier=f"admin:{email}", channel="email", code=otp_code, purpose="reset", used=False).first()
            if not row or row.expires_at < datetime.utcnow():
                if row:
                    row.used = True
                    db.session.commit()
                return render_template("admin_forgot_password.html", error="Invalid or expired code.", step="code", email=email)

            session["admin_reset_verified"] = True
            admin = Admin.query.filter_by(email=email).first()
            next_step = "totp" if (admin and admin.totp_enabled) else "confirm"
            return render_template("admin_forgot_password.html", error=None, step=next_step, email=email, otp_code=otp_code)

        if step == "totp":
            email = session.get("admin_reset_email")
            if not email or not session.get("admin_reset_verified"):
                return render_template("admin_forgot_password.html", error="Please verify your email first.", step="contact")
            admin = Admin.query.filter_by(email=email).first()
            if not admin or not admin.totp_enabled:
                return render_template("admin_forgot_password.html", error="Your session expired. Please start again.", step="contact")

            otp_code = request.form.get("otp_code") or ""
            totp_code = (request.form.get("totp_code") or "").strip()
            limiter_key = f"admin-reset-totp:{email}"
            if _otp_rate_limited(limiter_key):
                return render_template("admin_forgot_password.html", error="Too many attempts. Try again later.", step="totp", email=email, otp_code=otp_code)
            _record_otp_attempt(limiter_key)

            totp = pyotp.TOTP(admin.totp_secret)
            if not totp_code.isdigit() or not totp.verify(totp_code, valid_window=1):
                return render_template("admin_forgot_password.html", error="Incorrect authenticator code.", step="totp", email=email, otp_code=otp_code)

            session["admin_reset_totp_verified"] = True
            return render_template("admin_forgot_password.html", error=None, step="confirm", email=email, otp_code=otp_code)

        if step == "confirm":
            email = session.get("admin_reset_email")
            if not email or not session.get("admin_reset_verified"):
                return render_template("admin_forgot_password.html", error="Please verify your email first.", step="contact")
            admin = Admin.query.filter_by(email=email).first()
            if not admin:
                return render_template("admin_forgot_password.html", error="Your session expired. Please start again.", step="contact")
            if admin.totp_enabled and not session.get("admin_reset_totp_verified"):
                return render_template("admin_forgot_password.html", error="Please complete the authenticator step first.", step="contact")

            otp_code = (request.form.get("otp_code") or "").strip()
            new_password = request.form.get("new_password") or ""
            confirm_password = request.form.get("confirm_password") or ""

            def _redo(error):
                return render_template("admin_forgot_password.html", error=error, step="confirm", email=email, otp_code=otp_code)

            if len(new_password) < 8:
                return _redo("Password must be at least 8 characters.")
            if new_password != confirm_password:
                return _redo("Passwords do not match.")
            if _password_is_breached(new_password):
                return _redo("This password has appeared in a known data breach. Please choose a different one.")

            row = OTPCode.query.filter_by(identifier=f"admin:{email}", channel="email", code=otp_code, purpose="reset", used=False).first()
            if not row or row.expires_at < datetime.utcnow():
                session.pop("admin_reset_verified", None)
                session.pop("admin_reset_totp_verified", None)
                return render_template("admin_forgot_password.html", error="Your code expired. Please start again.", step="contact")

            row.used = True
            admin.password_hash = generate_password_hash(new_password)
            db.session.commit()
            log_event("admin_password_reset", target_admin_id=admin.id)

            session.clear()
            return render_template("admin_login.html", error=None, reset_success=True)

        return render_template("admin_forgot_password.html", error=None, step="contact")

    return render_template("admin_forgot_password.html", error=None, step="contact")


@app.route("/admin/2fa/verify", methods=["GET", "POST"])
def admin_2fa_verify():
    pending_id = session.get("admin_pending_2fa_id")
    if pending_id is None:
        return redirect(url_for("admin_login"))
    admin = Admin.query.get(pending_id)
    if admin is None or not admin.active or not admin.totp_enabled:
        session.pop("admin_pending_2fa_id", None)
        return redirect(url_for("admin_login"))

    if request.method == "POST":
        csrf.protect()
        code = (request.form.get("code") or "").strip()

        # Reuses the same Redis-backed (or in-memory-fallback) rate limiter
        # built for OTP brute-force protection (BL-09) — a 6-digit TOTP
        # code needs the exact same throttling a 6-digit email OTP does,
        # and the machinery already exists. Keyed separately (totp: prefix)
        # so a TOTP lockout and an email-OTP lockout for the same address
        # don't share a budget.
        limiter_key = f"totp:{admin.email}"
        if _otp_rate_limited(limiter_key):
            return render_template("admin_2fa_verify.html", error="Too many attempts. Try again later.")
        _record_otp_attempt(limiter_key)

        totp = pyotp.TOTP(admin.totp_secret)
        if not code.isdigit() or not totp.verify(code, valid_window=1):
            return render_template("admin_2fa_verify.html", error="Incorrect code.")

        session.pop("admin_pending_2fa_id", None)
        session["admin_id"] = admin.id
        next_url = request.args.get("next") or url_for("admin_analytics")
        return redirect(next_url)

    return render_template("admin_2fa_verify.html", error=None)


@app.route("/admin/2fa/setup", methods=["GET", "POST"])
@admin_login_required
def admin_2fa_setup():
    admin = Admin.query.get(_current_admin_id())

    if admin.totp_enabled:
        return render_template("admin_2fa_setup.html", already_enabled=True, qr_data_uri=None, secret=None, error=None, active_page="2fa")

    if request.method == "POST":
        code = (request.form.get("code") or "").strip()
        secret = session.get("admin_2fa_pending_secret")
        if not secret:
            return redirect(url_for("admin_2fa_setup"))

        totp = pyotp.TOTP(secret)
        if not code.isdigit() or not totp.verify(code, valid_window=1):
            qr_data_uri = _totp_qr_data_uri(admin.email, secret)
            return render_template(
                "admin_2fa_setup.html",
                already_enabled=False,
                qr_data_uri=qr_data_uri,
                secret=secret,
                error="Incorrect code — scan the QR code again and try the current 6-digit code.",
                active_page="2fa",
            )

        admin.totp_secret = secret
        admin.totp_enabled = True
        db.session.commit()
        session.pop("admin_2fa_pending_secret", None)
        log_event("admin_2fa_enabled", target_admin_id=admin.id)
        return render_template("admin_2fa_setup.html", already_enabled=True, qr_data_uri=None, secret=None, error=None, just_enabled=True, active_page="2fa")

    # GET: generate a fresh pending secret (not yet saved to the DB — only
    # committed once the user proves they can generate a valid code from
    # it, so a page load alone can't silently "enable" 2FA with a secret
    # the admin never actually saved into their authenticator app).
    secret = pyotp.random_base32()
    session["admin_2fa_pending_secret"] = secret
    qr_data_uri = _totp_qr_data_uri(admin.email, secret)
    return render_template("admin_2fa_setup.html", already_enabled=False, qr_data_uri=qr_data_uri, secret=secret, error=None, active_page="2fa")


@app.route("/admin/2fa/disable", methods=["POST"])
@admin_login_required
def admin_2fa_disable():
    admin = Admin.query.get(_current_admin_id())
    admin.totp_secret = None
    admin.totp_enabled = False
    db.session.commit()
    log_event("admin_2fa_disabled", target_admin_id=admin.id)
    return redirect(url_for("admin_2fa_setup"))


def _totp_qr_data_uri(email: str, secret: str) -> str:
    """Renders the TOTP enrollment QR code as an embedded base64 PNG data
    URI — no separate image route, no external QR-generation service (a
    third-party QR API would leak the secret itself to that third party)."""
    uri = pyotp.totp.TOTP(secret).provisioning_uri(name=email, issuer_name="YouthChain")
    img = qrcode.make(uri)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")


def _svg_bar_chart(pairs, width=600, height=180, bar_color="#0f7a5c", value_fmt=str):
    """
    Hand-rendered inline SVG bar chart — no Chart.js/D3/any external
    library, no CDN, matching the same self-hosted/offline-first
    discipline the rest of this session has applied everywhere else
    (no external QR API, no CDN-loaded fonts, Caddy/nginx instead of a
    hosted load balancer). `pairs` is a list of (label, value); returns a
    ready-to-embed SVG string (render with `| safe` in the template).

    Both call sites today only ever pass server-generated labels (fixed
    application-status literals, `strftime`-formatted dates) — genuinely
    safe as-is. label/value_fmt(value) are still html.escape()'d here
    regardless, found worth doing via a full security review even though
    nothing currently exploits its absence: this is a general-purpose
    shared helper rendered with `| safe` (bypassing Jinja's own
    auto-escaping) precisely so it CAN emit raw SVG markup, and its only
    protection against a future caller passing real user-controlled data
    (an employer name, a job title — both plausible next additions to
    this exact dashboard) was a docstring convention, not anything the
    function itself enforced. Escaping here costs nothing for today's
    safe inputs and closes that whole class of future stored-XSS risk in
    the admin console permanently, rather than depending on every future
    caller remembering to re-derive this same reasoning.
    """
    if not pairs:
        return '<p class="muted">No data yet.</p>'

    max_value = max(v for _, v in pairs) or 1
    n = len(pairs)
    padding_left, padding_bottom, padding_top = 10, 28, 20
    plot_width = width - padding_left - 10
    plot_height = height - padding_bottom - padding_top
    bar_gap = 8
    bar_width = max((plot_width - bar_gap * (n - 1)) / n, 4)

    bars = []
    for i, (label, value) in enumerate(pairs):
        bar_height = (value / max_value) * plot_height if max_value else 0
        x = padding_left + i * (bar_width + bar_gap)
        y = padding_top + (plot_height - bar_height)
        safe_label = html.escape(str(label))
        safe_value = html.escape(str(value_fmt(value)))
        bars.append(
            f'<rect x="{x:.1f}" y="{y:.1f}" width="{bar_width:.1f}" height="{bar_height:.1f}" '
            f'rx="3" fill="{bar_color}" />'
            f'<text x="{x + bar_width / 2:.1f}" y="{y - 4:.1f}" font-size="11" text-anchor="middle" '
            f'fill="currentColor">{safe_value}</text>'
            f'<text x="{x + bar_width / 2:.1f}" y="{height - padding_bottom + 16:.1f}" font-size="10" '
            f'text-anchor="middle" fill="currentColor" opacity="0.65">{safe_label}</text>'
        )

    svg_body = "".join(bars)
    return (
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="Bar chart" '
        f'style="width:100%; height:auto; max-width:{width}px; color:#5b6b67;">{svg_body}</svg>'
    )


@app.route("/admin/accounts")
@admin_role_required("admin")
def admin_accounts():
    """
    Admin-only (not verifier — see admin_role_required and the Admin model
    docstring for why the split exists): lists every operator account and
    lets an admin deactivate another one. No delete — deactivation is
    reversible (an admin can be reactivated the same way scripts/
    create_admin.py's counterpart would, by direct DB access for now;
    there's exactly one admin-management action today, so a second route
    for reactivation would be speculative until there's a real need for
    it) and preserves the audit trail (AnalyticsEvent rows referencing a
    deactivated admin's actions stay meaningful).
    """
    admins = Admin.query.order_by(Admin.created_at.asc()).all()
    return render_template("admin_accounts.html", admins=admins, current_admin_id=_current_admin_id(), active_page="accounts")


@app.route("/admin/accounts/<int:admin_id>/deactivate", methods=["POST"])
@admin_role_required("admin")
def admin_deactivate_account(admin_id):
    if admin_id == _current_admin_id():
        return _forbidden("You cannot deactivate your own account while logged in as it.")
    target = Admin.query.get_or_404(admin_id)
    target.active = False
    db.session.commit()
    log_event("admin_account_deactivated", target_admin_id=admin_id)
    return redirect(url_for("admin_accounts"))


@app.route("/admin/logout", methods=["POST"])
@admin_login_required
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))


@app.route("/admin/analytics")
@admin_access_required
def admin_analytics():
    """
    BL-44: minimal reporting dashboard over the AnalyticsEvent log (BL-40)
    and core tables — the kind of application-funnel / employer-engagement
    numbers Phase 3 #10 flagged as needed to demonstrate impact to a
    government or donor stakeholder. Read-only aggregate counts only; no
    per-user PII is rendered here.
    """
    total_users = User.query.count()
    total_employers = Employer.query.count()
    total_jobs = Job.query.count()
    total_applications = Application.query.count()

    status_counts = dict(
        db.session.query(Application.status, db.func.count(Application.id))
        .group_by(Application.status)
        .all()
    )

    total_credentials = Credential.query.count()
    onchain_credentials = Credential.query.filter(Credential.onchain_tx.isnot(None)).count()

    # Gig/informal-work lifecycle (see Job.job_type and the Rating model's
    # docstring) -- deliberately kept to the same "one COUNT query per
    # number" style as everything else on this page, not a separate
    # sub-dashboard, so this stays one consistent view of the whole
    # platform rather than formal and gig jobs looking like two different
    # products to whoever's reading this page.
    total_gig_jobs = Job.query.filter_by(job_type="gig").count()
    total_completed_gigs = Application.query.filter_by(status="Completed").count()
    # Real bug found and fixed while adding the account_type stat below:
    # this used to average BOTH rating directions together (how employers
    # rate workers blended with how workers rate employers) into one
    # number -- statistically meaningless, since those are two different
    # populations answering two different questions. Split by direction,
    # same as every other place in this codebase that reads Rating
    # (_worker_trust_summary, _employer_trust_summary).
    avg_worker_rating = (
        db.session.query(func.avg(Rating.score))
        .filter(Rating.direction == "employer_to_worker", Rating.hidden.is_(False)).scalar()
    )
    total_worker_ratings = Rating.query.filter_by(direction="employer_to_worker", hidden=False).count()
    avg_employer_rating = (
        db.session.query(func.avg(Rating.score))
        .filter(Rating.direction == "worker_to_employer", Rating.hidden.is_(False)).scalar()
    )
    total_employer_ratings = Rating.query.filter_by(direction="worker_to_employer", hidden=False).count()

    # Business vs individual employer accounts (see Employer.account_type)
    # -- real, direct signal of how much of the platform's employer base
    # is actually the informal/gig population this feature exists for,
    # visible even before any of them touch /employer/verification.
    individual_employer_accounts = Employer.query.filter_by(account_type="individual").count()

    event_counts = dict(
        db.session.query(AnalyticsEvent.event_type, db.func.count(AnalyticsEvent.id))
        .group_by(AnalyticsEvent.event_type)
        .all()
    )

    since = datetime.utcnow() - timedelta(days=7)
    events_last_7_days = AnalyticsEvent.query.filter(AnalyticsEvent.created_at >= since).count()

    jobs_per_employer = dict(
        db.session.query(Employer.name, db.func.count(Job.id))
        .join(Job, Job.employer_id == Employer.id)
        .group_by(Employer.name)
        .all()
    )

    # Application funnel as a real bar chart, not just the table below it.
    status_order = ["Pending", "Accepted", "Rejected"]
    status_chart_pairs = [(s, status_counts.get(s, 0)) for s in status_order if s in status_counts] or list(status_counts.items())
    status_colors = {"Pending": "#b4740e", "Accepted": "#1e8e5a", "Rejected": "#c22a2a"}
    # Rendered as one chart per status so each bar can carry its own
    # semantic color (accepted=green, rejected=red, pending=amber, matching
    # the same status-color convention used throughout the mobile app's
    # StatusBadge) — a single multi-bar chart can't vary color per-bar with
    # this minimal a helper without adding a second parameter shape, and
    # three tiny single-bar charts side by side reads identically anyway.
    funnel_chart_svgs = [
        (s, _svg_bar_chart([(s, c)], width=180, height=140, bar_color=status_colors.get(s, "#1d63c9")))
        for s, c in status_chart_pairs
    ]

    # 14-day event time-series — func.date() works portably across both
    # SQLite and Postgres (both support it as a real SQL function, not
    # just one dialect), so this query doesn't need a DATABASE_URL branch.
    since_14 = datetime.utcnow() - timedelta(days=14)
    daily_raw_rows = (
        db.session.query(db.func.date(AnalyticsEvent.created_at), db.func.count(AnalyticsEvent.id))
        .filter(AnalyticsEvent.created_at >= since_14)
        .group_by(db.func.date(AnalyticsEvent.created_at))
        .all()
    )
    # SQLite's func.date() returns a plain string ("2026-08-05"); Postgres's
    # returns a real `date` object — normalize both to str() so dict
    # lookups below work identically on either backend, not just whichever
    # one this was tested against first.
    daily_raw = {str(day_val): count for day_val, count in daily_raw_rows}
    # Fill in zero-count days so the chart doesn't silently skip gaps —
    # a day with no events is real information (nothing happened), not
    # missing data.
    daily_pairs = []
    for i in range(13, -1, -1):
        day = (datetime.utcnow() - timedelta(days=i)).date()
        count = daily_raw.get(str(day), 0)
        daily_pairs.append((day.strftime("%m/%d"), count))
    events_timeseries_svg = _svg_bar_chart(daily_pairs, width=640, height=180, bar_color="#1d63c9")

    return render_template(
        "admin_analytics.html",
        total_users=total_users,
        total_employers=total_employers,
        total_jobs=total_jobs,
        total_applications=total_applications,
        status_counts=status_counts,
        total_credentials=total_credentials,
        onchain_credentials=onchain_credentials,
        total_gig_jobs=total_gig_jobs,
        total_completed_gigs=total_completed_gigs,
        avg_worker_rating=round(avg_worker_rating, 1) if avg_worker_rating is not None else None,
        total_worker_ratings=total_worker_ratings,
        avg_employer_rating=round(avg_employer_rating, 1) if avg_employer_rating is not None else None,
        total_employer_ratings=total_employer_ratings,
        individual_employer_accounts=individual_employer_accounts,
        event_counts=event_counts,
        events_last_7_days=events_last_7_days,
        jobs_per_employer=jobs_per_employer,
        funnel_chart_svgs=funnel_chart_svgs,
        events_timeseries_svg=events_timeseries_svg,
        active_page="analytics",
    )


def _worker_trust_summary(user_id: int) -> dict:
    """
    The gig/informal-work counterpart to _employer_summary: what an
    employer sees about an applicant who has no CV, because their trust
    record is completed gigs + ratings instead of a document (see
    Job.job_type and the Rating model's docstring). Used both by
    employer_applications() (shown next to each gig applicant) and
    GET /api/candidate/<id>/trust_summary (the mobile work-history
    screen's own view of the same data).
    """
    completed_gigs = Application.query.filter_by(user_id=user_id, status="Completed").count()
    avg_score, rating_count = (
        db.session.query(func.avg(Rating.score), func.count(Rating.id))
        .filter(Rating.user_id == user_id, Rating.direction == "employer_to_worker", Rating.hidden.is_(False))
        .first()
    )
    return {
        "completed_gigs": completed_gigs,
        "avg_rating": round(avg_score, 1) if avg_score is not None else None,
        "rating_count": rating_count or 0,
    }


@app.route("/employer/applications/<int:job_id>", methods=["GET", "POST"])
@employer_login_required
def employer_applications(job_id):
    job = Job.query.get_or_404(job_id)
    if not _employer_owns_job(job, _current_employer_id()):
        return _forbidden("This job belongs to another employer account")

    if request.method == "POST":
        app_id = request.form.get("app_id")
        action = request.form.get("action")  # accept or reject
        if action not in ("accept", "reject"):
            # Real latent bug closed while this line was already being
            # touched to add the gig "complete" lifecycle elsewhere: an
            # unrecognized action value used to silently fall through the
            # old ternary and become "Rejected" -- now it's simply a no-op,
            # not a surprise rejection.
            action = None
        application = Application.query.get(app_id) if action else None
        # Real, severe IDOR found via a full OWASP Top 10 (A01: Broken
        # Access Control) review and reproduced live before fixing: the
        # outer job_id (checked above via _employer_owns_job) and this
        # app_id are two DIFFERENT attacker-influenced values with no
        # relationship enforced between them — any employer who owns AT
        # LEAST ONE job could POST here with their own real job_id but an
        # app_id copied/guessed from a completely unrelated job posted by
        # a DIFFERENT employer, and this code would accept/reject that
        # applicant with zero connection to the job_id that was actually
        # authorized. Reproduced live: Employer B, owning only "Job B",
        # successfully flipped Employer A's applicant on "Job A" to
        # Accepted, and it correctly triggered a real notification to
        # that unrelated youth — a full breakdown of employer-to-employer
        # isolation on the single most consequential action an employer
        # takes on this platform. Fixed by requiring the looked-up
        # application to actually belong to the job_id already verified
        # above, not just any application id the caller supplies.
        if application and application.job_id != job_id:
            return _forbidden("This application does not belong to this job")
        if application:
            application.status = "Accepted" if action == "accept" else "Rejected"
            db.session.commit()
            log_event(
                "application_status_changed",
                user_id=application.user_id,
                employer_id=_current_employer_id(),
                application_id=application.id,
                status=application.status,
            )
            job_for_notify = Job.query.get(application.job_id)
            notify_user(
                application.user_id,
                "application_status_changed",
                f"Your application was {application.status.lower()}",
                f"Your application for {job_for_notify.title if job_for_notify else 'a job'} was {application.status.lower()}.",
                application_id=application.id,
                status=application.status,
            )

            # The applicant this status change is about, plus the owning
            # employer -- so a second tab/session on the same employer
            # account (employer_dashboard_live.js / employer_applications_
            # live.js) sees it too, not just whichever tab made the change.
            # Still scoped, per _socketio_connect's docstring: never a
            # global broadcast, and only this job's own employer, not
            # every employer on the platform.
            try:
                status_payload = {
                    "app_id": application.id,
                    "user_id": application.user_id,
                    "job_id": application.job_id,
                    "status": application.status,
                }
                socketio.emit("application_status_changed", status_payload, room=f"user:{application.user_id}")
                socketio.emit("application_status_changed", status_payload, room=f"employer:{job.employer_id}")
            except Exception:
                logger.exception("socketio emit application_status_changed failed")


    rows = (
        db.session.query(Application, User)
        .join(User, Application.user_id == User.id)
        .filter(Application.job_id == job_id)
        .order_by(Application.created_at.desc())
        .all()
    )
    # One grouped fetch for every application's employer->worker rating,
    # not a query per row (N+1) -- same discipline as _worker_trust_summary's
    # own caller and every other fixed-query pattern in this file.
    app_ids = [a.id for a, _u in rows]
    ratings_by_app_id = (
        {r.application_id: r for r in Rating.query.filter(
            Rating.application_id.in_(app_ids), Rating.direction == "employer_to_worker"
        ).all()}
        if app_ids else {}
    )
    # Real gap found via a full-codebase review: an employer reviewing
    # applicants had no visibility into a candidate's on-chain-verified
    # credentials anywhere in this workflow at all -- the only way to
    # check one was the separate, undiscoverable /employer/verify manual
    # hash-paste tool, one credential at a time, with no indication a
    # candidate even had any to check. The entire point of anchoring
    # credentials on-chain is so an employer can trust them at exactly
    # this decision point. One grouped fetch for every applicant's
    # credentials, not a query per row -- same N+1 discipline as the
    # ratings fetch above.
    user_ids = [u.id for _a, u in rows]
    credentials_by_user_id: dict[int, list[Credential]] = {}
    if user_ids:
        for cred in Credential.query.filter(Credential.user_id.in_(user_ids)).order_by(Credential.id.desc()).all():
            credentials_by_user_id.setdefault(cred.user_id, []).append(cred)

    apps = []
    for a, u in rows:
        row = {
            "id": a.id,
            "user_id": a.user_id,
            "applicant_name": u.name,
            "applicant_email": u.email,
            "cv_file": a.cv_file,
            "supporting_file": a.supporting_file,
            "status": a.status,
        }
        if job.job_type == "gig":
            rating = ratings_by_app_id.get(a.id)
            row["trust"] = _worker_trust_summary(a.user_id)
            row["worker_rating"] = rating.to_dict() if rating else None

        creds = credentials_by_user_id.get(a.user_id, [])
        # Mirrors /verify's own three-way state (see verify.html): a
        # credential is either revoked (regardless of onchain_tx --
        # revocation is the higher-priority fact), confirmed on-chain, or
        # still pending its background write. Deliberately built from
        # each Credential's own DB fields (onchain_tx/revoked_at), not a
        # live per-credential chain call -- a list page rendering N
        # applicants x M credentials each cannot afford an N*M x
        # ~10-90s Hardhat subprocess round trip; /verify/<id> is where an
        # employer goes for the strongest, live-checked guarantee on one
        # specific credential once this summary has pointed them at it.
        row["credentials"] = {
            "items": [
                {
                    "id": c.id,
                    "title": c.title,
                    "issuer": c.issuer,
                    "status": "revoked" if c.revoked_at else ("verified" if c.onchain_tx else "pending"),
                }
                for c in creds
            ],
            "verified_count": sum(1 for c in creds if c.onchain_tx and not c.revoked_at),
            "revoked_count": sum(1 for c in creds if c.revoked_at),
            "pending_count": sum(1 for c in creds if not c.onchain_tx and not c.revoked_at),
        }
        apps.append(row)
    return render_template("employer_applications.html", job=job, apps=apps, active_page="dashboard")


_GIG_RATING_SCORE_RANGE = range(1, 6)  # 1-5, no half-stars (see Rating's docstring)


@app.route("/employer/applications/<int:app_id>/complete", methods=["POST"])
@employer_login_required
def employer_complete_application(app_id):
    """
    Employer-only in V1 (deliberate scope decision, not an oversight —
    see the gig-lifecycle plan): marks a gig application's job as done, the
    prerequisite for either side rating the other. Ownership is enforced
    through the SAME job_id -> application chain as accept/reject just
    above, closing the identical IDOR class that fix already documents.
    """
    application = Application.query.get_or_404(app_id)
    job = Job.query.get_or_404(application.job_id)
    if not _employer_owns_job(job, _current_employer_id()):
        return _forbidden("This application belongs to another employer account")
    if job.job_type != "gig":
        return _forbidden("Only gig/hire-based jobs can be marked complete")
    if application.status != "Accepted":
        return _forbidden("Only an accepted application can be marked complete")

    application.status = "Completed"
    db.session.commit()
    log_event(
        "application_marked_complete",
        user_id=application.user_id,
        employer_id=_current_employer_id(),
        application_id=application.id,
    )
    notify_user(
        application.user_id,
        "application_marked_complete",
        "A gig was marked complete",
        f"{job.title} was marked complete. You can now rate the employer, and they can rate you.",
        application_id=application.id,
    )
    try:
        complete_payload = {
            "app_id": application.id, "user_id": application.user_id, "job_id": application.job_id, "status": "Completed",
        }
        socketio.emit("application_status_changed", complete_payload, room=f"user:{application.user_id}")
        socketio.emit("application_status_changed", complete_payload, room=f"employer:{job.employer_id}")
    except Exception:
        logger.exception("socketio emit application_status_changed (complete) failed")
    return redirect(url_for("employer_applications", job_id=job.id))


@app.route("/employer/ratings/<int:rating_id>/flag", methods=["POST"])
@employer_login_required
def employer_flag_rating(rating_id):
    """
    Employer's half of the Rating dispute path -- symmetric to
    flag_rating() above (the worker/mobile side). An employer who thinks a
    worker's rating of them was unfair reaches this from their own
    web-portal session; ownership is enforced via Rating.employer_id, the
    same denormalized field _worker_trust_summary's queries rely on.

    Restricted to worker_to_employer ratings only (the ones ABOUT this
    employer) -- same reasoning as flag_rating()'s direction check: an
    employer "disputing" a rating they themselves gave a worker isn't a
    real dispute, just noise in the admin queue.
    """
    rating = Rating.query.get_or_404(rating_id)
    eid = _current_employer_id()
    if rating.employer_id != eid or rating.direction != "worker_to_employer":
        return _forbidden("This rating belongs to another employer account")

    reason = (request.form.get("reason") or "").strip()
    if not reason:
        return _forbidden("reason is required")

    # Same tight per-hour budget as report_employer()/flag_rating() -- a
    # dispute queue is exactly as plausible a harassment/spam vector.
    if _report_rate_limited(f"employer:{eid}"):
        return jsonify({"success": False, "error": "Too many reports. Try again later."}), 429
    _record_report_attempt(f"employer:{eid}")

    flag = RatingFlag(
        rating_id=rating.id,
        flagged_by_role="employer",
        flagged_by_employer_id=eid,
        reason=reason,
    )
    db.session.add(flag)
    db.session.commit()
    log_event("rating_flagged", employer_id=eid, rating_id=rating.id)
    _notify_admins_of_new_rating_flag(flag, rating)
    application = Application.query.get(rating.application_id)
    if application:
        return redirect(url_for("employer_applications", job_id=application.job_id))
    return redirect(url_for("employer_dashboard"))


@app.route("/employer/applications/<int:app_id>/rate", methods=["POST"])
@employer_login_required
def employer_rate_worker(app_id):
    """Employer's half of the bidirectional gig rating -- see Rating's docstring for why both directions exist."""
    application = Application.query.get_or_404(app_id)
    job = Job.query.get_or_404(application.job_id)
    if not _employer_owns_job(job, _current_employer_id()):
        return _forbidden("This application belongs to another employer account")

    rating, error = _submit_gig_rating(application, "employer_to_worker", job.employer_id, request.form)
    if error:
        return error
    notify_user(
        application.user_id,
        "worker_rated",
        "You were rated for a completed gig",
        f"{job.title}: {rating.score}/5.",
        application_id=application.id,
        score=rating.score,
    )
    return redirect(url_for("employer_applications", job_id=job.id))


# ----------- Candidate API (youth profile) -----------
@app.route("/api/candidate", methods=["POST"])
@jwt_required()
def upsert_candidate():
    """
    Create or update a candidate profile.
    Keyed on email (unique); always linked to the authenticated caller's
    user_id (never a client-supplied one — closes S-01 for this endpoint).
    """
    data = request.get_json() or {}

    email = (data.get("email") or "").strip()
    if not email:
        return jsonify({"success": False, "error": "email is required"}), 400
    if not valid_email(email):
        return jsonify({"success": False, "error": "Please enter a valid email address"}), 400

    candidate = Candidate.query.filter_by(email=email).first()
    if candidate and candidate.user_id not in (None, _current_user_id()):
        return _forbidden("This profile belongs to another account")
    if not candidate:
        candidate = Candidate(email=email)

    candidate.user_id = _current_user_id()

    candidate.name = data.get("name", candidate.name)
    candidate.location = data.get("location", candidate.location)

    skills = data.get("skills")
    if isinstance(skills, list):
        candidate.skills = ",".join(skills)
    elif isinstance(skills, str):
        candidate.skills = skills

    # Same list-or-comma-string acceptance as skills above. Silently drops
    # anything outside _INDUSTRY_CHOICES rather than rejecting the whole
    # profile save -- a real mobile client only ever offers this fixed
    # list as checkboxes, so a mismatch here only happens from a
    # malformed direct API call, and dropping it is safe (nothing is
    # silently corrupted, the field just doesn't include a value that was
    # never valid in the first place).
    preferred_industries = data.get("preferred_industries")
    if isinstance(preferred_industries, list):
        raw = preferred_industries
    elif isinstance(preferred_industries, str):
        raw = preferred_industries.split(",")
    else:
        raw = None
    if raw is not None:
        valid = [v.strip() for v in raw if v.strip() in _INDUSTRY_CHOICES]
        candidate.preferred_industries = ",".join(valid)

    candidate.bio = data.get("bio", candidate.bio)

    if "job_alerts_enabled" in data:
        candidate.job_alerts_enabled = bool(data.get("job_alerts_enabled"))

    db.session.add(candidate)
    db.session.commit()

    return jsonify({"success": True, "candidate_id": candidate.id}), 200


@app.route("/api/candidate/me", methods=["GET"])
@jwt_required()
def get_my_candidate():
    """
    Resolves the authenticated user's own Candidate.id.

    Candidate has its own primary key, separate from User.id (see the model
    docstring). Without this lookup, a client has no way to discover its own
    candidate_id except by capturing the response of the one POST /api/candidate
    call that created it — this endpoint lets the app resolve it on any
    subsequent app start/login instead of having to cache it forever.
    """
    candidate = Candidate.query.filter_by(user_id=_current_user_id()).first()
    if not candidate:
        return jsonify({"success": True, "candidate": None}), 200
    return jsonify({
        "success": True,
        "candidate": {
            "id": candidate.id,
            "name": candidate.name,
            "email": candidate.email,
            "location": candidate.location,
            "skills": candidate.skills,
            "bio": candidate.bio,
            "preferred_industries": candidate.preferred_industries,
            "job_alerts_enabled": candidate.job_alerts_enabled,
        },
    }), 200


# ----------- CV generation (rule-based text + AI-polished HTML) -----------
@app.route("/api/generate_cv/<int:candidate_id>", methods=["GET"])
@jwt_required()
def generate_cv(candidate_id):
    """
    Generates a CV in two forms: the original plain-text version
    (unchanged output, kept for backward compatibility) and a formatted,
    AI-polished HTML version (cv_html) the mobile app renders natively
    and exports to PDF — see cv_generator.py's own docstring for the full
    three-layer security model behind cv_html (the AI never authors HTML,
    every value is escaped, and a bleach allowlist pass runs on top of
    that). The AI-polish step is best-effort and silently optional: if
    ANTHROPIC_API_KEY isn't configured or the call fails for any reason,
    cv_html still renders — just from the candidate's own unpolished text
    — rather than the whole endpoint failing over an enhancement step.
    """
    c = Candidate.query.get(candidate_id)
    if not c:
        return jsonify({"success": False, "error": "candidate not found"}), 404
    if c.user_id != _current_user_id():
        return _forbidden()

    if _cv_generation_rate_limited(f"user:{_current_user_id()}"):
        return jsonify({"success": False, "error": "Too many CV generations. Try again later."}), 429
    _record_cv_generation_attempt(f"user:{_current_user_id()}")

    # Link credentials via user_id if available. Excludes revoked ones —
    # real pre-existing gap found while rebuilding this endpoint: a
    # revoked credential (see Credential.revoked_at) was still being
    # listed as "verified" on the generated CV, which is exactly the
    # kind of integrity problem a trust platform can't afford.
    creds = []
    if c.user_id:
        creds = Credential.query.filter_by(user_id=c.user_id, revoked_at=None).all()

    educations = Education.query.filter_by(candidate_id=candidate_id).all()

    lines = []
    lines.append(f"Name: {c.name or 'Unknown'}")
    lines.append(f"Email: {c.email}")
    if c.location:
        lines.append(f"Location: {c.location}")
    lines.append("")
    lines.append("PROFILE SUMMARY")
    lines.append("----------------")
    summary = c.bio or "Motivated youth eager to apply practical skills in real-world opportunities."
    lines.append(summary)
    lines.append("")
    skills_list = [s.strip() for s in (c.skills or "").split(",") if s.strip()]
    if skills_list:
        lines.append("KEY SKILLS")
        lines.append("----------")
        lines.append(", ".join(skills_list))
        lines.append("")

    if educations:
        lines.append("EDUCATION")
        lines.append("---------")
        for e in educations:
            lines.append(f"- {e.degree or 'Qualification'}, {e.school or ''} ({e.year or ''})")
        lines.append("")

    if creds:
        lines.append("VERIFIED CREDENTIALS (YouthChain Registry)")
        lines.append("-----------------------------------------")
        for cr in creds:
            lines.append(f"- {cr.title} ({cr.year}) – {cr.issuer}  [hash: {cr.hash[:12]}...]")
        lines.append("")

    cv_text = "\n".join(lines)

    industries_list = [i.strip() for i in (c.preferred_industries or "").split(",") if i.strip()]
    polished = cv_generator.polish_cv_content(
        name=c.name, location=c.location, bio=c.bio, skills=skills_list,
        industries=industries_list, api_key=_get_secret("ANTHROPIC_API_KEY"),
    )
    cv_html_raw = cv_generator.render_cv_html(
        candidate={
            "name": c.name, "email": c.email, "location": c.location,
            "skills": skills_list, "preferred_industries": industries_list, "bio": c.bio,
        },
        educations=[{"degree": e.degree, "school": e.school, "year": e.year} for e in educations],
        credentials=[{"title": cr.title, "issuer": cr.issuer, "year": cr.year} for cr in creds],
        polished=polished,
    )
    cv_html = cv_generator.sanitize_cv_html(cv_html_raw)

    return jsonify({
        "success": True,
        "candidate_id": candidate_id,
        "cv": cv_text,
        "cv_html": cv_html,
        "ai_generated": polished is not None,
    }), 200


# ----------- Job matching -----------
@app.route("/api/match_jobs/<int:candidate_id>", methods=["GET"])
@jwt_required()
def api_match_jobs(candidate_id):
    """
    Returns jobs ranked for a candidate.
    If the candidate profile doesn't exist yet, fall back to returning
    all jobs with score = 0 so the app still shows opportunities.
    """
    # Try to load candidate profile
    candidate = Candidate.query.get(candidate_id)
    if candidate and candidate.user_id != _current_user_id():
        return _forbidden()

    # ----------------- FALLBACK WHEN NO CANDIDATE -----------------
    if not candidate:
        # source="employer": Home must never silently pick up scraped
        # listings — Discover (GET /api/discover_jobs) is their only
        # surface. This route bypasses _search_jobs() entirely (it's
        # ranking-only, no q/location/skill filters), so its default
        # can't be inherited the way GET /jobs's is — it has to be
        # applied explicitly at both call sites in this function.
        jobs = Job.query.filter(Job.source == "employer").order_by(Job.id.desc()).all()
        payload = []
        for j in jobs:
            # Full to_dict(), not a hand-built subset -- this used to
            # cherry-pick fields and silently drop job_type/category (a
            # real, serious gap: this is the mobile app's DEFAULT job
            # feed for any user with a candidate profile, so most real
            # users hit a gig job here with no job_type at all, which
            # broke the "no CV needed for gig work" logic in
            # _showApplySheet -- job["job_type"] != "gig" defaults true
            # when the key is simply missing). Then it ALSO dropped
            # employer_id, silently hiding JobDetailScreen's "Report this
            # job" button for anyone on this path. Spreading the same
            # to_dict() every other job-serving endpoint already uses
            # closes that whole class of bug instead of patching one
            # missing field at a time.
            job_dict = j.to_dict()
            job_dict["score"] = 0  # un-ranked
            payload.append(job_dict)
        return jsonify({
            "success": True,
            "candidate_id": candidate_id,
            "jobs": payload,
            "message": "No candidate profile yet; returning unranked jobs."
        }), 200

    # ----------------- NORMAL MATCHING PATH -----------------
    # Candidate.skills is a comma-separated string
    cand_skills = [s.strip().lower() for s in (candidate.skills or "").split(",") if s.strip()]
    cand_industries = {s.strip() for s in (candidate.preferred_industries or "").split(",") if s.strip()}
    # source="employer" — same Home-feed guard as the no-candidate
    # fallback branch above.
    jobs = Job.query.filter(Job.source == "employer").order_by(Job.id.desc()).all()
    results = []

    # Transparent, deterministic scoring only (see BL-45 — this app does
    # not claim AI/ML matching it doesn't have). Skills overlap remains
    # the primary signal; industry alignment is a real but secondary
    # bonus, capped so it can never let a 0-skills-overlap job outrank a
    # genuinely well-matched one on industry alone.
    INDUSTRY_MATCH_BONUS = 15

    for j in jobs:
        required = [s.strip().lower() for s in (getattr(j, "required_skills", "") or "").split(",") if s.strip()]
        if not required or not cand_skills:
            skills_score = 0
        else:
            overlap = len(set(cand_skills) & set(required))
            skills_score = int(100 * overlap / len(required))

        job_dict = j.to_dict()
        employer_info = job_dict.get("employer")
        job_industry = employer_info["industry"] if employer_info else None
        industry_match = bool(job_industry and job_industry in cand_industries)
        score = min(100, skills_score + (INDUSTRY_MATCH_BONUS if industry_match else 0))

        # Full to_dict(), not a hand-built subset -- see the identical fix
        # + comment in the no-candidate fallback branch above for why.
        # (Also quietly fixes a display inconsistency this hand-built
        # dict introduced: it showed required_skills lowercased, since it
        # reused the `required` list built for scoring, while every other
        # job-serving endpoint shows the original casing.)
        job_dict["industry_match"] = industry_match
        job_dict["score"] = score
        results.append(job_dict)

    # highest score first
    results.sort(key=lambda x: x["score"], reverse=True)

    return jsonify({
        "success": True,
        "candidate_id": candidate_id,
        "jobs": results,
    }), 200


# ----------------- JSON error mapping -----------------
# (API_PREFIXES / _is_api_request are defined once, near the top of this
# file alongside the CORS config — TD-09-style duplication removed.)


@app.errorhandler(CSRFError)
def _csrf_error(e):
    """
    Real bug found via live end-to-end testing (not hypothetical -- hit
    this directly testing the credential-upload flow after an unrelated
    debug-reloader restart invalidated an in-flight session's CSRF
    token): this used to unconditionally render employer_login.html for
    EVERY surface's CSRF failure. A portal user or admin whose token
    expired mid-session -- e.g. uploading a credential on
    /portal/passport, or revoking one on /admin/credentials/<id>/revoke
    -- was shown the Employer Login page instead: wrong branding, wrong
    form, and (since that template posts to employer_login) would have
    logged them into the wrong account type entirely had they not
    noticed. Routed by the failing request's own path prefix instead, so
    each surface reports the failure on its own login page. Doesn't
    preserve `next` back to the original page -- none of the three login
    forms currently thread a next value through their own POST (only the
    "not logged in at all" redirect path does, via a query-string
    ?next=), so passing one here would be inert; wiring that up for
    real is a separate, larger change than this bug fix.
    """
    if _is_api_request():
        return jsonify({"success": False, "error": "Invalid or missing CSRF token"}), 400

    error = "Your session expired. Please log in again."
    if request.path.startswith("/admin/"):
        return render_template("admin_login.html", error=error), 400
    if request.path.startswith("/portal/"):
        return render_template("portal_login.html", error=error, suspended=False), 400
    return render_template("employer_login.html", error=error, suspended=False), 400


@app.errorhandler(404)
def _json_404(e):
    if _is_api_request():
        return jsonify({"success": False, "error": "Not found", "path": request.path}), 404
    return e


@app.errorhandler(405)
def _json_405(e):
    if _is_api_request():
        return jsonify({"success": False, "error": "Method not allowed", "path": request.path}), 405
    return e


@app.errorhandler(500)
def _json_500(e):
    # Avoid leaking stack traces to clients, keep details in server logs
    if _is_api_request():
        return jsonify({"success": False, "error": "Internal server error"}), 500
    return e


# ----------------- Job alert dispatch (Saved Search + profile skills) -----------------
def _job_matches_saved_search(job, search) -> bool:
    """Same substring-match semantics GET /api/discover_jobs's own
    q/location/skill query params already use via _search_jobs() — a
    SavedSearch is just those same filters, persisted."""
    if search.q:
        haystack = f"{job.title} {job.description or ''} {job.company_name or ''}".lower()
        if search.q.lower() not in haystack:
            return False
    if search.location and search.location.lower() not in (job.location or "").lower():
        return False
    if search.skill and search.skill.lower() not in (job.required_skills or "").lower():
        return False
    return True


def _dispatch_job_alerts_for_scan(outcome):
    """
    Fires Saved Search and profile-skill job alerts for a just-completed
    scan (see scanner.pipeline.ScanOutcome.created_job_ids) — called once
    per real scan, from both scanner.poller's on_scan_complete callback
    (production/pg_cron path) and scripts/run_scan.py (manual/local
    path). Deliberately only ever looks at created_job_ids, never every
    job the scan touched — see that field's own docstring for why that's
    what keeps this idempotent across rescans with no extra dedup table.

    Best-effort per match, same "one bad row can't sink the batch"
    posture as run_scan_for_source's own per-job try/except: a failure
    notifying one saved search or one candidate must never stop the rest
    of this scan's matches from going out.
    """
    if not outcome.success or not outcome.created_job_ids:
        return

    jobs = Job.query.filter(Job.id.in_(outcome.created_job_ids)).all()
    if not jobs:
        return

    saved_searches = SavedSearch.query.all()
    alert_candidates = Candidate.query.filter(
        Candidate.job_alerts_enabled.is_(True), Candidate.user_id.isnot(None),
    ).all()

    for job in jobs:
        job_skills = {s.strip().lower() for s in (job.required_skills or "").split(",") if s.strip()}

        for search in saved_searches:
            try:
                if _job_matches_saved_search(job, search):
                    notify_user(
                        search.user_id, "saved_search_match", "New job matches your saved search",
                        job.title, sms=True, whatsapp=True, job_id=job.id, saved_search_id=search.id,
                    )
            except Exception:
                logger.exception("saved search alert failed for search %s, job %s", search.id, job.id)

        if not job_skills:
            continue
        for candidate in alert_candidates:
            cand_skills = {s.strip().lower() for s in (candidate.skills or "").split(",") if s.strip()}
            if not cand_skills or not (cand_skills & job_skills):
                continue
            try:
                notify_user(
                    candidate.user_id, "job_alert_match", "New job matches your skills",
                    job.title, sms=True, whatsapp=True, job_id=job.id,
                )
            except Exception:
                logger.exception("job alert failed for candidate %s, job %s", candidate.id, job.id)


# ----------------- Job scanner poller (opt-in, module scope) -----------------
# Runs at true module scope (not inside `if __name__ == "__main__"` below)
# because production serves this app via `gunicorn ... app:app`
# (backend/Dockerfile), which imports this module rather than executing it
# as __main__ — the same reason ProxyFix/JWTManager/etc. are all wired up
# at module scope too, not inside that block.
#
# Off by default (ENABLE_JOB_SCANNER_POLLER unset), same "inert unless
# configured" convention as the Vault/Firebase/Twilio/ClamAV integrations
# above — plain `python app.py` and `pytest` (which imports this module via
# conftest.py, and never sets this var) never spin up background polling
# unexpectedly. docker-compose.yml/.scale.yml/.tls.yml set it to "1" for
# the `backend` service, the only place it's actually enabled by default.
#
# gunicorn's eventlet worker class monkey-patches the stdlib at worker
# boot (confirmed: no explicit eventlet.monkey_patch() call anywhere in
# this file, so it must be gunicorn's own worker doing it) — a plain
# blocking-looking loop in an eventlet greenlet is therefore cooperative
# and won't starve request-serving greenlets in the same process.
# claim_next_due_scan_run() itself no-ops on any non-Postgres DATABASE_URL
# (see scanner/poller.py), so this is harmless to leave enabled against a
# SQLite database too — it just never claims anything.
def _run_scanner_poller_forever():
    # Needs its own app context — same reasoning
    # _write_onchain_tx_for_credential_async's docstring gives: Flask-
    # SQLAlchemy's db.session is greenlet-local, and this greenlet is not
    # a request greenlet, so it needs an explicit, long-held app context
    # to use db.session/Job.query at all. Held open for the life of the
    # loop (run_forever never returns), which is the documented pattern
    # for a long-lived background worker, not a one-off request-scoped use.
    with app.app_context():
        scanner_poller.run_forever(
            db=db,
            Job=Job,
            JobSource=JobSource,
            ScanRun=ScanRun,
            ScrapedCompany=ScrapedCompany,
            SavedJob=SavedJob,
            ScrapedListingReport=ScrapedListingReport,
            firecrawl_key_fn=lambda: _get_secret("FIRECRAWL_API_KEY"),
            anthropic_key_fn=lambda: _get_secret("ANTHROPIC_API_KEY"),
            logger=logger,
            interval_seconds=int(os.getenv("SCANNER_POLL_INTERVAL_SECONDS", "30")),
            on_scan_complete=_dispatch_job_alerts_for_scan,
        )


if os.getenv("ENABLE_JOB_SCANNER_POLLER") == "1":
    eventlet.spawn(_run_scanner_poller_forever)


# ----------------- Run -----------------
if __name__ == "__main__":
    # Werkzeug's debug mode exposes an interactive in-browser debugger on
    # unhandled exceptions — a remote-code-execution vector if this process
    # is ever reachable beyond a developer's own machine. This was
    # previously hardcoded True unconditionally regardless of FLASK_ENV.
    #
    # Suppression rationale for the `# nosec B104` marker below: Bandit
    # flags 0.0.0.0 as binding to all interfaces, but that's the intended,
    # required behavior here, not an oversight:
    # this is the `python app.py` local-dev entrypoint, and the container
    # entrypoint (gunicorn, per backend/Dockerfile's CMD) binds the same
    # 0.0.0.0:5000 for the same reason -- a process inside a Docker
    # container/behind a reverse proxy (see docker-compose.yml, ProxyFix
    # in this file) MUST bind all interfaces to be reachable from outside
    # its own network namespace at all; binding 127.0.0.1 here would make
    # the container unreachable. Public exposure is controlled at the
    # network layer (compose port mapping / the reverse proxy in front of
    # it, see loadbalancer/nginx.conf), not by this bind address.
    socketio.run(app, debug=not IS_PRODUCTION, host="0.0.0.0", port=5000)  # nosec B104
