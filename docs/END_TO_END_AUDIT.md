# End-to-end audit manifest — PAPER V5 R4.5

Audit scope: public-source scoring, prospective wallet selection, source-strategy classification, delayed PAPER ingestion, order-book execution simulation, fee/slippage math, position accounting, settlement, evidence provenance, public snapshot generation, Cloudflare publication authority, dashboard freshness, CI and safety boundaries.

## Canonical invariants

- Canonical cohort: `PAPER_V5_R4_5_REGIME_EVIDENCE_2026_08_09`.
- Default runtime mode is `READ_ONLY`.
- PAPER requires both `TRADING_MODE=PAPER` and `PAPER_TRADING_ENABLED=true`.
- LIVE remains structurally unavailable and `LIVE_TRADING_ENABLED=true` is rejected.
- Authorized cost is exactly `USD 0`.
- No private trading key, signer or live-order adapter is part of the canonical path.
- Midpoint-based V2 output is historical provenance only.
- Canonical V5 execution consumes arrival-book L2 as FAK and supports partial/no-fill evidence.
- BUY consumes asks; SELL consumes bids.
- Supported dynamic per-market fee metadata is required; unsupported schedules fail closed.
- Exact market identity and market-delay metadata are evidence-bound.
- Prospective source selection forbids pre-selection backfill.
- Source-strategy provenance must predate selection and classify the source as directional.
- R4.5 UTC regime labels are immutable research evidence and never automatic execution gates.
- Accounting identities are independently reconciled.
- Public performance may not be described as profitable or alpha-positive without sufficient attributable settled out-of-sample evidence.

## Quality-score truth

`SHORT`, `LONG` and `GLOBAL` are bounded heuristic quality rankings. `GLOBAL = 0.60 × SHORT + 0.40 × LONG`. `EDGE` is execution-copyability evidence.

The following statements are explicitly false contracts and must not appear in product surfaces or reports:

- `score == success probability`;
- `score == expected return`;
- `EDGE == alpha`;
- `quality score >= threshold == profitability proven`.

Quality history is based on **decided outcomes** (`realizedPnl > 0` or `< 0`), not raw closed-row count. A horizon requires at least 20 decided outcomes and its history component is `min(decided_outcomes / 100, 1)`. Break-even/zero-PnL closes remain visible in `closed_count` but have zero score-history weight and are excluded from directional win rate. Directional win rate is `wins / (wins + losses)`.

This prevents flat-close padding from inflating either eligibility or the score-history component.

## Execution and accounting truth

PAPER execution is an evidence simulation, not a live fill claim. A canonical fill must survive:

```text
prospective source eligibility
-> exact market identity
-> supported market rules
-> decision executable book
-> source-relative price limit
-> arrival executable book
-> L2 FAK simulation
-> supported fee schedule
-> cash/position constraints
-> persisted execution evidence
```

Portfolio accounting uses executable liquidation marks. The watchdog checks:

```text
cash + exposure == equity
initial bankroll + realized PnL + unrealized PnL == equity
```

Exposure limits are intentionally mark-to-market limits. They must not be mislabeled as cumulative historical capital committed.

## Publication truth

The public Worker has exactly one authorized writer:

```text
.github/workflows/publish-cloudflare-terminal-v5.yml
```

The following workflows are retired/read-only and must contain neither Cloudflare credentials nor the deployment command:

```text
.github/workflows/publish-cloudflare-terminal.yml
.github/workflows/publish-cloudflare-terminal-v4.yml
.github/workflows/deploy-cloudflare.yml
```

The legacy `github-paper-trial.yml` is also retired from push/schedule/comment execution; its existing V2 artifacts remain historical provenance only.

The canonical V5 publisher must reject a successful source run when that run's SHA no longer equals current `main`, must fail if required Cloudflare credentials are absent, and must verify the deployed public snapshot after deployment.

Every R4.5 public snapshot uses schema version 5 and carries `truth_contract` metadata with:

- canonical cohort and execution model;
- canonical publisher workflow;
- `single_public_writer_required=true`;
- maximum public snapshot age of 10,800 seconds;
- quality-score kind `HEURISTIC_QUALITY_RANKING`;
- quality history basis `DECIDED_OUTCOMES`;
- calibrated probability / expected return / alpha flags all false;
- `profitability_proven=false`;
- `live_available=false`.

The dashboard computes freshness at read time. A V5 PASS snapshot older than the TTL is **verified historical evidence, not online/current state**.

## Verification gate

A green result is attributable only to the exact SHA that executed it. Previous successful CI is not inherited by later commits.

Exact candidate verification should include:

- dependency audits;
- lint/static checks;
- backend tests and coverage;
- dashboard/gateway syntax and tests;
- single-Cloudflare-writer contract test;
- freshness/heuristic-score UI regression tests;
- decided-outcome score-history padding tests;
- secret/safety scans;
- Compose/container validation when relevant;
- canonical R4.5 workflow execution;
- artifact/ledger/hash reconciliation;
- private rolling-state advancement only after PASS;
- exact Cloudflare publisher outcome and post-deploy source-SHA verification.

If GitHub runner provisioning fails before step 1, classify the result as `RUNTIME_BLOCKED_EXTERNAL_RUNNER`. Do not call the code failed, do not call CI passed, and do not weaken safety/billing/cost constraints to force a green badge.

## Acceptance language

Allowed states:

- `PASS_BY_CODE_REVIEW` — static implementation review only;
- `PASS_EXACT_TESTED_SHA` — checks actually ran on that SHA;
- `RUNTIME_BLOCKED_EXTERNAL_RUNNER` — runner never executed application steps;
- `VERIFIED_STALE` — snapshot once passed evidence contract but exceeded freshness TTL;
- `INSUFFICIENT_EVIDENCE` / `EXPLORATORY_ONLY` — research maturity labels;
- `PROFITABILITY_UNPROVEN` — required until attributable settled OOS evidence proves otherwise.

No aggregate numeric audit score may replace these evidence states.
