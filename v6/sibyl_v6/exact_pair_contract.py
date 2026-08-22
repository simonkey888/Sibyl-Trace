from __future__ import annotations

import re
from typing import Any

_HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _hex64(value: Any) -> bool:
    return bool(_HEX64.fullmatch(str(value or "")))


def _audit_projection(row: dict[str, Any]) -> dict[str, Any]:
    comparison = row.get("comparison") or {}
    return {
        "limitless_slug": str(row.get("limitless_slug") or ""),
        "polymarket_slug": str(row.get("polymarket_slug") or ""),
        "comparison_state": comparison.get("state"),
        "comparison_fingerprint": comparison.get("comparison_fingerprint"),
        "left_rule_fingerprint": comparison.get("left_rule_fingerprint"),
        "right_rule_fingerprint": comparison.get("right_rule_fingerprint"),
        "unknown_fields": comparison.get("unknown_fields") or [],
        "differing_fields": comparison.get("differing_fields") or [],
        "limitless_rule_source_url": row.get("limitless_rule_source_url"),
        "polymarket_rule_source_url": row.get("polymarket_rule_source_url"),
        "limitless_rule_payload_hash": row.get("limitless_rule_payload_hash"),
        "polymarket_rule_payload_hash": row.get("polymarket_rule_payload_hash"),
    }


def validate_exact_pair_cycle(cycle: dict[str, Any], audit: dict[str, Any]) -> dict[str, Any]:
    """Validate dynamic exact-pair evidence against the audit that selected it."""
    if cycle.get("DISCOVERY_CYCLE") != "PASS":
        raise AssertionError("DISCOVERY_CYCLE_NOT_PASS")
    if cycle.get("MARKET_DATA_CYCLE") != "PASS":
        raise AssertionError("MARKET_DATA_CYCLE_NOT_PASS")

    exact_rows = [row for row in (audit.get("exact_pairs") or []) if isinstance(row, dict)]
    if not exact_rows:
        raise AssertionError("AUTHORITATIVE_AUDIT_HAS_NO_EXACT_PAIR")
    if int(audit.get("EXACT_EQUIVALENT_PAIR_COUNT", -1)) != len(exact_rows):
        raise AssertionError("AUTHORITATIVE_AUDIT_EXACT_COUNT_MISMATCH")
    if int(cycle.get("EXACT_EQUIVALENT_PAIR_COUNT", -1)) != len(exact_rows):
        raise AssertionError("CYCLE_EXACT_COUNT_NOT_SAME_AUDIT")
    if int(cycle.get("CANDIDATE_PAIR_COUNT", -1)) != int(
        audit.get("CANDIDATE_PAIR_COUNT", -2)
    ):
        raise AssertionError("CYCLE_CANDIDATE_COUNT_NOT_SAME_AUDIT")
    if not _hex64(audit.get("authoritative_audit_hash")):
        raise AssertionError("AUTHORITATIVE_AUDIT_HASH_MISSING")

    selected = cycle.get("exact_pair")
    if not isinstance(selected, dict):
        raise AssertionError("CYCLE_EXACT_PAIR_MISSING")
    if selected.get("comparison_state") != "EXACT_EQUIVALENT":
        raise AssertionError("SELECTED_PAIR_NOT_EXACT_EQUIVALENT")
    if selected.get("unknown_fields") != []:
        raise AssertionError("SELECTED_PAIR_HAS_UNKNOWN_FIELDS")
    if selected.get("differing_fields") != []:
        raise AssertionError("SELECTED_PAIR_HAS_DIFFERING_FIELDS")

    left = selected.get("left_rule_fingerprint")
    right = selected.get("right_rule_fingerprint")
    if not (_hex64(left) and _hex64(right) and left == right):
        raise AssertionError("SELECTED_PAIR_RULE_FINGERPRINT_INVALID")
    if not _hex64(selected.get("comparison_fingerprint")):
        raise AssertionError("SELECTED_PAIR_COMPARISON_FINGERPRINT_INVALID")
    for key in ("limitless_rule_payload_hash", "polymarket_rule_payload_hash"):
        if not _hex64(selected.get(key)):
            raise AssertionError(f"SELECTED_PAIR_{key.upper()}_INVALID")
    for key in ("limitless_rule_source_url", "polymarket_rule_source_url"):
        value = str(selected.get(key) or "")
        if not value.startswith("https://"):
            raise AssertionError(f"SELECTED_PAIR_{key.upper()}_INVALID")

    matches = [row for row in exact_rows if _audit_projection(row) == selected]
    if len(matches) != 1:
        raise AssertionError("SELECTED_PAIR_NOT_UNIQUELY_BOUND_TO_SAME_AUDIT")
    return selected
