# End-to-end audit manifest

Audit scope: GitHub workflows, public API contracts, wallet scoring, delayed PAPER ingestion, deterministic risk, execution evidence, settlement, portfolio accounting, dashboard security, Oracle release activation, Cloudflare gateway, and backup recovery.

## Corrected invariants

- Default runtime mode is `READ_ONLY`.
- PAPER requires both `TRADING_MODE=PAPER` and `PAPER_TRADING_ENABLED=true`.
- LIVE remains structurally unavailable.
- Stale or invalid signals are rejected before external price lookup.
- Midpoints are cached per asset and cycle.
- Signal identities preserve distinct fills while remaining compatible with legacy state.
- Wallet evidence exposes SHORT, LONG, GLOBAL, and execution EDGE with sample sizes.
- GLOBAL is the deterministic risk input; EDGE is copyability evidence, not outcome alpha.
- Resolved PAPER positions settle idempotently from terminal public outcomes.
- Scheduled state is verified before restore and preserved after a bounded timeout.
- Private API and HTML responses are not cacheable.
- Oracle releases and custom images are identified by the exact reviewed commit.
- Backups are encrypted, HMAC-authenticated, and restored in one database transaction.
- GitHub Actions and runtime base images are pinned to reviewed commits or digests.

## Verification gate

This manifest is part of the exact candidate tree submitted to GitHub Actions. A green CI result must include dependency audits, lint, unit tests, JavaScript checks, gateway tests, Compose validation, custom image builds, and the digest-pinned tunnel image pull. It does not authorize deployment, merge, or LIVE execution.
