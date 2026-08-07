# Architecture

## Trust boundaries

1. **Browser**: untrusted presentation client. It never receives Oracle, database, R2, or trading secrets.
2. **Cloudflare Access + Worker**: identity perimeter, static dashboard, API proxy, security headers, no-store private responses, and owner-control token injection.
3. **Cloudflare Tunnel**: outbound-only connection from Oracle. The Oracle API has no public listener.
4. **FastAPI**: read API and owner controls. Production requests require a gateway shared secret.
5. **Engine worker**: the only process allowed to scan wallets and create PAPER signals, orders, marks, and settlements.
6. **PostgreSQL**: internal Docker network only.
7. **OpenAI Responses API**: optional risk-analysis output only. Requests use `store: false`, pseudonymous source IDs, strict JSON schema, and no execution tools.
8. **R2**: encrypted and HMAC-authenticated database backups. Encryption and authentication occur before upload.

## Data flow

- Candidate discovery: Data API `/v1/leaderboard` across WEEK, MONTH, and ALL.
- Wallet validation: `/closed-positions` supplies realized outcomes.
- Source scoring: SHORT uses the most recent 50 closes, LONG uses up to 200, GLOBAL combines both, and EDGE measures observed execution copyability.
- Signal ingestion: `/activity` is polled for the selected wallets with bounded pagination and fill-level deduplication.
- Risk preflight: code rejects stale, invalid, low-score, loss-limited, and drawdown-limited signals before price lookup.
- Price verification: CLOB `/midpoint` supplies the current observable PAPER price and is cached per asset per cycle.
- Risk decision: deterministic code evaluates slippage, exposure, cash, and position limits.
- Settlement: public Gamma closed-market data maps token IDs to terminal outcome prices; ambiguous results are deferred.
- Marking: unresolved open PAPER positions are periodically repriced from CLOB midpoint data.
- Advisory: GPT-5.6 may summarize score divergence, execution quality, settlement maturity, source risks, and anomalies, but cannot approve orders.
- Persistence: every signal, rejection, fill, settlement, state change, AI report, and worker error is recorded.

## Runtime modes

- `READ_ONLY` is the default.
- `PAPER` requires both `TRADING_MODE=PAPER` and `PAPER_TRADING_ENABLED=true`.
- Persisted mode is reconciled with reviewed runtime configuration at startup.
- `LIVE` has no adapter, signer, credential creation, or endpoint.

## Deliberate omissions

V1 contains no private-key parser, no order signer, no CLOB credential creation, and no LIVE-order endpoint. Accidental LIVE promotion is structurally impossible rather than merely configuration-dependent.
