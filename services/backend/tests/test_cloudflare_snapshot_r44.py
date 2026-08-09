from __future__ import annotations

import copy

import pytest

from app.cloudflare_snapshot_r44 import _validate_v5_r44


def truthful_r44():
    return {
        "schema_version": 5,
        "evidence_generation": "SIBYL_PAPER_V5_EXECUTION_REALISTIC",
        "cohort_id": "PAPER_V5_R4_4_SOURCE_STRATEGY_TRUTH_2026_08_08",
        "status": "PASS",
        "evidence_reconciliation": {"state": "PASS"},
        "execution_health": {"state": "GREEN"},
        "selection_provenance": {
            "state": "PASS",
            "selection_provenance_hash_mismatches": 0,
            "selection_execution_evidence_bridge_mismatches": 0,
        },
        "source_strategy_provenance": {
            "state": "PASS",
            "missing_prediction_profiles": 0,
            "profile_hash_mismatches": 0,
            "non_directional_predictions": 0,
            "profile_selection_temporal_mismatches": 0,
            "execution_evidence_bridge_mismatches": 0,
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
            "execution_model": "L2_TAKER_FAK_ARRIVAL_BOOK_V6_PROSPECTIVE_DIRECTIONAL_SOURCE_GATING",
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
            "execution_evidence_hash_includes_selection_provenance": True,
            "end_cycle_mark_uses_shadow_client": True,
            "book_state_timestamps_in_ledger": True,
            "book_timestamp_freshness_gate": False,
            "source_strategy_gate": True,
            "source_strategy_public_activity_only": True,
            "source_strategy_point_in_time_cutoff": True,
            "source_strategy_cutoff_predates_selection": True,
            "source_strategy_fail_closed": True,
            "maker_rebate_source_rejected": True,
            "split_merge_conversion_source_rejected": True,
            "repeated_two_sided_source_rejected": True,
            "source_strategy_provenance_in_ledger": True,
            "execution_evidence_hash_includes_source_strategy": True,
            "maker_or_lp_execution_imported": False,
            "live_order_path_added": False,
        },
    }


def test_r44_validator_accepts_complete_source_strategy_truth_contract():
    _validate_v5_r44(truthful_r44())


@pytest.mark.parametrize(
    "field",
    [
        "source_strategy_gate",
        "source_strategy_public_activity_only",
        "source_strategy_point_in_time_cutoff",
        "source_strategy_cutoff_predates_selection",
        "source_strategy_fail_closed",
        "maker_rebate_source_rejected",
        "split_merge_conversion_source_rejected",
        "repeated_two_sided_source_rejected",
        "source_strategy_provenance_in_ledger",
        "execution_evidence_hash_includes_source_strategy",
    ],
)
def test_r44_validator_rejects_missing_truth_gate(field):
    payload = truthful_r44()
    payload["methodology"][field] = False
    with pytest.raises(ValueError, match="source-strategy truth methodology"):
        _validate_v5_r44(payload)


def test_r44_validator_rejects_non_directional_prediction_leak():
    payload = truthful_r44()
    payload["source_strategy_provenance"]["non_directional_predictions"] = 1
    with pytest.raises(ValueError, match="source-strategy truth methodology"):
        _validate_v5_r44(payload)


def test_r44_validator_rejects_strategy_selection_temporal_mismatch():
    payload = truthful_r44()
    payload["source_strategy_provenance"]["profile_selection_temporal_mismatches"] = 1
    with pytest.raises(ValueError, match="source-strategy truth methodology"):
        _validate_v5_r44(payload)


def test_r44_validator_rejects_strategy_hash_bridge_mismatch():
    payload = truthful_r44()
    payload["source_strategy_provenance"]["execution_evidence_bridge_mismatches"] = 1
    with pytest.raises(ValueError, match="source-strategy truth methodology"):
        _validate_v5_r44(payload)


def test_r44_validator_does_not_mutate_input_when_reusing_r43_contract():
    payload = truthful_r44()
    before = copy.deepcopy(payload)
    _validate_v5_r44(payload)
    assert payload == before
