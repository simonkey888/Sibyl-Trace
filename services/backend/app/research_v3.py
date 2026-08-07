from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from dataclasses import asdict
from datetime import UTC, datetime
from math import log
from pathlib import Path
from statistics import pstdev
from typing import Any

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.evidence import hash_payload
from app.features_v3 import build_cross_market_features
from app.journal_v3 import export_journal, journal_row_count, persist_downsampled_capture
from app.market_data_v3 import (
    V3Capture,
    V3Event,
    V3Target,
    capture_market_window,
    discover_btc_target,
    source_counts,
)
from app.market_making_v3 import decide_quotes, decision_payload
from app.microstructure_v3 import (
    adverse_selection_toxicity,
    book_metrics,
    signed_flow_ewma,
    simulate_l1_take,
)
from app.polymarket import PolymarketClient
from app.replay_v3 import replay_capture
from app.research_models import ResearchExperiment
from app.venue_v3 import NormalizedBook, PriceLevel
from app.watchdogs import feed_watchdog

EVIDENCE_GENERATION = "SIBYL_RESEARCH_V3"
BASELINE_SHA = "ceb2db8aa093037a76b820d176188471ea18bf9d"
MASTER_ORDER_PATH = Path("docs/MASTER_ORDER_RESEARCH_LAB_V3.md")


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def validate_v2_source(payload: dict[str, Any]) -> dict[str, str]:
    run = payload.get("run")
    safety = payload.get("safety")
    if not isinstance(run, dict) or run.get("status") != "PASS":
        raise ValueError("Research V3 requires an exact PASS PAPER V2 source")
    if not isinstance(safety, dict):
        raise ValueError("PAPER V2 safety payload missing")
    if (
        safety.get("trading_mode") != "PAPER"
        or safety.get("live_available") is not False
        or float(safety.get("cost_authorized_usd", -1)) != 0
    ):
        raise ValueError("Research V3 source violates PAPER/LIVE/$0 invariants")
    return {
        "github_run_id": str(run.get("github_run_id") or ""),
        "github_sha": str(run.get("github_sha") or ""),
        "completed_at": str(run.get("completed_at") or ""),
    }


def _book_from_event(event: V3Event) -> NormalizedBook | None:
    if (
        event.source != "POLYMARKET"
        or event.event_type != "BOOK"
        or not event.asset_id
        or event.bid is None
        or event.ask is None
        or event.bid_size is None
        or event.ask_size is None
        or event.bid >= event.ask
        or event.bid_size <= 0
        or event.ask_size <= 0
    ):
        return None
    return NormalizedBook(
        venue="POLYMARKET",
        asset_id=event.asset_id,
        bids=(PriceLevel(event.bid, event.bid_size),),
        asks=(PriceLevel(event.ask, event.ask_size),),
        source_timestamp_ms=event.source_timestamp_ms,
        receive_timestamp_ms=event.receive_timestamp_ms,
    )


def _latest_books(events: tuple[V3Event, ...]) -> dict[str, NormalizedBook]:
    books: dict[str, NormalizedBook] = {}
    for event in events:
        book = _book_from_event(event)
        if book is not None:
            books[book.asset_id] = book
    return books


def _asset_volatility(events: tuple[V3Event, ...], asset_id: str) -> float:
    mids = [
        (event.bid + event.ask) / 2.0
        for event in events
        if event.source == "POLYMARKET"
        and event.event_type == "BOOK"
        and event.asset_id == asset_id
        and event.bid is not None
        and event.ask is not None
        and 0 < event.bid < event.ask < 1
    ]
    returns = [
        log(current / previous)
        for previous, current in zip(mids, mids[1:])
        if previous > 0 and current > 0
    ]
    return pstdev(returns) if len(returns) >= 2 else 0.0


def _asset_flow(events: tuple[V3Event, ...], asset_id: str) -> float:
    signed = [
        event.size if event.aggressor_side == "BUY" else -event.size
        for event in events
        if event.source == "POLYMARKET"
        and event.event_type == "TRADE"
        and event.asset_id == asset_id
        and event.size is not None
        and event.size > 0
        and event.aggressor_side in {"BUY", "SELL"}
    ]
    return signed_flow_ewma(signed)


