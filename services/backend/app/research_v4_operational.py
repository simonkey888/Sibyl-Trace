from __future__ import annotations

import argparse
import asyncio
import gzip
import hashlib
import json
import os
import shutil
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.event_tape_v4 import TapeEvent, reconstruct_l2, stable_tape_order
from app.journal_v3 import read_journal
from app.kalshi_v4 import KalshiReadOnlyVenue, normalize_kalshi_contract, rank_btc_markets
from app.market_data_v3 import V3Event, discover_btc_target
from app.market_data_v4 import V4Capture, capture_polymarket_l2
from app.market_identity_v4 import MarketContract, compare_contracts
from app.polymarket import PolymarketClient
from app.temporal_features_v4 import build_temporal_features

EVIDENCE_GENERATION = "SIBYL_RESEARCH_V4_OPERATIONAL"
MASTER_ORDER_PATH = Path("docs/MASTER_ORDER_RESEARCH_LAB_V4.md")
COPY_FORWARD_FILES = (
    "trial-summary.json",
    "research-summary.json",
    "latency-summary.json",
    "evidence-manifest.json",
    "research-v3-summary.json",
    "research-journal-v3.jsonl.gz",
    "evidence-manifest-v3.json",
)


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def validate_v3_source(payload: dict[str, Any]) -> None:
    if payload.get("status") != "PASS":
        raise ValueError("Research V4 requires PASS Research V3 evidence")
    if payload.get("evidence_generation") != "SIBYL_RESEARCH_V3":
        raise ValueError("Research V4 source is not Research V3")
    safety = payload.get("safety") or {}
    if (
        safety.get("trading_mode") != "PAPER"
        or safety.get("live_available") is not False
        or safety.get("real_money") is not False
        or float(safety.get("cost_authorized_usd", -1)) != 0
        or safety.get("paid_apis") is not False
    ):
        raise ValueError("Research V3 source violates PAPER/LIVE/$0 invariants")


def _v3_events(path: Path) -> tuple[V3Event, ...]:
    rows = read_journal(path)
    events: list[V3Event] = []
    for row in rows:
        payload = row.get("payload")
        if not isinstance(payload, dict):
            continue
        try:
            events.append(
                V3Event(
                    source=str(payload.get("source") or ""),
                    event_type=str(payload.get("event_type") or ""),
                    source_timestamp_ms=payload.get("source_timestamp_ms"),
                    receive_timestamp_ms=int(payload.get("receive_timestamp_ms") or 0),
                    price=payload.get("price"),
                    bid=payload.get("bid"),
                    ask=payload.get("ask"),
                    bid_size=payload.get("bid_size"),
                    ask_size=payload.get("ask_size"),
                    size=payload.get("size"),
                    aggressor_side=payload.get("aggressor_side"),
                    asset_id=payload.get("asset_id"),
                    sequence=payload.get("sequence"),
                )
            )
        except (TypeError, ValueError):
            continue
    return tuple(
        event for event in events if event.source and event.receive_timestamp_ms > 0
    )


