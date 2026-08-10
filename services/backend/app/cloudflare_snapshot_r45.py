from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from app import cloudflare_snapshot_r44 as r44

COHORT_ID = "PAPER_V5_R4_5_REGIME_EVIDENCE_2026_08_09"
EXECUTION_MODEL = (
    "L2_TAKER_FAK_ARRIVAL_BOOK_V7_PROSPECTIVE_DIRECTIONAL_REGIME_EVIDENCE"
)
R44_COHORT_ID = "PAPER_V5_R4_4_SOURCE_STRATEGY_TRUTH_2026_08_08"
R44_EXECUTION_MODEL = (
    "L2_TAKER_FAK_ARRIVAL_BOOK_V6_PROSPECTIVE_DIRECTIONAL_SOURCE_GATING"
)
CANONICAL_PUBLISHER_WORKFLOW = "publish-cloudflare-terminal-v5.yml"
PUBLIC_SNAPSHOT_MAX_AGE_SECONDS = 10_800
SCORE_SEMANTICS = {
    "kind": "HEURISTIC_QUALITY_RANKING",
    "calibrated_probability": False,
    "expected_return_claim": False,
    "alpha_claim": False,
    "global_formula": "0.60*SHORT+0.40*LONG",
    "short_horizon": "most recent 50 closed positions",
    "long_horizon": "up to 200 closed positions",
    "edge_semantics": "execution copyability evidence, not outcome alpha",
}
_BASE_R44_VALIDATE = r44._validate_v5_r44


def _validate_v5_r45(v5: dict[str, Any]) -> None:
    if not v5:
        return
    method = v5.get("methodology") or {}
    if v5.get("cohort_id") != COHORT_ID or method.get("execution_model") != EXECUTION_MODEL:
        raise ValueError("PAPER V5 snapshot is not R4.5 regime evidence")

    inherited = copy.deepcopy(v5)
    inherited["cohort_id"] = R44_COHORT_ID
    inherited.setdefault("methodology", {})["execution_model"] = R44_EXECUTION_MODEL
    _BASE_R44_VALIDATE(inherited)

    provenance = v5.get("regime_provenance") or {}
    analysis = v5.get("regime_analysis") or {}
    settled = int(analysis.get("settled_observations") or 0)
    resolved = int(analysis.get("resolved_directional_observations") or 0)
    attributable = int(analysis.get("attributable_economic_observations") or 0)
    unattributable = int(analysis.get("unattributable_economic_observations") or 0)
    minimum = int(method.get("regime_min_settled_exploratory") or 0)
    analysis_minimum = int(analysis.get("minimum_settled_for_exploratory_breakdown") or 0)
    effective_minimum = max(minimum, 50)
    expected_state = (
        "INSUFFICIENT_EVIDENCE"
        if attributable < effective_minimum
        else "EXPLORATORY_ONLY"
    )
    if (
        method.get("regime_context_in_ledger") is not True
        or method.get("regime_context_utc_only") is not True
        or method.get("execution_evidence_hash_includes_regime_context") is not True
        or method.get("regime_analysis_settled_only") is not True
        or method.get("regime_filters_research_only") is not True
        or method.get("regime_execution_gate") is not False
        or method.get("weekday_weekend_rule_imported") is not False
        or method.get("time_of_day_rule_imported") is not False
        or method.get("naive_strategy_inversion") is not False
        or method.get("loss_cluster_metrics_settled_only") is not True
        or method.get("regime_pnl_requires_single_fill_asset_no_exit") is not True
        or method.get("regime_unattributable_pnl_excluded") is not True
        or method.get("regime_provenance_retry_safe") is not True
        or method.get("loss_cluster_timestamp_ties_deterministic") is not True
        or method.get("regime_exploratory_threshold_uses_attributable_economics") is not True
        or minimum < 50
        or analysis_minimum != minimum
        or method.get("regime_filter_requires_out_of_sample_confirmation") is not True
        or provenance.get("state") != "PASS"
        or int(provenance.get("missing_prediction_contexts") or 0) != 0
        or int(provenance.get("context_hash_or_timestamp_mismatches") or 0) != 0
        or int(provenance.get("execution_evidence_bridge_mismatches") or 0) != 0
        or analysis.get("state") != expected_state
        or analysis.get("evidence_level_basis") != "attributable_economic_observations"
        or settled != attributable
        or analysis.get("automatic_execution_gate") is not False
        or analysis.get("out_of_sample_confirmation_required") is not True
        or analysis.get("weekday_weekend_claim_verified") is not False
        or analysis.get("time_of_day_claim_verified") is not False
        or analysis.get("naive_strategy_inversion_allowed") is not False
        or attributable > resolved
        or unattributable != max(resolved - attributable, 0)
    ):
        raise ValueError("PAPER V5 R4.5 snapshot violates regime evidence methodology")


def build_cloudflare_snapshot(input_dir: Path) -> dict[str, Any]:
    original = r44._validate_v5_r44
    r44._validate_v5_r44 = _validate_v5_r45
    try:
        snapshot = r44.build_cloudflare_snapshot(input_dir)
    finally:
        r44._validate_v5_r44 = original

    # Public truth is deliberately self-describing. A consumer must not infer
    # calibration, alpha, freshness, or publisher authority from a numeric score.
    snapshot["truth_contract"] = {
        "canonical_cohort_id": COHORT_ID,
        "canonical_execution_model": EXECUTION_MODEL,
        "canonical_publisher_workflow": CANONICAL_PUBLISHER_WORKFLOW,
        "single_public_writer_required": True,
        "max_public_snapshot_age_seconds": PUBLIC_SNAPSHOT_MAX_AGE_SECONDS,
        "quality_score": dict(SCORE_SEMANTICS),
        "profitability_proven": False,
        "live_available": False,
    }
    return snapshot


def write_cloudflare_snapshot(input_dir: Path, output_dir: Path) -> Path:
    snapshot = build_cloudflare_snapshot(input_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / "snapshot.json"
    destination.write_text(
        json.dumps(snapshot, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build sanitized Cloudflare PAPER R4.5 regime evidence snapshot"
    )
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    destination = write_cloudflare_snapshot(args.input_dir, args.output_dir)
    print(destination)


if __name__ == "__main__":
    main()
