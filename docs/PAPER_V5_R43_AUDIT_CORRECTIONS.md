# Sibyl Trace — PAPER V5 R4.3 audit corrections

R4.3 is an additive prospective cohort. It does not rewrite V2, R3, R4, R4.1, or the unexecuted R4.2 cohort.

## Why R4.3 exists

An external adversarial review was re-audited against the private repository and the downloadable canonical GitHub artifacts. Only claims supported by primary evidence were promoted to code changes.

### Confirmed P1 — retrospective wallet-selection lookahead

The R4.1 canonical artifact (`run 31194666537`, artifact `9000414869`) contains 829 predictions. Independent parsing of its immutable ledger showed:

- run start: `2026-08-07T15:51:21.903338+00:00`;
- earliest source trade: `2026-08-07T14:21:37+00:00`;
- latest source trade: `2026-08-07T15:49:52+00:00`;
- all `829 / 829` source trades predate the run start;
- source ages at run start span about `89.9s` to `5384.9s`, with median about `2306.9s`.

The pre-R4.3 orchestration scanned and scored current wallet history first, then ingested unseen activity using those newly computed scores. That allowed information available at scan time to select a wallet for source trades that had already happened.

R4.3 reverses the temporal authority:

1. load the wallet selection that was armed by the previous successful cycle;
2. process only activity at or after that selection's effective timestamp;
3. settle and mark the resulting PAPER state;
4. rescan current public wallet history only after the active cycle's ingestion;
5. timestamp the new selection and arm it exclusively for the next cycle.

Source activity timestamps have integer-second resolution. The prospective selection boundary therefore starts on the next whole second after scoring so same-second activity cannot be retrospectively authorized by a score computed milliseconds later.

A clean R4.3 state therefore produces no copied trades on its first cycle. It only arms a future selection. This intentional cold start is evidence, not a failure.

Each R4.3 prediction persists `selection_effective_at`, `source_timestamp`, wallet identity and score in `paper_v5_r43_selection_provenance`. The immutable ledger requires `source_timestamp >= selection_effective_at`.

Selection evidence is cryptographically bound per prediction. R4.3 persists:

- the selection material hash;
- the inherited R4.2 execution-evidence hash when one exists;
- the derived R4.3 execution-evidence hash that binds those two objects.

The canonical report and Cloudflare publication gate fail closed on selection-material hash mismatch or selection-to-execution-evidence bridge mismatch.

### Confirmed P1 — end-cycle mark bypassed run-local shadow debt

R4.2 applies conservative run-local self-impact through `_R42TruthClient`, but the inherited orchestration performed its final `mark_positions_v5(db, client)` with the raw underlying client. The final portfolio mark could therefore ignore shadow depth already consumed earlier in the same run.

R4.3 uses the R4.2 truth client for the final post-ingestion mark. The pre-ingestion mark remains raw because no R4.3 run-local fills have occurred yet. This makes final canonical exposure/equity consistent with the same conservative shadow debt used by the execution path.

### Confirmed truth-surface bug — unavailable snapshot looked like zero performance

The external review observed `$0.00`, `0 / 0`, `NO V5` and the snapshot-loading failure message and concluded that previously published evidence had been replaced by zero values.

That interpretation was not supported by the code. The dashboard HTML contained numeric zero placeholders before any snapshot was loaded. On `/data/snapshot.json` failure, `renderFailure()` changed the failure banner but did not overwrite those placeholders. The zeros therefore represented unavailable UI state, not canonical evidence.

R4.3 changes unloaded numeric placeholders to `—`, `— / —`, `Snapshot unavailable`, and `No verified snapshot`. A failed snapshot fetch can no longer visually masquerade as a zero-equity or zero-fill evidence set.

Historical deployment evidence also proves that the snapshot asset existed at least at the R3 deployment: successful Cloudflare publisher run `31184618441` verified V4 evidence, attached V5 run `31184431024`, generated `apps/dashboard/public/data/snapshot.json`, and Wrangler explicitly uploaded `/data/snapshot.json` before deploying worker version `1cf735db-8cd2-4403-9de7-cbd20a191a56`.

The present external reason a later browser may fail to load that asset is not inferred here without fresh runtime evidence.

## Evidence/reporting corrections

### Canonical R4.1 ZIP digest

The previously generated external context pack accidentally truncated the R4.1 artifact digest. The repository/artifact evidence is not corrupt.

Correct ZIP digest:

`sha256:9f75a5202f28d7dbcbf5bff9cf151344abebbb1b3c999ba6ff6a1aef5e2cdd0e`

The R4.1 release also exposes independently hashed assets, including:

