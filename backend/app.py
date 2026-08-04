from flask import Flask, render_template, request, redirect, url_for, jsonify, send_from_directory, session
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect
from flask_wtf.csrf import CSRFError
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from functools import wraps
import filetype
import logging
import os, re, hashlib, secrets, shutil, smtplib, ssl, subprocess
from datetime import datetime, timedelta
from flask_socketio import SocketIO
from flask_jwt_extended import (
    JWTManager,
    create_access_token,
    jwt_required,
    get_jwt_identity,
)
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

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

# ----------------- Secrets (SECRET_KEY / JWT_SECRET_KEY) -----------------
# Same key drives both: Flask session signing (employer web portal, see below)
# and JWT signing (mobile/API). Closes S-10 (SECRET_KEY was never set) and is
# the prerequisite for BL-02 (real authentication).
_APP_SECRET = os.getenv("JWT_SECRET_KEY")
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
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# Secure cookies are only sent over HTTPS. Forcing this on in local dev
# (plain http://127.0.0.1) would silently break every employer-portal login,
# so it's gated on FLASK_ENV=production — set that (and deploy behind TLS,
# per the ProxyFix note above) for any real environment.
app.config["SESSION_COOKIE_SECURE"] = IS_PRODUCTION
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

# ----------------- Database config -----------------
app.config["SQLALCHEMY_DATABASE_URI"] = os.getenv("DATABASE_URL", "sqlite:///youthchain.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# ----------------- File upload config -----------------
BASE_DIR = os.getcwd()
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
APPLICATION_FOLDER = os.path.join(UPLOAD_FOLDER, "applications")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(APPLICATION_FOLDER, exist_ok=True)

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

db = SQLAlchemy(app)

# ----------------- MODELS -----------------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    phone = db.Column(db.String(100), unique=True, nullable=False)
    email = db.Column(db.String(100), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)

    credentials = db.relationship("Credential", backref="user", lazy=True)

    def to_dict(self):
        return {"id": self.id, "name": self.name, "email": self.email}


class Employer(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)
    email = db.Column(db.String(200), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {"id": self.id, "name": self.name, "email": self.email}


class Job(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    location = db.Column(db.String(100), nullable=False)
    duration = db.Column(db.String(50), nullable=False)
    # Comma-separated required skills, used for matching
    required_skills = db.Column(db.Text, nullable=True)
    # Owning employer. Nullable to tolerate pre-existing/seeded jobs created
    # before employer accounts existed (see startup migration below).
    employer_id = db.Column(db.Integer, db.ForeignKey("employer.id"), nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "location": self.location,
            "duration": self.duration,
            "required_skills": self.required_skills or "",
        }


class Credential(db.Model):
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
    hash = db.Column(db.String(64), unique=True, nullable=False)  # SHA256
    onchain_tx = db.Column(db.String(80), nullable=True)          # tx hash, if written on-chain

    def to_dict(self):
        return {
            "id": self.id,
            "title": self.title,
            "issuer": self.issuer,
            "year": self.year,
            "hash": self.hash,
            "onchain_tx": self.onchain_tx,
        }



class Application(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    job_id = db.Column(db.Integer, db.ForeignKey("job.id"), nullable=False)
    cv_file = db.Column(db.String(300), nullable=False)
    supporting_file = db.Column(db.String(300), nullable=True)
    status = db.Column(db.String(50), default="Pending")  # Pending / Accepted / Rejected
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

    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Education(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    candidate_id = db.Column(db.Integer, db.ForeignKey("candidate.id"), nullable=False)
    school = db.Column(db.String(200))
    degree = db.Column(db.String(200))
    year = db.Column(db.String(10))


class OTPCode(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(200), index=True, nullable=False)
    code = db.Column(db.String(6), nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False)
    used = db.Column(db.Boolean, default=False)


# ----------------- DB init + light migration -----------------
with app.app_context():
    db.create_all()
    try:
        with db.engine.begin() as conn:
            # Ensure created_at exists (old DBs)
            cols = conn.execute(text("PRAGMA table_info(application);")).fetchall()
            col_names = [c[1] for c in cols]
            if "created_at" not in col_names:
                conn.execute(text("ALTER TABLE application ADD COLUMN created_at DATETIME"))
                conn.execute(text("UPDATE application SET created_at = CURRENT_TIMESTAMP WHERE created_at IS NULL"))
            # Unique index to prevent duplicate (user_id, job_id)
            conn.execute(text("CREATE UNIQUE INDEX IF NOT EXISTS uq_user_job ON application (user_id, job_id)"))

            # Ensure job.employer_id exists (old DBs predating employer accounts)
            job_cols = conn.execute(text("PRAGMA table_info(job);")).fetchall()
            job_col_names = [c[1] for c in job_cols]
            if "employer_id" not in job_col_names:
                conn.execute(text("ALTER TABLE job ADD COLUMN employer_id INTEGER"))
    except Exception as e:
        logger.warning("Startup schema check skipped: %s", e)


# ----------------- HELPERS -----------------
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def valid_email(email: str) -> bool:
    return bool(EMAIL_RE.match((email or "").strip()))


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# Extension-only validation (allowed_file above) can be trivially bypassed
# by renaming any file — S-08 / Phase 5 of the engineering review. This maps
# each allowed extension to the content signature(s) that must actually be
# present, via the `filetype` library's magic-byte sniffing (not a full
# malware/AV scan — that would need a real scanning engine like ClamAV as
# an infrastructure dependency, out of scope for this fix; recorded as a
# follow-up rather than faked here).
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
        cmd = [npx_path, "hardhat", "run", f"scripts/{script}", "--network", "localhost"]
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
    """
    hash_hex = (credential.hash or "").strip()
    if not hash_hex:
        return None

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
    """Integer user id from the JWT identity, or None if missing/malformed."""
    try:
        raw = get_jwt_identity()
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _forbidden(msg: str = "Forbidden"):
    return jsonify({"success": False, "error": msg}), 403


def _current_employer_id():
    """Employer id from the Flask session (web portal login), or None."""
    eid = session.get("employer_id")
    try:
        return int(eid) if eid is not None else None
    except (TypeError, ValueError):
        return None


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


# ----------------- OTP brute-force protection (BL-09 / S-04) -----------------
# In-memory, per-process throttle: fine for a single-instance deployment;
# a multi-instance deployment would need a shared store (e.g. Redis) instead.
_otp_attempts: dict[str, list[datetime]] = {}
_OTP_MAX_ATTEMPTS = 5
_OTP_WINDOW = timedelta(minutes=10)


def _otp_rate_limited(email: str) -> bool:
    now = datetime.utcnow()
    attempts = [t for t in _otp_attempts.get(email, []) if now - t < _OTP_WINDOW]
    _otp_attempts[email] = attempts
    return len(attempts) >= _OTP_MAX_ATTEMPTS


def _record_otp_attempt(email: str) -> None:
    _otp_attempts.setdefault(email, []).append(datetime.utcnow())


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

    filename = secure_filename(file.filename)
    file_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    file.save(file_path)
    file_hash = generate_file_hash(file_path)

    existing = Credential.query.filter_by(hash=file_hash).first()
    if existing:
        # Same file uploaded again: don't create a duplicate row, but do
        # retry the on-chain write if it never succeeded the first time.
        if not existing.onchain_tx:
            tx = _write_onchain_tx_for_credential(existing)
            if tx:
                existing.onchain_tx = tx
                db.session.commit()
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

    onchain_tx = _write_onchain_tx_for_credential(new_cred)
    if onchain_tx:
        new_cred.onchain_tx = onchain_tx
        db.session.commit()

    return new_cred, None, True


# ----------------- MIDDLEWARE / HEADERS -----------------
@app.after_request
def _no_cache_for_lists(resp):
    # Keep lists always fresh in clients that might cache (jobs & application list)
    if request.path.startswith("/jobs") or request.path.startswith("/my_applications"):
        resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp


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


@app.route("/")
def home():
    return jsonify({"message": "YouthChain API with Users, Jobs, Applications, Credentials"})


# ----------- USER AUTH -----------
@app.route("/register", methods=["POST"])
def register():
    data = request.json or {}
    name = data.get("name")
    phone = data.get("phone")
    email = data.get("email")
    password = data.get("password")
    # OTP code supplied by client when ENFORCE_EMAIL_OTP_REG is ON
    otp_code = (data.get("otp_code") or "").strip()

    if not all([name, phone, email, password]):
        return jsonify({"success": False, "error": "Missing required fields"}), 400
    if not valid_email(email):
        return jsonify({"success": False, "error": "Please enter a valid email address"}), 400

    if len(password) < 8:
        return jsonify({"success": False, "error": "Password must be at least 8 characters"}), 400

    # Enforce a real/working email via OTP
    if ENFORCE_EMAIL_OTP_REG:
        if _otp_rate_limited(email):
            return jsonify({"success": False, "error": "Too many attempts. Try again later."}), 429
        if len(otp_code) != 6 or not otp_code.isdigit():
            return jsonify({"success": False, "error": "Enter the 6-digit code sent to your email"}), 400
        _record_otp_attempt(email)
        row = OTPCode.query.filter_by(email=email, code=otp_code, used=False).first()
        if not row or row.expires_at < datetime.utcnow():
            if row:
                row.used = True
                db.session.commit()
            return jsonify({"success": False, "error": "Invalid or expired code"}), 400
        # Mark code used now that we’re creating the account
        row.used = True
        db.session.commit()

    if User.query.filter((User.phone == phone) | (User.email == email)).first():
        # Deliberately the same generic shape as a real validation error —
        # closes S-07 (user enumeration) alongside the /auth/otp/request fix.
        return jsonify({"success": False, "error": "Unable to register with the details provided"}), 400

    hashed_pw = generate_password_hash(password)
    new_user = User(name=name, phone=phone, email=email, password_hash=hashed_pw)
    db.session.add(new_user)
    db.session.commit()

    token = create_access_token(identity=str(new_user.id))
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

    user = User.query.filter((User.phone == phone_or_email) | (User.email == phone_or_email)).first()
    # Same error for "no such user" and "wrong password" — do not let a caller
    # distinguish account existence from credential correctness (S-07).
    if not user or not check_password_hash(user.password_hash, password or ""):
        return jsonify({"success": False, "error": "❌ Incorrect phone/email or password"}), 401

    token = create_access_token(identity=str(user.id))
    return jsonify({
        "message": "✅ Login successful",
        "user": user.to_dict(),
        "access_token": token,
    }), 200


# ----------- EMAIL OTP AUTH (LOGIN) -----------
@app.route("/auth/otp/request", methods=["POST"])
def otp_request():
    data = request.json or {}
    email = (data.get("email") or "").strip()
    if not valid_email(email):
        return jsonify({"success": False, "error": "Please enter a valid email address"}), 400

    user = User.query.filter_by(email=email).first()
    # Same success-shaped response whether or not the account exists (S-07) —
    # no OTP is actually sent for an unregistered email, but the caller can't
    # tell the difference from the response alone.
    if user:
        OTPCode.query.filter(OTPCode.expires_at < datetime.utcnow()).delete()
        code = _otp_code()
        expires = datetime.utcnow() + timedelta(minutes=10)
        OTPCode.query.filter_by(email=email, used=False).update({"used": True})
        db.session.add(OTPCode(email=email, code=code, expires_at=expires, used=False))
        db.session.commit()
        _send_email(email, "Your YouthChain OTP", f"Your login code is {code}. It expires in 10 minutes.")

    return jsonify({"message": "✅ If that email has an account, a code has been sent"}), 200


@app.route("/auth/otp/verify", methods=["POST"])
def otp_verify():
    data = request.json or {}
    email = (data.get("email") or "").strip()
    code = (data.get("code") or "").strip()

    if not valid_email(email) or len(code) != 6 or not code.isdigit():
        return jsonify({"success": False, "error": "Invalid email or code"}), 400

    if _otp_rate_limited(email):
        return jsonify({"success": False, "error": "Too many attempts. Try again later."}), 429
    _record_otp_attempt(email)

    row = OTPCode.query.filter_by(email=email, code=code, used=False).first()
    if not row or row.expires_at < datetime.utcnow():
        if row:
            row.used = True
            db.session.commit()
        return jsonify({"success": False, "error": "Invalid or expired code"}), 400

    row.used = True
    db.session.commit()

    user = User.query.filter_by(email=email).first()
    if not user:
        return jsonify({"success": False, "error": "Invalid or expired code"}), 400

    token = create_access_token(identity=str(user.id))
    return jsonify({"message": "✅ OTP verified", "user": user.to_dict(), "access_token": token}), 200


# ----------- EMAIL OTP for REGISTRATION (NEW) -----------
@app.route("/auth/otp/register/request", methods=["POST"])
def otp_request_for_registration():
    """
    Send an OTP to ANY valid email (account may not exist yet).
    Used to prove the email is reachable before registration.
    """
    data = request.json or {}
    email = (data.get("email") or "").strip()
    if not valid_email(email):
        return jsonify({"success": False, "error": "Please enter a valid email address"}), 400
    if _otp_rate_limited(email):
        return jsonify({"success": False, "error": "Too many attempts. Try again later."}), 429
    _record_otp_attempt(email)

    # Cleanup old rows
    OTPCode.query.filter(OTPCode.expires_at < datetime.utcnow()).delete()

    code = _otp_code()
    expires = datetime.utcnow() + timedelta(minutes=10)
    # Invalidate previous codes for this email
    OTPCode.query.filter_by(email=email, used=False).update({"used": True})
    db.session.add(OTPCode(email=email, code=code, expires_at=expires, used=False))
    db.session.commit()

    sent = _send_email(email, "Verify your YouthChain email", f"Your registration code is {code}. It expires in 10 minutes.")
    msg = "OTP sent to email" if sent else "OTP generated (SMTP not configured; check server logs)"
    return jsonify({"message": f"✅ {msg}"}), 200


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


@app.route("/jobs", methods=["GET"])
def get_jobs():
    limit, offset = _pagination_params()
    jobs = Job.query.order_by(Job.id.desc()).limit(limit).offset(offset).all()
    return jsonify([job.to_dict() for job in jobs])


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

    if not user_id or not job_id or not cv_file:
        return jsonify({"success": False, "error": "Missing required fields"}), 400

    # Prevent duplicate application for same (user, job)
    existing = Application.query.filter_by(user_id=user_id, job_id=job_id).first()
    if existing:
        return jsonify({"success": False, "error": "You have already applied for this job"}), 400

    # Validate CV type
    if not cv_file.filename or not allowed_file(cv_file.filename):
        return jsonify({"success": False, "error": "Unsupported CV file type"}), 400
    if not content_matches_extension(cv_file, cv_file.filename):
        return jsonify({"success": False, "error": "CV file content does not match its extension"}), 400

    # Save CV with unique prefix
    ts = datetime.utcnow().strftime('%Y%m%d%H%M%S')
    cv_filename_orig = secure_filename(cv_file.filename)
    cv_filename = f"{ts}_{user_id}_{cv_filename_orig}"
    cv_path = os.path.join(APPLICATION_FOLDER, cv_filename)
    cv_file.save(cv_path)

    # Optional supporting doc
    support_filename = None
    if supporting_file and supporting_file.filename:
        if not allowed_file(supporting_file.filename):
            return jsonify({"success": False, "error": "Unsupported supporting file type"}), 400
        if not content_matches_extension(supporting_file, supporting_file.filename):
            return jsonify({"success": False, "error": "Supporting file content does not match its extension"}), 400
        support_orig = secure_filename(supporting_file.filename)
        support_filename = f"{ts}_{user_id}_{support_orig}"
        support_path = os.path.join(APPLICATION_FOLDER, support_filename)
        supporting_file.save(support_path)

    app_obj = Application(
        user_id=user_id,
        job_id=job_id,
        cv_file=cv_filename,
        supporting_file=support_filename,
    )
    db.session.add(app_obj)
    try:
        db.session.commit()

        # notify clients
               # notify all connected clients (broadcast is now default)
        try:
            socketio.emit("application_created", app_obj.to_dict())
        except Exception:
            logger.exception("socketio emit application_created failed")


    except IntegrityError:
        db.session.rollback()
        return jsonify({"success": False, "error": "You have already applied for this job"}), 400

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
    out = []
    for a, job in rows:
        item = a.to_dict()
        if job:
            item.update({"job_title": job.title, "job_location": job.location, "job_duration": job.duration})
        out.append(item)
    return jsonify(out)


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

    cred, error, created = _issue_credential_internal(user_id, title, issuer, file)
    if error:
        return jsonify({"success": False, "error": error}), 400

    return jsonify({
        "message": "✅ Credential issued" if created else "⚠ Credential already exists",
        "hash": cred.hash,
        "credential_id": cred.id,
        "onchain_tx": cred.onchain_tx,
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

    cred, error, created = _issue_credential_internal(user_id, title, issuer, file)
    if error:
        return jsonify({"success": False, "error": error}), 400

    verify_url = url_for("verify_by_id", cred_id=cred.id, _external=False)
    return jsonify({
        "success": True,
        "credential_id": cred.id,
        "hash": cred.hash,
        "onchain_tx": cred.onchain_tx,
        "verify_url": verify_url,
    }), 201 if created else 200


# ----------- File downloads -----------
@app.route("/certificate/<filename>", methods=["GET"])
@jwt_required()
def download_certificate(filename):
    cred = Credential.query.filter_by(file_path=filename).first()
    if not cred or cred.user_id != _current_user_id():
        return _forbidden("You do not have access to this file")
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename, as_attachment=False)


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
    if uid is not None and row.user_id == uid:
        return send_from_directory(APPLICATION_FOLDER, filename, as_attachment=True)

    eid = _current_employer_id()
    if eid is not None:
        job = Job.query.get(row.job_id)
        if job and (job.employer_id is None or job.employer_id == eid):
            return send_from_directory(APPLICATION_FOLDER, filename, as_attachment=True)

    return _forbidden("You do not have access to this file")


# ----------- Employer Portal (session-cookie auth) -----------
@app.route("/employer/register", methods=["GET", "POST"])
def employer_register():
    if request.method == "POST":
        csrf.protect()
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip()
        password = request.form.get("password") or ""

        error = None
        if not name or not email or not password:
            error = "All fields are required."
        elif not valid_email(email):
            error = "Please enter a valid email address."
        elif len(password) < 8:
            error = "Password must be at least 8 characters."
        elif Employer.query.filter_by(email=email).first():
            error = "An employer account with that email already exists."

        if error:
            return render_template("employer_register.html", error=error)

        employer = Employer(name=name, email=email, password_hash=generate_password_hash(password))
        db.session.add(employer)
        db.session.commit()
        session.clear()
        session["employer_id"] = employer.id
        return redirect(url_for("employer_dashboard"))

    return render_template("employer_register.html", error=None)


@app.route("/employer/login", methods=["GET", "POST"])
def employer_login():
    if request.method == "POST":
        csrf.protect()
        email = (request.form.get("email") or "").strip()
        password = request.form.get("password") or ""

        employer = Employer.query.filter_by(email=email).first()
        if not employer or not check_password_hash(employer.password_hash, password):
            return render_template("employer_login.html", error="Incorrect email or password.")

        session.clear()
        session["employer_id"] = employer.id
        next_url = request.args.get("next") or url_for("employer_dashboard")
        return redirect(next_url)

    return render_template("employer_login.html", error=None)


@app.route("/employer/logout", methods=["POST"])
@employer_login_required
def employer_logout():
    session.clear()
    return redirect(url_for("employer_login"))


@app.route("/employer")
@employer_login_required
def employer_dashboard():
    eid = _current_employer_id()
    jobs = Job.query.filter((Job.employer_id == eid) | (Job.employer_id.is_(None))).all()
    job_data = []
    for job in jobs:
        applicant_count = Application.query.filter_by(job_id=job.id).count()
        job_data.append({
            "id": job.id,
            "title": job.title,
            "location": job.location,
            "duration": job.duration,
            "applicant_count": applicant_count
        })
    employer = Employer.query.get(eid)
    return render_template("employer_dashboard.html", jobs=job_data, employer=employer)


@app.route("/employer/post", methods=["GET", "POST"])
@employer_login_required
def post_job():
    if request.method == "POST":
        title = request.form.get("title")
        location = request.form.get("location")
        duration = request.form.get("duration")
        required_skills = request.form.get("required_skills") or ""

        new_job = Job(
            title=title,
            location=location,
            duration=duration,
            required_skills=required_skills,
            employer_id=_current_employer_id(),
        )
        db.session.add(new_job)
        db.session.commit()
        # notify all connected clients
        try:
            socketio.emit("job_created", new_job.to_dict())
        except Exception:
            logger.exception("socketio emit job_created failed")


        return redirect(url_for("employer_dashboard"))

    return render_template("post_job.html")


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
            onchain_status = _check_onchain_registered(cred.hash)
        else:
            result = "not_found"

    return render_template(
        "verify.html", result=result, credential=credential, onchain_status=onchain_status
    )

# Verify by credential ID (shareable URL)
@app.route("/verify/<int:cred_id>")
def verify_by_id(cred_id):
    cred = Credential.query.get(cred_id)
    if not cred:
        return render_template("verify.html", result="not_found", onchain_status=None)

    onchain_status = _check_onchain_registered(cred.hash)
    return render_template(
        "verify.html", result="ok", credential=cred, onchain_status=onchain_status
    )


@app.route("/employer/applications/<int:job_id>", methods=["GET", "POST"])
@employer_login_required
def employer_applications(job_id):
    job = Job.query.get_or_404(job_id)
    if job.employer_id is not None and job.employer_id != _current_employer_id():
        return _forbidden("This job belongs to another employer account")

    if request.method == "POST":
        app_id = request.form.get("app_id")
        action = request.form.get("action")  # accept or reject
        application = Application.query.get(app_id)
        if application:
            application.status = "Accepted" if action == "accept" else "Rejected"
            db.session.commit()

                       # notify all connected clients
            try:
                socketio.emit("application_status_changed", {
                    "app_id": application.id,
                    "user_id": application.user_id,
                    "job_id": application.job_id,
                    "status": application.status
                })
            except Exception:
                logger.exception("socketio emit application_status_changed failed")


    rows = (
        db.session.query(Application, User)
        .join(User, Application.user_id == User.id)
        .filter(Application.job_id == job_id)
        .order_by(Application.created_at.desc())
        .all()
    )
    apps = []
    for a, u in rows:
        apps.append({
            "id": a.id,
            "user_id": a.user_id,
            "applicant_name": u.name,
            "applicant_email": u.email,
            "cv_file": a.cv_file,
            "supporting_file": a.supporting_file,
            "status": a.status
        })
    return render_template("employer_applications.html", job=job, apps=apps)


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

    candidate.bio = data.get("bio", candidate.bio)

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
        },
    }), 200


# ----------- Rule-based CV generation -----------
@app.route("/api/generate_cv/<int:candidate_id>", methods=["GET"])
@jwt_required()
def generate_cv(candidate_id):
    """
    Generate a structured CV text for the candidate.
    This is a rule-based generator that the mobile app can display or export.
    """
    c = Candidate.query.get(candidate_id)
    if not c:
        return jsonify({"success": False, "error": "candidate not found"}), 404
    if c.user_id != _current_user_id():
        return _forbidden()

    # Link credentials via user_id if available
    creds = []
    if c.user_id:
        creds = Credential.query.filter_by(user_id=c.user_id).all()

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
    if c.skills:
        lines.append("KEY SKILLS")
        lines.append("----------")
        lines.append(", ".join([s.strip() for s in c.skills.split(",") if s.strip()]))
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
    return jsonify({"success": True, "candidate_id": candidate_id, "cv": cv_text}), 200


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
        jobs = Job.query.order_by(Job.id.desc()).all()
        payload = []
        for j in jobs:
            # be defensive in case required_skills column is missing/empty
            rs = getattr(j, "required_skills", "") or ""
            payload.append({
                "id": j.id,
                "title": j.title,
                "location": j.location,
                "duration": j.duration,
                "required_skills": rs,
                "score": 0,   # un-ranked
            })
        return jsonify({
            "success": True,
            "candidate_id": candidate_id,
            "jobs": payload,
            "message": "No candidate profile yet; returning unranked jobs."
        }), 200

    # ----------------- NORMAL MATCHING PATH -----------------
    # Candidate.skills is a comma-separated string
    cand_skills = [s.strip().lower() for s in (candidate.skills or "").split(",") if s.strip()]
    jobs = Job.query.order_by(Job.id.desc()).all()
    results = []

    for j in jobs:
        required = [s.strip().lower() for s in (getattr(j, "required_skills", "") or "").split(",") if s.strip()]
        if not required or not cand_skills:
            score = 0
        else:
            overlap = len(set(cand_skills) & set(required))
            score = int(100 * overlap / len(required))

        results.append({
            "id": j.id,
            "title": j.title,
            "location": j.location,
            "duration": j.duration,
            "required_skills": ", ".join(required),
            "score": score,
        })

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
    if _is_api_request():
        return jsonify({"success": False, "error": "Invalid or missing CSRF token"}), 400
    return render_template("employer_login.html", error="Your session expired. Please log in again."), 400


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


# ----------------- Run -----------------
if __name__ == "__main__":
    socketio.run(app, debug=True, host="0.0.0.0", port=5000)
