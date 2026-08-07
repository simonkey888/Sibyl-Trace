from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from websockets.asyncio.client import connect


BINANCE_BTC_WS = "wss://stream.binance.com:9443/ws/btcusdt@aggTrade"
COINBASE_WS = "wss://ws-feed.exchange.coinbase.com"
POLYMARKET_MARKET_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


@dataclass(frozen=True)
class LatencyTarget:
    condition_id: str
    question: str
    end_timestamp_ms: int
    outcome_assets: dict[str, str]
    fee_rate: float
    tick_size: float


@dataclass(frozen=True)
class FeedEvent:
    source: str
    source_timestamp_ms: int | None
    receive_timestamp_ms: int
    price: float | None = None
    bid: float | None = None
    ask: float | None = None
    bid_size: float | None = None
    ask_size: float | None = None
    asset_id: str | None = None
    sequence: int | None = None


@dataclass(frozen=True)
class CaptureResult:
    events: tuple[FeedEvent, ...]
    errors: tuple[str, ...]


@dataclass(frozen=True)
class LatencyOpportunity:
    trigger_timestamp_ms: int
    direction: str
    exchange_move_bps: float
    asset_id: str
    entry_ask: float | None
    available_shares: float | None
    exit_bid: float | None
    lag_ms: int | None
    gross_edge_per_share: float | None
    fee_per_share: float | None
    net_edge_per_share: float | None
    executable: bool


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _iso_ms(value: Any) -> int | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp() * 1000)


def parse_binance_message(payload: Any, received_ms: int | None = None) -> FeedEvent | None:
    if not isinstance(payload, dict) or payload.get("e") != "aggTrade":
        return None
    price = _float(payload.get("p"))
    if price is None or price <= 0:
        return None
    return FeedEvent(
        source="BINANCE",
        source_timestamp_ms=int(payload.get("T") or payload.get("E") or 0) or None,
        receive_timestamp_ms=received_ms or now_ms(),
        price=price,
        sequence=int(payload.get("a") or 0) or None,
    )


def parse_coinbase_message(payload: Any, received_ms: int | None = None) -> FeedEvent | None:
    if not isinstance(payload, dict) or payload.get("type") != "ticker":
        return None
    if payload.get("product_id") != "BTC-USD":
        return None
    price = _float(payload.get("price"))
    if price is None or price <= 0:
        return None
    return FeedEvent(
        source="COINBASE",
        source_timestamp_ms=_iso_ms(payload.get("time")),
        receive_timestamp_ms=received_ms or now_ms(),
        price=price,
        bid=_float(payload.get("best_bid")),
        ask=_float(payload.get("best_ask")),
        sequence=int(payload.get("sequence") or 0) or None,
    )


def _best(levels: Any, *, highest: bool) -> tuple[float | None, float | None]:
    if not isinstance(levels, list):
        return None, None
    normalized: list[tuple[float, float]] = []
    for level in levels:
        if not isinstance(level, dict):
            continue
        price = _float(level.get("price"))
        size = _float(level.get("size"))
        if price is None or size is None or size <= 0:
            continue
        normalized.append((price, size))
    if not normalized:
        return None, None
    return max(normalized) if highest else min(normalized)


def parse_polymarket_message(payload: Any, received_ms: int | None = None) -> list[FeedEvent]:
    if isinstance(payload, list):
        events: list[FeedEvent] = []
        for item in payload:
            events.extend(parse_polymarket_message(item, received_ms))
        return events
    if not isinstance(payload, dict):
        return []
    event_type = payload.get("event_type")
    asset_id = str(payload.get("asset_id") or "") or None
    timestamp = int(payload.get("timestamp") or 0) or None
    received = received_ms or now_ms()
    if event_type == "best_bid_ask":
        return [
            FeedEvent(
                source="POLYMARKET",
                source_timestamp_ms=timestamp,
                receive_timestamp_ms=received,
                bid=_float(payload.get("best_bid")),
                ask=_float(payload.get("best_ask")),
                asset_id=asset_id,
            )
        ]
    if event_type == "book":
        bid, bid_size = _best(payload.get("bids"), highest=True)
        ask, ask_size = _best(payload.get("asks"), highest=False)
        return [
            FeedEvent(
                source="POLYMARKET",
                source_timestamp_ms=timestamp,
                receive_timestamp_ms=received,
                bid=bid,
                ask=ask,
                bid_size=bid_size,
                ask_size=ask_size,
                asset_id=asset_id,
            )
        ]
    return []


