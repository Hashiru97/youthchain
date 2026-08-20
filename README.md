# 🌍 YouthChain
### **Verified Skills. Real Opportunities.**

YouthChain is digital employment infrastructure for Sierra Leone (and, over time, the wider region): a unified digital identity and verifiable-credential platform aimed at the problems that actually block youth employment — fake credentials, employer mistrust, skill verification, and portable employment history. It combines a Flask API, a Flutter mobile app, an employer web portal, an admin console, and an Ethereum-compatible smart contract for credential integrity.

It started as an entry for the DSTI Big 5 Hackathon. It is no longer scoped like one — the sections below describe what actually exists today: real authentication and authorization, rate limiting, RBAC, content-validated file uploads, employer verification and abuse reporting, containerized deployment with a load-tested Postgres backend, automated backups, and CI that runs the full test suite against both supported database engines on every change.

---

## 🚀 Features

### Youth Mobile App (Flutter)
- Registration with email-OTP verification, JWT-based login
- Skill- and industry-tailored job matching (transparent, explainable scoring — no unverified AI/ML claims)
- Apply with CV and supporting documents (content-validated uploads, not just extension checks)
- In-app messaging with employers (read receipts, attachments), report-abuse action on any employer
- Push (Firebase) and SMS (Twilio) notifications where configured, in-app notifications always on
- Digital **Employment Passport** showing blockchain-verified credentials
- Real widget test coverage, including the report/industry-matching flows

### Employer Web Portal (Flask templates, session auth)
- Post jobs, review applicants, message candidates
- Employer verification workflow (document upload → admin review)
- Industry self-classification, feeding the mobile app's job-matching bonus
- Suspension + a real, in-product appeal path if an employer believes a suspension was a mistake

### Admin Console
- Employer verification queue, duplicate-applicant detection queue
- Employer abuse-report queue (aggregated and sorted by repeat-offender count, not just chronological)
- Suspension-appeal review queue
- Named operator accounts with role-based access (`admin` vs read-only `verifier`) and TOTP 2FA
- Analytics dashboard (usage events, no fabricated metrics)

### Blockchain Credential Registry
- Credentials hashed and the hash registered on-chain via a Solidity contract with real access control (not an open write)
- Backend shells out to the Hardhat CLI to write/verify on-chain state; degrades gracefully (a credential still issues, just without on-chain verification) if the chain is unreachable — this is a deliberate, tested fallback, not a silent failure
- Contract tests under `blockchain/test/`, independent of the backend's Python suite
- **What on-chain verification actually proves**: that a specific file's hash was registered by the platform's own issuer key at a specific time — file integrity + timestamp, not independent confirmation that the credential's stated issuer (a school, an employer) really issued it. That identity lives in this backend's own database, entered by the credential holder, the same as on a paper certificate. `/verify`'s own page copy says this explicitly rather than implying more.

#### Governance: a single key vs. a Safe multisig
`YouthChainRegistry.sol` is `Ownable2Step` — only its `owner()` can accredit/revoke issuers or revoke a credential. By default that owner is whichever single EOA deployed it (`DEPLOYER_PRIVATE_KEY`), which means one compromised key or host controls the entire registry's governance. `OWNER_ADDRESS` can point that owner at a [Safe](https://safe.global) multisig instead, requiring N-of-M independent signers to agree before any governance action executes:

