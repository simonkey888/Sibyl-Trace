# SIBYL TRACE — MASTER ORDER / RESEARCH LAB V3

Status: AUTHORIZED FOR EXECUTION
Baseline: `ceb2db8aa093037a76b820d176188471ea18bf9d`
Mode: additive / PAPER-only / zero-cost

## 0. Non-negotiable invariants

1. Preserve all pre-existing PAPER evidence, rolling SQLite state, release assets and freeze manifests.
2. Do not reset, truncate, rewrite or reinterpret historical evidence to make results look better.
3. Keep `LIVE_TRADING_ENABLED=false`; no private keys, signing, order-posting, wallet funding or real-money paths may be added to the active GitHub PAPER runtime.
4. `COST_AUTHORIZED_USD=0` remains structural. No paid API or payment data may be required.
5. Existing wallet scanner, scoring, settlement, accounting reconciliation, watchdogs, Cloudflare terminal and hourly GitHub PAPER cycle remain functional.
6. New research modules must fail soft unless corruption of evidence/accounting is detected. A new feed or experimental model may be unavailable without taking down legacy PAPER.
7. Production-visible Cloudflare data remains sanitized, read-only and derived only from PASS PAPER evidence.
8. No copied third-party implementation is accepted blindly. Re-implement patterns in Sibyl-native code, preserving license boundaries and recording provenance.

## 1. Objective

Increase the fidelity of Sibyl-Trace research evidence without changing the trusted execution/risk core. The upgrade must add historical replayability, execution microstructure, richer cross-market features, a deterministic market-making PAPER laboratory, and a future-proof venue abstraction.

## 2. Source patterns selected

Primary engineering references:

- `Jon-Becker/prediction-market-analysis` — resumable market-data collection, durable research datasets, explicit schemas and reproducible analysis.
- `nkaz001/hftbacktest` — feed/order latency, queue position, L2/L3 replay and realistic fill modeling.
- `humanplane/cross-market-state-fusion` — cross-market feature construction from fast crypto feeds and Polymarket state; no adoption of its optimistic midpoint fill assumptions or profitability claims.
- `warproxxx/poly-maker` — pure quoting function, microprice, inventory skew, volatility/toxicity signals, market regimes and journal/replay separation.
- `pmxt-dev/pmxt` — normalized venue concepts and capability boundaries.
- `nautechsystems/nautilus_trader` / prediction-market replay projects — deterministic event-clock and strategy/execution separation patterns only; do not embed the complete framework.

## 3. Workstream A — normalized venue surface

Create a read-only venue abstraction that normalizes public market data without exposing trading credentials or order placement.

Required concepts:

- `VenueCapabilities`
- `NormalizedBook`
- `NormalizedTrade`
- read-only `VenueAdapter` protocol
- Polymarket adapter around the existing `PolymarketClient`

Acceptance:

- no order placement method in the protocol;
- deterministic normalization tests;
- existing Polymarket client contract remains intact.

## 4. Workstream B — microstructure and queue-aware PAPER execution

Add a pure deterministic microstructure library independent of networking and persistence.

Required calculations:

- best bid/ask and spread validation;
- L1 imbalance;
- depth-weighted microprice;
- signed-flow EWMA;
- adverse-selection/markout metric;
- conservative queue-ahead fill estimator;
- feed latency + synthetic order latency accounting;
- explicit reasons for `FILLED`, `PARTIAL`, `NOT_FILLED`, `INVALID`.

Queue simulation rules:

- never assume a resting order fills merely because the trade price touched it;
- consume queue-ahead before own quantity;
- ignore volume that cannot legally execute against the simulated side;
- cap simulated fill by requested quantity;
- expose assumptions in the result payload.

This module is research-only. It must not replace the frozen legacy PAPER accounting engine in V3.

## 5. Workstream C — richer cross-market features

Enrich captured fast-market events while retaining the known-good Binance/Coinbase/Polymarket feeds.

Additive features:

- signed aggressor flow from Binance spot trades when available;
- optional Binance Futures public feed as a non-critical source;
- rolling returns over multiple horizons;
- order-flow imbalance;
- cumulative signed volume / CVD-style feature;
- spread / microprice / volatility features from Polymarket book events;
- temporal feature snapshots suitable for deterministic research.

Rules:

