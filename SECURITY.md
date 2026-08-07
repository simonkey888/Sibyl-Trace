# Security policy

Do not report secrets in public issues. Rotate any value that appears in logs, screenshots, commits, chat messages, or build artifacts.

## Invariants

- No wallet private key belongs in this repository or GitHub Actions.
- V1 must remain incapable of placing LIVE orders.
- The runtime defaults to `READ_ONLY`; PAPER requires both an explicit mode and an independent enable flag.
- Persisted runtime mode is reconciled with the reviewed configuration at startup.
- Production rejects development, placeholder, or short administrative secrets.
- PostgreSQL is isolated on an internal container network and has no public inbound port.
- The FastAPI origin must not have a public inbound port.
- Every dashboard and API request must carry a valid Cloudflare Access JWT for the configured audience and owner email.
- The Worker validates JWT signature, issuer, audience, and owner identity before serving assets or proxying API traffic.
- Private HTML and JSON responses must be `Cache-Control: no-store`.
- Production API calls must traverse the Worker and carry its shared origin secret.
- Administrative actions require a same-origin JSON POST and are audit logged.
- GPT analysis is read-only, receives pseudonymous wallet identifiers, and cannot call an order tool.
- GitHub Actions are pinned to reviewed commit SHAs.
- Oracle releases and custom container images are identified by the exact checked-out commit SHA.
- Backups are encrypted before leaving Oracle and authenticated with HMAC-SHA256 before restore.
- Restore must fail before database mutation when authentication, decryption, or `pg_restore` validation fails.
- Scheduled PAPER state must be integrity-checked before use and preserved after a bounded timeout.
