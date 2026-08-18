# Deployment runbook — first production deploy

This is the actual, step-by-step path from "nothing running" to a real
YouthChain instance on a real domain, writing to a real blockchain
network. It assumes the reader is comfortable with a Linux shell and SSH,
and it is written against what this repository actually contains today —
`docker-compose.tls.yml`, `docker-compose.backup-external.yml`,
`docker-compose.observability.yml`, `backend/Dockerfile`,
`blockchain/hardhat.config.js` — not generic cloud-deployment advice.

Three things below are genuinely this project's call, not something a
runbook can decide on its own: which VPS provider, which blockchain
network, and where the signer keys live long-term. Each is presented with
the real tradeoff, not a single unexplained answer.

---

## 0. What's already decided, and why

- **Hosting**: a VPS running this repo's own `docker-compose` stack, not a
  PaaS (Railway/Render/etc.) — see the audit register for the reasoning.
  This runbook assumes that.
- **The backend image can now actually reach the blockchain.** As of this
  pass, `backend/Dockerfile` bundles Node 22 and a compiled
  `blockchain/` project at `/blockchain`, and `docker-compose*.yml`'s
  build context is the repo root. If you're reading this against an older
  checkout, confirm `docker build -f backend/Dockerfile .` (context: repo
  root) succeeds before anything else here — none of this works otherwise.

---

## 1. Provision the server

**Recommendation: Hetzner Cloud, CX-series, EU region (Falkenhagen or
Nuremberg).** Best price/performance of the mainstream VPS providers, no
usage-based surprises, and a Docker-first user base means the rough edges
are well-documented. Nearest realistic region to West Africa among the
providers with mature Docker/volume support — none of the major providers
have an African region as of this writing.

**Alternative: DigitalOcean.** Slightly more expensive per core/GB, but a
gentler first-time experience (better docs, a simpler dashboard) if this
is the first time provisioning a Linux server matters more than saving a
few dollars a month. Functionally interchangeable with Hetzner for
everything below.

**Sizing**: start with 2 vCPU / 4GB RAM (Hetzner CX22 or equivalent).
`docs/load-testing.md`'s own numbers (33.5 req/s, 298ms median at 4
gunicorn workers under a real k6 load test) were measured on hardware in
that class. This is a starting point, not a forecast — nobody involved in
this runbook has real production traffic numbers for this specific
deployment yet, and this is exactly the kind of thing to revisit once
there are some.

Once provisioned:

```bash
# As root, first login:
adduser deploy && usermod -aG sudo deploy
# Copy your SSH public key to the new user, then disable password auth
# and root login in /etc/ssh/sshd_config (PasswordAuthentication no,
# PermitRootLogin no), then: systemctl restart sshd

# Basic firewall — only what this stack actually needs public
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable

# Docker + Compose plugin (as `deploy`, per Docker's own install docs)
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker deploy
# log out and back in for the group change to take effect
```

Everything from here on runs as the `deploy` user, not root.

---

## 2. DNS

Point a real subdomain at the server's IP **before** starting Caddy — Let's
Encrypt's HTTP-01 challenge needs to reach the server on port 80 for the
domain it's issuing a certificate for.

1. In whichever registrar/DNS provider holds the domain, add an `A` record
   (and `AAAA` if the server has IPv6) for the chosen hostname
   (e.g. `api.youthchain.example`) pointing at the server's public IP.
2. Wait for it to resolve: `dig +short api.youthchain.example` from your
   own machine should return the server's IP. This can take anywhere from
   a minute to a few hours depending on the provider/TTL — don't proceed
   to starting Caddy until it does, or the certificate request will fail.
3. Edit `Caddyfile` in the repo: replace `your-domain.example` with the
   real hostname from step 1. This file is committed, so this is a real
   edit to the checkout on the server, not a secret.

---

## 3. The blockchain decision: network, RPC, and signer keys

This is the one section of this runbook that is a real product decision,
not an infrastructure mechanic. Two independent choices:

### 3a. Which network

| | Public testnet (Sepolia) | A real L2 (Base, Polygon, Arbitrum) |
|---|---|---|
| **Cost** | Free (faucet ETH) | Real but low — cents per credential, not dollars |
| **Permanence** | Testnets get reset/deprecated over years; not a promise of permanence | Real, persistent chain state |
| **Right for** | Pilot / soft-launch — proving the credential-verification flow works end-to-end for real users, without any real financial exposure | The actual national-scale launch, once the pilot has validated the product |

