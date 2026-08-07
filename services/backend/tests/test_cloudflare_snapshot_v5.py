from __future__ import annotations

import json

import pytest

from app.cloudflare_snapshot import build_cloudflare_snapshot


def write_json(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")


def legacy_trial():
    return {
        "evidence_generation": "SIBYL_PAPER_V2",
        "run": {
            "status": "PASS",
            "github_run_id": "legacy-run",
            "github_sha": "a" * 40,
            "completed_at": "2026-08-07T12:00:00+00:00",
            "profile": "GITHUB_DELAYED_PAPER",
        },
        "safety": {"trading_mode": "PAPER", "live_available": False},
        "portfolio": {"equity": 289.0},
    }


def truthful_v5():
    return {
        "schema_version": 5,
        "cohort_id": "PAPER_V5_R3_INTRACYCLE_MARK_2026_08_07",
        "evidence_generation": "SIBYL_PAPER_V5_EXECUTION_REALISTIC",
        "status": "PASS",
        "run": {
            "github_run_id": "v5-run",
            "github_sha": "b" * 40,
            "completed_at": "2026-08-07T13:00:00+00:00",
        },
        "safety": {
            "trading_mode": "PAPER",
            "live_available": False,
            "real_money": False,
            "order_placement": False,
            "private_keys": False,
            "paid_apis": False,
            "cost_authorized_usd": 0,
        },
        "methodology": {
            "execution_model": "L2_TAKER_FAK_ARRIVAL_BOOK_V1",
            "midpoint_fills": False,
            "arrival_book_refetch": True,
            "l2_depth_consumed": True,
            "partial_fills": True,
            "legacy_history_rewritten": False,
            "immediate_post_fill_marking": True,
            "end_cycle_mark_refresh": True,
            "closed_book_404_is_no_fill": True,
            "unknown_delayed_schedule_fail_closed": True,
            "crypto_delayed_market_ms": 250,
        },
        "portfolio": {"initial_bankroll": 300, "equity": 300},
        "totals": {"predictions": 0, "wins": 0, "losses": 0, "accuracy": None},
        "selected_wallets": [
            {"wallet": "0x1111111111111111111111111111111111111111", "username": "source"}
        ],
        "recent_orders": [],
        "open_positions": [],
    }


def test_public_snapshot_prefers_v5_and_labels_v2_legacy(tmp_path) -> None:
    write_json(tmp_path / "trial-summary.json", legacy_trial())
    write_json(tmp_path / "paper-v5-summary.json", truthful_v5())
    snapshot = build_cloudflare_snapshot(tmp_path)

    assert snapshot["schema_version"] == 4
    assert snapshot["source"]["evidence_generation"] == "SIBYL_PAPER_V5_EXECUTION_REALISTIC"
    assert snapshot["source"]["github_run_id"] == "v5-run"
    assert snapshot["paper_v5"]["canonical_performance"] is True
    assert snapshot["paper_v5"]["methodology"]["midpoint_fills"] is False
    assert snapshot["trial"]["canonical_performance"] is False
    assert snapshot["trial"]["methodology_label"] == "LEGACY_SIMULATION_MIDPOINT_V2"
    assert snapshot["paper_v5"]["selected_wallets"][0]["wallet"].endswith("…1111")


def test_public_snapshot_rejects_v5_that_reenables_midpoint_fills(tmp_path) -> None:
    write_json(tmp_path / "trial-summary.json", legacy_trial())
    v5 = truthful_v5()
    v5["methodology"]["midpoint_fills"] = True
    write_json(tmp_path / "paper-v5-summary.json", v5)
    with pytest.raises(ValueError, match="truthful-execution methodology"):
        build_cloudflare_snapshot(tmp_path)


def test_public_snapshot_rejects_v5_with_live_or_order_placement(tmp_path) -> None:
    write_json(tmp_path / "trial-summary.json", legacy_trial())
    v5 = truthful_v5()
    v5["safety"]["order_placement"] = True
    write_json(tmp_path / "paper-v5-summary.json", v5)
    with pytest.raises(ValueError, match="PAPER/LIVE/\\$0 policy"):
        build_cloudflare_snapshot(tmp_path)


def test_public_snapshot_rejects_v5_without_r3_mark_contract(tmp_path) -> None:
    write_json(tmp_path / "trial-summary.json", legacy_trial())
    v5 = truthful_v5()
    v5["methodology"]["immediate_post_fill_marking"] = False
    write_json(tmp_path / "paper-v5-summary.json", v5)
    with pytest.raises(ValueError, match="truthful-execution methodology"):
        build_cloudflare_snapshot(tmp_path)