async def _collect_binance(
    deadline: float,
    events: list[FeedEvent],
    errors: list[str],
    max_events: int,
) -> None:
    try:
        async with connect(BINANCE_BTC_WS, open_timeout=8, close_timeout=2) as socket:
            count = 0
            while time.monotonic() < deadline and count < max_events:
                remaining = max(deadline - time.monotonic(), 0.05)
                raw = await asyncio.wait_for(socket.recv(), timeout=min(remaining, 2.0))
                event = parse_binance_message(json.loads(raw), now_ms())
                if event is not None:
                    events.append(event)
                    count += 1
    except TimeoutError:
        return
    except Exception as exc:
        errors.append(f"BINANCE:{type(exc).__name__}:{str(exc)[:160]}")


async def _collect_coinbase(
    deadline: float,
    events: list[FeedEvent],
    errors: list[str],
    max_events: int,
) -> None:
    subscription = {
        "type": "subscribe",
        "product_ids": ["BTC-USD"],
        "channels": ["ticker", "heartbeat"],
    }
    try:
        async with connect(COINBASE_WS, open_timeout=8, close_timeout=2) as socket:
            await socket.send(json.dumps(subscription))
            count = 0
            while time.monotonic() < deadline and count < max_events:
                remaining = max(deadline - time.monotonic(), 0.05)
                raw = await asyncio.wait_for(socket.recv(), timeout=min(remaining, 2.0))
                event = parse_coinbase_message(json.loads(raw), now_ms())
                if event is not None:
                    events.append(event)
                    count += 1
    except TimeoutError:
        return
    except Exception as exc:
        errors.append(f"COINBASE:{type(exc).__name__}:{str(exc)[:160]}")


async def _collect_polymarket(
    target: LatencyTarget,
    deadline: float,
    events: list[FeedEvent],
    errors: list[str],
    max_events: int,
) -> None:
    subscription = {
        "assets_ids": list(target.outcome_assets.values()),
        "type": "market",
        "custom_feature_enabled": True,
    }
    try:
        async with connect(POLYMARKET_MARKET_WS, open_timeout=8, close_timeout=2) as socket:
            await socket.send(json.dumps(subscription))
            next_text_ping = time.monotonic() + 8.0
            count = 0
            while time.monotonic() < deadline and count < max_events:
                if time.monotonic() >= next_text_ping:
                    await socket.send("PING")
                    next_text_ping = time.monotonic() + 8.0
                remaining = max(deadline - time.monotonic(), 0.05)
                raw = await asyncio.wait_for(socket.recv(), timeout=min(remaining, 2.0))
                if raw == "PONG":
                    continue
                parsed = json.loads(raw)
                new_events = parse_polymarket_message(parsed, now_ms())
                events.extend(new_events)
                count += len(new_events)
    except TimeoutError:
        return
    except Exception as exc:
        errors.append(f"POLYMARKET:{type(exc).__name__}:{str(exc)[:160]}")


async def capture_latency_window(
    target: LatencyTarget,
    *,
    duration_seconds: float = 20.0,
    max_events_per_source: int = 250,
) -> CaptureResult:
    if not 2 <= duration_seconds <= 120:
        raise ValueError("duration_seconds must be between 2 and 120")
    events: list[FeedEvent] = []
    errors: list[str] = []
    deadline = time.monotonic() + duration_seconds
    await asyncio.gather(
        _collect_binance(deadline, events, errors, max_events_per_source),
        _collect_coinbase(deadline, events, errors, max_events_per_source),
        _collect_polymarket(target, deadline, events, errors, max_events_per_source),
    )
    return CaptureResult(
        events=tuple(sorted(events, key=lambda event: event.receive_timestamp_ms)),
        errors=tuple(errors),
    )


def _price_before(events: list[FeedEvent], timestamp_ms: int) -> FeedEvent | None:
    candidates = [event for event in events if event.receive_timestamp_ms <= timestamp_ms]
    return candidates[-1] if candidates else None


def _latest_price_at_or_before(events: list[FeedEvent], timestamp_ms: int) -> float | None:
    event = _price_before(events, timestamp_ms)
    return event.price if event is not None else None


def _fee_per_share(price: float, fee_rate: float) -> float:
    return fee_rate * price * (1.0 - price)


