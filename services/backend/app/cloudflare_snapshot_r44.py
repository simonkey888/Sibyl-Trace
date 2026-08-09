from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

from app import cloudflare_snapshot_r43 as r43

COHORT_ID = "PAPER_V5_R4_4_SOURCE_STRATEGY_TRUTH_2026_08_08"
EXECUTION_MODEL = (
    "L2_TAKER_FAK_ARRIVAL_BOOK_V6_PROSPECTIVE_DIRECTIONAL_SOURCE_GATING"
)
R43_COHORT_ID = "PAPER_V5_R4_3_PROSPECTIVE_TRUTH_2026_08_08"
R43_EXECUTION_MODEL = (
    "L2_TAKER_FAK_ARRIVAL_BOOK_V5_PROSPECTIVE_SELECTION_SHADOW_MARK"
)
_BASE_R43_VALIDATE = r43._validate_v5_r43


def _validate_v5_r44(v5: dict[str, Any]) -> None:
    if not v5:
        return
    method = v5.get("methodology") or {}
    if v5.get("cohort_id") != COHORT_ID or method.get("execution_model") != EXECUTION_MODEL:
        raise ValueError("PAPER V5 snapshot is not R4.4 source-strategy truth evidence")

    inherited = copy.deepcopy(v5)
    inherited["cohort_id"] = R43_COHORT_ID
    inherited.setdefault("methodology", {})["execution_model"] = R43_EXECUTION_MODEL
    _BASE_R43_VALIDATE(inherited)

    strategy = v5.get("source_strategy_provenance") or {}
    if (
        method.get("source_strategy_gate") is not True
        or method.get("source_strategy_public_activity_only") is not True
        or method.get("source_strategy_point_in_time_cutoff") is not True
        or method.get("source_strategy_cutoff_predates_selection") is not True
        or method.get("source_strategy_fail_closed") is not True
        or method.get("maker_rebate_source_rejected") is not True
        or method.get("split_merge_conversion_source_rejected") is not True
        or method.get("repeated_two_sided_source_rejected") is not True
        or method.get("source_strategy_provenance_in_ledger") is not True
        or method.get("execution_evidence_hash_includes_source_strategy") is not True
        or method.get("maker_or_lp_execution_imported") is not False
        or method.get("live_order_path_added") is not False
        or strategy.get("state") != "PASS"
        or int(strategy.get("missing_prediction_profiles") or 0) != 0
        or int(strategy.get("profile_hash_mismatches") or 0) != 0
        or int(strategy.get("non_directional_predictions") or 0) != 0
        or int(strategy.get("profile_selection_temporal_mismatches") or 0) != 0
        or int(strategy.get("execution_evidence_bridge_mismatches") or 0) != 0
    ):
        raise ValueError("PAPER V5 R4.4 snapshot violates source-strategy truth methodology")


def build_cloudflare_snapshot(input_dir: Path) -> dict[str, Any]:
    original = r43._validate_v5_r43
    r43._validate_v5_r43 = _validate_v5_r44
    try:
        return r43.build_cloudflare_snapshot(input_dir)
    finally:
        r43._validate_v5_r43 = original


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
        description="Build sanitized Cloudflare PAPER R4.4 source-strategy snapshot"
    )
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    destination = write_cloudflare_snapshot(args.input_dir, args.output_dir)
    print(destination)


if __name__ == "__main__":
    main()
