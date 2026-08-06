# GitHub-only delayed PAPER trial

This mode runs Sibyl Trace without Oracle, Cloudflare, a VPS, or an end-user PC.

## What it does

The `GitHub PAPER Trial` workflow runs one bounded cycle every hour:

1. Restores the previous SQLite state and verifies its SHA-256 digest.
2. Reconciles the persisted runtime mode with the explicit PAPER configuration.
3. Scans the public Polymarket leaderboards.
4. Calculates `SHORT`, `LONG`, `GLOBAL`, and execution `EDGE` scores.
5. Selects up to three eligible sources using `GLOBAL`.
6. Settles resolved PAPER positions from terminal public market outcomes.
7. Marks remaining open PAPER positions at the current CLOB midpoint.
8. Ingests unseen wallet trades using bounded, deduplicated pagination.
9. Rejects invalid or stale signals before requesting external prices.
10. Applies deterministic PAPER risk rules and caches midpoint per asset per cycle.
11. Optionally creates a read-only GPT advisory report.
12. Writes a Markdown and JSON report.
13. Packages the last committed SQLite state even when the bounded cycle times out.
14. Moves the rolling private state tag to the exact run SHA and replaces its assets.

The workflow can also be started manually from **Actions → GitHub PAPER Trial → Run workflow**.

## Delayed profile

This is explicitly a `GITHUB_DELAYED_PAPER` experiment.

GitHub-hosted runners are ephemeral and scheduled workflows can start late. Therefore:

- schedule: hourly, at minute 17;
- activity lookback: 90 minutes;
- maximum simulated signal age: 90 minutes;
- activity ceiling: 2,000 records per tracked wallet and cycle;
- internal cycle budget: 12 minutes;
- job budget: 20 minutes;
- LIVE trading: unavailable;
- no signer, private key, CLOB credentials, or live order endpoint exists.

The delayed values are applied only through workflow environment variables. The normal application signal-age default remains 30 seconds.

Results from this mode measure delayed-copy behavior, settlement accounting, and operational resilience. They are not evidence that a low-latency strategy or any wallet source will be profitable.

## Score definitions

- `SHORT`: most recent 50 closed positions.
- `LONG`: up to 200 closed positions.
- `GLOBAL`: `60% SHORT + 40% LONG`; deterministic risk consumes this value.
- `EDGE`: observed ability to copy at an equal or better price, confidence-weighted toward neutral until 30 observations. EDGE does not measure eventual market correctness.

Each score includes sample sizes in the JSON/Markdown evidence.

## Persistent state

The repository remains private. The rolling state is stored in the private prerelease:

```text
github-paper-state-v1
```

Assets:

- `sibyl.db.gz` — current SQLite PAPER ledger;
- `trial-summary.json` — machine-readable latest report;
- `trial-summary.md` — human-readable latest report;
- `SHA256SUMS` — archive integrity digest.

The database contains public wallet activity and simulated accounting only. It contains no wallet private keys, exchange credentials, API keys, or GitHub secrets.

Each run also creates a small audit artifact retained for 14 days. The database itself is not included in the per-run artifact.

## Timeout recovery

The Python cycle is terminated before the outer GitHub job limit. If it does not finish, the workflow creates a failure report, packages the last transactionally committed SQLite file, uploads the evidence, and then fails closed. A failed run never becomes a false PASS.

## Optional GPT advisory

The scheduled trial does not require OpenAI.

To enable the optional read-only report:

1. Add the repository secret `OPENAI_API_KEY`.
2. Add the repository variable `SIBYL_AI_ENABLED=true`.
3. Optionally set `SIBYL_OPENAI_MODEL`.

GPT remains unable to place, approve, or size an order.

## Resetting the experiment

Run the workflow manually and enable `reset_state`.

This discards the restored database for that cycle and replaces the rolling release asset with a new ledger. The old database is overwritten, so download it first when historical preservation is required.

## Limits

This mode is suitable for unattended PAPER observation and delayed execution evidence. It is not suitable for:

- continuous WebSocket monitoring;
- guaranteed execution times;
- real-time copy trading;
- a continuously available dashboard;
- PostgreSQL durability;
- LIVE execution.

GitHub Actions can delay or drop scheduled jobs under load. Private repositories consume the account's included Actions minutes. The hourly cadence is an evidence experiment, not an always-on server.
