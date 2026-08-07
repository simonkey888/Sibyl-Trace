from __future__ import annotations

import asyncio
import json
import os
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.evidence import hash_payload
from app.hypothesis import HypothesisSpec, make_hypothesis
from app.latency import (
    CaptureResult,
    FeedEvent,
    LatencyTarget,
    analyze_latency_opportunities,
    capture_latency_window,
)
from app.polymarket import PolymarketClient, taker_fee_rate_for_category
from app.research_models import (
    ResearchCheckpoint,
    ResearchExperiment,
    ResearchHypothesis,
    ResearchObservation,
    WatchdogEvent,
)
from app.trader_research import trader_reconstruction, weather_hypothesis_status
from app.watchdogs import feed_watchdog, global_watchdog_state, sample_watchdog


def utcnow() -> datetime:
    return datetime.now(UTC)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _iso_ms(value: Any) -> int:
    if not isinstance(value, str):
        return 0
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return 0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp() * 1000)


def _experiment(
    db: Session,
    *,
    experiment_id: str,
    kind: str,
    strategy_version: str,
    settings: Settings,
    config: dict[str, Any],
) -> ResearchExperiment:
    row = db.get(ResearchExperiment, experiment_id)
    if row is None:
        row = ResearchExperiment(
            experiment_id=experiment_id,
            kind=kind,
            strategy_version=strategy_version,
            evidence_generation=settings.evidence_generation,
            config_hash=hash_payload(config),
            code_sha=os.getenv("GITHUB_SHA", settings.app_version),
            status="RESEARCH",
        )
        db.add(row)
    return row


def _preregister(db: Session, spec: HypothesisSpec) -> ResearchHypothesis:
    row = db.get(ResearchHypothesis, spec.hypothesis_id)
    if row is None:
        payload = asdict(spec)
        row = ResearchHypothesis(
            hypothesis_id=spec.hypothesis_id,
            parent_id=spec.parent_id,
            kind=spec.kind,
            thesis=spec.thesis,
            config_json=_json(spec.config),
            preregistration_json=_json(payload),
            status="PREREGISTERED",
        )
        db.add(row)
    return row


def preregister_default_hypotheses(db: Session) -> list[str]:
    specs = [
        make_hypothesis(
            kind="BTC_LATENCY",
            thesis=(
                "Consensus BTC spot moves on Binance and Coinbase may precede a fee-adjusted, "
                "depth-executable repricing of short-horizon Polymarket BTC outcomes."
            ),
            config={
                "lookback_ms": 1000,
                "trigger_bps": 2.0,
                "convergence_window_ms": 3000,
                "requires_depth": True,
                "requires_both_exchanges": True,
            },
            minimum_sample=30,
        ),
        make_hypothesis(
            kind="PAYOUT_ASYMMETRY",
            thesis=(
                "A trader can have positive expectancy below 50% win rate when average winners "
                "are sufficiently larger than average losers."
            ),
            config={"reference_username": "djdjdjekekek", "metric": "expectancy_r"},
            minimum_sample=100,
        ),
        make_hypothesis(
            kind="WEATHER_STYLE",
            thesis=(
                "The public okkokok history may show persistent edge concentration in specific "
                "price buckets and a positive relationship between entry price and position size."
            ),
            config={
                "reference_username": "okkokok",
                "price_buckets": ["LOW_01_10", "MID_50_70"],
            },
            minimum_sample=100,
        ),
        make_hypothesis(
            kind="SPORTS_FAIR_PRICE",
            thesis=(
                "Dixon-Coles fair probabilities can identify sports mispricing after fees and "
                "execution costs without using future outcomes in the decision timestamp."
            ),
            config={"model": "DIXON_COLES", "anti_lookahead": True},
            minimum_sample=100,
        ),
        make_hypothesis(
            kind="HIGH_TURNOVER_STYLE",
            thesis=(
                "High breadth, small sizing, short holding periods and positive payout asymmetry "
                "may matter more than raw hit rate for high-turnover traders."
            ),
            config={"operational_strategy": False, "cross_market_style_metric": True},
            minimum_sample=250,
        ),
    ]
    for spec in specs:
        _preregister(db, spec)
    db.commit()
    return [spec.hypothesis_id for spec in specs]


def checkpoint(db: Session, run_id: str, phase: str, state: Any) -> None:
    state_hash = hash_payload(state)
    row = db.scalar(
        select(ResearchCheckpoint).where(
            ResearchCheckpoint.run_id == run_id,
            ResearchCheckpoint.phase == phase,
        )
    )
    if row is None:
        db.add(ResearchCheckpoint(run_id=run_id, phase=phase, state_hash=state_hash))
    elif row.state_hash != state_hash:
        row.state_hash = state_hash
        row.completed_at = utcnow()
    db.commit()


