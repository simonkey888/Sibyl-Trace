from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .matcher import PairState


DEFAULT_UPSTREAM_CONTROLS: dict[str, Any] = {
    "order_size": 5,
    "margin_bps": 100,
    "hedge_threshold": 2.0,
    "hedge_interval": 5,
    "hedge_settle_ms": 12000,
    "min_requote_ms": 2000,
    "liveness_check_ms": 10000,
    "max_net_shares": 20,
    "max_hedge_failures": 3,
    "max_loss_usd": 10,
    "flatten_on_stop": True,
}


def _assert_exact_pair(row: dict[str, Any]) -> None:
    comparison = row.get("comparison")
    if not isinstance(comparison, dict):
        raise RuntimeError("UPSTREAM_PAIR_MISSING_COMPARISON")
    if comparison.get("state") != PairState.EXACT_EQUIVALENT.value:
        raise RuntimeError("UPSTREAM_PAIR_NOT_EXACT_EQUIVALENT")
    if comparison.get("unknown_fields") or comparison.get("differing_fields"):
        raise RuntimeError("UPSTREAM_PAIR_COMPARISON_NOT_CLEAN")
    if comparison.get("left_rule_fingerprint") != comparison.get("right_rule_fingerprint"):
        raise RuntimeError("UPSTREAM_PAIR_FINGERPRINT_MISMATCH")
    if not row.get("polymarket_slug") or not row.get("limitless_slug"):
        raise RuntimeError("UPSTREAM_PAIR_SLUGS_REQUIRED")


def build_upstream_config(
    exact_pairs: Iterable[dict[str, Any]],
    *,
    controls: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the only config R1 may hand to official cross-market-mm.

    The input is the output of discovery.load_verified_pairs(), never raw title
    candidates. The measurement target is intentionally absent: it cannot
    influence order size, inventory, loss, or requote controls.
    """
    pairs = list(exact_pairs)
    if not pairs:
        raise RuntimeError("NO_EXACT_EQUIVALENT_PAIR")
    for row in pairs:
        _assert_exact_pair(row)

    requested = dict(DEFAULT_UPSTREAM_CONTROLS)
    if controls:
        unknown = set(controls) - set(DEFAULT_UPSTREAM_CONTROLS)
        if unknown:
            raise RuntimeError("UNSUPPORTED_UPSTREAM_CONTROL:" + ",".join(sorted(unknown)))
        requested.update(controls)

    return {
        "schema_version": "SIBYL_V6_UPSTREAM_CONFIG_V1",
        "dry_run": True,
        "poly_funder": "",
        "poly_signature_type": 3,
        **requested,
        "market_pairs": [
            {
                "polymarket_slug": str(row["polymarket_slug"]),
                "limitless_slug": str(row["limitless_slug"]),
            }
            for row in pairs
        ],
        "binding": {
            "source": "EXACT_EQUIVALENT_ONLY",
            "pair_count": len(pairs),
            "comparison_fingerprints": [
                str(row["comparison"]["comparison_fingerprint"]) for row in pairs
            ],
        },
    }


def write_upstream_config(
    exact_pairs: Iterable[dict[str, Any]],
    path: Path,
    *,
    controls: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = build_upstream_config(exact_pairs, controls=controls)
    path.parent.mkdir(parents=True, exist_ok=True)
    # JSON is a valid YAML subset and is parsed by upstream js-yaml. Using JSON
    # avoids adding a second YAML implementation to the isolated Python wrapper.
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload
