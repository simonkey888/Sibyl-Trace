# PAPER V5 R4.4 — source-strategy truth review

## Decision

R4.4 does **not** import a market-making, LP, merge/redeem, signing, WebSocket execution, or LIVE order path. It adds one evidence gate before prospective wallet selection: a source wallet with positive historical ranking evidence must also have public activity consistent with a directional strategy before Sibyl may treat its individual trades as a directional research candidate. Passing this gate is not alpha or profitability evidence.

## Why this is required

R4.3 fixed temporal lookahead, but wallet ranking still used closed-position PnL, win rate, profit factor and concentration. Those metrics do not establish *how* the wallet earned the PnL. A wallet can be profitable from maker behavior, two-sided inventory management, or complete-set operations while an observer copying one trade leg loses the economics that made the source profitable.

That is a source-attribution failure, not an execution-speed problem.

## Evidence reviewed

### Polymarket public mechanics

Official Polymarket documentation describes complete-set mechanics: collateral can be split into complementary outcome tokens and equal complementary quantities can be merged back into collateral. The current public user-activity API exposes, among others, `TRADE`, `SPLIT`, `MERGE`, `CONVERSION`, `MAKER_REBATE`, `TAKER_REBATE` and `REDEEM`, and documents `type` as a comma-separated list.

R4.4 treats `SPLIT/MERGE/CONVERSION` and repeated two-sided outcome trading as structural evidence that the observed source behavior is not cleanly represented by Sibyl's directional taker-copy model. `MAKER_REBATE` is execution-style metadata only: its presence does not establish directionality or non-directionality and cannot by itself select or reject a source. Rebate/cashflow metadata remains research-only and does not alter score weights or execution.

### `ohehe` claim supplied for review

The public profile exists and recent public esports leaderboard data shows substantial positive PnL. The supplied exact claims of USD 212k monthly profit, USD 3.3m volume and 54.1% win rate were not independently established during this review and are **not** encoded as facts or thresholds.

The architectural lesson does not depend on those exact numbers: complete-set and two-sided economics are mechanically possible and must not be mislabeled as directional predictive evidence.

### External repositories

Reviewed conceptually, not vendored or copied into runtime:

- `alsk1992/CloddsBot`: models arbitrage as a strategy class separate from directional prediction.
- `MrFadiAi/Polymarket-bot`: its copy-trading notes explicitly question whether a target has copyable alpha, distinguish trade/position/signal following, and discuss maker behavior and latency/slippage.
- `lihanyu81/polymarket_lp_tool`: contains inventory-aware passive liquidity and anti-sniping ideas. These are maker/LP concerns and are intentionally **not** imported into Sibyl's PAPER taker-copy engine.

## R4.4 policy

For a bounded point-in-time public activity sample captured before selection becomes effective:

1. only events with a positive timestamp at or before the fixed cutoff are admissible evidence;
2. `MAKER_REBATE` is recorded as execution-style metadata only and has no directionality shortcut;
3. any `SPLIT`, `MERGE` or `CONVERSION` => `NON_DIRECTIONAL_FULL_SET`;
4. repeated trading of both outcomes in at least the configured number of conditions and at/above the configured trade fraction => `NON_DIRECTIONAL_TWO_SIDED`;
5. fewer than the configured minimum **attributable** trades (condition + outcome) => `INSUFFICIENT_EVIDENCE`;
6. only the remainder => `DIRECTIONAL_CANDIDATE`.

The public activity request uses pages of at most 500 rows and a configured upper bound of 10,000 rows, but that bound is **not** treated as proof of completeness. `start=1` and `end=cutoff_at` define the point-in-time request. Selection is allowed only when pagination proves `COMPLETE + exhausted + no_more`; a full bound, malformed row, transport failure, missing completeness metadata, or unknown continuation fails closed. The thresholds are research policy, not universal market facts. They are persisted in the evidence profile and hashed with the point-in-time sample.

`outcomeIndex` is preferred for paired-outcome classification. If it is absent, normalized `outcome` is the fallback and is also included in the canonical event hash. Missing/future/zero timestamps cannot authorize a source.

## Temporal authority

The source-strategy cutoff must be strictly earlier than the R4.3 prospective `selection_effective_at`. R4.4 checks that invariant before processing a source trade and again during report reconciliation. A valid profile hash captured too late is therefore still invalid for the active selection.

## Evidence chain

Each selected source has a deterministic source-strategy profile containing:

- point-in-time cutoff;
- count of admissible events and invalid timestamp evidence;
- attributable/unattributable trade counts;
- activity sample hash;
- classification and reason;
- maker/taker rebate and full-set event counts;
- paired-outcome metrics;
- exact policy thresholds;
- source-strategy evidence hash.

Each R4.4 prediction then extends the immutable chain:

`R4.2 book/market evidence -> R4.3 prospective-selection evidence -> R4.4 source-strategy evidence`.

A missing, non-directional, hash-invalid, or temporally invalid profile fails closed.

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
