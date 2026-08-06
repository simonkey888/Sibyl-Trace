# Security policy

Do not report secrets in public issues. Rotate any value that appears in logs, screenshots, commits, chat messages, or build artifacts.

## Invariants

- No wallet private key belongs in this repository or GitHub Actions.
- V1 must remain incapable of placing live orders.
- PostgreSQL and the FastAPI origin must not have public inbound ports.
- Production API calls must traverse Cloudflare Access and the Worker gateway.
- Administrative actions must be audit logged.
- Backups must be encrypted before leaving Oracle.
