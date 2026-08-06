# GitHub-only delayed PAPER trial

This mode runs Sibyl Trace without Oracle, Cloudflare, a VPS, or an end-user PC.

## What it does

The `GitHub PAPER Trial` workflow runs one bounded cycle every three hours:

1. Restores the previous SQLite state from a private prerelease asset.
2. Scans the public Polymarket leaderboards.
3. Scores candidate wallets from closed-position history.
4. Selects up to three eligible sources.
5. Marks existing PAPER positions at the current CLOB midpoint.
6. Ingests unseen wallet trades from the configured lookback window.
7. Applies deterministic PAPER risk rules.
8. Optionally creates a read-only GPT advisory report.
9. Writes a Markdown and JSON report.
10. Replaces the rolling private state asset only after validating the database archive.

The workflow can also be started manually from **Actions → GitHub PAPER Trial → Run workflow**.

## Delayed profile

This is explicitly a `GITHUB_DELAYED_PAPER` experiment.

GitHub-hosted runners are ephemeral and scheduled workflows can start late. Therefore:

- schedule: every three hours, at minute 17;
- activity lookback: four hours;
- maximum simulated signal age: four hours;
- LIVE trading: unavailable;
- no signer, private key, CLOB credentials, or live order endpoint exists.

The longer signal window is applied only through workflow environment variables. The normal application default remains 30 seconds.

Results from this mode measure delayed-copy behavior and operational resilience. They are not evidence that a low-latency strategy would be profitable.

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

This mode is suitable for unattended PAPER observation and backtesting-like delayed execution. It is not suitable for:

- continuous WebSocket monitoring;
- guaranteed execution times;
- real-time copy trading;
- a continuously available dashboard;
- PostgreSQL durability;
- LIVE execution.

GitHub Actions can delay or drop scheduled jobs under load. A standard GitHub-hosted job is bounded, and private repositories consume the account's included Actions minutes. The three-hour cadence is chosen to keep the trial useful without turning GitHub into an improvised always-on server.
