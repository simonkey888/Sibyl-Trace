# Wallet Forensics V1

`WALLET_FORENSICS_V1` is a read-only evidence lane that runs beside the existing
R4.4 source-strategy gate. It does **not** authorize PAPER execution and it does
not weaken the directional-only selection rule.

## Purpose

The original gate answers one narrow question: is a public source sufficiently
directional to be prospectively copied in the current PAPER experiment?

Forensics answers a different question: what observable public behavior explains
a wallet, and which claims remain unavailable from the evidence Sibyl actually
has?

The separation is intentional:

- `DIRECTIONAL_COPY_RESEARCH` may remain eligible under the existing R4.4 gate.
- `STRUCTURAL_MAKER_RESEARCH`, `STRUCTURAL_FULL_SET_RESEARCH`, and
  `STRUCTURAL_TWO_SIDED_RESEARCH` are research-only lanes.
- Forensics never changes `Wallet.selected` by itself.

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
`takerOnly=true`. The taker rows must reconcile as a multiset subset of the
all-trades rows inside a comparable time window. If the taker page is row-limited,
the comparison is restricted to the shared covered window. Any orphan taker row
fails the metric closed. The resulting ratio is explicitly labelled
`RECENT_OVERLAP_SAMPLE`; it is not a lifetime maker/taker claim.

## Claims deliberately not made

V1 does **not** manufacture fields that require evidence Sibyl does not possess.
The profile therefore leaves these values null/unproven:

- 10s/30s/60s markout;
- audit-grade cashflow PnL reconstruction;
- population/control-group percentile;
- hold-to-resolution and scratch-exit ratios;
- alpha, expected return, or profitability proof.

Maker/taker remains null/unproven whenever the two public trade pages cannot be
reconciled safely. A successful recent maker/taker sample is execution-style
evidence only; it is not alpha, expected return, or profitability evidence.

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
- The existing R4.4 directional gate remains the only source-strategy selector.
- No private key, signing, order placement, deploy, or paid data source is added.
- `COST_AUTHORIZED_USD=0` and structural LIVE absence are unchanged.
