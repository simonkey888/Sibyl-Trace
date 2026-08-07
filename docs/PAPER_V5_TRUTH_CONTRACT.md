# Sibyl Trace — PAPER V5 Truth Contract

`PAPER V5` is a new additive cohort. It does **not** rewrite the historical PAPER V2 database, fills, PnL, settlements or evidence. V2 remains labeled `LEGACY_SIMULATION_MIDPOINT_V2` because its simulator treated an observed midpoint as an immediate fill.

## What V5 is allowed to call a fill

A V5 copied trade may become a simulated fill only when all of the following survive:

1. the source activity is a concrete public Polymarket trade with a stable hashed identity;
2. the source wallet passes the frozen risk/source-quality policy;
3. current public CLOB market metadata exposes tick size, minimum order size and a supported per-market taker fee schedule;
4. an executable decision quote exists — BUY uses asks, SELL uses bids; midpoint is forbidden;
5. the best executable price passes the slippage/risk gate against the source trade;
6. V5 re-fetches the public order book at the simulated arrival point;
7. a Fill-And-Kill simulation can consume actual displayed L2 size without crossing the worst-price limit;
8. fees are charged per consumed price level using the current market fee schedule.

The unfilled remainder is cancelled. `PARTIAL_FILLED` and `NO_FILL` are valid evidence, not errors to be hidden.

## Latency

V5 does not invent a generic exchange latency. For ordinary markets the arrival book is the immediately re-fetched public book. Both decision and arrival book hashes/timestamps are retained. If the CLOB market metadata explicitly flags taker-order delay, V5 injects the documented 250 ms delayed-market wait before the arrival-book re-fetch. This remains a PAPER approximation: without submitting a real order we cannot prove the exact queue position or exchange match that a real order would receive.

## Fees

V5 does not rely on a hard-coded category fallback. It requires the fee details returned for the concrete CLOB market and fails closed if the schedule is absent or unsupported.

For the currently documented taker-only schedule, V5 applies:

`fee = shares × feeRate × price × (1 - price)`

and rounds the fee to five decimal places. BUY cost basis includes fees; SELL proceeds are net of fees.

## Mark-to-market

Open V5 inventory is not marked at midpoint. It is marked to **net executable liquidation value** by walking current bids as a simulated SELL FAK, including fees. Any residual shares that cannot be liquidated in the observed book receive a zero mark. This is intentionally conservative.

## WIN / LOSS contract

A source signal is not a prediction result.

Directional accuracy includes only V5 `BUY` predictions that:

- received a non-zero `FILLED` or `PARTIAL_FILLED` simulated execution; and
- later obtained a terminal closed-market token result from Polymarket.

Terminal token price `1` => `WIN`; terminal token price `0` => `LOSS`.

The following are excluded from the accuracy denominator:

- `REJECTED`
- `NO_FILL`
- unresolved/open BUYs
- SELL signals, which are treated as exits rather than new directional predictions.

If there are no resolved filled BUYs, accuracy is `UNPROVEN`, never `0%` or `100%` by implication.

## Provenance

The private V5 ledger retains, per prediction:

- public source transaction hash;
- normalized source-payload SHA-256;
- source timestamp and price;
- decision-book hash and timestamp;
- arrival-book hash and timestamp;
- fee/tick/minimum-order rules;
- requested and filled size;
- average and effective fill price;
- fee, slippage, fill fraction and levels consumed;
- execution status;
- terminal resolution status/result when available.

The public Cloudflare terminal receives only the sanitized V5 summary. The raw prediction ledger and rolling SQLite state remain private artifacts.

## Safety boundary

PAPER V5 keeps the existing hard boundaries:

- `LIVE_TRADING_ENABLED=false`
- no real-money execution
- no order placement
- no signing/private keys
- no paid APIs
- `COST_AUTHORIZED_USD=0`

## External basis

Implementation choices were checked against current public sources on 2026-08-07, especially:

- Polymarket Prices & Orderbooks: https://docs.polymarket.com/market-data/prices-order-books
- Polymarket Market WebSocket channel: https://docs.polymarket.com/developers/CLOB/websocket/market-channel
- Polymarket Fees: https://docs.polymarket.com/trading/fees
- Polymarket order types / market orders: https://docs.polymarket.com/developers/CLOB/orders/create-order
- Polymarket public CLOB market metadata API.

Recent research was used as a skepticism check, not as ground truth for fills: contemporary benchmark work models prediction-market strategies against order-book execution, and recent large-scale microstructure studies report that apparent predictive signals often fail out of sample. No external paper is used to manufacture a V5 fill or a positive edge claim.

## R2 correction — 2026-08-07

The first integrated V5 run is retained only as invalid-adapter audit evidence and is not canonical. A live public CLOB probe showed fee details `e=1` for sports/eSports (`r=0.05`) and BTC (`r=0.07`). R2 validates that observed/documented fee curve, uses the official 250 ms delayed-market window when `itode=true`, starts a fresh rolling state, and fails the run if adapter failures occur while zero decision books are reached. Cohort ID: `PAPER_V5_R2_CLOB_FEE_CURVE_2026_08_07`.
