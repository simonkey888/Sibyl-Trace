from __future__ import annotations

import json

import pytest

from app.cloudflare_snapshot_r43 import build_cloudflare_snapshot


def write_json(path, payload):
    path.write_text(json.dumps(payload), encoding="utf-8")


def legacy_trial():
    return {
        "evidence_generation": "SIBYL_PAPER_V2",
        "run": {
            "status": "PASS",
            "github_run_id": "legacy-run",
            "github_sha": "a" * 40,
            "completed_at": "2026-08-08T12:00:00+00:00",
            "profile": "GITHUB_DELAYED_PAPER",
        },
        "safety": {"trading_mode": "PAPER", "live_available": False},
        "portfolio": {"equity": 289.0},
    }


def truthful_r43():
    return {
        "schema_version": 5,
        "cohort_id": "PAPER_V5_R4_3_PROSPECTIVE_TRUTH_2026_08_08",
        "evidence_generation": "SIBYL_PAPER_V5_EXECUTION_REALISTIC",
        "status": "PASS",
        "run": {
            "github_run_id": "r43-run",
            "github_sha": "b" * 40,
            "completed_at": "2026-08-08T13:00:00+00:00",
        },
        "evidence_reconciliation": {"state": "PASS"},
        "execution_health": {"state": "GREEN"},
        "selection_provenance": {
            "state": "PASS",
            "active_selection_effective_at": 1_786_000_000,
            "active_selection": [],
            "next_selection_effective_at": 1_786_003_600,
            "next_selection": [],
            "predictions_with_selection_provenance": 0,
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
            "execution_model": "L2_TAKER_FAK_ARRIVAL_BOOK_V5_PROSPECTIVE_SELECTION_SHADOW_MARK",
            "midpoint_fills": False,
            "arrival_book_refetch": True,
            "l2_depth_consumed": True,
            "partial_fills": True,
            "legacy_history_rewritten": False,
            "immediate_post_fill_marking": True,
            "end_cycle_mark_refresh": True,
            "market_state_404_classification": True,
            "active_tradable_404_is_data_failure": True,
            "synthetic_canonical_latency": False,
            "actual_request_gap_recorded": True,
            "fee_schedule_dynamic": True,
            "fee_rate_bps_crosscheck": True,
            "execution_evidence_hash": True,
            "summary_ledger_reconciliation": True,
            "unknown_official_delay_fail_closed": True,
            "market_identity_exact": True,
            "post_delay_market_state_revalidation": True,
            "shadow_self_impact": True,
            "shadow_self_impact_live_claim": False,
            "public_book_hash_bridge_persisted": True,
            "execution_evidence_hash_includes_book_provenance": True,
            "copy_decay_metrics_in_ledger": True,
            "fee_provenance_in_ledger": True,
            "delayed_market_arrival_delay_ms": None,
            "regular_arrival_delay_ms": None,
            "prospective_wallet_selection": True,
            "preselection_activity_backfill": False,
            "selection_provenance_in_ledger": True,
            "end_cycle_mark_uses_shadow_client": True,
            "book_state_timestamps_in_ledger": True,
            "book_timestamp_freshness_gate": False,
        },
        "portfolio": {"initial_bankroll": 300, "equity": 300},
        "totals": {"predictions": 0, "wins": 0, "losses": 0, "accuracy": None},
        "selected_wallets": [],
        "recent_orders": [],
        "open_positions": [],
    }


def test_public_snapshot_accepts_r43_prospective_truth(tmp_path):
    write_json(tmp_path / "trial-summary.json", legacy_trial())
    write_json(tmp_path / "paper-v5-summary.json", truthful_r43())
    snapshot = build_cloudflare_snapshot(tmp_path)
    assert snapshot["paper_v5"]["canonical_performance"] is True
    assert snapshot["paper_v5"]["methodology"]["prospective_wallet_selection"] is True
    assert snapshot["paper_v5"]["selection_provenance"]["state"] == "PASS"


def test_public_snapshot_rejects_r43_that_allows_preselection_backfill(tmp_path):
    write_json(tmp_path / "trial-summary.json", legacy_trial())
    v5 = truthful_r43()
    v5["methodology"]["preselection_activity_backfill"] = True
    write_json(tmp_path / "paper-v5-summary.json", v5)
    with pytest.raises(ValueError, match="prospective truth methodology"):
        build_cloudflare_snapshot(tmp_path)


def test_public_snapshot_rejects_r43_without_shadow_consistent_final_mark(tmp_path):
    write_json(tmp_path / "trial-summary.json", legacy_trial())
    v5 = truthful_r43()
    v5["methodology"]["end_cycle_mark_uses_shadow_client"] = False
    write_json(tmp_path / "paper-v5-summary.json", v5)
    with pytest.raises(ValueError, match="prospective truth methodology"):
        build_cloudflare_snapshot(tmp_path)


def test_public_snapshot_rejects_r43_with_failed_selection_provenance(tmp_path):
    write_json(tmp_path / "trial-summary.json", legacy_trial())
    v5 = truthful_r43()
    v5["selection_provenance"]["state"] = "FAIL"
    write_json(tmp_path / "paper-v5-summary.json", v5)
    with pytest.raises(ValueError, match="prospective truth methodology"):
        build_cloudflare_snapshot(tmp_path)