def _target_from_market(client: PolymarketClient, market: dict[str, Any]) -> LatencyTarget:
    condition_id = str(market.get("conditionId") or "")
    if not condition_id:
        raise ValueError("market has no conditionId")
    info = client.clob_market_info(condition_id)
    tokens = info.get("t")
    if not isinstance(tokens, list):
        raise ValueError("CLOB market info has no token list")
    outcome_assets = {
        str(token.get("o") or ""): str(token.get("t") or "")
        for token in tokens
        if isinstance(token, dict) and token.get("o") and token.get("t")
    }
    if not outcome_assets:
        raise ValueError("CLOB market info exposed no outcome assets")
    tick_size = float(info.get("mts") or market.get("orderPriceMinTickSize") or 0)
    if tick_size <= 0:
        raise ValueError("market has no valid tick size")
    return LatencyTarget(
        condition_id=condition_id,
        question=str(market.get("question") or market.get("slug") or condition_id),
        end_timestamp_ms=_iso_ms(market.get("endDate")),
        outcome_assets=outcome_assets,
        fee_rate=taker_fee_rate_for_category(str(market.get("category") or "Crypto")),
        tick_size=tick_size,
    )


def _source_counts(capture: CaptureResult) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in capture.events:
        counts[event.source] = counts.get(event.source, 0) + 1
    return counts


def _capture_summary(target: LatencyTarget, capture: CaptureResult, requested_shares: float) -> dict:
    opportunities = analyze_latency_opportunities(
        target,
        capture,
        requested_shares=requested_shares,
    )
    executable = [item for item in opportunities if item.executable]
    lags = [item.lag_ms for item in opportunities if item.lag_ms is not None]
    net_edges = [
        item.net_edge_per_share
        for item in executable
        if item.net_edge_per_share is not None
    ]
    return {
        "target": {
            "condition_id": target.condition_id,
            "question": target.question,
            "end_timestamp_ms": target.end_timestamp_ms,
            "outcome_assets": target.outcome_assets,
            "fee_rate": target.fee_rate,
            "tick_size": target.tick_size,
        },
        "source_counts": _source_counts(capture),
        "feed_errors": list(capture.errors),
        "events": len(capture.events),
        "divergences": len(opportunities),
        "executable_divergences": len(executable),
        "average_lag_ms": sum(lags) / len(lags) if lags else None,
        "average_executable_edge_per_share": (
            sum(net_edges) / len(net_edges) if net_edges else None
        ),
        "opportunities": [asdict(item) for item in opportunities],
    }


def run_latency_lab(
    db: Session,
    client: PolymarketClient,
    settings: Settings,
    run_id: str,
) -> dict[str, Any]:
    if not settings.latency_lab_enabled:
        return {"status": "DISABLED"}
    markets = client.active_btc_short_markets(horizon_minutes=30)
    if not markets:
        assessment = feed_watchdog({}, ("POLYMARKET:NO_ACTIVE_BTC_TARGET",))
        db.add(
            WatchdogEvent(
                watchdog=assessment.watchdog,
                state=assessment.state,
                message=assessment.message,
                payload_json=_json(assessment.payload),
            )
        )
        db.commit()
        return {"status": "NO_TARGET", "watchdog": asdict(assessment)}

    target = _target_from_market(client, markets[0])
    capture = asyncio.run(
        capture_latency_window(
            target,
            duration_seconds=settings.latency_capture_seconds,
            max_events_per_source=120,
        )
    )
    summary = _capture_summary(target, capture, settings.latency_requested_shares)
    assessment = feed_watchdog(summary["source_counts"], capture.errors)
    db.add(
        WatchdogEvent(
            watchdog=assessment.watchdog,
            state=assessment.state,
            message=assessment.message,
            payload_json=_json(assessment.payload),
        )
    )
    experiment_id = "LATENCY_LAB_V1"
    _experiment(
        db,
        experiment_id=experiment_id,
        kind="BTC_LATENCY",
        strategy_version="LATENCY_V1",
        settings=settings,
        config={
            "capture_seconds": settings.latency_capture_seconds,
            "requested_shares": settings.latency_requested_shares,
            "public_feeds_only": True,
        },
    )
    for opportunity in summary["opportunities"]:
        key_payload = {
            "condition_id": target.condition_id,
            "trigger_timestamp_ms": opportunity["trigger_timestamp_ms"],
            "direction": opportunity["direction"],
        }
        observation_key = hash_payload(key_payload)
        if db.scalar(
            select(ResearchObservation.id).where(
                ResearchObservation.observation_key == observation_key
            )
        ) is not None:
            continue
        db.add(
            ResearchObservation(
                observation_key=observation_key,
                experiment_id=experiment_id,
                source="PUBLIC_L2_LATENCY",
                market_id=target.condition_id,
                asset_id=opportunity["asset_id"],
                category="CRYPTO",
                source_timestamp_ms=opportunity["trigger_timestamp_ms"],
                receive_timestamp_ms=opportunity["trigger_timestamp_ms"],
                market_price=opportunity["entry_ask"],
                gross_edge=opportunity["gross_edge_per_share"],
                costs=opportunity["fee_per_share"],
                net_edge=opportunity["net_edge_per_share"],
                fillable=bool(opportunity["executable"]),
                payload_hash=hash_payload(opportunity),
                payload_json=_json(opportunity),
            )
        )
    db.commit()
    checkpoint(db, run_id, "LATENCY_CAPTURE", summary)
    return {"status": "CAPTURED", **summary, "watchdog": asdict(assessment)}


