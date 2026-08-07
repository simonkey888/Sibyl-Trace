from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db import Base
from app import paper_v2


def test_session_factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


def settings() -> Settings:
    return Settings(
        trading_mode="PAPER",
        paper_trading_enabled=True,
        research_enabled=True,
        evidence_generation="SIBYL_PAPER_V2",
    )


def legacy_report() -> dict:
    return {
        "schema_version": 2,
        "run": {"status": "PASS", "errors": []},
        "cycle": {
            "positions_settled": 0,
            "selected_wallets": 0,
            "signals_processed": 0,
        },
        "selected_wallets": [],
        "totals": {
            "wallets": 0,
            "signals": 0,
            "orders": 0,
            "filled_orders": 0,
            "rejected_orders": 0,
            "open_positions": 0,
            "settled_positions": 0,
        },
    }


class FakeClient:
    def __init__(self, _settings: Settings) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def install_harness(monkeypatch, output_dir: Path, research_result: dict) -> None:
    factory = test_session_factory()

    def fake_legacy(target: Path) -> int:
        target.mkdir(parents=True, exist_ok=True)
        (target / "trial-summary.json").write_text(
            json.dumps(legacy_report()),
            encoding="utf-8",
        )
        (target / "trial-summary.md").write_text("# legacy\n", encoding="utf-8")
        return 0

    monkeypatch.setattr(paper_v2, "get_settings", settings)
    monkeypatch.setattr(paper_v2, "run_legacy_cycle", fake_legacy)
    monkeypatch.setattr(paper_v2, "init_db", lambda: None)
    monkeypatch.setattr(paper_v2, "SessionLocal", factory)
    monkeypatch.setattr(paper_v2, "PolymarketClient", FakeClient)
    monkeypatch.setattr(
        paper_v2,
        "run_research_cycle",
        lambda *_args, **_kwargs: research_result,
    )
    monkeypatch.setattr(
        paper_v2,
        "protected_hashes",
        lambda *_args, **_kwargs: {"services/backend/app/config.py": "a" * 64},
    )
    monkeypatch.setattr(paper_v2, "_tree_sha", lambda _root: "b" * 40)
    monkeypatch.setattr(paper_v2, "_node_version", lambda: "v24.0.0")


def test_v2_wrapper_emits_manifest_and_reconciles_empty_portfolio(
    monkeypatch,
    tmp_path,
) -> None:
    output = tmp_path / "trial-output"
    install_harness(
        monkeypatch,
        output,
        {
            "status": "COMPLETE",
            "watchdog_state": "YELLOW",
            "latency": {"status": "DISABLED"},
            "totals": {"experiments": 0, "hypotheses": 5, "observations": 0, "watchdogs": 0},
            "reference_research": {"status": "DISABLED", "traders": {}},
        },
    )

    status = paper_v2.run(output)
    report = json.loads((output / "trial-summary.json").read_text(encoding="utf-8"))
    manifest = json.loads((output / "evidence-manifest.json").read_text(encoding="utf-8"))
    latency = json.loads((output / "latency-summary.json").read_text(encoding="utf-8"))

    assert status == 0
    assert report["schema_version"] == 3
    assert report["evidence_generation"] == "SIBYL_PAPER_V2"
    assert report["accounting_watchdog"]["state"] == "GREEN"
    assert manifest["cost_policy"]["authorized_usd"] == 0
    assert manifest["live_policy"]["available"] is False
    assert manifest["baseline_sha"] == paper_v2.BASELINE_SHA
    assert latency["status"] == "DISABLED"
    assert (output / "latency-summary.md").is_file()
    assert (output / "research-summary.json").is_file()


def test_v2_wrapper_fails_closed_but_preserves_report_on_research_exception(
    monkeypatch,
    tmp_path,
) -> None:
    output = tmp_path / "trial-output"
    install_harness(monkeypatch, output, {"status": "unused"})

    def explode(*_args, **_kwargs):
        raise RuntimeError("feed contract drift")

    monkeypatch.setattr(paper_v2, "run_research_cycle", explode)
    status = paper_v2.run(output)
    report = json.loads((output / "trial-summary.json").read_text(encoding="utf-8"))
    research = json.loads((output / "research-summary.json").read_text(encoding="utf-8"))

    assert status == 1
    assert report["run"]["status"] == "DEGRADED"
    assert report["run"]["errors"][0]["phase"] == "research_v2"
    assert research["status"] == "DEGRADED"
    assert research["watchdog_state"] == "RED"
    assert (output / "evidence-manifest.json").is_file()
