# Sibyl V6 — Cross-Market MM R1

Additive, cloud-only PAPER/DRY_RUN research lane. It does not replace or modify Sibyl R4.5 and does not use the Cloudflare execution/public-control path.

Execution substrate: official `limitless-labs-group/agents-starter` `src/strategies/cross-market-mm`, pinned to `e35ad881f88c7b5d60388461095ee11b7aa161c5`. Sibyl does not fork/copy the strategy. The container fetches that exact commit, preserves its repository/license, and the runtime verifies provenance before delegating to `npm run cross-market-mm`.

R1 safety law:

- `DRY_RUN=true` is forced by the wrapper.
- LIVE is impossible: `LIVE_PREFLIGHT=NOT_RUN`; committed config cannot arm it.
- trading/signing credentials are stripped before upstream execution.
- no order, signing, funding, account/login, capital movement, Cloudflare execution, Boros, Kalshi, NautilusTrader, OpenBB, Alpha56, Hyperliquid, copy-trading expansion, LLM trading decision, or directional BTC model is added.
- candidate title similarity is never called a match. `EXACT_EQUIVALENT` requires a complete rule fingerprint and exact agreement on every resolution field.
- realized rebates/rewards are recorded only from attributable realized events; never inferred.
- target `$80/24h` is measurement-only and cannot affect risk limits or size.

## V6 boundaries

`public venue reads -> candidate discovery -> persisted rule comparison -> EXACT_EQUIVALENT gate -> pinned upstream DRY_RUN substrate -> Sibyl L2/fee/hedge evidence -> economics ledger -> Cloud Storage checkpoint/evidence`

High-frequency logs go to stdout/Cloud Logging. Cloud Storage is durable checkpoint/evidence only, never a FUSE append log.

## Required rule fingerprint

Each venue side must explicitly provide all of: underlying asset/entity, polarity, threshold, comparison operator, reference source/oracle, UTC window start/end, exact resolution instant, price-to-beat construction, equality/tie handling, invalid-market rules, cancellation rules, fallback/oracle-failure rules, settlement semantics. Missing data means `UNVERIFIED_TITLE_ONLY`, never exact equivalence.

## Attribution

The executable trading substrate remains upstream Limitless Labs `agents-starter`; see `UPSTREAM_MANIFEST.json`. This lane adds only Sibyl gating/evidence/cloud wrappers around it.