def _asset_toxicity(events: tuple[V3Event, ...], asset_id: str) -> float:
    observations: list[tuple[float, float, str]] = []
    asset_events = [
        event
        for event in events
        if event.source == "POLYMARKET" and event.asset_id == asset_id
    ]
    for index, event in enumerate(asset_events):
        if (
            event.event_type != "TRADE"
            or event.price is None
            or event.aggressor_side not in {"BUY", "SELL"}
        ):
            continue
        later = next(
            (
                candidate
                for candidate in asset_events[index + 1 :]
                if candidate.event_type == "BOOK"
                and candidate.receive_timestamp_ms <= event.receive_timestamp_ms + 2_000
                and candidate.bid is not None
                and candidate.ask is not None
                and candidate.bid < candidate.ask
            ),
            None,
        )
        if later is None:
            continue
        observations.append(
            (
                event.price,
                (later.bid + later.ask) / 2.0,
                event.aggressor_side,
            )
        )
    return adverse_selection_toxicity(observations)  # type: ignore[arg-type]


def analyze_capture(
    target: V3Target,
    capture: V3Capture,
    *,
    requested_shares: float = 5.0,
    analysis_now_ms: int | None = None,
) -> dict[str, Any]:
    events = capture.events
    counts = source_counts(events)
    feed = feed_watchdog(counts, capture.core_errors)
    features = build_cross_market_features(events)
    books = _latest_books(events)
    micro_assets: list[dict[str, Any]] = []
    maker_assets: list[dict[str, Any]] = []
    now_value = analysis_now_ms or (
        events[-1].receive_timestamp_ms
        if events
        else int(datetime.now(UTC).timestamp() * 1000)
    )
    for asset_id, book in sorted(books.items()):
        metrics = book_metrics(book)
        take = simulate_l1_take(side="BUY", quantity=requested_shares, book=book)
        volatility = _asset_volatility(events, asset_id)
        flow = _asset_flow(events, asset_id)
        toxicity = _asset_toxicity(events, asset_id)
        micro_assets.append(
            {
                "asset_id": asset_id,
                "metrics": asdict(metrics),
                "l1_take_buy": asdict(take),
                "volatility": volatility,
                "signed_flow_ewma": flow,
                "toxicity": toxicity,
            }
        )
        decision = decide_quotes(
            book=book,
            tick_size=target.tick_size,
            now_ms=now_value,
            end_timestamp_ms=target.end_timestamp_ms,
            inventory=0.0,
            volatility=volatility,
            toxicity=toxicity,
            signed_flow_ewma=flow,
        )
        maker_assets.append(
            {
                "asset_id": asset_id,
                "mode": "PAPER_RESEARCH_ONLY",
                **decision_payload(decision),
            }
        )

    replay = replay_capture(events)
    return {
        "watchdog": asdict(feed),
        "source_counts": counts,
        "core_errors": list(capture.core_errors),
        "optional_errors": list(capture.optional_errors),
        "cross_market_features_v3": features,
        "microstructure_v3": {
            "status": "CAPTURED" if micro_assets else "NO_DEPTH",
            "assets": micro_assets,
            "queue_model": "conservative_queue_ahead",
            "legacy_fills_rewritten": False,
        },
        "market_making_v3": {
            "status": "ANALYZED" if maker_assets else "NO_DEPTH",
            "execution_enabled": False,
            "assets": maker_assets,
        },
        "replay_v3": replay,
    }


def _upsert_experiment(db: Any, settings: Any) -> None:
    experiment_id = "RESEARCH_LAB_V3"
    if db.get(ResearchExperiment, experiment_id) is not None:
        return
    config = {
        "public_feeds_only": True,
        "paper_only": True,
        "queue_model": "conservative_queue_ahead",
        "optional_binance_futures": True,
    }
    db.add(
        ResearchExperiment(
            experiment_id=experiment_id,
            kind="MICROSTRUCTURE_REPLAY",
            strategy_version="RESEARCH_LAB_V3",
            evidence_generation=EVIDENCE_GENERATION,
            config_hash=hash_payload(config),
            code_sha=os.getenv("GITHUB_SHA", settings.app_version),
            status="RESEARCH",
        )
    )
    db.commit()


def _sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _render_markdown(summary: dict[str, Any]) -> str:
    target = summary.get("target") or {}
    micro = summary.get("microstructure_v3") or {}
    maker = summary.get("market_making_v3") or {}
    replay = summary.get("replay_v3") or {}
    features = summary.get("cross_market_features_v3") or {}
    return "\n".join(
        [
            "# Sibyl Trace — Research Lab V3",
            "",
            f"**Pipeline:** `{summary.get('status', 'UNKNOWN')}`  ",
            f"**Watchdog:** `{summary.get('watchdog_state', 'UNKNOWN')}`  ",
            "**LIVE:** `ABSENT`  ",
            "**Authorized cost:** `$0`  ",
            "**Edge:** `UNPROVEN`",
            "",
            "## Target",
            "",
            f"- {target.get('question', 'NO ACTIVE BTC TARGET')}",
            f"- condition: `{target.get('condition_id', 'none')}`",
            "",
            "## Evidence",
            "",
            f"- Captured events: {summary.get('events', 0)}",
            f"- Persisted journal rows: {summary.get('journal_rows', 0)}",
            f"- Microstructure assets: {len(micro.get('assets') or [])}",
            f"- Maker PAPER decisions: {len(maker.get('assets') or [])}",
            f"- Replay queue probes: {replay.get('queue_probes', 0)}",
            "- Binance Futures observations: "
            + str(
                ((features.get("sources") or {}).get("BINANCE_FUTURES") or {}).get(
                    "events", 0
                )
            ),
            "",
            "Research V3 never places or signs an order and never rewrites legacy PAPER fills.",
            "",
        ]
    )


