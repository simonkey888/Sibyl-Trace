# Sibyl Trace

Cloud-native prediction-market wallet intelligence, auditable paper trading, and risk-controlled execution.

## Safety posture

- `READ_ONLY` and `PAPER` are implemented.
- `LIVE` is fail-closed and intentionally unavailable in V1.
- Private keys are not required for scanning, ranking, or paper trading.
- Risk decisions are deterministic and stored with every signal.
- Nothing runs on an end-user PC.

## Architecture

```text
Browser -> Cloudflare Access -> Worker gateway/static dashboard
                                  |
                                  v
                         Cloudflare Tunnel
                                  |
                                  v
Oracle VM: FastAPI + worker + PostgreSQL + encrypted R2 backup
```

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
