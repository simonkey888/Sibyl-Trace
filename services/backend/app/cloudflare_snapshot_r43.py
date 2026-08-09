from __future__ import annotations

import argparse
import copy
from pathlib import Path
from typing import Any

from app import cloudflare_snapshot as base

COHORT_ID = "PAPER_V5_R4_3_PROSPECTIVE_TRUTH_2026_08_08"
EXECUTION_MODEL = (
    "L2_TAKER_FAK_ARRIVAL_BOOK_V5_PROSPECTIVE_SELECTION_SHADOW_MARK"
)
R42_COHORT_ID = "PAPER_V5_R4_2_AUDIT_CORRECTIONS_2026_08_07"
R42_EXECUTION_MODEL = (
    "L2_TAKER_FAK_ARRIVAL_BOOK_V4_POST_DELAY_REVALIDATION_SHADOW_IMPACT"
)


def _validate_v5_r43(v5: dict[str, Any]) -> None:
    if not v5:
        return
    method = v5.get("methodology") or {}
    if v5.get("cohort_id") != COHORT_ID or method.get("execution_model") != EXECUTION_MODEL:
        raise ValueError("PAPER V5 snapshot is not R4.3 prospective truth evidence")

    # Reuse every inherited R4.2 safety and execution-truth gate without
    # weakening it; only normalize the two version identifiers for validation.
    inherited = copy.deepcopy(v5)
    inherited["cohort_id"] = R42_COHORT_ID
    inherited.setdefault("methodology", {})["execution_model"] = R42_EXECUTION_MODEL
    base._validate_v5(inherited)

    selection = v5.get("selection_provenance") or {}
    if (
        method.get("prospective_wallet_selection") is not True
        or method.get("preselection_activity_backfill") is not False
        or method.get("selection_provenance_in_ledger") is not True
        or method.get("end_cycle_mark_uses_shadow_client") is not True
        or method.get("book_state_timestamps_in_ledger") is not True
        or method.get("book_timestamp_freshness_gate") is not False
        or selection.get("state") != "PASS"
    ):
        raise ValueError("PAPER V5 R4.3 snapshot violates prospective truth methodology")


def build_cloudflare_snapshot(input_dir: Path) -> dict[str, Any]:
    original = base._validate_v5
    base._validate_v5 = _validate_v5_r43
    try:
        return base.build_cloudflare_snapshot(input_dir)
    finally:
        base._validate_v5 = original


def write_cloudflare_snapshot(input_dir: Path, output_dir: Path) -> Path:
    snapshot = build_cloudflare_snapshot(input_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / "snapshot.json"
    destination.write_text(
        __import__("json").dumps(snapshot, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return destination


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build sanitized Cloudflare PAPER R4.3 dashboard snapshot"
    )
    parser.add_argument("--input-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    destination = write_cloudflare_snapshot(args.input_dir, args.output_dir)
    print(destination)


if __name__ == "__main__":
    main()
