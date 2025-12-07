from flask import Flask, render_template, request, redirect, url_for, jsonify, send_from_directory
from flask_cors import CORS
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from werkzeug.exceptions import RequestEntityTooLarge
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
import os, re, hashlib, random, smtplib, ssl, subprocess
from datetime import datetime, timedelta
from flask import request
from flask_socketio import SocketIO

app = Flask(__name__)
# CORS: keep permissive for local dev; can tighten to specific origins later
CORS(app, resources={r"/*": {"origins": "*"}})

socketio = SocketIO(app, cors_allowed_origins="*")

# ----------------- Database config -----------------
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///youthchain.db"
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

# 🔒 NEW: gatekeeping for email OTP at registration (default ON)
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


class Job(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    location = db.Column(db.String(100), nullable=False)
    duration = db.Column(db.String(50), nullable=False)
    # NEW: comma-separated required skills for matching
    required_skills = db.Column(db.Text, nullable=True)

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
    year = db.Column(db.Integer, default=datetime.now().year)
    file_path = db.Column(db.String(300), nullable=True)
    hash = db.Column(db.String(64), unique=True, nullable=False)  # SHA256
    onchain_tx = db.Column(db.String(80), nullable=True)          # NEW: tx hash

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


# -------- NEW: youth profile models --------
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
    except Exception as e:
        print(f"[startup] schema check skipped: {e}")


# ----------------- HELPERS -----------------
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")


def valid_email(email: str) -> bool:
    return bool(EMAIL_RE.match((email or "").strip()))


def allowed_file(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def generate_file_hash(file_path: str) -> str:
    sha256_hash = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            sha256_hash.update(chunk)
    return sha256_hash.hexdigest()

def _write_onchain_tx_for_credential(credential: Credential) -> str | None:
    """
    Call the /blockchain Hardhat project to register this credential's hash
    on the local Ethereum node. Best-effort: returns tx hash or None.
    """
    try:
        hash_hex = (credential.hash or "").strip()
        if not hash_hex:
            return None

        blockchain_dir = os.path.abspath(
            os.path.join(BASE_DIR, "..", "blockchain")
        )

        cmd = [
            "npx",
            "hardhat",
            "run",
            "scripts/registerCredential.js",
            "--network",
            "localhost",
        ]

        env = os.environ.copy()
        env["HASH"] = hash_hex  # important

        print("[HARDHAT] running:", " ".join(cmd), "cwd=", blockchain_dir)

        proc = subprocess.run(
            cmd,
            cwd=blockchain_dir,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=90,
        )

        print("[HARDHAT stdout]\n", proc.stdout)
        print("[HARDHAT stderr]\n", proc.stderr)

        if proc.returncode != 0:
            print("[HARDHAT] non-zero exit code:", proc.returncode)
            return None

        tx_hash = None
        for line in proc.stdout.splitlines():
            line = line.strip()
            if line.startswith("✅ Mined tx:") or line.startswith("Mined tx:"):
                parts = line.split()
                tx_hash = parts[-1].strip()

        return tx_hash
    except Exception as e:
        print("[HARDHAT ERROR]", e)
        return None


def _send_email(to_email: str, subject: str, body: str) -> bool:
    """Send email if SMTP configured; else log and return False (non-fatal)."""
    if not (SMTP_HOST and SMTP_USER and SMTP_PASS and SMTP_FROM):
        print(f"[OTP EMAIL] SMTP not configured; would send to {to_email}: {subject}\n{body}")
        return False
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.starttls(context=ctx)
            server.login(SMTP_USER, SMTP_PASS)
            msg = f"From: {SMTP_FROM}\r\nTo: {to_email}\r\nSubject: {subject}\r\n\r\n{body}"
            server.sendmail(SMTP_FROM, [to_email], msg)
        return True
    except Exception as e:
        print(f"[SMTP ERROR] {e}")
        return False


def _otp_code() -> str:
    return f"{random.randint(0, 999999):06d}"


# -------- NEW: shared credential issuing helper --------
def _issue_credential_internal(user_id, title, issuer, file):
    """
    Shared logic for issuing a credential.
    Used by both /issue_credential and /api/certificate/upload.
    """
    if not user_id or not title or not issuer:
        return None, "Missing required fields"

    if not file or not allowed_file(file.filename):
        return None, "Invalid or missing certificate file"

    filename = secure_filename(file.filename)
    file_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    file.save(file_path)
    file_hash = generate_file_hash(file_path)

    new_cred = Credential(
        user_id=user_id,
        title=title,
        issuer=issuer,
        file_path=filename,
        hash=file_hash,
    )
    db.session.add(new_cred)
    db.session.commit()
    return new_cred, None


# ----------------- MIDDLEWARE / HEADERS -----------------
@app.after_request
def _no_cache_for_lists(resp):
    # Keep lists always fresh in clients that might cache (jobs & application list)
    if request.path.startswith("/jobs") or request.path.startswith("/my_applications"):
        resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp


# ----------------- ERROR HANDLERS -----------------
@app.errorhandler(RequestEntityTooLarge)
def _too_large(e):
    return jsonify({"error": "File too large (max 16 MB)"}), 413


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
    # 🔒 NEW: OTP code supplied by client when ENFORCE_EMAIL_OTP_REG is ON
    otp_code = (data.get("otp_code") or "").strip()

    if not all([name, phone, email, password]):
        return jsonify({"error": "Missing required fields"}), 400
    if not valid_email(email):
        return jsonify({"error": "Please enter a valid email address"}), 400

    # Enforce a real/working email via OTP
    if ENFORCE_EMAIL_OTP_REG:
        if len(otp_code) != 6 or not otp_code.isdigit():
            return jsonify({"error": "Enter the 6-digit code sent to your email"}), 400
        row = OTPCode.query.filter_by(email=email, code=otp_code, used=False).first()
        if not row or row.expires_at < datetime.utcnow():
            if row:
                row.used = True
                db.session.commit()
            return jsonify({"error": "Invalid or expired code"}), 400
        # Mark code used now that we’re creating the account
        row.used = True
        db.session.commit()

    if User.query.filter((User.phone == phone) | (User.email == email)).first():
        return jsonify({"error": "❌ Email or phone already registered"}), 400

    hashed_pw = generate_password_hash(password)
    new_user = User(name=name, phone=phone, email=email, password_hash=hashed_pw)
    db.session.add(new_user)
    db.session.commit()

    return jsonify({"message": "✅ User registered successfully", "user": new_user.to_dict()}), 201


@app.route("/login", methods=["POST"])
def login():
    data = request.json or {}
    phone_or_email = data.get("phone") or data.get("email")
    password = data.get("password")

    user = User.query.filter((User.phone == phone_or_email) | (User.email == phone_or_email)).first()
    if not user:
        return jsonify({"error": "❌ User not found"}), 404
    if not check_password_hash(user.password_hash, password or ""):
        return jsonify({"error": "❌ Incorrect password"}), 401

    return jsonify({"message": "✅ Login successful", "user": user.to_dict()}), 200


# ----------- EMAIL OTP AUTH (LOGIN) -----------
@app.route("/auth/otp/request", methods=["POST"])
def otp_request():
    data = request.json or {}
    email = (data.get("email") or "").strip()
    if not valid_email(email):
        return jsonify({"error": "Please enter a valid email address"}), 400

    user = User.query.filter_by(email=email).first()
    if not user:
        return jsonify({"error": "No account with that email"}), 404

    # Light cleanup of expired codes (keeps table small)
    OTPCode.query.filter(OTPCode.expires_at < datetime.utcnow()).delete()

    code = _otp_code()
    expires = datetime.utcnow() + timedelta(minutes=10)
    # Invalidate previous codes for this email
    OTPCode.query.filter_by(email=email, used=False).update({"used": True})
    db.session.add(OTPCode(email=email, code=code, expires_at=expires, used=False))
    db.session.commit()

    sent = _send_email(email, "Your YouthChain OTP", f"Your login code is {code}. It expires in 10 minutes.")
    msg = "OTP sent to email" if sent else "OTP generated (SMTP not configured; check server logs)"
    return jsonify({"message": f"✅ {msg}"}), 200


@app.route("/auth/otp/verify", methods=["POST"])
def otp_verify():
    data = request.json or {}
    email = (data.get("email") or "").strip()
    code = (data.get("code") or "").strip()

    if not valid_email(email) or len(code) != 6 or not code.isdigit():
        return jsonify({"error": "Invalid email or code"}), 400

    row = OTPCode.query.filter_by(email=email, code=code, used=False).first()
    if not row:
        return jsonify({"error": "Invalid or expired code"}), 400
    if row.expires_at < datetime.utcnow():
        row.used = True
        db.session.commit()
        return jsonify({"error": "Invalid or expired code"}), 400

    row.used = True
    db.session.commit()

    user = User.query.filter_by(email=email).first()
    if not user:
        return jsonify({"error": "No account with that email"}), 404

    return jsonify({"message": "✅ OTP verified", "user": user.to_dict()}), 200


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
        return jsonify({"error": "Please enter a valid email address"}), 400

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
@app.route("/jobs", methods=["GET"])
def get_jobs():
    jobs = Job.query.all()
    return jsonify([job.to_dict() for job in jobs])


# ----------- APPLICATIONS (Youth applies) -----------
@app.route("/apply", methods=["POST"])
def apply():
    user_id = request.form.get("user_id")
    job_id = request.form.get("job_id")
    cv_file = request.files.get("cv")
    supporting_file = request.files.get("supporting")

    if not user_id or not job_id or not cv_file:
        return jsonify({"error": "Missing required fields"}), 400

    # Prevent duplicate application for same (user, job)
    existing = Application.query.filter_by(user_id=user_id, job_id=job_id).first()
    if existing:
        return jsonify({"error": "You have already applied for this job"}), 400

    # Validate CV type
    if not cv_file.filename or not allowed_file(cv_file.filename):
        return jsonify({"error": "Unsupported CV file type"}), 400

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
            return jsonify({"error": "Unsupported supporting file type"}), 400
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
        except Exception as e:
            print("[socketio application_created]", e)


    except IntegrityError:
        db.session.rollback()
        return jsonify({"error": "You have already applied for this job"}), 400

    return jsonify({"message": "✅ Application submitted"}), 201


@app.route("/my_applications/<int:user_id>", methods=["GET"])
def my_applications(user_id):
    apps = Application.query.filter_by(user_id=user_id).order_by(Application.created_at.desc()).all()
    out = []
    for a in apps:
        job = Job.query.get(a.job_id)
        item = a.to_dict()
        if job:
            item.update({"job_title": job.title, "job_location": job.location, "job_duration": job.duration})
        out.append(item)
    return jsonify(out)


# ----------- CREDENTIALS -----------
@app.route("/passport/<int:user_id>", methods=["GET"])
def get_passport(user_id):
    creds = Credential.query.filter_by(user_id=user_id).all()
    return jsonify([cred.to_dict() for cred in creds])


@app.route("/issue_credential", methods=["POST"])
def issue_credential():
    user_id = request.form.get("user_id")
    title = request.form.get("title")
    issuer = request.form.get("issuer")
    file = request.files.get("file")

    if not user_id or not title or not issuer:
        return jsonify({"error": "Missing required fields"}), 400

    filename = None
    file_hash = None
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        file_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        file.save(file_path)
        file_hash = generate_file_hash(file_path)
    else:
        return jsonify({"error": "Invalid or missing certificate file"}), 400
 # Prevent duplicates — but if it's not yet on-chain, write it now
    existing = Credential.query.filter_by(hash=file_hash).first()
    if existing:
        # If this credential has never been written on-chain, do it now
        if not existing.onchain_tx:
            tx = _write_onchain_tx_for_credential(existing)
            if tx:
                existing.onchain_tx = tx
                db.session.commit()

        return jsonify({
            "message": "⚠ Credential already exists",
            "credential_id": existing.id,
            "hash": existing.hash,
            "verify_url": f"/verify/{existing.id}",
            "onchain_tx": existing.onchain_tx,
        }), 200

    new_cred = Credential(
        user_id=user_id,
        title=title,
        issuer=issuer,
        file_path=filename,
        hash=file_hash,
    )
    db.session.add(new_cred)
    db.session.commit()  # now new_cred.id is set

    # ⛓️ Write to blockchain (best effort)
    onchain_tx = _write_onchain_tx_for_credential(new_cred)
    if onchain_tx:
        new_cred.onchain_tx = onchain_tx
        db.session.commit()

    return jsonify({
        "message": "✅ Credential issued",
        "hash": file_hash,
        "credential_id": new_cred.id,
        "onchain_tx": onchain_tx,
        "verify_url": f"/verify/{new_cred.id}",
    }), 201


# NEW: API-friendly upload endpoint
@app.route("/api/certificate/upload", methods=["POST"])
def api_certificate_upload():
    """
    API wrapper for issuing a credential.

    Expects multipart/form-data:
      - user_id
      - title
      - issuer
      - file
    """
    user_id = request.form.get("user_id")
    title = request.form.get("title")
    issuer = request.form.get("issuer")
    file = request.files.get("file")

    cred, error = _issue_credential_internal(user_id, title, issuer, file)
    if error:
        return jsonify({"success": False, "error": error}), 400

    verify_url = url_for("verify_by_id", cred_id=cred.id, _external=False)
    return jsonify({
        "success": True,
        "credential_id": cred.id,
        "hash": cred.hash,
        "verify_url": verify_url,
    }), 201


# ----------- File downloads -----------
@app.route("/certificate/<filename>", methods=["GET"])
def download_certificate(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename, as_attachment=False)


@app.route("/application_file/<filename>", methods=["GET"])
def download_application(filename):
    return send_from_directory(APPLICATION_FOLDER, filename, as_attachment=True)


# ----------- Employer Portal -----------
@app.route("/employer")
def employer_dashboard():
    jobs = Job.query.all()
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
    return render_template("employer_dashboard.html", jobs=job_data)


@app.route("/employer/post", methods=["GET", "POST"])
def post_job():
    if request.method == "POST":
        title = request.form.get("title")
        location = request.form.get("location")
        duration = request.form.get("duration")
        required_skills = request.form.get("required_skills") or ""  # NEW

        new_job = Job(
            title=title,
            location=location,
            duration=duration,
            required_skills=required_skills,
        )
        db.session.add(new_job)
        db.session.commit()
        # notify all connected clients
        try:
            socketio.emit("job_created", new_job.to_dict())
        except Exception as e:
            print("[socketio job_created]", e)


        return redirect(url_for("employer_dashboard"))

    return render_template("post_job.html")


@app.route("/employer/verify", methods=["GET", "POST"])
def verify():
    result = None
    credential = None

    if request.method == "POST":
        hash_code = (request.form.get("hash") or "").strip()
        cred = Credential.query.filter_by(hash=hash_code).first()
        if cred:
            credential = cred
            result = "ok"
        else:
            result = "not_found"

    return render_template("verify.html", result=result, credential=credential)

# NEW: verify by credential ID (shareable URL)
@app.route("/verify/<int:cred_id>")
def verify_by_id(cred_id):
    cred = Credential.query.get(cred_id)
    if cred:
        result = f"✅ Verified: {cred.title} ({cred.year}) by {cred.issuer}"
    else:
        result = "❌ Invalid or Unverified"
    return render_template("verify.html", result=result)


@app.route("/employer/applications/<int:job_id>", methods=["GET", "POST"])
def employer_applications(job_id):
    job = Job.query.get_or_404(job_id)

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
            except Exception as e:
                print("[socketio application_status_changed]", e)


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
def upsert_candidate():
    """
    Create or update a candidate profile.
    We key on email (unique) and optionally link to an existing User via user_id.
    """
    data = request.get_json() or {}

    email = (data.get("email") or "").strip()
    if not email:
        return jsonify({"success": False, "error": "email is required"}), 400
    if not valid_email(email):
        return jsonify({"success": False, "error": "Please enter a valid email address"}), 400

    candidate = Candidate.query.filter_by(email=email).first()
    if not candidate:
        candidate = Candidate(email=email)

    user_id = data.get("user_id")
    if user_id:
        candidate.user_id = user_id

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


# ----------- AI-style CV generation -----------
@app.route("/api/generate_cv/<int:candidate_id>", methods=["GET"])
def generate_cv(candidate_id):
    """
    Generate a structured CV text for the candidate.
    This is a rule-based generator that the mobile app can display or export.
    """
    c = Candidate.query.get(candidate_id)
    if not c:
        return jsonify({"success": False, "error": "candidate not found"}), 404

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
def api_match_jobs(candidate_id):
    """
    Returns jobs ranked for a candidate.
    If the candidate profile doesn't exist yet, fall back to returning
    all jobs with score = 0 so the app still shows opportunities.
    """
    # Try to load candidate profile
    candidate = Candidate.query.get(candidate_id)

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
_API_PREFIXES = (
    "/register",
    "/login",
    "/auth/",
    "/jobs",
    "/apply",
    "/my_applications",
    "/passport",
    "/certificate",
    "/application_file",
    "/api/",  # treat API routes consistently
)


def _is_api_request() -> bool:
    try:
        return any(request.path.startswith(p) for p in _API_PREFIXES)
    except Exception:
        return False


@app.errorhandler(404)
def _json_404(e):
    if _is_api_request():
        return jsonify({"error": "Not found", "path": request.path}), 404
    return e


@app.errorhandler(405)
def _json_405(e):
    if _is_api_request():
        return jsonify({"error": "Method not allowed", "path": request.path}), 405
    return e


@app.errorhandler(500)
def _json_500(e):
    # Avoid leaking stack traces to clients, keep details in server logs
    if _is_api_request():
        return jsonify({"error": "Internal server error"}), 500
    return e


# ----------------- Run -----------------
if __name__ == "__main__":
    socketio.run(app, debug=True, host="0.0.0.0", port=5000)
