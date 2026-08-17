# PAPER risk policy — canonical R4.5 contract

This document describes the current PAPER V5 R4.5 implementation. Legacy midpoint/V2 behavior is historical only.

## Deterministic limits

| Rule | Application default | GitHub delayed R4.5 profile |
|---|---:|---:|
| Minimum GLOBAL quality ranking | 65 | 65 |
| Maximum signal age | 30 s | 5,400 s |
| Activity lookback | 120 s default | 5,400 s |
| Maximum absolute source-to-executable slippage | 0.03 | 0.03 |
| Maximum mark-to-market exposure per outcome | 2% of equity | 2% |
| Maximum total mark-to-market exposure | 15% of equity | 15% |
| Maximum daily loss | 3% of equity | 3% |
| Maximum drawdown | 10% | 10% |
| Copied source notional | 10% | 10% |
| Minimum PAPER order | USD 1 | USD 1 |

The 5,400-second GitHub values are an explicit delayed-scheduler profile. They must not be described as a low-latency execution model.

## Quality ranking is not probability

Wallet eligibility and ranking are deterministic heuristics used to choose sources for PAPER evidence collection.

- `SHORT`: examines the most recent 50 closed positions and scores decided outcomes.
- `LONG`: examines up to 200 closed positions and scores decided outcomes.
- `GLOBAL`: `0.60 × SHORT + 0.40 × LONG`.
- `EDGE`: execution-copyability evidence after observed price movement.

The numeric 0–100 values are **heuristic quality rankings only**. They are not calibrated probabilities, expected returns, or alpha estimates. A value of 80 does not mean 80% success probability.

A decided outcome is a close with strictly positive or negative realized PnL. Each SHORT/LONG horizon fails closed with fewer than 20 decided outcomes, non-positive realized PnL, or more than 65% of positive PnL concentrated in its best three positive closes. Break-even/zero-PnL closes remain reported in total `closed_count` but have zero quality-scoring weight: they do not satisfy the minimum history requirement, do not increase the history component, and do not enter win rate. Directional win rate is `wins / (wins + losses)`.

The quality-score history component is:

```text
history = min(decided_outcomes / 100, 1)
```

not `closed_count / 100`. This prevents flat-close padding from manufacturing apparent evidence maturity.

## Prospective source selection

R4.5 inherits the R4.3/R4.4 prospective-selection contract:

1. Execute only the source set armed by the previous cycle.
2. Reject activity whose `source_timestamp` predates `selection_effective_at`.
3. Bind selection provenance to execution evidence.
4. Require a pre-selection source-strategy profile classified as `DIRECTIONAL_CANDIDATE`.
5. Only after the cycle, rescore current public history and arm the next selection.

There is no pre-selection backfill.

### Trusted out-of-sample preregistration

Any claim labeled `VERIFIED` out-of-sample requires a cohort registered before its cutoff through the GitHub-native two-phase preregistration workflow. Local `created_at` values are descriptive only and cannot establish prospective timing. The evaluator binds GitHub run/artifact identity and server timestamp to the cohort hash, membership hash, feature-contract hash, selection-input hash and algorithm/source SHA. Missing, post-cutoff, mismatched, or locally backdated registration evidence fails closed. No OOS result changes score weights or execution policy automatically.

## Execution model

Canonical R4.5 never fills at midpoint.

For each eligible copied trade:

1. Resolve exact market identity.
2. Read official market delay metadata and fail closed when unsupported/unknown.
3. Fetch the decision CLOB book.
4. Use asks for BUY and bids for SELL.
5. Apply source-relative worst-price/slippage limits.
6. Refetch the arrival book after the applicable delay.
7. Simulate a FAK order against executable L2 depth.
8. Permit full fill, partial fill, or explicit no-fill.
9. Apply the per-market taker fee schedule.

Current fee economics use the Polymarket fee form implemented by `execution_v5.py`:

```text
fee_usd = shares × fee_rate × price × (1 - price)
```

Market metadata is authoritative for whether fees are enabled and which supported rate/exponent applies; unsupported fee schedules fail closed.

## Portfolio accounting

PAPER accounting uses:

```text
cash = initial_bankroll + execution_cash_deltas + settlement_proceeds
unrealized_pnl = executable_liquidation_value - open_cost_basis
equity = cash + executable_liquidation_value
```

BUY cost basis includes simulated fees. Partial SELL allocates cost basis proportionally to shares sold. Settlement is idempotent and realizes remaining cost basis exactly once.

Exposure limits use **current executable liquidation mark value**, not historical dollars ever committed. This is an intentional mark-to-market risk definition. Cash, daily-loss and drawdown constraints are separate controls; mark exposure must not be relabeled as lifetime committed capital.

The accounting watchdog independently reconciles both:

```text
cash + exposure == equity
initial_bankroll + realized_pnl + unrealized_pnl == equity
```

within the configured numerical tolerance.

## Settlement

Open PAPER positions settle only from public closed-market evidence whose token exposes a terminal price near `0` or `1`. Ambiguous terminal state is deferred, never guessed. Settlement records shares, terminal price, proceeds and realized PnL and creates an auditable portfolio snapshot.

## Regime evidence

R4.5 attaches immutable UTC weekday/hour/4-hour-bucket context to copied predictions. Regime analysis is research-only:

- no automatic weekday/hour execution gate;
- no naive inversion of a losing strategy;
- at least 50 attributable settled economic observations before even `EXPLORATORY_ONLY` status;
- out-of-sample confirmation required before any future strategy change.

## Claims prohibited by current evidence

The following remain unproven unless future attributable settled out-of-sample evidence establishes them:

- profitability;
- alpha;
- calibrated success probability from `GLOBAL`/`SHORT`/`LONG`;
- expected return from `EDGE`;
- profitable weekday/hour filters;
- LIVE readiness.

Engineering correctness and evidence integrity are necessary conditions, not evidence of profitability.
