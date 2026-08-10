# GitHub delayed PAPER — canonical V5 R4.5

Two GitHub PAPER workflows exist for historical reasons. They are not equivalent.

## Canonical workflow

The current canonical unattended experiment is:

```text
GitHub PAPER V5 Truthful Execution
.github/workflows/github-paper-v5.yml
cohort=PAPER_V5_R4_5_REGIME_EVIDENCE_2026_08_09
```

It runs hourly at minute 47 when GitHub Actions provisions a runner.

The older `GitHub PAPER Trial` workflow is **legacy V2**. It is retired from push, schedule and issue-comment execution. Its existing midpoint-based evidence is retained for provenance and must never be presented as canonical V5 execution-realistic performance.

## R4.5 cycle order

A canonical cycle:

1. Checks out canonical `main`.
2. Restores the private rolling R4.5 SQLite state only after SHA-256 verification.
3. Uses the wallet selection armed by the previous cycle.
4. Ignores copied activity predating `selection_effective_at`.
5. Requires a pre-selection directional source-strategy profile.
6. Resolves exact market identity and supported official market-delay metadata.
7. Fetches decision and arrival CLOB books.
8. Simulates FAK execution against arrival-book L2 depth using asks for BUY and bids for SELL.
9. Applies supported per-market fees, price limits, partial fills and explicit no-fill outcomes.
10. Marks and settles PAPER inventory from public market evidence.
11. Reconciles summary, ledger, execution evidence and accounting identities.
12. Binds selection, source-strategy and immutable UTC regime provenance into the evidence chain.
13. Rescores current public source history only after the active cycle and arms the next prospective selection.
14. Packages immutable hashes and advances private rolling state only when every required gate passes.

A clean first R4.5 cycle may legitimately have zero predictions/executions while it arms the next prospective selection. A present zero-byte `prediction-ledger-v5.jsonl` is valid only for that reconciled zero-activity state and is still SHA-256 hashed.

## Delayed GitHub profile

GitHub scheduled runners are not continuous infrastructure. R4.5 therefore uses an explicit delayed profile:

```text
schedule                       = 47 * * * *
ACTIVITY_LOOKBACK_SECONDS      = 5400
RISK_MAX_SIGNAL_AGE_SECONDS    = 5400
ACTIVITY_FETCH_LIMIT           = 2000
job timeout                    = 20 minutes
TRADING_MODE                   = PAPER
PAPER_TRADING_ENABLED          = true
LIVE_TRADING_ENABLED           = false
COST_AUTHORIZED_USD            = 0
AI_ANALYSIS_ENABLED            = false
RESEARCH_ENABLED               = false
```

The normal application default remains 30 seconds for maximum signal age. The 5,400-second GitHub value is evidence collection for delayed copyability, not a low-latency claim.

## Source quality

- `SHORT`: examines the latest 50 closed positions and scores decided outcomes.
- `LONG`: examines up to 200 closed positions and scores decided outcomes.
- `GLOBAL`: `60% SHORT + 40% LONG`.
- `EDGE`: execution copyability evidence.

These are not calibrated probabilities, expected-return estimates or proof of alpha. A decided outcome has strictly positive or negative realized PnL. Break-even closes remain reported in `closed_count` but have zero score-history weight, cannot satisfy the 20-outcome minimum, and do not enter directional `wins / (wins + losses)`.

## Persistent state

The private rolling R4.5 release is:

```text
github-paper-v5-state-v4-5
```

It contains only PAPER/public-source state and integrity material. It contains no trading private key, signer, exchange credential, paid API authorization or LIVE adapter.

A degraded or failed cycle may retain audit artifacts, but it **does not advance canonical rolling state**.

## Public terminal publication

A successful PAPER cycle does not itself make a public deployment canonical. The only workflow allowed to write the `sibyl-trace` Cloudflare Worker is:

```text
.github/workflows/publish-cloudflare-terminal-v5.yml
```

That publisher:

- accepts only a successful V5 workflow-run event from `main`;
- requires the successful run SHA still to equal current `main`;
- fails if required Cloudflare credentials are unavailable;
- verifies V5 artifact hashes and exact R4.5 cohort/methodology gates;
- combines Research V4 only as a noncanonical research anchor;
- emits public schema v5 with a machine-readable truth contract;
- publishes no raw prediction ledger;
- fetches the deployed Worker after publication and verifies exact source SHA/truth contract;
- marks public data stale client-side after 10,800 seconds without a fresh verified snapshot.

Legacy V2/V3, V4 and generic Cloudflare workflows are retired from deployment authority.

## Runtime failure semantics

GitHub can delay, skip, or refuse scheduled jobs. A run that never receives a runner has executed no application code and provides no CI/runtime evidence. It must not be described as a product failure or a PASS.

If runner provisioning is unavailable, Sibyl remains PAPER-only and fail-closed. No billing/spending change, paid fallback, manual LIVE path or strategy weakening is authorized by this profile.

## Limits

R4.5 GitHub PAPER is suitable for delayed, unattended evidence collection. It is not evidence of:

- guaranteed hourly execution;
- continuous WebSocket monitoring;
- low-latency copy trading;
- profitability;
- calibrated score probabilities;
- continuously fresh public state;
- LIVE execution.
