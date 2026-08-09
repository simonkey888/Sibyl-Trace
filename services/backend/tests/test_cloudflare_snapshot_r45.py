from __future__ import annotations

import copy

import pytest

from app.cloudflare_snapshot_r45 import _validate_v5_r45


def truthful_r45():
    return {
        "schema_version": 5,
        "evidence_generation": "SIBYL_PAPER_V5_EXECUTION_REALISTIC",
        "cohort_id": "PAPER_V5_R4_5_REGIME_EVIDENCE_2026_08_09",
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
        "regime_provenance": {
            "state": "PASS",
            "missing_prediction_contexts": 0,
            "context_hash_or_timestamp_mismatches": 0,
            "execution_evidence_bridge_mismatches": 0,
        },
        "regime_analysis": {
            "state": "INSUFFICIENT_EVIDENCE",
            "settled_observations": 0,
            "resolved_directional_observations": 0,
            "attributable_economic_observations": 0,
            "unattributable_economic_observations": 0,
            "evidence_level_basis": "attributable_economic_observations",
            "minimum_settled_for_exploratory_breakdown": 50,
            "automatic_execution_gate": False,
            "out_of_sample_confirmation_required": True,
            "weekday_weekend_claim_verified": False,
            "time_of_day_claim_verified": False,
            "naive_strategy_inversion_allowed": False,
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
            "execution_model": "L2_TAKER_FAK_ARRIVAL_BOOK_V7_PROSPECTIVE_DIRECTIONAL_REGIME_EVIDENCE",
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
            "regime_context_in_ledger": True,
            "regime_context_utc_only": True,
            "execution_evidence_hash_includes_regime_context": True,
            "regime_analysis_settled_only": True,
            "regime_filters_research_only": True,
            "regime_execution_gate": False,
            "weekday_weekend_rule_imported": False,
            "time_of_day_rule_imported": False,
            "naive_strategy_inversion": False,
            "loss_cluster_metrics_settled_only": True,
            "regime_pnl_requires_single_fill_asset_no_exit": True,
            "regime_unattributable_pnl_excluded": True,
            "regime_provenance_retry_safe": True,
            "loss_cluster_timestamp_ties_deterministic": True,
            "regime_exploratory_threshold_uses_attributable_economics": True,
            "regime_min_settled_exploratory": 50,
            "regime_filter_requires_out_of_sample_confirmation": True,
        },
    }


def test_r45_validator_accepts_regime_evidence_without_auto_gate():
    _validate_v5_r45(truthful_r45())


@pytest.mark.parametrize(
    "field,bad",
    [
        ("regime_context_in_ledger", False),
        ("regime_context_utc_only", False),
        ("execution_evidence_hash_includes_regime_context", False),
        ("regime_analysis_settled_only", False),
        ("regime_filters_research_only", False),
        ("regime_execution_gate", True),
        ("weekday_weekend_rule_imported", True),
        ("time_of_day_rule_imported", True),
        ("naive_strategy_inversion", True),
        ("loss_cluster_metrics_settled_only", False),
        ("regime_pnl_requires_single_fill_asset_no_exit", False),
        ("regime_unattributable_pnl_excluded", False),
        ("regime_provenance_retry_safe", False),
        ("loss_cluster_timestamp_ties_deterministic", False),
        ("regime_exploratory_threshold_uses_attributable_economics", False),
        ("regime_filter_requires_out_of_sample_confirmation", False),
    ],
)
def test_r45_validator_rejects_regime_truth_contract_drift(field, bad):
    payload = truthful_r45()
    payload["methodology"][field] = bad
    with pytest.raises(ValueError, match="regime evidence methodology"):
        _validate_v5_r45(payload)


def test_r45_validator_rejects_regime_hash_bridge_mismatch():
    payload = truthful_r45()
    payload["regime_provenance"]["execution_evidence_bridge_mismatches"] = 1
    with pytest.raises(ValueError, match="regime evidence methodology"):
        _validate_v5_r45(payload)