Recommendation: **deploy to Sepolia first for the pilot**, and treat the
move to a real L2 as its own later milestone (a fresh `deploy.js` run
against the new network — the contract and all app code are already
network-agnostic; nothing in `app.py` or the contract needs to change).
Do not deploy to Ethereum mainnet directly — L2 gas costs are a fraction
of mainnet's, and this contract has no functional need for mainnet's
specific security guarantees over a major L2's.

### 3b. RPC endpoint

Don't run your own node. Use a managed RPC provider — **Alchemy** or
**Infura**, both have a free tier that comfortably covers this contract's
call volume (a handful of transactions per credential event, not
per-request). Sign up, create an app for the chosen network, copy the
HTTPS RPC URL.

### 3c. Signer keys — the part that has real financial/security stakes

Two keys, two different jobs, and they should **not be the same key**:

- **`DEPLOYER_PRIVATE_KEY`** — deploys the contract, becomes its `owner`.
  Only ever used for `accreditIssuer`/`revokeIssuer`/`revokeCredential`/
  `transferOwnership` — rare, deliberate admin actions, not routine
  traffic. This key should **not live on the production server at all**
  day-to-day: generate it, use it for the one-time deploy + issuer
  accreditation in step 4, then move it to cold storage (a hardware
  wallet, or at minimum an encrypted offline backup) and remove it from
  any `.env` file on the server.
- **`ISSUER_PRIVATE_KEY`** — the key `registerCredential.js` actually
  signs with on every real credential issuance. This one does live on the
  server (it's what the backend calls on every `/issue_credential`), so
  its blast radius if compromised should be minimized: it can only call
  `registerCredential()` (gated by `onlyAccreditedIssuer` — see
  `YouthChainRegistry.sol`), nothing else. If it's ever compromised, the
  fix is `revokeIssuer(address)` (owner-only, via the deployer key) plus
  generating and accrediting a new issuer key.

**Never** reuse Hardhat's well-known local test mnemonic (the accounts
printed by `npx hardhat node`) for either key on a real network — those
private keys are public knowledge; funds or trust placed on them will be
taken. Generate both fresh:

```bash
# From the blockchain/ directory, using ethers (already a dependency):
node -e "const {Wallet} = require('ethers'); const w = Wallet.createRandom(); console.log('Address:', w.address); console.log('Private key:', w.privateKey);"
```

Run this twice (once per key), fund the **deployer** address with a small
amount of the target network's native token (enough for a handful of
transactions — a few dollars' worth on an L2), and keep the raw private
keys somewhere that isn't this repo, isn't Slack, and isn't a plaintext
note.

**Where the issuer key actually lives on the server**: the honest state
today is `blockchain/.env`, file-permissioned to the `deploy` user only
(`chmod 600`). `docker-compose.vault.yml` exists in this repo, but its own
header is explicit that its dev-mode configuration (single-node, fixed
root token checked into the file) is not production-appropriate — using
it for real key custody would need a real Vault deployment with genuine
unseal-key custody procedures, which is a separate, larger piece of work
than this runbook covers. Track that as a follow-up, not a blocker for
first deploy; `.env` + file permissions + the deployer-key-offline
practice above is a reasonable interim posture, not a fiction.

---

## 4. First-deploy checklist

Run through in order. Each step assumes the previous one succeeded.

**4.1 — Get the code onto the server**

```bash
git clone <this repo's real URL> youthchain && cd youthchain
```

**4.2 — Configure `backend/.env`**

```bash
cp backend/.env.example backend/.env
```

Set, at minimum:

| Variable | Value |
|---|---|
| `FLASK_ENV` | `production` |
| `JWT_SECRET_KEY` | `openssl rand -hex 32` |
| `ADMIN_API_KEY` | `openssl rand -hex 32` (or leave blank if only named admin accounts, from 4.7, will ever be used) |
| `HARDHAT_NETWORK` | `sepolia` (or your chosen network name from 3a) |
| `SMTP_HOST` / `SMTP_USER` / `SMTP_PASS` / `SMTP_FROM` | real SMTP creds — without these, OTP codes only ever reach the server log, not the user |