def _reference_category(username: str) -> str:
    if username.casefold() == "okkokok":
        return "WEATHER"
    if username.casefold() == "djdjdjekekek":
        return "SPORTS"
    return "OVERALL"


def run_reference_research(
    db: Session,
    client: PolymarketClient,
    settings: Settings,
) -> dict[str, Any]:
    if not settings.reference_research_enabled:
        return {"status": "DISABLED", "traders": {}}
    output: dict[str, Any] = {}
    for username in settings.reference_username_list:
        leaderboard = client.leaderboard_username(
            username,
            category=_reference_category(username),
            period="ALL",
        )
        if not leaderboard:
            output[username] = {"status": "NOT_FOUND"}
            continue
        wallet = str(leaderboard.get("proxyWallet") or "").lower()
        if len(wallet) != 42:
            output[username] = {"status": "INVALID_WALLET"}
            continue
        positions = client.research_closed_positions(wallet, limit=1000)
        summary = trader_reconstruction(positions)
        if username.casefold() == "okkokok":
            summary["weather_hypothesis"] = weather_hypothesis_status(summary)
        summary["leaderboard"] = {
            "pnl": leaderboard.get("pnl"),
            "vol": leaderboard.get("vol"),
            "rank": leaderboard.get("rank"),
        }
        output[username] = {"status": "RECONSTRUCTED", "wallet": wallet, **summary}
        experiment_id = f"REFERENCE_{username.upper()}"
        _experiment(
            db,
            experiment_id=experiment_id,
            kind="REFERENCE_RECONSTRUCTION",
            strategy_version="REFERENCE_V1",
            settings=settings,
            config={"username": username, "category": _reference_category(username)},
        )
        last_timestamp = max((int(row.get("timestamp") or 0) for row in positions), default=0)
        observation_key = hash_payload(
            {"username": username, "sample": len(positions), "last_timestamp": last_timestamp}
        )
        if db.scalar(
            select(ResearchObservation.id).where(
                ResearchObservation.observation_key == observation_key
            )
        ) is None:
            db.add(
                ResearchObservation(
                    observation_key=observation_key,
                    experiment_id=experiment_id,
                    source="POLYMARKET_DATA_API",
                    category=_reference_category(username),
                    source_timestamp_ms=last_timestamp * 1000 if last_timestamp else None,
                    receive_timestamp_ms=int(utcnow().timestamp() * 1000),
                    payload_hash=hash_payload(summary),
                    payload_json=_json(summary),
                )
            )
    db.commit()
    return {"status": "COMPLETE", "traders": output}


def research_totals(db: Session) -> dict[str, int]:
    return {
        "experiments": int(db.scalar(select(func.count()).select_from(ResearchExperiment)) or 0),
        "hypotheses": int(db.scalar(select(func.count()).select_from(ResearchHypothesis)) or 0),
        "observations": int(db.scalar(select(func.count()).select_from(ResearchObservation)) or 0),
        "watchdogs": int(db.scalar(select(func.count()).select_from(WatchdogEvent)) or 0),
    }


def run_research_cycle(
    db: Session,
    client: PolymarketClient,
    settings: Settings,
    run_id: str,
) -> dict[str, Any]:
    if not settings.research_enabled:
        return {"status": "DISABLED", "watchdog_state": "YELLOW"}
    preregistered = preregister_default_hypotheses(db)
    reference = run_reference_research(db, client, settings)
    latency = run_latency_lab(db, client, settings, run_id)
    latency_sample = int(
        db.scalar(
            select(func.count())
            .select_from(ResearchObservation)
            .where(ResearchObservation.experiment_id == "LATENCY_LAB_V1")
        )
        or 0
    )
    assessments = [sample_watchdog("LATENCY_EVIDENCE_GAP", latency_sample, minimum=30)]
    if isinstance(latency.get("watchdog"), dict):
        watchdog = latency["watchdog"]
        if watchdog.get("state") in {"GREEN", "YELLOW", "RED"}:
            from app.watchdogs import WatchdogAssessment

            assessments.append(
                WatchdogAssessment(
                    watchdog=str(watchdog.get("watchdog") or "LATENCY_FEED_DESYNC"),
                    state=str(watchdog["state"]),
                    message=str(watchdog.get("message") or ""),
                    payload=dict(watchdog.get("payload") or {}),
                )
            )
    summary = {
        "status": "COMPLETE",
        "evidence_generation": settings.evidence_generation,
        "preregistered_hypotheses": preregistered,
        "reference_research": reference,
        "latency": latency,
        "totals": research_totals(db),
        "watchdog_state": global_watchdog_state(assessments),
    }
    checkpoint(db, run_id, "WATCHDOG", summary["totals"])
    return summary