def test_r45_validator_rejects_automatic_regime_execution_gate():
    payload = truthful_r45()
    payload["regime_analysis"]["automatic_execution_gate"] = True
    with pytest.raises(ValueError, match="regime evidence methodology"):
        _validate_v5_r45(payload)


def test_r45_validator_rejects_inconsistent_pnl_attribution_counts():
    payload = truthful_r45()
    payload["regime_analysis"].update(
        {
            "settled_observations": 1,
            "resolved_directional_observations": 3,
            "attributable_economic_observations": 1,
            "unattributable_economic_observations": 1,
        }
    )
    with pytest.raises(ValueError, match="regime evidence methodology"):
        _validate_v5_r45(payload)


@pytest.mark.parametrize(
    "field",
    ["weekday_weekend_claim_verified", "time_of_day_claim_verified"],
)
def test_r45_validator_rejects_unverified_regime_claim_promotion(field):
    payload = truthful_r45()
    payload["regime_analysis"][field] = True
    with pytest.raises(ValueError, match="regime evidence methodology"):
        _validate_v5_r45(payload)


def test_r45_validator_rejects_exploratory_state_without_50_attributable_settlements():
    payload = truthful_r45()
    payload["regime_analysis"].update(
        {
            "state": "EXPLORATORY_ONLY",
            "settled_observations": 40,
            "resolved_directional_observations": 50,
            "attributable_economic_observations": 40,
            "unattributable_economic_observations": 10,
        }
    )
    with pytest.raises(ValueError, match="regime evidence methodology"):
        _validate_v5_r45(payload)


def test_r45_validator_accepts_exploratory_state_at_50_attributable_settlements():
    payload = truthful_r45()
    payload["regime_analysis"].update(
        {
            "state": "EXPLORATORY_ONLY",
            "settled_observations": 50,
            "resolved_directional_observations": 60,
            "attributable_economic_observations": 50,
            "unattributable_economic_observations": 10,
        }
    )
    _validate_v5_r45(payload)


def test_r45_validator_tracks_higher_declared_minimum():
    payload = truthful_r45()
    payload["methodology"]["regime_min_settled_exploratory"] = 100
    payload["regime_analysis"].update(
        {
            "minimum_settled_for_exploratory_breakdown": 100,
            "state": "INSUFFICIENT_EVIDENCE",
            "settled_observations": 75,
            "resolved_directional_observations": 80,
            "attributable_economic_observations": 75,
            "unattributable_economic_observations": 5,
        }
    )
    _validate_v5_r45(payload)


def test_r45_validator_tracks_higher_declared_minimum_at_threshold():
    payload = truthful_r45()
    payload["methodology"]["regime_min_settled_exploratory"] = 100
    payload["regime_analysis"].update(
        {
            "minimum_settled_for_exploratory_breakdown": 100,
            "state": "EXPLORATORY_ONLY",
            "settled_observations": 100,
            "resolved_directional_observations": 110,
            "attributable_economic_observations": 100,
            "unattributable_economic_observations": 10,
        }
    )
    _validate_v5_r45(payload)


def test_r45_validator_rejects_analysis_methodology_minimum_mismatch():
    payload = truthful_r45()
    payload["regime_analysis"]["minimum_settled_for_exploratory_breakdown"] = 100
    with pytest.raises(ValueError, match="regime evidence methodology"):
        _validate_v5_r45(payload)


def test_r45_validator_rejects_wrong_evidence_level_basis():
    payload = truthful_r45()
    payload["regime_analysis"]["evidence_level_basis"] = "resolved_directional_observations"
    with pytest.raises(ValueError, match="regime evidence methodology"):
        _validate_v5_r45(payload)


def test_r45_validator_does_not_mutate_input():
    payload = truthful_r45()
    before = copy.deepcopy(payload)
    _validate_v5_r45(payload)
    assert payload == before
