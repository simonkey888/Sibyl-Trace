# Wallet Forensics V1

`WALLET_FORENSICS_V1` is a read-only evidence lane beside the existing R4.4
source-strategy gate. It does **not** authorize PAPER execution, enable LIVE, or
change which source-strategy classifications are eligible.

## Purpose

The original gate asks whether a public source is sufficiently directional to be
prospectively copied in the current PAPER experiment. Forensics asks a different
question: what observable public behavior explains the wallet, and which claims
remain unavailable from the evidence Sibyl actually has?

The lanes are:

- `DIRECTIONAL_COPY_RESEARCH` — may remain eligible under R4.4.
- `STRUCTURAL_MAKER_RESEARCH` — research-only maker-rebate behavior.
- `STRUCTURAL_FULL_SET_RESEARCH` — research-only split/merge/conversion behavior.
- `STRUCTURAL_TWO_SIDED_RESEARCH` — research-only paired-outcome behavior.
- `INSUFFICIENT_EVIDENCE_RESEARCH` / `UNAVAILABLE_RESEARCH` — fail-closed.

Maker/taker mix is deliberately an **execution-style** observation, not a
strategy-direction label. A directional trader can execute passively, so a high
maker ratio alone never rewrites R4.4 directionality or selection.

## Public evidence used

V1 reuses the bounded point-in-time Data API activity sample and the closed
positions already fetched by the scanner. To avoid relying on ambiguous
multi-value query serialization, the activity fetch does not send a `type`
filter. It reads the bounded public activity stream and classifies the currently
recognized event types locally:

`TRADE,SPLIT,MERGE,REDEEM,REWARD,CONVERSION,MAKER_REBATE,REFERRAL_REWARD`.

Unknown future event types may be present in the source sample but do not acquire
meaning silently. A non-list activity response fails the source-strategy fetch
closed.

For each profiled wallet V1 records descriptive counts and observed USDC fields,
including BUY/SELL counts, paired-condition evidence, round-trip assets,
SPLIT/MERGE/REDEEM activity, maker rebates, rewards, and the bounded
closed-position PnL sample already used by the scanner.

V1 also measures a bounded recent maker/taker sample using the public Data API
`/trades` endpoint twice: once with `takerOnly=false` and once with
`takerOnly=true`. Taker rows must reconcile as a multiset subset of all-trade
rows inside a comparable time window. Truncation is detected from the raw page
length before filtering. Any malformed row or orphan taker row makes the ratio
unproven rather than estimating through the gap. Both reconciled samples receive
deterministic hashes. The ratio is labelled `RECENT_OVERLAP_SAMPLE`; it is not a
lifetime claim.

When at least 20 reconciled fills are available, V1 labels execution style as
`MAKER_HEAVY` (maker ratio >= 80%), `TAKER_HEAVY` (maker ratio <= 20%), or
`MIXED`. This label is descriptive only and never changes `Wallet.selected`.

## Claims deliberately not made

V1 does **not** manufacture fields that require evidence Sibyl does not possess.
These remain null/unproven:

- 10s/30s/60s markout;
- audit-grade cashflow PnL reconstruction;
- randomized population/control-group percentile;
- hold-to-resolution and scratch-exit ratios;
- alpha, expected return, or profitability proof.

Maker/taker also remains null/unproven whenever the two public trade pages cannot
be reconciled safely. A successful maker/taker sample is execution-style evidence
only; it is not alpha, expected return, or profitability evidence.

`reported_realized_pnl` is explicitly labelled as Data API closed-position
reported PnL. It is not a reconstructed cash ledger and must not be read as a
lifetime total when the upstream closed-position sample is bounded.

A scanner-cohort realized-PnL percentile may be emitted as descriptive context.
It is labelled `CURRENT_SCANNER_CANDIDATES_REPORTED_REALIZED_PNL` and must never
be presented as a randomized control group.

## Failure isolation

Source-strategy evidence remains the R4.4 selection authority. If the activity
fetch or classification fails, selection fails closed. If only the optional
forensics calculation fails after a valid R4.4 classification, Sibyl persists an
`UNAVAILABLE_RESEARCH` forensic profile but preserves the valid source-strategy
classification. Optional forensics therefore cannot become an accidental new
selection blocker.

## Persistence and API

Profiles are persisted in `wallet_forensics_profiles` with a deterministic
SHA-256 evidence hash and also written to the scanner state key
`paper_v5_wallet_forensics_profiles`.

They are exposed as nested `forensics` data on `/api/v1/wallets` and
`/api/v1/dashboard`, plus a read-only `/api/v1/wallet-forensics` endpoint.

## Safety invariants

- `execution_gate=false` in every forensic profile.
- R4.4 remains the source-strategy selection gate; forensics does not promote or veto.
- No private key, signing, order placement, deploy, or paid data source is added.
- `COST_AUTHORIZED_USD=0` and structural LIVE absence are unchanged.
