from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.evidence import build_manifest, protected_hashes
from app.polymarket import PolymarketClient
from app.repository import current_portfolio
from app.research_cycle import checkpoint, run_research_cycle
from app.research_dashboard import render_research_dashboard
from app.trial import run_cycle as run_legacy_cycle
from app.watchdogs import accounting_watchdog

BASELINE_SHA = "e4676c8d494a9d83f42749a0b85eac2288de5a54"
PROTECTED_PATHS = [
    ".github/workflows/github-paper-trial.yml",
    "services/backend/app/config.py",
    "services/backend/app/domain.py",
    "services/backend/app/paper.py",
    "services/backend/app/scoring.py",
    "services/backend/app/settlement.py",
]


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _tree_sha(repo_root: Path) -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD^{tree}"],
            cwd=repo_root,
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _node_version() -> str:
    try:
        return subprocess.check_output(
            ["node", "--version"], text=True, stderr=subprocess.DEVNULL
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def _render_research_markdown(research: dict[str, Any], accounting: dict[str, Any]) -> str:
    latency = research.get("latency", {}) if isinstance(research, dict) else {}
    reference = research.get("reference_research", {}) if isinstance(research, dict) else {}
    totals = research.get("totals", {}) if isinstance(research, dict) else {}
    lines = [
        "# Sibyl Trace — PAPER Research V2",
        "",
        "**Evidence generation:** `SIBYL_PAPER_V2`  ",
        f"**Research status:** `{research.get('status', 'UNKNOWN')}`  ",
        f"**Watchdog:** `{research.get('watchdog_state', 'YELLOW')}`  ",
        f"**Accounting:** `{accounting.get('state', 'UNKNOWN')}`",
        "",
        "## Research totals",
        "",
        f"- Experiments: {totals.get('experiments', 0)}",
        f"- Preregistered hypotheses: {totals.get('hypotheses', 0)}",
        f"- Persisted observations: {totals.get('observations', 0)}",
        f"- Watchdog events: {totals.get('watchdogs', 0)}",
        "",
        "## BTC Latency Lab",
        "",
        f"- Status: `{latency.get('status', 'DISABLED')}`",
        f"- Raw public-feed observations this run: {latency.get('events', 0)}",
        f"- Detected divergences: {latency.get('divergences', 0)}",
        f"- Depth/fee executable divergences: {latency.get('executable_divergences', 0)}",
        f"- Average convergence lag: {latency.get('average_lag_ms')}",
        f"- Average executable edge/share: {latency.get('average_executable_edge_per_share')}",
        "",
        "## Reference reconstructions",
        "",
    ]
    traders = reference.get("traders", {}) if isinstance(reference, dict) else {}
    if not traders:
        lines.append("_Reference reconstruction disabled or unavailable._")
    else:
        for username, summary in traders.items():
            lines.append(
                f"- **{username}** — `{summary.get('status', 'UNKNOWN')}`; "
                f"sample={summary.get('sample_size', 0)}"
            )
    lines.extend(
        [
            "",
            "No output in this report is permission to place a real order. LIVE is absent, "
            "paid APIs are disabled, and all new strategy hypotheses remain PAPER research.",
            "",
        ]
    )
    return "\n".join(lines)


def run(output_dir: Path) -> int:
    settings = get_settings()
    output_dir.mkdir(parents=True, exist_ok=True)
    legacy_status = run_legacy_cycle(output_dir)
    init_db()
    run_id = os.getenv("GITHUB_RUN_ID") or f"local-{os.getpid()}"
    client = PolymarketClient(settings)
    research_error: str | None = None
    research: dict[str, Any] = {"status": "SKIPPED"}
    accounting_payload: dict[str, Any] = {
        "watchdog": "ACCOUNTING_RECONCILIATION_FAILURE",
        "state": "RED",
        "message": "Accounting reconciliation did not complete",
        "payload": {},
    }
    try:
        with SessionLocal() as db:
            legacy_report = _read_json(output_dir / "trial-summary.json")
            cycle = legacy_report.get("cycle", {})
            checkpoint(
                db,
                run_id,
                "SETTLEMENT",
                cycle.get("positions_settled", 0),
            )
            checkpoint(db, run_id, "SCAN", cycle.get("selected_wallets", 0))
            checkpoint(db, run_id, "SCORE", legacy_report.get("selected_wallets", []))
            checkpoint(db, run_id, "INGEST", cycle.get("signals_processed", 0))
            checkpoint(db, run_id, "COPY_PAPER", legacy_report.get("totals", {}))

            try:
                research = run_research_cycle(db, client, settings, run_id)
            except Exception as exc:
                db.rollback()
                research_error = f"{type(exc).__name__}: {str(exc)[:500]}"
                research = {
                    "status": "DEGRADED",
                    "watchdog_state": "RED",
                    "errors": [research_error],
                }

            portfolio = current_portfolio(db, settings.initial_bankroll_usd)
            accounting = accounting_watchdog(
                cash=portfolio["cash"],
                open_market_value=portfolio["exposure"],
                equity=portfolio["equity"],
                initial_bankroll=portfolio["initial_bankroll"],
                realized_pnl=portfolio["realized_pnl"],
                unrealized_pnl=portfolio["unrealized_pnl"],
                tolerance=0.02,
            )
            accounting_payload = {
                "watchdog": accounting.watchdog,
                "state": accounting.state,
                "message": accounting.message,
                "payload": accounting.payload,
            }
            checkpoint(db, run_id, "RECONCILE", accounting_payload)

            legacy_report["schema_version"] = 3
            legacy_report["evidence_generation"] = settings.evidence_generation
            legacy_report["research"] = research
            legacy_report["accounting_watchdog"] = accounting_payload
            if research_error:
                legacy_report.setdefault("run", {}).setdefault("errors", []).append(
                    {
                        "phase": "research_v2",
                        "type": "ResearchCycleError",
                        "message": research_error,
                    }
                )
                legacy_report["run"]["status"] = "DEGRADED"
            _write_json(output_dir / "trial-summary.json", legacy_report)
            (output_dir / "research-dashboard.html").write_text(
                render_research_dashboard(legacy_report),
                encoding="utf-8",
            )
            checkpoint(
                db,
                run_id,
                "REPORT",
                {"schema_version": 3, "research": research.get("status")},
            )
    finally:
        client.close()

    latency = research.get("latency", {}) if isinstance(research, dict) else {}
    _write_json(output_dir / "latency-summary.json", latency)
    (output_dir / "latency-summary.md").write_text(
        _render_research_markdown(research, accounting_payload),
        encoding="utf-8",
    )
    _write_json(output_dir / "research-summary.json", research)

    repo_root = Path(
        os.environ.get("GITHUB_WORKSPACE") or Path(__file__).resolve().parents[3]
    )
    manifest = build_manifest(
        baseline_sha=BASELINE_SHA,
        tree_sha=_tree_sha(repo_root),
        python_version=platform.python_version(),
        node_version=_node_version(),
        scoring_version="SCORE_V2",
        risk_version="RISK_V1_FROZEN",
        simulator_version="PAPER_SIM_V2",
        contract_version="POLYMARKET_PREDICTIONS_2026-08-07",
        evidence_generation=settings.evidence_generation,
        protected_files=protected_hashes(repo_root, PROTECTED_PATHS),
    )
    _write_json(output_dir / "evidence-manifest.json", manifest)

    if legacy_status != 0 or research_error or accounting_payload.get("state") == "RED":
        return 1
    return 0


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run Sibyl Trace PAPER V2")
    parser.add_argument("--output-dir", type=Path, default=Path("trial-output"))
    args = parser.parse_args()
    return run(args.output_dir)


if __name__ == "__main__":
    sys.exit(main())