def detect_consensus_impulses(
    events: tuple[FeedEvent, ...],
    *,
    lookback_ms: int = 1000,
    threshold_bps: float = 2.0,
    max_source_skew_ms: int = 750,
    min_trigger_spacing_ms: int = 500,
) -> list[tuple[int, str, float]]:
    by_source = {
        source: [event for event in events if event.source == source and event.price]
        for source in ("BINANCE", "COINBASE")
    }
    if not all(by_source.values()):
        return []
    timeline = sorted(
        by_source["BINANCE"] + by_source["COINBASE"],
        key=lambda event: event.receive_timestamp_ms,
    )
    triggers: list[tuple[int, str, float]] = []
    last_trigger = -10**18
    for event in timeline:
        timestamp = event.receive_timestamp_ms
        current_events = {
            source: _price_before(source_events, timestamp)
            for source, source_events in by_source.items()
        }
        if any(value is None for value in current_events.values()):
            continue
        current_values = [value for value in current_events.values() if value is not None]
        source_skew = max(value.receive_timestamp_ms for value in current_values) - min(
            value.receive_timestamp_ms for value in current_values
        )
        if source_skew > max_source_skew_ms:
            continue
        moves: list[float] = []
        for source, source_events in by_source.items():
            current = current_events[source]
            previous_price = _latest_price_at_or_before(
                source_events,
                timestamp - lookback_ms,
            )
            if (
                current is None
                or current.price is None
                or previous_price is None
                or previous_price <= 0
            ):
                moves = []
                break
            moves.append((current.price / previous_price - 1.0) * 10_000.0)
        if len(moves) != 2 or min(abs(move) for move in moves) < threshold_bps:
            continue
        if moves[0] * moves[1] <= 0:
            continue
        if timestamp - last_trigger < min_trigger_spacing_ms:
            continue
        direction = "UP" if moves[0] > 0 else "DOWN"
        triggers.append((timestamp, direction, sum(moves) / len(moves)))
        last_trigger = timestamp
    return triggers


def analyze_latency_opportunities(
    target: LatencyTarget,
    capture: CaptureResult,
    *,
    requested_shares: float = 5.0,
    convergence_window_ms: int = 3000,
) -> list[LatencyOpportunity]:
    if requested_shares <= 0:
        raise ValueError("requested_shares must be positive")
    poly_by_asset: dict[str, list[FeedEvent]] = defaultdict(list)
    for event in capture.events:
        if event.source == "POLYMARKET" and event.asset_id:
            poly_by_asset[event.asset_id].append(event)
    outcomes = {name.upper(): asset for name, asset in target.outcome_assets.items()}
    up_asset = next((asset for name, asset in outcomes.items() if "UP" in name), None)
    down_asset = next((asset for name, asset in outcomes.items() if "DOWN" in name), None)
    if not up_asset or not down_asset:
        return []

    opportunities: list[LatencyOpportunity] = []
    for timestamp, direction, move_bps in detect_consensus_impulses(capture.events):
        asset = up_asset if direction == "UP" else down_asset
        market_events = poly_by_asset.get(asset, [])
        before = _price_before(market_events, timestamp)
        entry_ask = before.ask if before else None
        available = before.ask_size if before else None
        exit_event = next(
            (
                event
                for event in market_events
                if timestamp < event.receive_timestamp_ms <= timestamp + convergence_window_ms
                and event.bid is not None
            ),
            None,
        )
        exit_bid = exit_event.bid if exit_event else None
        lag_ms = exit_event.receive_timestamp_ms - timestamp if exit_event else None
        gross = (
            exit_bid - entry_ask
            if entry_ask is not None and exit_bid is not None
            else None
        )
        fees = (
            _fee_per_share(entry_ask, target.fee_rate)
            + _fee_per_share(exit_bid, target.fee_rate)
            if entry_ask is not None and exit_bid is not None
            else None
        )
        net = gross - fees if gross is not None and fees is not None else None
        executable = bool(
            entry_ask is not None
            and exit_bid is not None
            and available is not None
            and available >= requested_shares
            and net is not None
            and net > 0
        )
        opportunities.append(
            LatencyOpportunity(
                trigger_timestamp_ms=timestamp,
                direction=direction,
                exchange_move_bps=move_bps,
                asset_id=asset,
                entry_ask=entry_ask,
                available_shares=available,
                exit_bid=exit_bid,
                lag_ms=lag_ms,
                gross_edge_per_share=gross,
                fee_per_share=fees,
                net_edge_per_share=net,
                executable=executable,
            )
        )
    return opportunities
