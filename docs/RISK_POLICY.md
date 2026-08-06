# Paper risk policy

| Rule | Default |
|---|---:|
| Minimum wallet score | 65 |
| Maximum signal age | 30 seconds |
| Maximum absolute copy slippage | 0.03 |
| Maximum exposure per outcome | 2% of equity |
| Maximum total exposure | 15% of equity |
| Maximum daily loss | 3% of equity |
| Maximum drawdown | 10% |
| Copied source notional | 10% |
| Minimum paper order | USD 1 |

Sells are never opened naked. A copied SELL can only reduce an existing paper position in the same asset.

## Wallet eligibility

A wallet is rejected when it has fewer than 20 closed positions, non-positive realized PnL, or more than 65% of positive PnL concentrated in its best three closed positions. Eligible wallets are ranked using history depth, win rate, profit factor, and diversification.

These are initial engineering limits, not evidence of profitability. They must be calibrated from paper evidence before any future live implementation is considered.
