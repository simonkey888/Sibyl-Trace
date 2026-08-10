# Wallet Forensics V1

`WALLET_FORENSICS_V1` is a read-only evidence lane beside the existing R4.4
source-strategy gate. It does **not** authorize PAPER execution, enable LIVE, or
promote a source that R4.4 already rejected.

## Purpose

The original gate asks whether a public source is sufficiently directional to be
prospectively copied in the current PAPER experiment. Forensics asks a different
question: what observable public behavior explains the wallet, and which claims
remain unavailable from the evidence Sibyl actually has?

The lanes are:

- `DIRECTIONAL_COPY_RESEARCH` — may remain eligible under R4.4.
- `STRUCTURAL_MAKER_RESEARCH` — research-only maker-style behavior.
- `STRUCTURAL_FULL_SET_RESEARCH` — research-only split/merge/conversion behavior.
- `STRUCTURAL_TWO_SIDED_RESEARCH` — research-only paired-outcome behavior.
- `INSUFFICIENT_EVIDENCE_RESEARCH` / `UNAVAILABLE_RESEARCH` — fail-closed.

Forensics can only **narrow** eligibility. A nominally directional source is
vetoed when a reconciled recent sample contains at least 20 fills and maker ratio
is at least 80%. The veto can never make a rejected wallet copyable.

## Public evidence used

V1 reuses the bounded point-in-time Data API activity sample and the closed
positions already fetched by the scanner. The activity request is restricted to
the currently documented Polymarket activity enums:

`TRADE,SPLIT,MERGE,REDEEM,REWARD,CONVERSION,MAKER_REBATE,REFERRAL_REWARD`.

For each profiled wallet it records descriptive counts and observed USDC fields,
including BUY/SELL counts, paired-condition evidence, round-trip assets,
SPLIT/MERGE/REDEEM activity, maker rebates, rewards, and reported closed-position
PnL.

V1 also measures a bounded recent maker/taker sample using the public Data API
`/trades` endpoint twice: once with `takerOnly=false` and once with
`takerOnly=true`. Taker rows must reconcile as a multiset subset of all-trade
rows inside a comparable time window. If the taker page is row-limited, the
comparison is restricted to the shared covered window. Any orphan taker row
fails the metric closed. The ratio is labelled `RECENT_OVERLAP_SAMPLE`; it is not
a lifetime maker/taker claim.

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
reported PnL. It is not a reconstructed cash ledger.

A scanner-cohort realized-PnL percentile may be emitted as descriptive context.
It is labelled `CURRENT_SCANNER_CANDIDATES_REPORTED_REALIZED_PNL` and must never
be presented as a randomized control group.

## Persistence and API

Profiles are persisted in `wallet_forensics_profiles` with a deterministic
SHA-256 evidence hash and also written to the scanner state key
`paper_v5_wallet_forensics_profiles`.

They are exposed as nested `forensics` data on `/api/v1/wallets` and
`/api/v1/dashboard`, plus a read-only `/api/v1/wallet-forensics` endpoint.

## Safety invariants

- `execution_gate=false` in every forensic profile.
- Forensics may only narrow R4.4 eligibility through the structural-maker veto.
- No private key, signing, order placement, deploy, or paid data source is added.
- `COST_AUTHORIZED_USD=0` and structural LIVE absence are unchanged.
