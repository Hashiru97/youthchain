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