- Binance Futures failure must be recorded but must not make the core feed watchdog RED when Binance spot + Coinbase + Polymarket remain healthy;
- no model may infer an executable edge from missing depth;
- source timestamp and receive timestamp remain distinct.

## 6. Workstream D — Market-Making PAPER Lab

Create a pure strategy research module. It may produce target quotes but may never submit orders.

Required inputs:

- current normalized book;
- inventory;
- short-window volatility;
- toxicity/adverse-selection estimate;
- market time remaining;
- deterministic config.

Required outputs:

- fair value / microprice;
- reservation price;
- bid/ask quote targets;
- proposed size;
- regime: `QUIET`, `TRENDING`, `EVENT`, `REDUCE_ONLY`, `HALTED`;
- reason codes and risk flags.

Safety:

- stale/invalid books => `HALTED`;
- near-expiry or inventory hard cap => `REDUCE_ONLY` or `HALTED`;
- quotes must remain inside [tick, 1-tick];
- no private key/API credentials/order endpoints.

## 7. Workstream E — durable replay journal

Persist enough public market observations to replay research decisions later without downloading a 36 GB external dataset during every Action run.

Implementation constraints:

- canonical SQLite remains the durable state already persisted in the private rolling release;
- add compact, schema-versioned research observations for raw/derived microstructure evidence;
- export a bounded compressed JSONL research journal per run for audit/replay;
- include manifest metadata and SHA256 in existing evidence hashing;
- avoid heavy new dependencies such as `pyarrow` in the hourly runner unless objectively necessary.

The journal must be append-derived from evidence; it must never mutate old rows.

## 8. Workstream F — deterministic replay and validation

Add an offline replay engine over normalized events.

Required behavior:

- stable ordering by receive timestamp with deterministic tie-breaking;
- no look-ahead access;
- replay book state and strategy decisions;
- allow queue-aware execution estimates;
- produce replay metrics and invariant violations;
- compare baseline top-of-book fill assumption versus queue-aware estimate without rewriting historical fills.

Existing Brier, CLV, edge velocity and deflated-signal metrics remain canonical and must not be duplicated under new names.

## 9. Workstream G — research-cycle integration

Integrate the new laboratories as additive sections of `research-summary.json`.

Expected top-level sections:

- `latency` — existing contract preserved;
- `microstructure_v3`;
- `cross_market_features_v3`;
- `market_making_v3`;
- `replay_v3`;
- `reference_research` — preserved;
- `totals` / watchdog state — preserved.

Any experimental section may report `DISABLED`, `NO_DATA` or `DEGRADED` without fabricating results.

## 10. Workstream H — Cloudflare terminal

Extend the existing terminal only after backend contracts are stable.

Display, when present:

- queue-aware execution evidence;
- current microprice / imbalance / toxicity;
- fast-market feature health;
- market-making PAPER regime and target quotes;
- replay sample count and disagreement versus naive fills.

Do not expose raw wallet addresses, secrets, API/control routes or unsafe payload fields.

## 11. Tests and gates

Mandatory before merge:

1. Ruff clean.
2. Full pytest suite green; no weakening/removal of existing tests.
3. New unit tests for venue normalization, microstructure, feature calculation, market-making, journal/replay and integration.
4. Existing accounting regression green.
5. Existing safety tests proving LIVE absent and cost zero green.
6. Dashboard/gateway tests green.
7. Container/build checks green.
8. Secret scan green.
9. PR diff review confirms no protected evidence deletion and no private-key/order-posting path.
10. Exact-head GitHub PAPER smoke restores previous SQLite state without reset and produces PASS.
11. A second PAPER smoke proves continuity/idempotency.
12. Cloudflare terminal is republished only from a PASS snapshot and remains read-only.

## 12. Promotion policy

This order promotes only RESEARCH capability. It does not authorize LIVE, real-money trading, credentials, paid services, external infrastructure spend or risk-limit relaxation.

A V3 hypothesis remains `UNPROVEN` until evidence thresholds are met. Zero opportunities/fills is a valid result and must never be replaced by synthetic success.

## 13. Completion definition

Complete only when:

- additive implementation merged to `main`;
- all gates PASS on exact main SHA;
- rolling PAPER state restored and advanced, never reset;
- new evidence appears in the PAPER artifact/release;
- Cloudflare terminal continues serving a sanitized PASS snapshot;
- final report distinguishes implemented capability from statistically proven edge.
