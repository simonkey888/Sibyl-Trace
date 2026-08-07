# SIBYL TRACE — MASTER ORDER / RESEARCH LAB V4

Status: AUTHORIZED FOR EXECUTION
Baseline: `c72391278e4e12632bde91134fca5806953ef036`
Mode: additive / PAPER-only / zero-cost / shadow research

## 0. Invariants

1. Preserve PAPER V2 and Research V3 code, evidence, rolling SQLite state and release assets.
2. Never rewrite historical fills or reinterpret old evidence to improve results.
3. Keep LIVE trading absent. No private keys, signing, wallet funding or order-posting paths.
4. Keep `COST_AUTHORIZED_USD=0`; no paid service is required.
5. V4 is shadow research. Failure of V4 cannot take down PAPER V2/V3.
6. New evidence must be deterministic, provenance-bearing and auditable.
7. Cross-venue equivalence requires resolution-rule parity, not title similarity alone.
8. Never claim L3 fidelity when only L2/public trades are observable.

## 1. Objective

Raise evidence fidelity from top-of-book probes to reconstructable L2 event tapes; derive persistent lead/lag features over seconds and minutes; add read-only Kalshi normalization; gate cross-venue parity on explicit market identity; add sealed benchmark episodes; and separate forecast skill from trading PnL with market-relative statistical evidence.

## 2. Workstreams

### A — Lossless event tape and deterministic L2 reconstruction

Add schema-versioned snapshot, delta and trade events with sequence/gap checks. Reconstruct L2 books deterministically without inventing order identity.

### B — Durable temporal features

Retain 1s/5s/10s features and add 1m/5m/10m/30m features from persisted observations, including CVD velocity/acceleration, spot-futures basis, venue divergence and response-lag helpers.

### C — Read-only Kalshi venue surface

Normalize public Kalshi market metadata, YES/NO bid books and trades into Sibyl-native read-only structures. No portfolio/order endpoints and no credentials in the active path.

### D — Market identity engine

Represent venue contracts using underlying, event, outcome, strike, cutoff, timezone, resolution source/rule and exceptions. Semantic/token similarity may nominate candidates but cannot establish equivalence. Output EXACT_EQUIVALENT, CONDITIONAL_EQUIVALENT, NON_EQUIVALENT or UNKNOWN.

### E — Cross-venue parity lab

Compare executable binary prices only after identity gating. Include available size and explicit fee/settlement adjustments. Never emit an executable order.

### F — Sealed benchmark episodes

Create immutable episode manifests with hashes for events, settlement, strategy config and expected invariants. Identical inputs must produce identical episode IDs and replay summaries.

### G — Forecast skill and power

Preserve canonical Brier/CLV/deflated-signal metrics. Add Brier decomposition, market-relative Brier alpha, bootstrap confidence intervals and minimum-sample power approximations. Profit is not evidence of forecast skill by itself.

### H — V3/V4 shadow comparison

Produce an additive V4 summary that can compare V3 top-of-book/queue-probe assumptions with richer L2/tape evidence without mutating V2/V3 outputs.

## 3. Acceptance gates

- Existing full backend test suite remains green.
- Existing PAPER accounting regression remains green.
- Existing safety freeze remains green.
- New V4 unit tests cover tape gaps, reconstruction, temporal horizons, identity mismatch, Kalshi complement conversion, parity costs, sealed episode stability and forecast skill.
- Ruff, pip check, dependency audit, dashboard and container gates remain green.
- PR diff contains no private-key/order-posting path and no protected evidence deletion.
- Merge only from exact tested PR head.
- Exact merged `main` must be re-verified by CI.

## 4. Completion semantics

V4 capability may be COMPLETE while `EDGE_STATUS=UNPROVEN`. Zero parity opportunities, insufficient statistical power and disagreement with V3 are valid outcomes and must remain visible.