from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

REQUIRED_REGIONS = {"us-east1", "us-central1", "southamerica-east1"}
REQUIRED_TARGETS = {
    "polymarket_rest",
    "polymarket_ws",
    "limitless_rest",
    "limitless_ws",
}


def _read(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError(f"INVALID_PROBE:{path}")
    return payload


def _score_target(target: str, row: dict[str, Any]) -> float:
    if target.endswith("_ws"):
        metric = row.get("ws_connect") or {}
        value = metric.get("p95_ms")
        if value is None:
            raise RuntimeError(f"MISSING_WS_P95:{target}")
        return float(value)
    total = 0.0
    for name in ("connect", "tls", "ttfb"):
        metric = row.get(name) or {}
        value = metric.get("p95_ms")
        if value is None:
            raise RuntimeError(f"MISSING_{name.upper()}_P95:{target}")
        total += float(value)
    return total


def select_region(payloads: list[dict[str, Any]], *, min_protocol_successes: int = 3) -> dict[str, Any]:
    by_region: dict[str, dict[str, Any]] = {}
    for payload in payloads:
        region = str(payload.get("region") or "")
        if region in by_region:
            raise RuntimeError(f"DUPLICATE_REGION:{region}")
        by_region[region] = payload
    if set(by_region) != REQUIRED_REGIONS:
        missing = sorted(REQUIRED_REGIONS - set(by_region))
        extra = sorted(set(by_region) - REQUIRED_REGIONS)
        raise RuntimeError(f"REGION_SET_INVALID:missing={missing}:extra={extra}")

    scores: dict[str, float] = {}
    geoblocked: list[dict[str, str]] = []
    for region, payload in by_region.items():
        if payload.get("jurisdiction_bypass_attempted") is not False:
            raise RuntimeError(f"JURISDICTION_BYPASS_FLAG_INVALID:{region}")
        summary = payload.get("summary")
        if not isinstance(summary, dict) or set(summary) != REQUIRED_TARGETS:
            raise RuntimeError(f"TARGET_SET_INVALID:{region}")
        region_score = 0.0
        for target in sorted(REQUIRED_TARGETS):
            row = summary[target]
            if not isinstance(row, dict):
                raise RuntimeError(f"TARGET_INVALID:{region}:{target}")
            if row.get("geoblock_451_observed") is True:
                geoblocked.append({"region": region, "target": target})
            if int(row.get("protocol_successful_samples") or 0) < min_protocol_successes:
                raise RuntimeError(f"INSUFFICIENT_PROTOCOL_SUCCESS:{region}:{target}")
            region_score += _score_target(target, row)
        scores[region] = round(region_score, 3)

    # A 451 in any probe location blocks selection entirely. Choosing another
    # region would be using hosting geography to route around jurisdiction.
    if geoblocked:
        return {
            "schema_version": "SIBYL_V6_REGION_SELECTION_V1",
            "status": "BLOCKED_GEOBLOCK",
            "selected_region": None,
            "scores_ms": scores,
            "geoblock_evidence": geoblocked,
            "jurisdiction_bypass_attempted": False,
        }

    selected = min(scores, key=lambda region: (scores[region], region))
    return {
        "schema_version": "SIBYL_V6_REGION_SELECTION_V1",
        "status": "SELECTED",
        "selected_region": selected,
        "scores_ms": scores,
        "geoblock_evidence": [],
        "jurisdiction_bypass_attempted": False,
        "selection_metric": "SUM_REST_TCP_TLS_TTFB_P95_PLUS_WS_CONNECT_P95",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("probes", nargs=3, type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    payload = select_region([_read(path) for path in args.probes])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, sort_keys=True))
    return 0 if payload["status"] == "SELECTED" else 4


if __name__ == "__main__":
    raise SystemExit(main())