def _gzip_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    lines = [
        json.dumps(
            row,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        for row in rows
    ]
    raw = ("\n".join(lines) + ("\n" if lines else "")).encode()
    path.write_bytes(gzip.compress(raw, compresslevel=9, mtime=0))


def _tape_rows(events: tuple[TapeEvent, ...]) -> list[dict[str, Any]]:
    return [asdict(event) for event in stable_tape_order(events)]


def _reconstruction_summary(capture: V4Capture) -> dict[str, Any]:
    assets = sorted({event.asset_id for event in capture.events})
    reconstructed: list[dict[str, Any]] = []
    for asset_id in assets:
        asset_events = tuple(
            event for event in capture.events if event.asset_id == asset_id
        )
        try:
            result = reconstruct_l2(asset_events)
            best_bid = (
                result.book.best_bid.price
                if result.book is not None and result.book.best_bid is not None
                else None
            )
            best_ask = (
                result.book.best_ask.price
                if result.book is not None and result.book.best_ask is not None
                else None
            )
            reconstructed.append(
                {
                    "asset_id": asset_id,
                    "status": result.status,
                    "applied_events": result.applied_events,
                    "gaps": list(result.gaps),
                    "final_bid_levels": len(result.book.bids) if result.book else 0,
                    "final_ask_levels": len(result.book.asks) if result.book else 0,
                    "best_bid": best_bid,
                    "best_ask": best_ask,
                }
            )
        except ValueError as exc:
            reconstructed.append(
                {
                    "asset_id": asset_id,
                    "status": "INVALID",
                    "applied_events": 0,
                    "gaps": [f"{type(exc).__name__}:{str(exc)[:160]}"],
                    "final_bid_levels": 0,
                    "final_ask_levels": 0,
                    "best_bid": None,
                    "best_ask": None,
                }
            )
    kinds = {
        kind: sum(event.kind == kind for event in capture.events)
        for kind in ("SNAPSHOT", "DELTA", "TRADE")
    }
    return {
        "status": "CAPTURED" if capture.events else "NO_DATA",
        "fidelity": "L2_AGGREGATE",
        "server_sequence_available": False,
        "continuity": capture.continuity,
        "raw_records": len(capture.raw_records),
        "normalized_events": len(capture.events),
        "snapshots": kinds["SNAPSHOT"],
        "deltas": kinds["DELTA"],
        "trades": kinds["TRADE"],
        "errors": list(capture.errors),
        "assets": reconstructed,
    }


def _polymarket_contract(
    question: str,
    condition_id: str,
    end_timestamp_ms: int,
) -> MarketContract:
    cutoff = (
        datetime.fromtimestamp(end_timestamp_ms / 1000, tz=UTC).isoformat()
        if end_timestamp_ms > 0
        else None
    )
    return MarketContract(
        venue="POLYMARKET",
        market_id=condition_id,
        title=question,
        underlying="BTC",
        event=None,
        outcome=None,
        strike=None,
        cutoff_iso=cutoff,
        timezone="UTC" if cutoff else None,
        resolution_source=None,
        resolution_rule=None,
        exceptions=(),
    )


def _kalshi_research(question: str, contract: MarketContract) -> dict[str, Any]:
    venue = KalshiReadOnlyVenue()
    try:
        markets = venue.list_markets(status="open", limit=1000)
        candidates = rank_btc_markets(markets, question)[:5]
        rows: list[dict[str, Any]] = []
        for market in candidates:
            candidate = normalize_kalshi_contract(market)
            decision = compare_contracts(contract, candidate)
            rows.append(
                {
                    "ticker": candidate.market_id,
                    "title": candidate.title,
                    "candidate_similarity": decision.candidate_similarity,
                    "identity_decision": decision.decision,
                    "mismatches": list(decision.mismatches),
                    "unknown_fields": list(decision.unknown_fields),
                    "parity_allowed": decision.decision == "EXACT_EQUIVALENT",
                }
            )
        exact = sum(
            row["identity_decision"] == "EXACT_EQUIVALENT" for row in rows
        )
        return {
            "status": "CANDIDATES" if rows else "NO_CANDIDATES",
            "public_read_only": True,
            "markets_scanned": len(markets),
            "candidates": rows,
            "exact_equivalents": exact,
            "parity_execution_enabled": False,
        }
    except Exception as exc:
        return {
            "status": "DEGRADED",
            "public_read_only": True,
            "markets_scanned": 0,
            "candidates": [],
            "exact_equivalents": 0,
            "parity_execution_enabled": False,
            "error": f"{type(exc).__name__}:{str(exc)[:160]}",
        }
    finally:
        venue.close()


def _copy_forward(input_dir: Path, output_dir: Path) -> None:
    for name in COPY_FORWARD_FILES:
        source = input_dir / name
        if source.is_file():
            shutil.copy2(source, output_dir / name)


def _render_markdown(summary: dict[str, Any]) -> str:
    tape = summary.get("l2_tape_v4") or {}
    cross = summary.get("cross_venue_v4") or {}
    target = summary.get("target") or {}
    counts = (
        f"{tape.get('snapshots', 0)} / "
        f"{tape.get('deltas', 0)} / "
        f"{tape.get('trades', 0)}"
    )
    return "\n".join(
        [
            "# Sibyl Trace — Research Lab V4 Operational",
            "",
            f"**Pipeline:** `{summary.get('status', 'UNKNOWN')}`  ",
            "**Mode:** `PAPER_SHADOW_ONLY`  ",
            "**LIVE:** `ABSENT`  ",
            "**Authorized cost:** `$0`  ",
            f"**Edge:** `{summary.get('edge_status', 'UNPROVEN')}`",
            "",
            "## Target",
            "",
            f"- {target.get('question', 'NO ACTIVE BTC TARGET')}",
            f"- condition: `{target.get('condition_id', 'none')}`",
            "",
            "## Real L2 evidence",
            "",
            f"- Raw websocket records: {tape.get('raw_records', 0)}",
            f"- Normalized tape events: {tape.get('normalized_events', 0)}",
            f"- Snapshots / deltas / trades: {counts}",
            f"- Fidelity: `{tape.get('fidelity', 'NO_DATA')}`",
            f"- Continuity statement: `{tape.get('continuity', 'UNKNOWN')}`",
            "",
            "## Cross venue",
            "",
            f"- Kalshi scan: `{cross.get('status', 'NO_DATA')}`",
            f"- Candidate contracts: {len(cross.get('candidates') or [])}",
            f"- Exact equivalents: {cross.get('exact_equivalents', 0)}",
            "- Cross-venue order execution: `DISABLED`",
            "",
            "V4 is additive evidence only. It never rewrites V2/V3 fills or state.",
            "",
        ]
    )


def run(input_dir: Path, output_dir: Path) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    v3 = _read_json(input_dir / "research-v3-summary.json")
    validate_v3_source(v3)
    settings = get_settings()
    if settings.live_trading_enabled or settings.cost_authorized_usd != 0:
        raise ValueError("unsafe runtime configuration")

    history_events = _v3_events(input_dir / "research-journal-v3.jsonl.gz")
    temporal = build_temporal_features(history_events)
    _copy_forward(input_dir, output_dir)

    source = {
        "github_run_id": os.getenv("SOURCE_V3_RUN_ID", ""),
        "github_sha": os.getenv("SOURCE_V3_SHA", os.getenv("GITHUB_SHA", "")),
        "research_v3_summary_sha256": _sha256(
            input_dir / "research-v3-summary.json"
        ),
        "research_v3_journal_sha256": _sha256(
            input_dir / "research-journal-v3.jsonl.gz"
        ),
    }
    summary: dict[str, Any] = {
        "schema_version": 4,
        "evidence_generation": EVIDENCE_GENERATION,
        "status": "PASS",
        "edge_status": "UNPROVEN",
        "source_v3": source,
        "safety": {
            "mode": "PAPER_SHADOW_ONLY",
            "trading_mode": "PAPER",
            "live_available": False,
            "real_money": False,
            "cost_authorized_usd": 0,
            "paid_apis": False,
            "order_placement": False,
            "private_keys": False,
            "historical_fill_rewrite": False,
        },
        "temporal_features_v4": temporal,
        "v3_v4_shadow": {
            "v3_preserved": True,
            "v4_replaces_v3_fills": False,
            "v4_mutates_rolling_db": False,
        },
    }

    client = PolymarketClient(settings)
    try:
        target = discover_btc_target(client)
        if target is None:
            summary["research_state"] = "NO_TARGET"
            summary["target"] = None
            summary["l2_tape_v4"] = {
                "status": "NO_TARGET",
                "fidelity": "L2_AGGREGATE",
                "server_sequence_available": False,
                "continuity": "NO_CAPTURE",
                "raw_records": 0,
                "normalized_events": 0,
                "snapshots": 0,
                "deltas": 0,
                "trades": 0,
                "errors": [],
                "assets": [],
            }
            summary["cross_venue_v4"] = {
                "status": "NO_TARGET",
                "candidates": [],
                "exact_equivalents": 0,
                "parity_execution_enabled": False,
            }
            _gzip_jsonl(output_dir / "research-tape-v4.jsonl.gz", [])
            _gzip_jsonl(output_dir / "research-raw-v4.jsonl.gz", [])
        else:
            capture = asyncio.run(
                capture_polymarket_l2(
                    target,
                    duration_seconds=15.0,
                    max_messages=240,
                )
            )
            summary["research_state"] = (
                "CAPTURED" if capture.events else "DEGRADED"
            )
            summary["target"] = asdict(target)
            summary["l2_tape_v4"] = _reconstruction_summary(capture)
            poly_contract = _polymarket_contract(
                target.question,
                target.condition_id,
                target.end_timestamp_ms,
            )
            summary["cross_venue_v4"] = _kalshi_research(
                target.question,
                poly_contract,
            )
            _gzip_jsonl(
                output_dir / "research-tape-v4.jsonl.gz",
                _tape_rows(capture.events),
            )
            _gzip_jsonl(
                output_dir / "research-raw-v4.jsonl.gz",
                list(capture.raw_records),
            )
    finally:
        client.close()

    _write_json(output_dir / "research-v4-summary.json", summary)
    (output_dir / "research-v4-summary.md").write_text(
        _render_markdown(summary),
        encoding="utf-8",
    )

    root = Path(os.getenv("GITHUB_WORKSPACE") or Path(__file__).resolve().parents[3])
    manifest = {
        "schema_version": 4,
        "evidence_generation": EVIDENCE_GENERATION,
        "source_v3": source,
        "code_sha": os.getenv("GITHUB_SHA", settings.app_version),
        "master_order_sha256": _sha256(root / MASTER_ORDER_PATH),
        "summary_sha256": _sha256(output_dir / "research-v4-summary.json"),
        "tape_sha256": _sha256(output_dir / "research-tape-v4.jsonl.gz"),
        "raw_sha256": _sha256(output_dir / "research-raw-v4.jsonl.gz"),
        "cost_policy": {"authorized_usd": 0, "paid_apis": False},
        "live_policy": {"available": False, "real_money": False},
        "rolling_database_mutated": False,
        "legacy_evidence_mutated": False,
    }
    _write_json(output_dir / "evidence-manifest-v4.json", manifest)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run operational PAPER-only Research Lab V4"
    )
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    raise SystemExit(run(args.input_dir, args.output_dir))


if __name__ == "__main__":
    main()
