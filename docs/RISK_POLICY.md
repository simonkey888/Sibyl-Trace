# PAPER risk policy

| Rule | Default |
|---|---:|
| Minimum GLOBAL wallet score | 65 |
| Maximum signal age | 30 seconds |
| Maximum absolute copy slippage | 0.03 |
| Maximum exposure per outcome | 2% of equity |
| Maximum total exposure | 15% of equity |
| Maximum daily loss | 3% of equity |
| Maximum drawdown | 10% |
| Copied source notional | 10% |
| Minimum PAPER order | USD 1 |

Signals that are stale, invalid, below the wallet threshold, blocked by daily loss, or blocked by drawdown are rejected before an external price request. Midpoints are cached per asset for each ingestion cycle.

SELL orders are never opened naked. A copied SELL can only reduce an existing PAPER position in the same asset.

## Wallet eligibility

A horizon is rejected when it has fewer than 20 closed positions, non-positive realized PnL, or more than 65% of positive PnL concentrated in its best three closed positions.

Scores:

- `SHORT`: the most recent 50 closed positions;
- `LONG`: up to 200 closed positions;
- `GLOBAL`: `60% SHORT + 40% LONG`; this is the deterministic risk input;
- `EDGE`: confidence-weighted execution copyability derived from source price versus observed price. It remains neutral at 50 without evidence and is not a prediction or expected-return score.

Eligible wallets require both SHORT and LONG to pass the conservative eligibility rules.

## Settlement

Open PAPER positions are checked against public closed-market outcomes. A position is settled only when its token exposes a terminal price near `0` or `1`. Settlement is idempotent, records proceeds and cost basis, realizes PnL, updates cash, and creates a portfolio snapshot. Ambiguous closed-market prices are deferred rather than inferred.

These are engineering controls, not evidence of profitability. SHORT/LONG divergence, EDGE sample size, resolved settlements, realized PnL, drawdown, and operational failures must all mature before any future LIVE implementation is considered.
