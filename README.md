# Sibyl Trace

Cloud-native prediction-market wallet intelligence, auditable paper trading, and risk-controlled execution.

## Safety posture

- `READ_ONLY` and `PAPER` are implemented.
- `LIVE` is fail-closed and intentionally unavailable in V1.
- GPT-5.6 is an optional read-only risk analyst; deterministic code remains authoritative.
- Private keys are not required for scanning, ranking, or paper trading.
- Risk decisions are deterministic and stored with every signal.
- Nothing runs on an end-user PC.

## GitHub-only PAPER trial

Sibyl Trace can run as an unattended delayed PAPER experiment using GitHub Actions only.

```text
GitHub schedule
    -> restore private rolling SQLite state
    -> scan and score public wallets
    -> ingest unseen activity
    -> deterministic delayed PAPER decisions
    -> persist state in a private prerelease
    -> publish Markdown/JSON run evidence
```

The workflow runs every three hours and can also be triggered manually. It does not require Oracle, Cloudflare, or a local computer. This profile is intentionally named `GITHUB_DELAYED_PAPER`: scheduled runners are not a continuous or low-latency execution environment, and LIVE remains unavailable.

See `docs/GITHUB_PAPER_TRIAL.md`.

## Full cloud architecture

```text
Browser -> Cloudflare Access -> Worker gateway/static dashboard
                                  |
                                  v
                         Cloudflare Tunnel
                                  |
                                  v
Oracle VM: FastAPI + worker + PostgreSQL + encrypted R2 backup
```

The full architecture remains available for a later persistent deployment, but it is not required for the GitHub-only trial.

## Quick verification

```bash
cd services/backend
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
pytest
ruff check .
```

Production deployment is fully remote through GitHub Actions. See `docs/DEPLOYMENT.md`.

The optional advisory layer defaults to `gpt-5.6-luna`, uses the Responses API with structured output and `store: false`, sends pseudonymous source IDs, and is disabled unless both `AI_ANALYSIS_ENABLED=true` and `OPENAI_API_KEY` are present.
