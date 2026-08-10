# Sibyl Trace

Cloud-native prediction-market wallet intelligence and auditable PAPER execution research.

## Canonical truth

The current implementation contract is **PAPER V5 R4.5** (`PAPER_V5_R4_5_REGIME_EVIDENCE_2026_08_09`).

- Default runtime mode is `READ_ONLY`.
- PAPER requires both `TRADING_MODE=PAPER` and `PAPER_TRADING_ENABLED=true`.
- `LIVE` is structurally unavailable; `LIVE_TRADING_ENABLED=true` is rejected by configuration.
- Authorized cost is hard-limited to `USD 0`.
- No signer or private key is required or used by the canonical PAPER path.
- Canonical V5 execution uses arrival-book L2, FAK semantics, dynamic per-market fee metadata, partial/no-fill outcomes, prospective source selection and immutable evidence bridges.
- Midpoint-based V2 evidence is retained only as historical provenance and is never canonical V5 performance.

A successful historical snapshot is not automatically “online.” The public terminal treats V5 evidence older than 10,800 seconds as **verified but stale**.

## Quality-score contract

Sibyl exposes source-quality rankings; they are not probabilities.

- `SHORT`: examines the most recent 50 closed positions and scores only decided outcomes.
- `LONG`: examines up to 200 closed positions and scores only decided outcomes.
- `GLOBAL`: `60% SHORT + 40% LONG`; deterministic PAPER risk uses this ranking.
- `EDGE`: confidence-weighted execution copyability after observed price movement.

A decided outcome is a close with strictly positive or negative realized PnL. Break-even/zero-PnL closes remain visible in `closed_count`, but have zero score-history weight, do not satisfy the minimum 20-outcome eligibility requirement, and do not enter the directional win-rate denominator. Directional win rate is `wins / (wins + losses)`.

`SHORT`, `LONG`, `GLOBAL` and `EDGE` are **not calibrated probabilities, expected-return estimates, or alpha claims**. Profitability remains unproven until sufficient attributable settled out-of-sample evidence exists.

## Canonical GitHub PAPER R4.5 cycle

`.github/workflows/github-paper-v5.yml` is the canonical unattended PAPER workflow. It runs hourly at minute 47 when GitHub Actions can provision a runner.

```text
restore verified private R4.5 state
  -> use previously armed prospective source selection
  -> ingest only post-selection activity
  -> exact market identity + official market delay metadata
  -> decision book -> arrival book -> L2 FAK simulation
  -> dynamic fees + partial/no-fill evidence
  -> mark / settle / reconcile accounting
  -> bind selection -> source-strategy -> regime provenance
  -> rescore current public history and arm the next prospective selection
  -> package hashes and advance private rolling state only on PASS
```

The delayed GitHub profile intentionally overrides the normal 30-second application signal-age default with `ACTIVITY_LOOKBACK_SECONDS=5400` and `RISK_MAX_SIGNAL_AGE_SECONDS=5400`. That is a scheduling profile, not a low-latency execution claim.

Rolling private state tag:

```text
github-paper-v5-state-v4-5
```

A failed/degraded cycle never advances canonical rolling state.

## Public Cloudflare terminal

There is exactly one workflow authorized to write the public `sibyl-trace` Worker:

```text
.github/workflows/publish-cloudflare-terminal-v5.yml
```

The legacy V2/V3 publisher, V4 publisher and generic Cloudflare deploy workflow are retired/read-only and contain no Cloudflare credentials or deployment command. The V5 publisher accepts only a successful `main` R4.5 run whose source SHA still equals current `main`; stale successful SHAs fail closed. Missing Cloudflare credentials also fail the publisher rather than producing a false green. After deployment, the publisher fetches the public snapshot and verifies its exact source SHA and R4.5 truth contract.

The public snapshot uses schema version 5 and carries a machine-readable `truth_contract` containing the canonical cohort, execution model, publisher identity, 10,800-second freshness TTL and non-calibrated score semantics.

## Full cloud architecture

A persistent deployment architecture remains available for future use:

```text
Browser -> Cloudflare gateway/static dashboard
                     |
                     v
              private origin/tunnel
                     |
                     v
             backend + database
```

This architecture does not create a LIVE trading path.

## Verification

Repository CI is defined in `.github/workflows/ci.yml` and includes dependency audits, lint, backend tests with coverage, dashboard/gateway tests, safety checks and container validation. A prior green CI run must not be treated as proof for a newer HEAD: the exact commit under review must execute its own checks before being called CI-PASS.

Local verification when a full checkout is available:

```bash
cd services/backend
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
ruff check .
pytest

cd ../../../apps/gateway
npm ci --ignore-scripts --no-audit --no-fund
npm test
```

See `docs/RISK_POLICY.md`, `docs/GITHUB_PAPER_TRIAL.md`, and `docs/END_TO_END_AUDIT.md` for the detailed contracts.