Leave `DATABASE_URL` blank — `docker-compose.tls.yml` sets it to the
bundled Postgres container for you.

**4.3 — Configure `blockchain/.env`**

```bash
cp blockchain/.env.example blockchain/.env
```

Set `SEPOLIA_RPC_URL` (from 3b) and `DEPLOYER_PRIVATE_KEY` (from 3c) —
this file is only used for the one-time deploy in the next step and never
read by the running backend container directly (the backend only reads
`backend/.env`).

**4.4 — Deploy the contract (one time)**

This runs directly on the VPS, not inside the backend container — needs
Node 22 on the host too (same version the Dockerfile installs, for the
same reason: Hardhat 3 refuses to run on anything older):

```bash
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo bash -
sudo apt-get install -y nodejs

cd blockchain
npm ci
npx hardhat run scripts/deploy.js --network sepolia
```

This prints the deployed address and writes `blockchain/deployed.json`.
**Copy the printed address into `backend/.env` as `CONTRACT_ADDRESS=0x...`
— do not rely on `deployed.json` alone.** The backend container's
`/blockchain` is baked into the image at build time; `deployed.json`
written by a manual deploy run won't survive the container being
recreated (a redeploy, an image rebuild) unless the address is also
captured in `backend/.env`, which does persist.

**4.5 — Accredit the issuer key**

If `ISSUER_PRIVATE_KEY` (3c) is a different key from the deployer (it
should be), accredit it as a one-off script rather than the interactive
console — verified live against a real deployed contract; the API is:

```bash
cat > scripts/_accredit_issuer.js << 'EOF'
import { network } from "hardhat";
const { ethers } = await network.create();
const registry = await ethers.getContractAt("YouthChainRegistry", "0xYOUR_CONTRACT_ADDRESS");
const tx = await registry.accreditIssuer("0xYOUR_ISSUER_ADDRESS");
await tx.wait();
console.log("accredited:", await registry.accreditedIssuers("0xYOUR_ISSUER_ADDRESS"));
EOF
npx hardhat run scripts/_accredit_issuer.js --network sepolia
rm scripts/_accredit_issuer.js
```

Add `ISSUER_PRIVATE_KEY` to `backend/.env` too — `registerCredential.js`
reads it directly from the environment the backend container runs with.

**4.5b — Real off-site backup storage, not the bundled dev MinIO**

`docker-compose.backup.yml`'s own header is explicit that its bundled
MinIO (fixed root credentials, checked into the file) is dev/local-only.
Worse than just "don't use the defaults": its `backup-cron` service also
hardcodes `BACKUP_S3_*` in its own `environment:` block pointing at that
MinIO, which — per Compose's own precedence rules — wins over anything set
in `backend/.env`, so simply overriding the env file alone would not have
worked.

Use **`docker-compose.backup-external.yml`** instead (added alongside
this runbook) — the same `backup-cron` service with no hardcoded S3
values at all, reading `BACKUP_S3_ENDPOINT`/`BUCKET`/`ACCESS_KEY`/
`SECRET_KEY` purely from `backend/.env`. Pick a real S3-compatible
provider (Backblaze B2 and Wasabi are meaningfully cheaper than AWS S3
for this access pattern — infrequent writes, rare reads — and speak the
identical API), create a bucket + access key, and set the four
`BACKUP_S3_*` values in `backend/.env` for real.

If self-hosting MinIO is genuinely preferred over a managed bucket,
`docker-compose.backup.yml` is still there — but change
`MINIO_ROOT_USER`/`MINIO_ROOT_PASSWORD` and the matching
`BACKUP_S3_ACCESS_KEY`/`BACKUP_S3_SECRET_KEY` away from the checked-in
dev values first, in a local override you keep on the server only.

**4.6 — Bring up the real stack**

Set a real Grafana admin password first — `docker-compose.observability.yml`
falls back to the checked-in `admin` placeholder when this is unset, which
is fine for local dev but must not ship as-is here:

```bash
cd ..  # repo root
echo "GRAFANA_ADMIN_PASSWORD=$(openssl rand -hex 16)" >> .env
```

Then bring the stack up — Compose auto-loads that root `.env` file:

```bash
docker compose \
  -f docker-compose.tls.yml \
  -f docker-compose.backup-external.yml \
  -f docker-compose.observability.yml \
  up -d --build
```

