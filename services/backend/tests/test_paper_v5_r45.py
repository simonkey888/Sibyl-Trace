from __future__ import annotations

import copy

from app import paper_v5_r44 as r44
from app.paper_v5_r45 import (
    MIN_EXPLORATORY_SETTLED,
    _loss_cluster_metrics,
    _regime_analysis_from_observations,
    _regime_binding_valid,
    _regime_context,
    _regime_context_hash_valid,
)


def observation(ts: int, pnl: float, *, weekpart: str = "WEEKDAY", bucket: str = "12-15"):
    return {
        "prediction_id": ts,
        "source_timestamp": ts,
        "pnl": pnl,
        "win": pnl > 0,
        "loss": pnl < 0,
        "weekpart": weekpart,
        "utc_hour": 12,
        "utc_4h_bucket": bucket,
    }


def test_regime_context_is_deterministic_utc_and_hash_bound():
    # 2026-08-08T12:34:56Z is Saturday.
    context = _regime_context(1_786_192_496)
    assert context["utc_weekday_index"] == 5
    assert context["utc_weekday"] == "SATURDAY"
    assert context["weekpart"] == "WEEKEND"
    assert context["utc_hour"] == 12
    assert context["utc_4h_bucket"] == "12-15"
    assert _regime_context_hash_valid(context) is True

    tampered = copy.deepcopy(context)
    tampered["utc_hour"] = 13
    assert _regime_context_hash_valid(tampered) is False


def test_regime_hash_bridge_is_recomputable_and_tamper_evident():
    context = _regime_context(1_786_192_496)
    parent = "a" * 64
    child = r44.r43.r4._canonical_hash(
        {
            "r4_4_execution_evidence_hash": parent,
            "regime_context_hash": context["regime_context_hash"],
        }
    )
    payload = {
        "prediction_id": 1,
        "regime_context": context,
        "r4_4_execution_evidence_hash": parent,
        "r4_5_execution_evidence_hash": child,
    }
    assert _regime_binding_valid(payload, child) is True

    tampered = copy.deepcopy(payload)
    tampered["regime_context"]["weekpart"] = "WEEKDAY"
    assert _regime_binding_valid(tampered, child) is False


def test_loss_clustering_uses_only_attributable_economic_losses():
    rows = [
        observation(0, -1),
        observation(600, -2),
        observation(1200, -3),
        observation(1800, 1),
        observation(2400, -1),
        observation(3000, -1),
        observation(5000, -1),
    ]
    metrics = _loss_cluster_metrics(rows)
    assert metrics["max_consecutive_attributable_economic_losses"] == 3
    assert metrics["max_attributable_economic_losses_in_rolling_60m"] == 5


def test_regime_analysis_never_turns_anecdote_into_execution_gate():
    rows = [observation(1_700_000_000 + i * 60, 1 if i % 2 else -1) for i in range(10)]
    analysis = _regime_analysis_from_observations(rows)
    assert analysis["state"] == "INSUFFICIENT_EVIDENCE"
    assert analysis["settled_observations"] == 10
    assert analysis["attributable_economic_observations"] == 10
    assert analysis["minimum_settled_for_exploratory_breakdown"] == MIN_EXPLORATORY_SETTLED
    assert analysis["automatic_execution_gate"] is False
    assert analysis["out_of_sample_confirmation_required"] is True
    assert analysis["weekday_weekend_claim_verified"] is False
    assert analysis["time_of_day_claim_verified"] is False
    assert analysis["naive_strategy_inversion_allowed"] is False


def test_unattributable_settlements_are_excluded_from_pnl_and_loss_clusters():
    directional = [
        observation(1_700_000_000, -1),
        observation(1_700_000_060, -1),
        observation(1_700_000_120, -1),
    ]
    economic = [directional[0]]
    analysis = _regime_analysis_from_observations(directional, economic)
    assert analysis["resolved_directional_observations"] == 3
    assert analysis["attributable_economic_observations"] == 1
    assert analysis["unattributable_economic_observations"] == 2
    assert analysis["loss_clustering"]["max_consecutive_attributable_economic_losses"] == 1
    assert analysis["economic_by_weekpart"]["WEEKDAY"]["attributable_settled"] == 1


def test_even_fifty_settlements_remain_exploratory_not_auto_gated():
    rows = [observation(1_700_000_000 + i * 60, 1.0) for i in range(MIN_EXPLORATORY_SETTLED)]
    analysis = _regime_analysis_from_observations(rows)
    assert analysis["state"] == "EXPLORATORY_ONLY"
    assert analysis["automatic_execution_gate"] is False
    assert analysis["out_of_sample_confirmation_required"] is True
