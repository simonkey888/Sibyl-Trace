from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

SOURCE_PRICE_SEMANTICS = (
    "historical source-wallet TRADE price from Polymarket Data API activity; "
    "not a current market quote"
)


def _percentile(values: list[int], fraction: float) -> int | None:
    if not values:
        return None
    if not 0 <= fraction <= 1:
        raise ValueError("percentile fraction must be between zero and one")
    ordered = sorted(values)
    index = max(math.ceil(fraction * len(ordered)) - 1, 0)
    return ordered[index]


def _distribution(values: list[int]) -> dict[str, int | None]:
    return {
        "min": min(values) if values else None,
        "p50": _percentile(values, 0.50),
        "p95": _percentile(values, 0.95),
        "max": max(values) if values else None,
    }


def _timing_row(row: dict[str, Any]) -> dict[str, Any] | None:
    source_timestamp = int(row.get("source_timestamp") or 0)
    if source_timestamp <= 0:
        return None
    source_ms = source_timestamp * 1000
    evidence = row.get("execution_evidence") or {}
    execution = row.get("execution") or {}
    copy_decay = row.get("copy_decay") or {}
    decision_ms = evidence.get("decision_received_at_ms")
    arrival_ms = evidence.get("arrival_received_at_ms")
    decision_age = int(decision_ms) - source_ms if decision_ms is not None else None
    arrival_age = int(arrival_ms) - source_ms if arrival_ms is not None else None
    return {
        "prediction_id": int(row.get("prediction_id") or 0),
        "wallet_address": row.get("wallet_address"),
        "market": row.get("market"),
        "asset_id": row.get("asset_id"),
        "side": row.get("side"),
        "source_timestamp": source_timestamp,
        "source_price": row.get("source_price"),
        "decision_best_price": execution.get("decision_best_price"),
        "effective_fill_price": execution.get("effective_price"),
        "execution_status": execution.get("status"),
        "source_to_decision_book_ms": decision_age,
        "source_to_arrival_book_ms": arrival_age,
        "decision_vs_source": copy_decay.get("decision_vs_source"),
        "effective_fill_vs_source": copy_decay.get("effective_fill_vs_source"),
    }


def build_copy_timing_analysis(rows: list[dict[str, Any]]) -> dict[str, Any]:
    observations = [item for row in rows if (item := _timing_row(row)) is not None]
    decision_ages = [
        int(row["source_to_decision_book_ms"])
        for row in observations
        if row["source_to_decision_book_ms"] is not None
    ]
    arrival_ages = [
        int(row["source_to_arrival_book_ms"])
        for row in observations
        if row["source_to_arrival_book_ms"] is not None
    ]
    negative_rows = sum(
        1
        for row in observations
        if (
            row.get("source_to_decision_book_ms") is not None
            and int(row["source_to_decision_book_ms"]) < 0
        )
        or (
            row.get("source_to_arrival_book_ms") is not None
            and int(row["source_to_arrival_book_ms"]) < 0
        )
    )
    negative_measurements = sum(value < 0 for value in decision_ages) + sum(
        value < 0 for value in arrival_ages
    )
    positive_decision_decay = [
        row
        for row in observations
        if row.get("decision_vs_source") is not None
        and float(row["decision_vs_source"]) > 0
        and row.get("source_to_decision_book_ms") is not None
    ]
    worst_age = sorted(
        [row for row in observations if row.get("source_to_decision_book_ms") is not None],
        key=lambda row: int(row["source_to_decision_book_ms"]),
        reverse=True,
    )[:10]
    worst_decay = sorted(
        positive_decision_decay,
        key=lambda row: float(row["decision_vs_source"]),
        reverse=True,
    )[:10]
    return {
        "schema_version": 1,
        "source_price_semantics": SOURCE_PRICE_SEMANTICS,
        "source_timestamp_resolution_ms": 1000,
        "percentile_method": "nearest_rank",
        "purpose": "measure copy latency and copy-decay without changing execution eligibility",
        "automatic_execution_gate": False,
        "arbitrary_max_age_filter_imported": False,
        "rows_total": len(rows),
        "rows_with_source_timestamp": len(observations),
        "decision_timing_observations": len(decision_ages),
        "arrival_timing_observations": len(arrival_ages),
        "negative_clock_age_rows": negative_rows,
        "negative_clock_age_measurements": negative_measurements,
        "source_to_decision_book_ms": _distribution(decision_ages),
        "source_to_arrival_book_ms": _distribution(arrival_ages),
        "worst_source_to_decision_age": worst_age,
        "worst_positive_decision_copy_decay": worst_decay,
    }


def read_ledger(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def write_analysis(ledger_path: Path, output_path: Path) -> dict[str, Any]:
    analysis = build_copy_timing_analysis(read_ledger(ledger_path))
    output_path.write_text(
        json.dumps(analysis, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return analysis


def main() -> None:
    parser = argparse.ArgumentParser(description="Build Sibyl PAPER V5 copy-timing evidence")
    parser.add_argument("ledger", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    write_analysis(args.ledger, args.output)


if __name__ == "__main__":
    main()