def run(input_dir: Path, output_dir: Path) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    source_payload = _read_json(input_dir / "trial-summary.json")
    source = validate_v2_source(source_payload)
    settings = get_settings()
    if settings.live_trading_enabled or settings.cost_authorized_usd != 0:
        raise ValueError("unsafe runtime configuration")
    init_db()
    summary: dict[str, Any] = {
        "schema_version": 1,
        "evidence_generation": EVIDENCE_GENERATION,
        "status": "PASS",
        "edge_status": "UNPROVEN",
        "source_v2": source,
        "safety": {
            "trading_mode": "PAPER",
            "live_available": False,
            "real_money": False,
            "cost_authorized_usd": 0,
            "paid_apis": False,
        },
    }
    client = PolymarketClient(settings)
    try:
        target = discover_btc_target(client)
        if target is None:
            summary.update(
                {
                    "watchdog_state": "YELLOW",
                    "research_state": "NO_TARGET",
                    "events": 0,
                    "target": None,
                    "cross_market_features_v3": {"status": "NO_DATA"},
                    "microstructure_v3": {"status": "NO_TARGET", "assets": []},
                    "market_making_v3": {
                        "status": "NO_TARGET",
                        "execution_enabled": False,
                        "assets": [],
                    },
                    "replay_v3": {
                        "status": "NO_DATA",
                        "event_count": 0,
                        "queue_probes": 0,
                    },
                }
            )
            with SessionLocal() as db:
                _upsert_experiment(db, settings)
                summary["journal_rows"] = journal_row_count(db)
                export_journal(db, output_dir / "research-journal-v3.jsonl.gz")
        else:
            capture = asyncio.run(
                capture_market_window(
                    target,
                    duration_seconds=15.0,
                    max_events_per_source=160,
                    include_futures=True,
                )
            )
            analysis = analyze_capture(target, capture)
            summary.update(analysis)
            summary["watchdog_state"] = analysis["watchdog"]["state"]
            summary["research_state"] = "CAPTURED" if capture.events else "DEGRADED"
            summary["events"] = len(capture.events)
            summary["target"] = asdict(target)
            with SessionLocal() as db:
                _upsert_experiment(db, settings)
                inserted = persist_downsampled_capture(
                    db,
                    experiment_id="RESEARCH_LAB_V3",
                    market_id=target.condition_id,
                    run_id=os.getenv("GITHUB_RUN_ID", "local"),
                    events=capture.events,
                )
                summary["journal_rows_inserted"] = inserted
                summary["journal_rows"] = journal_row_count(db)
                export_journal(db, output_dir / "research-journal-v3.jsonl.gz")
    finally:
        client.close()

    _write_json(output_dir / "research-v3-summary.json", summary)
    (output_dir / "research-v3-summary.md").write_text(
        _render_markdown(summary),
        encoding="utf-8",
    )
    root = Path(os.getenv("GITHUB_WORKSPACE") or Path(__file__).resolve().parents[3])
    manifest = {
        "schema_version": 1,
        "evidence_generation": EVIDENCE_GENERATION,
        "baseline_main_sha": BASELINE_SHA,
        "source_v2": source,
        "code_sha": os.getenv("GITHUB_SHA", settings.app_version),
        "master_order_sha256": _sha256(root / MASTER_ORDER_PATH),
        "summary_sha256": _sha256(output_dir / "research-v3-summary.json"),
        "journal_sha256": _sha256(output_dir / "research-journal-v3.jsonl.gz"),
        "cost_policy": {"authorized_usd": 0, "paid_apis": False},
        "live_policy": {"available": False, "real_money": False},
        "legacy_evidence_mutated": False,
    }
    _write_json(output_dir / "evidence-manifest-v3.json", manifest)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Run additive Sibyl Research Lab V3")
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    return run(args.input_dir, args.output_dir)


if __name__ == "__main__":
    raise SystemExit(main())