- **Deploying fresh?** Point `OWNER_ADDRESS` straight at a Safe from the start — see `blockchain/scripts/deploySafe.js`'s own printed instructions after it runs.
- **Registry already deployed with a single owner?** `blockchain/scripts/transferRegistryOwnershipToSafe.js` walks the two-step `transferOwnership()`/`acceptOwnership()` migration (Ownable2Step's own safety property: nothing changes until the Safe itself accepts, so a wrong address never locks anyone out).
- **Local/dev use**: `deploySafe.js` deploys the real, official Safe v1.4.1 contracts (via `@safe-global/safe-contracts`'s own compiled artifacts — not a mock) and a demo 2-of-3 Safe using Hardhat's published local test accounts, entirely on your own machine. `test/SafeOwnership.test.js` proves the actual property this exists for: one signer's confirmation alone is provably *not* enough to execute a governance action, two *is*.
- **Real deployment**: set `SAFE_OWNERS` to the real signers' addresses (never their keys — this tooling only ever needs addresses) and create the Safe itself the normal way, through [app.safe.global](https://app.safe.global) with each owner connecting their own wallet, or have `deploySafe.js` do it with `SAFE_OWNERS`/`SAFE_THRESHOLD` set and `DEPLOYER_PRIVATE_KEY` only ever paying deployment gas, never becoming a Safe owner unless its own address is also in `SAFE_OWNERS`. `deploy.js` refuses to deploy the registry at any address without on-chain contract code, and refuses `OWNER_ADDRESS` entirely without an explicit `OWNER_ADDRESS_CONFIRMED=1` — that value becomes the registry's *permanent* owner the instant deployment mines, with no two-step confirmation of its own.

---

## 🏛 System Architecture

```
                +----------------------+
                |   Flutter Mobile /   |
                |   Employer Web /     |
                |   Admin Console      |
                +-----------+----------+
                            |
                            |  REST / WebSocket (JWT or session auth)
                            v
      +----------------------------------------------+
      |                  Flask API                    |
      |  Auth · Jobs · Applications · Messaging ·      |
      |  Reports · Passport · Credential Issuance      |
      +------------------+------------------+----------+
                          |                  |
              SQLAlchemy ORM        subprocess: npx hardhat
                          |                  |
                          v                  v
              +----------------------+   +---------------------------+
              |     PostgreSQL       |   |   Hardhat / Ethereum EVM   |
              | (SQLite fallback for |   |  CredentialRegistry.sol —  |
              |     local dev)       |   |  stores credential hash    |
              +----------------------+   +---------------------------+
```

Redis (optional but required for a multi-instance deployment) backs shared rate limiting across replicas — see `docker-compose.scale.yml`.

---

## 📦 Project Structure

```
youthchain/
├── backend/              Flask API, SQLAlchemy models, Alembic migrations, tests
├── youthchain_app/       Flutter mobile app
├── blockchain/           Hardhat project — CredentialRegistry.sol, deploy/register/check scripts, contract tests
├── docs/                 disaster-recovery.md, load-testing.md, monitoring.md, secrets-management.md
├── loadtest/             k6 scripts backing docs/load-testing.md's measured numbers
├── observability/        Prometheus/Grafana/Loki config (docker-compose.observability.yml)
├── loadbalancer/         nginx config for docker-compose.scale.yml
├── docker-compose.yml            Local/staging stack — bundled Postgres, single backend instance
├── docker-compose.tls.yml        Self-contained production stack with Caddy auto-HTTPS
├── docker-compose.scale.yml      Horizontal scaling: 3 backend replicas + nginx + Postgres + Redis
├── docker-compose.backup.yml     Additive: scheduled backups + self-hosted MinIO off-site sync
├── docker-compose.vault.yml      Additive: self-hosted HashiCorp Vault for secrets
├── docker-compose.observability.yml   Additive: Prometheus + Grafana + Loki
└── CLAUDE.md              Engineering operating instructions for this repo
```

---

## 🧪 Quickstart

### Option A — Docker Compose (recommended)

Brings up the backend with a bundled, health-checked Postgres instance and applies all migrations automatically. This is the closest thing to "production topology" you can run with one command.

```bash
cp backend/.env.example backend/.env
# Edit backend/.env: at minimum set JWT_SECRET_KEY to a real random value.
docker compose up -d --build
curl http://127.0.0.1:5000/healthz
```

The blockchain node isn't included here (it's local dev/test tooling, not a production service this platform runs itself — see `docker-compose.yml`'s own header comment). Credential issuance still works without it; it just won't write on-chain. To exercise the full on-chain path locally, run the blockchain node separately (Option B, step 1) alongside the container stack.

### Option B — Run each piece directly

**1. Blockchain**
```bash
cd blockchain
npm install
npx hardhat node          # keep this terminal open
```
In a second terminal:
```bash
cd blockchain
npx hardhat run scripts/deploy.js --network localhost
```
This writes `blockchain/deployed.json`, which `registerCredential.js`/`checkRegistered.js` read automatically — no address to copy/paste anywhere.

**2. Backend**
```bash
cd backend
python -m venv venv && source venv/bin/activate   # or venv\Scripts\activate on Windows
pip install -r requirements.txt
cp .env.example .env   # set JWT_SECRET_KEY; leave DATABASE_URL blank for a zero-config local SQLite file
python app.py
```
Runs on `http://127.0.0.1:5000`. Registration requires an email OTP by default (`ENFORCE_EMAIL_OTP_REG=1`); without real SMTP configured, the code is logged to the console instead of emailed — see `.env.example`. Request one via `POST /auth/otp/register/request`, then include `otp_code` in your `POST /register` call.

**3. Mobile app**
```bash
cd youthchain_app
flutter pub get
flutter run
```
The API base URL is `ApiClient.baseUrl` (`lib/services/api_client.dart`), not a separate config file.

---

## ⚙️ Environment Configuration

Every backend environment variable — what it does, its default/fallback behavior, and which are required vs. optional — is documented inline in [`backend/.env.example`](backend/.env.example). Copy it to `backend/.env` and start there rather than guessing at values; it's kept current as the source of truth, not duplicated here where it would drift.

Blockchain-side signer configuration (`ISSUER_PRIVATE_KEY`, optional `CONTRACT_ADDRESS`, `OWNER_ADDRESS`) is documented the same way in [`blockchain/.env.example`](blockchain/.env.example) — nothing needs to be set there for local development against the Hardhat node.

---

## ✅ Testing

```bash
# Backend — 149 tests, runs against SQLite by default
cd backend && python -m pytest tests/ -v

# Backend against a real local Postgres instead (matches CI's second matrix leg):
DATABASE_URL=postgresql://user:pass@localhost:5432/db python -m pytest tests/ -v

# Blockchain contract tests
cd blockchain && npx hardhat test

# Mobile widget tests
cd youthchain_app && flutter test
```

CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs all three on every push/PR, plus a real Redis service for the backend job, a Postgres matrix leg alongside the SQLite one, and a Docker build-and-`/healthz`-check job — the same database engine production actually runs on is verified on every change, not just tested manually.

---

## 🚢 Deployment & Operations

- **Local/staging**: `docker-compose.yml` — bundled Postgres, single backend instance.
- **Public-facing with TLS**: `docker-compose.tls.yml` — adds Caddy for automatic Let's Encrypt HTTPS.
- **Horizontal scaling**: `docker-compose.scale.yml` — 3 backend replicas behind nginx, Postgres, Redis-backed shared rate limiting. Real, measured numbers (not estimated) in [`docs/load-testing.md`](docs/load-testing.md).
- **Backups**: `docker-compose.backup.yml` (additive) — scheduled `pg_dump`/SQLite snapshots with optional S3-compatible off-site sync. Verified end-to-end in [`docs/disaster-recovery.md`](docs/disaster-recovery.md).
- **Secrets**: `docker-compose.vault.yml` (additive) — self-hosted HashiCorp Vault integration. See [`docs/secrets-management.md`](docs/secrets-management.md).
- **Observability**: `docker-compose.observability.yml` (additive) — Prometheus + Grafana + Loki. See [`docs/monitoring.md`](docs/monitoring.md) for wiring `/healthz` to real external alerting.

Compose files marked "additive" run alongside `docker-compose.yml` (`docker compose -f docker-compose.yml -f docker-compose.X.yml up -d`); `docker-compose.tls.yml` and `docker-compose.scale.yml` are self-contained alternatives, not overlays.

---

## 🔒 Security Posture (honest summary, not a claim of completeness)

JWT auth for the mobile app, session auth for the employer/admin web portals, both re-checked for account-active status on every request (immediate revocation on suspension, not just at next login). Role-based admin access with TOTP 2FA. Redis-backed rate limiting (OTP, uploads, reports) with an in-memory fallback for single-instance/local use. Content-validated (magic-byte, not extension-only) file uploads with per-user unique filenames. CSRF protection on all session-authenticated forms. See `CLAUDE.md` for the standing engineering discipline this repo is held to, and the codebase's own inline comments (searchable for `BL-`/`S-` prefixes) for the specific finding behind each of these.

This is not a claim that every gap is closed — treat any specific security question as worth verifying against the current code, not this summary.
