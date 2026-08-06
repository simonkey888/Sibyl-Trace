# Security policy

Do not report secrets in public issues. Rotate any value that appears in logs, screenshots, commits, chat messages, or build artifacts.

## Invariants

- No wallet private key belongs in this repository or GitHub Actions.
- The OpenAI API key belongs only in the Oracle runtime environment, never the dashboard or Worker.
- V1 must remain incapable of placing live orders.
- PostgreSQL and the FastAPI origin must not have public inbound ports.
- Every dashboard and API request must carry a valid Cloudflare Access JWT for the configured audience and owner email.
- The Worker validates the JWT signature, issuer, audience, and owner identity before serving assets or proxying API traffic.
- Production API calls must traverse the Worker and carry its shared origin secret.
- Administrative actions require a same-origin JSON POST and are audit logged.
- GPT analysis is read-only, receives pseudonymous wallet identifiers, and cannot call an order tool.
- Backups must be encrypted before leaving Oracle.
