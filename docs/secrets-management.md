# Secrets management

Real, working HashiCorp Vault integration — not aspirational. `JWT_SECRET_KEY`,
`ADMIN_API_KEY`, `FIREBASE_CREDENTIALS_JSON`, `TWILIO_ACCOUNT_SID`,
`TWILIO_AUTH_TOKEN`, and `TWILIO_FROM_NUMBER` are all fetched from Vault's
KV v2 engine (via the same `_get_secret()` helper) when `VAULT_ADDR` and
`VAULT_TOKEN` are set; otherwise the app falls back to plain environment
variables exactly as before. Live-verified: connected a real
`hashicorp/vault` dev-mode container, wrote both secrets into it, and
confirmed the running app resolved the exact values from Vault (not the
environment) — `_vault_client` connected, and `_APP_SECRET`/`ADMIN_API_KEY`
matched what was written into Vault byte-for-byte.

## Local / dev usage

```
docker compose -f docker-compose.yml -f docker-compose.vault.yml up -d
docker exec <vault-container> vault kv put secret/youthchain/config \
  JWT_SECRET_KEY=<a-real-random-value> ADMIN_API_KEY=<another-real-value>
```

Then in `backend/.env`:
```
VAULT_ADDR=http://vault:8200
VAULT_TOKEN=youthchain-dev-root-token
```

## What this is, and isn't

`docker-compose.vault.yml` runs Vault in **dev mode**: single-node,
in-memory storage, auto-unsealed, with a fixed root token committed to
the compose file. That's correct for local development and for proving
the integration genuinely works end-to-end — it is explicitly **not**
production-appropriate. A real deployment needs:

- A real storage backend (Raft integrated storage or Consul), not
  in-memory — dev mode loses everything on restart.
- Unseal keys held by separate operators (Vault's standard operating
  model), not a single fixed root token.
- A token distributed out-of-band to the application (a cloud secrets
  manager, a CI/CD secret, an operator typing it in) — never committed to
  a file that lives in version control.

Those are real infrastructure and operational-process decisions that
belong to whoever runs this in production, not something a compose file
checked into this repo can responsibly decide on your behalf. What this repo *does* provide: the actual
application-level integration code (`_get_secret()` in `app.py`), proven
to work against a real Vault instance, ready to point at a properly
operated one.

## Backup encryption keys (age) — a second, deliberately separate keypair

`scripts/backup.py` encrypts every backup archive with
[age](https://age-encryption.org) before an off-site upload (see
`docs/disaster-recovery.md` for the full story; real gap this closes —
these archives contain real PII, and previously went to S3/MinIO
completely unencrypted). The two halves of that keypair are **not**
routed through `_get_secret()`/Vault above, and that's a deliberate
choice, not an oversight:

- **`BACKUP_ENCRYPTION_RECIPIENT`** (the public key) only needs to be
  present on whatever runs `backup.py` — `backup-cron` — and a public key
  isn't secret at all; putting it in `backend/.env`/`docker-compose.backup.yml`
  directly (as this repo already does for its own dev-only demo keypair —
  see that compose file's own comment) carries no real exposure risk, the
  same way a TLS certificate's public half is routinely committed while
  its private key never is.
- **`BACKUP_ENCRYPTION_IDENTITY`** (the private key) is the one that
  actually matters, and by design is needed **only** on whatever machine
  runs `scripts/restore.py` against an encrypted archive — never on
  `backup-cron` itself, which only ever encrypts (a one-way, public-key
  operation). Keeping it off the machine that creates backups means a
  compromise of `backup-cron`/its container/its image can't leak the key
  needed to decrypt every backup this deployment has ever made — routing
  it through the *same* Vault instance `backup-cron` already reads
  `DATABASE_URL`/`BACKUP_S3_*` from would undermine exactly that
  separation. A real deployment should store this identity somewhere
  genuinely separate from the backup pipeline itself — a distinct Vault
  path with different access policy, a separate secrets manager, an
  offline/paper backup for disaster recovery of last resort — the same
  "who can read this, and does it match who's supposed to be able to"
  question this doc already asks about Vault's own token distribution
  above.

**Rotation**: age has no built-in key-rotation ceremony (unlike a Vault
dynamic secret) — generating a new keypair with `age-keygen` and updating
`BACKUP_ENCRYPTION_RECIPIENT` only encrypts *future* backups with the new
key. Existing archives already encrypted under the old recipient still
need the old identity to restore; keep it until every backup made under
it has aged out of both local and remote retention (`--keep`, see
`docs/disaster-recovery.md`), not delete it the moment you rotate.

What this repo provides here, same honesty as the Vault section above:
the real, live-verified `encrypt_backup()`/`_decrypt_archive()` integration
code and a throwaway dev-only demo keypair for local `docker compose up`
use — not a production key-management process. Generating, storing, and
rotating the real production identity safely is the same class of
operator responsibility this doc already states for Vault's unseal keys
and root token.
