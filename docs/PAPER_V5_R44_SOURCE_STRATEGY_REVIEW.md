# PAPER V5 R4.4 — source-strategy truth review

## Decision

R4.4 does **not** import a market-making, LP, merge/redeem, signing, WebSocket execution, or LIVE order path. It adds one evidence gate before prospective wallet selection: a profitable source wallet must also have public activity consistent with a directional strategy before Sibyl may treat its individual trades as copyable directional alpha.

## Why this is required

R4.3 fixed temporal lookahead, but wallet ranking still used closed-position PnL, win rate, profit factor and concentration. Those metrics do not establish *how* the wallet earned the PnL. A wallet can be profitable from maker rebates, two-sided inventory management, or complete-set operations while an observer copying one trade leg loses the economics that made the source profitable.

That is a source-attribution failure, not an execution-speed problem.

## Evidence reviewed

### Polymarket public mechanics

Official Polymarket documentation describes complete-set mechanics: collateral can be split into complementary outcome tokens and equal complementary quantities can be merged back into collateral. Public user activity also exposes event types including `TRADE`, `SPLIT`, `MERGE`, `CONVERSION`, `MAKER_REBATE` and `REDEEM`.

R4.4 therefore treats `MAKER_REBATE` and `SPLIT/MERGE/CONVERSION` as direct evidence that historical profitability contains economics Sibyl's directional taker-copy model does not reproduce.

### `ohehe` claim supplied for review

The public profile exists and recent public esports leaderboard data shows substantial positive PnL. The supplied exact claims of USD 212k monthly profit, USD 3.3m volume and 54.1% win rate were not independently established during this review and are **not** encoded as facts or thresholds.

The architectural lesson does not depend on those exact numbers: complete-set and two-sided economics are mechanically possible and must not be mislabeled directional alpha.

### External repositories

Reviewed conceptually, not vendored or copied into runtime:

- `alsk1992/CloddsBot`: models arbitrage as a strategy class separate from directional prediction.
- `MrFadiAi/Polymarket-bot`: its copy-trading notes explicitly question whether a target has copyable alpha, distinguish trade/position/signal following, and discuss maker behavior and latency/slippage.
- `lihanyu81/polymarket_lp_tool`: contains inventory-aware passive liquidity and anti-sniping ideas. These are maker/LP concerns and are intentionally **not** imported into Sibyl's PAPER taker-copy engine.

## R4.4 policy

For a bounded point-in-time public activity sample captured before selection becomes effective:

1. any `MAKER_REBATE` => `NON_DIRECTIONAL_MAKER`;
2. any `SPLIT`, `MERGE` or `CONVERSION` => `NON_DIRECTIONAL_FULL_SET`;
3. repeated trading of both outcomes in at least the configured number of conditions and at/above the configured trade fraction => `NON_DIRECTIONAL_TWO_SIDED`;
4. fewer than the configured minimum trade observations => `INSUFFICIENT_EVIDENCE`;
5. only the remainder => `DIRECTIONAL_CANDIDATE`.

The thresholds are research policy, not universal market facts. They are persisted in the evidence profile and hashed with the point-in-time sample.

## Evidence chain

Each selected source has a deterministic source-strategy profile containing:

- point-in-time cutoff;
- activity sample hash;
- classification and reason;
- event counts and paired-outcome metrics;
- exact policy thresholds;
- source-strategy evidence hash.

Each R4.4 prediction then extends the immutable chain:

`R4.2 book/market evidence -> R4.3 prospective-selection evidence -> R4.4 source-strategy evidence`.

A missing, non-directional, or hash-invalid profile fails closed.

## Explicit non-changes

R4.4 does not:

- claim the source wallet has economic edge;
- claim profitability after copying;
- add real order placement or private keys;
- add maker posting/cancel/replace logic;
- add merge/redeem execution;
- add WebSocket latency racing;
- add paid APIs or authorized spend;
- rewrite any historical V2/R3/R4/R4.1/R4.2/R4.3 evidence;
- change wallet-score weights;
- add arbitrary low-price/share caps or a fabricated book-staleness SLA.

A source that passes R4.4 is only a **directional candidate**. Economic edge remains unproven until prospective settled evidence supports it.