- `paper-v5-summary.json`: `d461dd6971be8ea4ecb97c31ed90586df5ab84eeb18f64b31393f3e9debf31b0`
- `paper-v5-summary.md`: `455f86e240d34c9e2e52b37ba8642f4a26943126325960f2ef512e49850a08c7`
- `prediction-ledger-v5.jsonl`: `2ca15e6a86d7a53ee7bb035c58284f5029915c118a9781f691ff3be98bc47db5`
- `evidence-manifest-v5.json`: `98f1653a5d585b7e74ed1476aa36aa11a5cafb095505c60514894357b5ecba3d`
- rolling DB gzip: `b745a060e6610871c5578dbdb0bba640e028890e15589864c4e43e576ef382e4`

### R4.1 economics directly verified from the artifact

Direct immutable-ledger parsing gives:

- 10 fills;
- 10 distinct arrival-book hashes;
- 9 distinct assets;
- total entry fees: `1.07366 USD`;
- all 10 fills use dynamic fee rate `0.05`, exponent `1`;
- minimum order size `5.0` on all 10 fills;
- tick size `0.01` on 9 fills and `0.001` on 1 fill;
- effective fee-inclusive copy-decay versus source: mean `+0.0136374544`, median `+0.0182194245`.

These are artifact-derived observations. They do not establish profitability because the cohort still has zero settled predictions.

### R4.1 reject/no-fill taxonomy was already internally exact

The external review's `819 vs 608` overlap claim was false. Direct ledger parsing gives:

- `REJECTED/slippage_limit = 557`
- `REJECTED/insufficient_risk_capacity = 51`
- rejected total = `608`
- `NO_FILL/market_not_trade_ready = 176`
- `NO_FILL/empty_executable_book = 30`
- `NO_FILL/below_min_order_or_price_limit = 5`
- no-fill total = `211`
- `FILLED = 10`
- total = `608 + 211 + 10 = 829`

No new adapter-failure status or reject taxonomy is introduced from that incorrect premise.

### R4 adapter failures were already classified

Direct parsing of the R4 artifact (`run 31192915763`) gives:

- `REJECTED/slippage_limit = 422`;
- `REJECTED/market_data_unavailable:...condition mismatch = 232`;
- `REJECTED/insufficient_risk_capacity = 88`;
- `NO_FILL/empty_executable_book = 50`;
- `FILLED = 8`.

The 232 Gamma identity adapter failures were already part of the `REJECTED` population, not missing from the status partition.

## Claims rejected as insufficient for a truth correction

### Arbitrary order-book staleness threshold

The CLOB book `timestamp` is retained in the execution database. R4.3 also emits it in the immutable ledger together with local receipt times and both signed offsets and nonnegative ages. It is treated as an upstream book-state timestamp, not as proof of HTTP response freshness. A quiet but valid book may legitimately retain an older state timestamp. R4.3 therefore does not invent an unsupported millisecond fail threshold.

### Price-band or maximum-share censorship

The R4.1 low-price fill near `0.001` is unusual and distorts share-weighted descriptive statistics, but no primary exchange rule was found that makes the fill invalid solely because its probability is very small or its share count is large. R4.3 does not alter the trading hypothesis with an arbitrary price band or share cap. Copy-decay remains a per-fill metric; any aggregate analysis must use robust statistics explicitly.

### Retroactive R4.1 self-impact rewrite

All ten canonical R4.1 fills have distinct arrival-book hashes. That does not prove perfect live-equivalent liquidity, but it defeats the specific claim that the ten fills demonstrably double-consumed one identical arrival snapshot. Historical evidence remains frozen.

### Public-repository workaround

Making the private repository public merely to obtain GitHub-hosted runner capacity is not an evidence correction and would change a security/privacy boundary. R4.3 does not do it. No paid workaround is authorized either.

## R4.3 truth contract

R4.3 inherits all R4.2 requirements and adds:

- `prospective_wallet_selection = true`
- `preselection_activity_backfill = false`
- next-whole-second effective boundary for newly scored wallet selections
- per-prediction selection provenance in the immutable ledger
- `source_timestamp >= selection_effective_at` for every prediction
- cryptographic selection provenance hash
- R4.2 execution-evidence parent hash retained when available
- R4.3 execution-evidence hash bound to selection provenance
- zero selection hash/bridge mismatches required for PASS/publication
- final post-ingestion mark uses the run-local shadow client
- decision/arrival CLOB state timestamps and signed receipt offsets exposed in ledger timing evidence
- book state timestamps are not misrepresented as a network-freshness SLA
- unavailable public snapshot renders unavailable values, never performance zeros
- clean rolling state tag: `github-paper-v5-state-v4-3`
- cohort: `PAPER_V5_R4_3_PROSPECTIVE_TRUTH_2026_08_08`

Safety remains unchanged: PAPER only, no order placement, no private keys, no real money, no paid APIs, authorized cost USD 0.