(Verified this three-file combination merges into one project correctly —
`docker compose ... config --services` lists all eight services — `loki`,
`promtail`, `postgres`, `backend`, `backup-cron`, `caddy`, `prometheus`,
`grafana` — with no conflicts.) First build will take a few minutes — it's
compiling the contract and installing the full Python + Node dependency set.

**4.7 — Create the first real admin account**

```bash
docker compose -f docker-compose.tls.yml exec backend python scripts/create_admin.py \
  --email you@your-org.org --name "Your Name"
```

Prompts for a password interactively (never appears in shell history).
There is no self-registration route for admin accounts, by design.

---

## 5. Post-deploy smoke test

Don't call it done until every one of these is true:

- [ ] `https://your-domain/healthz` returns 200 with a real certificate
      (not a browser warning) — confirms Caddy's Let's Encrypt issuance
      succeeded.
- [ ] A real mobile-app (or `curl`) registration + OTP flow completes.
- [ ] `/issue_credential` returns 201, and within ~30-90s the credential's
      `onchain_tx` is set (poll `/passport/<id>` or check the DB) —
      confirms the backend container can actually reach the RPC endpoint
      and sign with the issuer key.
- [ ] `/verify/<credential_id>` shows "IS registered and valid" with a
      live on-chain check, not the "could not reach the blockchain"
      fallback badge.
- [ ] Log into `/admin/login` with the account from 4.7, reach
      `/admin/credentials`, and confirm a test credential can be revoked
      and `/verify` immediately reflects REVOKED.
- [ ] `docker compose ... logs backup-cron` shows a successful first
      backup run (or wait for `BACKUP_INTERVAL_SECONDS` — default daily —
      and check `youthchain_backup_last_attempt_success` on `/metrics`).
- [ ] Grafana (`ssh -L 3000:localhost:3000 deploy@server`, then
      `localhost:3000` — it's loopback-only by design, see
      `docker-compose.observability.yml`) shows real request metrics.

---

## 6. Ongoing operations

- **Restarting/redeploying**: `docker compose -f docker-compose.tls.yml -f docker-compose.backup-external.yml -f docker-compose.observability.yml up -d --build` again after a `git pull` — migrations run automatically on container start (`docker-entrypoint.sh`).
- **Restoring from a backup**: `docs/disaster-recovery.md` plus this
  session's addition to `scripts/restore.py` — the Postgres path now
  takes its own pre-restore snapshot and requires confirmation before
  touching anything.
- **Scaling beyond one instance**: `docker-compose.scale.yml` exists for
  this (3 backend replicas behind its own nginx load balancer), but
  checked live just now: combining it with `docker-compose.tls.yml`
  doesn't error, but produces a broken topology, not a working
  scaled+TLS stack — `docker-compose.scale.yml` defines `backend1`/`2`/`3`
  + its own `nginx`, with no plain `backend` service at all; merging in
  `docker-compose.tls.yml` adds a *fourth*, separate `backend` container
  that Caddy proxies to, while the three real scaled replicas sit behind
  `nginx` with no TLS of its own and nothing routing real traffic to
  them. This is a real, open gap (see the audit register's deferred
  items) — revisit when there's real load data suggesting one instance
  isn't enough, not by naively combining these two files.
  **Also true running `docker-compose.scale.yml` completely standalone,
  as its own header instructs**: it sets `FLASK_ENV=production` on all
  three replicas with no TLS termination anywhere in that stack, which
  makes `app.py` mark the session cookie `Secure` (see that file's
  comment on `SESSION_COOKIE_SECURE`) while `loadbalancer/nginx.conf`
  only ever `listen`s on plain 80 — employer/portal login (session-cookie
  auth, not JWT) will silently never persist a session on this stack as
  shipped. Do not point real users at it until `nginx.conf` itself
  terminates TLS (cert + `listen 443 ssl` + redirect from 80) — it's this
  topology's actual internet-facing edge, so TLS added anywhere upstream
  of it doesn't help either (see `docker-compose.scale.yml`'s own header
  for why).
- **Rotating the issuer key**: `revokeIssuer(oldAddress)` (owner/deployer
  key) → generate a new key → `accreditIssuer(newAddress)` → update
  `ISSUER_PRIVATE_KEY` in `backend/.env` → `docker compose ... up -d` to
  pick up the new value.
