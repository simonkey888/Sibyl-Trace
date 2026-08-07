from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.cloudflare_snapshot import build_cloudflare_snapshot


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _v2_fixture(tmp_path: Path) -> None:
    _write(
        tmp_path / "trial-summary.json",
        {
            "evidence_generation": "SIBYL_PAPER_V2",
            "run": {
                "status": "PASS",
                "completed_at": "2026-08-07T09:00:00+00:00",
                "github_run_id": "321",
                "github_sha": "a" * 40,
                "profile": "GITHUB_PAPER_RESEARCH_V2",
            },
            "safety": {"trading_mode": "PAPER", "live_available": False},
        },
    )
    _write(tmp_path / "research-summary.json", {"watchdog_state": "YELLOW"})
    _write(tmp_path / "latency-summary.json", {"events": 12})
    _write(
        tmp_path / "evidence-manifest.json",
        {
            "cost_policy": {"authorized_usd": 0, "paid_apis": False},
            "live_policy": {"available": False, "real_money": False},
        },
    )


def _v3_fixture(tmp_path: Path, *, source_run: str = "321") -> None:
    _write(
        tmp_path / "research-v3-summary.json",
        {
            "schema_version": 1,
            "evidence_generation": "SIBYL_RESEARCH_V3",
            "status": "PASS",
            "edge_status": "UNPROVEN",
            "source_v2": {"github_run_id": source_run, "github_sha": "a" * 40},
            "safety": {
                "trading_mode": "PAPER",
                "live_available": False,
                "real_money": False,
                "cost_authorized_usd": 0,
                "paid_apis": False,
            },
            "microstructure_v3": {
                "status": "CAPTURED",
                "assets": [{"asset_id": "token", "toxicity": 0.001}],
            },
            "market_making_v3": {
                "status": "ANALYZED",
                "execution_enabled": False,
                "assets": [{"asset_id": "token", "regime": "QUIET"}],
            },
            "replay_v3": {"status": "REPLAYED", "queue_probes": 2},
        },
    )


def test_cloudflare_snapshot_embeds_matching_v3(tmp_path: Path) -> None:
    _v2_fixture(tmp_path)
    _v3_fixture(tmp_path)
    snapshot = build_cloudflare_snapshot(tmp_path)
    assert snapshot["schema_version"] == 2
    assert snapshot["research_v3"]["edge_status"] == "UNPROVEN"
    assert snapshot["research_v3"]["market_making_v3"]["execution_enabled"] is False


def test_cloudflare_snapshot_refuses_v3_from_another_v2_run(tmp_path: Path) -> None:
    _v2_fixture(tmp_path)
    _v3_fixture(tmp_path, source_run="999")
    with pytest.raises(ValueError, match="source run"):
        build_cloudflare_snapshot(tmp_path)


def test_cloudflare_snapshot_refuses_v3_live_or_cost_drift(tmp_path: Path) -> None:
    _v2_fixture(tmp_path)
    _v3_fixture(tmp_path)
    path = tmp_path / "research-v3-summary.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["safety"]["cost_authorized_usd"] = 1
    _write(path, payload)
    with pytest.raises(ValueError, match=r"PAPER/LIVE/\$0"):
        build_cloudflare_snapshot(tmp_path)
