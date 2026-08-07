import json
from pathlib import Path

import pytest

from app.cloudflare_snapshot import build_cloudflare_snapshot


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _fixture(tmp_path: Path) -> None:
    _write(
        tmp_path / "trial-summary.json",
        {
            "evidence_generation": "SIBYL_PAPER_V2",
            "run": {
                "status": "PASS",
                "completed_at": "2026-08-07T10:27:00+00:00",
                "github_run_id": "100",
                "github_sha": "a" * 40,
                "profile": "GITHUB_DELAYED_PAPER",
            },
            "safety": {"trading_mode": "PAPER", "live_available": False},
        },
    )
    _write(
        tmp_path / "evidence-manifest.json",
        {
            "cost_policy": {"authorized_usd": 0, "paid_apis": False},
            "live_policy": {"available": False, "real_money": False},
        },
    )
    _write(
        tmp_path / "research-v3-summary.json",
        {
            "status": "PASS",
            "evidence_generation": "SIBYL_RESEARCH_V3",
            "source_v2": {"github_run_id": "100", "github_sha": "a" * 40},
            "safety": {
                "trading_mode": "PAPER",
                "live_available": False,
                "real_money": False,
                "cost_authorized_usd": 0,
                "paid_apis": False,
            },
        },
    )
    _write(
        tmp_path / "research-v4-summary.json",
        {
            "status": "PASS",
            "evidence_generation": "SIBYL_RESEARCH_V4_OPERATIONAL",
            "edge_status": "UNPROVEN",
            "safety": {
                "mode": "PAPER_SHADOW_ONLY",
                "trading_mode": "PAPER",
                "live_available": False,
                "real_money": False,
                "cost_authorized_usd": 0,
                "paid_apis": False,
                "order_placement": False,
                "private_keys": False,
                "historical_fill_rewrite": False,
            },
            "l2_tape_v4": {
                "status": "CAPTURED",
                "fidelity": "L2_AGGREGATE",
                "normalized_events": 10,
            },
        },
    )


def test_v4_pass_summary_is_published_without_raw_tape(tmp_path: Path) -> None:
    _fixture(tmp_path)
    snapshot = build_cloudflare_snapshot(tmp_path)
    assert snapshot["schema_version"] == 3
    assert snapshot["research_v4"]["edge_status"] == "UNPROVEN"
    assert snapshot["research_v4"]["safety"]["order_placement"] is False
    assert "research_raw_v4" not in snapshot
    assert "research_tape_v4" not in snapshot


def test_v4_live_or_order_capability_fails_closed(tmp_path: Path) -> None:
    _fixture(tmp_path)
    path = tmp_path / "research-v4-summary.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["safety"]["order_placement"] = True
    _write(path, payload)
    with pytest.raises(ValueError, match="V4 snapshot violates"):
        build_cloudflare_snapshot(tmp_path)
