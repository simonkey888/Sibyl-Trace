# Architecture

## Trust boundaries

1. **Browser**: untrusted presentation client. It never receives Oracle, database, R2, or trading secrets.
2. **Cloudflare Access + Worker**: identity perimeter, static dashboard, API proxy, security headers, and owner-control token injection.
3. **Cloudflare Tunnel**: outbound-only connection from Oracle. The Oracle API has no public listener.
4. **FastAPI**: read API and owner controls. Production requests require a gateway shared secret.
5. **Worker**: the only process allowed to scan wallets and create paper signals/orders.
6. **PostgreSQL**: private Docker network only.
7. **R2**: encrypted database backups only. Encryption occurs before upload.

## Data flow

- Candidate discovery: Data API `/v1/leaderboard` across WEEK, MONTH, and ALL.
- Wallet validation: `/closed-positions` supplies realized outcomes used for deterministic scoring.
- Signal ingestion: `/activity` is polled for the three selected wallets and deduplicated.
- Price verification: CLOB `/midpoint` supplies the current observable paper price.
- Risk gate: code evaluates staleness, source quality, slippage, exposure, loss, and drawdown.
- Persistence: every signal, rejection, fill, state change, and worker error is recorded.

## Deliberate omissions

V1 contains no private-key parser, no order signer, no CLOB credential creation, and no live-order endpoint. This makes accidental live promotion structurally impossible rather than merely configuration-dependent.
