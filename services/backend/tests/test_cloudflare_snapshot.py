from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.cloudflare_snapshot import build_cloudflare_snapshot, sanitize_public


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _fixture(tmp_path: Path) -> None:
    _write(
        tmp_path / "trial-summary.json",
        {
            "evidence_generation": "SIBYL_PAPER_V2",
            "run": {
                "status": "PASS",
                "completed_at": "2026-08-07T07:31:41+00:00",
                "github_run_id": "123",
                "github_sha": "a" * 40,
                "profile": "GITHUB_DELAYED_PAPER",
            },
            "safety": {"trading_mode": "PAPER", "live_available": False},
            "selected_wallets": [
                {
                    "wallet": "0x1234567890123456789012345678901234567890",
                    "username": "0x1234567890123456789012345678901234567890-1",
                }
            ],
            "portfolio": {"equity": 300.0},
            "research": {"duplicated": True},
        },
    )
    _write(tmp_path / "research-summary.json", {"status": "COMPLETE"})
    _write(tmp_path / "latency-summary.json", {"events": 10})
    _write(
        tmp_path / "evidence-manifest.json",
        {
            "baseline_sha": "b" * 40,
            "tree_sha": "c" * 40,
            "manifest_hash": "d" * 64,
            "evidence_generation": "SIBYL_PAPER_V2",
            "risk_version": "RISK_V1_FROZEN",
            "scoring_version": "SCORE_V2",
            "simulator_version": "PAPER_SIM_V2",
            "polymarket_contract_version": "POLYMARKET_PREDICTIONS_2026-08-07",
            "cost_policy": {"authorized_usd": 0, "paid_apis": False},
            "live_policy": {"available": False, "real_money": False},
        },
    )


def test_snapshot_is_paper_only_and_masks_wallets(tmp_path: Path) -> None:
    _fixture(tmp_path)
    snapshot = build_cloudflare_snapshot(tmp_path)

    assert snapshot["snapshot_at"] == "2026-08-07T07:31:41+00:00"
    assert snapshot["trial"]["selected_wallets"][0]["wallet"] == "0x1234…7890"
    assert snapshot["trial"]["selected_wallets"][0]["username"] == "0x1234…7890-1"
    assert "research" not in snapshot["trial"]
    assert snapshot["manifest"]["live_policy"]["available"] is False
    assert snapshot["manifest"]["cost_policy"]["authorized_usd"] == 0


def test_snapshot_refuses_live_evidence(tmp_path: Path) -> None:
    _fixture(tmp_path)
    payload = json.loads((tmp_path / "trial-summary.json").read_text(encoding="utf-8"))
    payload["safety"]["live_available"] = True
    _write(tmp_path / "trial-summary.json", payload)

    with pytest.raises(ValueError, match="PAPER-only"):
        build_cloudflare_snapshot(tmp_path)


def test_snapshot_refuses_nonzero_cost(tmp_path: Path) -> None:
    _fixture(tmp_path)
    payload = json.loads((tmp_path / "evidence-manifest.json").read_text(encoding="utf-8"))
    payload["cost_policy"]["authorized_usd"] = 1
    _write(tmp_path / "evidence-manifest.json", payload)

    with pytest.raises(ValueError, match="zero-cost"):
        build_cloudflare_snapshot(tmp_path)


def test_sanitizer_fails_closed_on_sensitive_keys() -> None:
    with pytest.raises(ValueError, match="sensitive-looking key"):
        sanitize_public({"nested": {"api_key": "do-not-publish"}})
