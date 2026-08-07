from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from websockets.asyncio.client import connect

from app.polymarket import PolymarketClient, taker_fee_rate_for_category

BINANCE_SPOT_WS = "wss://data-stream.binance.vision/ws/btcusdt@aggTrade"
BINANCE_FUTURES_WS = "wss://fstream.binance.com/ws/btcusdt@aggTrade"
COINBASE_WS = "wss://ws-feed.exchange.coinbase.com"
POLYMARKET_MARKET_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


@dataclass(frozen=True)
class V3Target:
    condition_id: str
    question: str
    end_timestamp_ms: int
    outcome_assets: dict[str, str]
    fee_rate: float
    tick_size: float


@dataclass(frozen=True)
class V3Event:
    source: str
    event_type: str
    source_timestamp_ms: int | None
    receive_timestamp_ms: int
    price: float | None = None
    bid: float | None = None
    ask: float | None = None
    bid_size: float | None = None
    ask_size: float | None = None
    size: float | None = None
    aggressor_side: str | None = None
    asset_id: str | None = None
    sequence: int | None = None


@dataclass(frozen=True)
class V3Capture:
    events: tuple[V3Event, ...]
    core_errors: tuple[str, ...]
    optional_errors: tuple[str, ...]


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def _float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed or None


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


def parse_binance_aggtrade(
    payload: Any,
    *,
    source: str,
    received_ms: int | None = None,
) -> V3Event | None:
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        payload = payload["data"]
    if not isinstance(payload, dict) or payload.get("e") != "aggTrade":
        return None
    price = _float(payload.get("p"))
    size = _float(payload.get("q"))
    if price is None or price <= 0:
        return None
    aggressor_side = "SELL" if payload.get("m") is True else "BUY"
    return V3Event(
        source=source,
        event_type="TRADE",
        source_timestamp_ms=_int(payload.get("T") or payload.get("E")),
        receive_timestamp_ms=received_ms or now_ms(),
        price=price,
        size=size if size is not None and size > 0 else None,
        aggressor_side=aggressor_side,
        sequence=_int(payload.get("a")),
    )


def parse_coinbase_ticker(payload: Any, *, received_ms: int | None = None) -> V3Event | None:
    if not isinstance(payload, dict) or payload.get("type") != "ticker":
        return None
    if payload.get("product_id") != "BTC-USD":
        return None
    price = _float(payload.get("price"))
    if price is None or price <= 0:
        return None
    side = str(payload.get("side") or "").upper()
    return V3Event(
        source="COINBASE",
        event_type="TRADE",
        source_timestamp_ms=_iso_ms(payload.get("time")),
        receive_timestamp_ms=received_ms or now_ms(),
        price=price,
        bid=_float(payload.get("best_bid")),
        ask=_float(payload.get("best_ask")),
        size=_float(payload.get("last_size")),
        aggressor_side=side if side in {"BUY", "SELL"} else None,
        sequence=_int(payload.get("sequence")),
    )


def _best(levels: Any, *, highest: bool) -> tuple[float | None, float | None]:
    if not isinstance(levels, list):
        return None, None
    normalized: list[tuple[float, float]] = []
    for item in levels:
        if not isinstance(item, dict):
            continue
        price = _float(item.get("price"))
        size = _float(item.get("size"))
        if price is None or size is None or not 0 < price < 1 or size <= 0:
            continue
        normalized.append((price, size))
    if not normalized:
        return None, None
    return max(normalized) if highest else min(normalized)


def parse_polymarket_message(payload: Any, *, received_ms: int | None = None) -> list[V3Event]:
    if isinstance(payload, list):
        output: list[V3Event] = []
        for item in payload:
            output.extend(parse_polymarket_message(item, received_ms=received_ms))
        return output
    if not isinstance(payload, dict):
        return []
    event_type = str(payload.get("event_type") or "")
    asset_id = str(payload.get("asset_id") or "") or None
    timestamp = _int(payload.get("timestamp"))
    received = received_ms or now_ms()
    if event_type == "best_bid_ask":
        return [
            V3Event(
                source="POLYMARKET",
                event_type="BOOK",
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
            V3Event(
                source="POLYMARKET",
                event_type="BOOK",
                source_timestamp_ms=timestamp,
                receive_timestamp_ms=received,
                bid=bid,
                ask=ask,
                bid_size=bid_size,
                ask_size=ask_size,
                asset_id=asset_id,
            )
        ]
    if event_type == "last_trade_price":
        side = str(payload.get("side") or "").upper()
        return [
            V3Event(
                source="POLYMARKET",
                event_type="TRADE",
                source_timestamp_ms=timestamp,
                receive_timestamp_ms=received,
                price=_float(payload.get("price")),
                size=_float(payload.get("size")),
                aggressor_side=side if side in {"BUY", "SELL"} else None,
                asset_id=asset_id,
            )
        ]
    return []


def _fee_rate(info: dict[str, Any], category: str | None) -> float:
    detail = info.get("fd")
    if isinstance(detail, dict):
        rate = _float(detail.get("r"))
        if rate is not None and 0 <= rate <= 1:
            return rate
    return taker_fee_rate_for_category(category)


def discover_btc_target(client: PolymarketClient, *, horizon_minutes: int = 30) -> V3Target | None:
    markets = client.active_btc_short_markets(horizon_minutes=horizon_minutes)
    if not markets:
        return None
    market = markets[0]
    condition_id = str(market.get("conditionId") or "")
    if not condition_id:
        return None
    info = client.clob_market_info(condition_id)
    tokens = info.get("t")
    if not isinstance(tokens, list):
        return None
    outcomes = {
        str(token.get("o") or ""): str(token.get("t") or "")
        for token in tokens
        if isinstance(token, dict) and token.get("o") and token.get("t")
    }
    if len(outcomes) < 2:
        return None
    tick_size = _float(info.get("mts") or market.get("orderPriceMinTickSize"))
    if tick_size is None or tick_size <= 0:
        return None
    return V3Target(
        condition_id=condition_id,
        question=str(market.get("question") or market.get("slug") or condition_id),
        end_timestamp_ms=_iso_ms(market.get("endDate")) or 0,
        outcome_assets=outcomes,
        fee_rate=_fee_rate(info, str(market.get("category") or "Crypto")),
        tick_size=tick_size,
    )


async def _collect_binance(
    url: str,
    source: str,
    deadline: float,
    events: list[V3Event],
    errors: list[str],
    max_events: int,
) -> None:
    try:
        async with connect(url, open_timeout=8, close_timeout=2) as socket:
            count = 0
            while time.monotonic() < deadline and count < max_events:
                remaining = max(deadline - time.monotonic(), 0.05)
                raw = await asyncio.wait_for(socket.recv(), timeout=min(remaining, 2.0))
                event = parse_binance_aggtrade(
                    json.loads(raw), source=source, received_ms=now_ms()
                )
                if event is not None:
                    events.append(event)
                    count += 1
    except TimeoutError:
        return
    except Exception as exc:
        errors.append(f"{source}:{type(exc).__name__}:{str(exc)[:160]}")


async def _collect_coinbase(
    deadline: float,
    events: list[V3Event],
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
                event = parse_coinbase_ticker(json.loads(raw), received_ms=now_ms())
                if event is not None:
                    events.append(event)
                    count += 1
    except TimeoutError:
        return
    except Exception as exc:
        errors.append(f"COINBASE:{type(exc).__name__}:{str(exc)[:160]}")


async def _collect_polymarket(
    target: V3Target,
    deadline: float,
    events: list[V3Event],
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
            count = 0
            next_ping = time.monotonic() + 8.0
            while time.monotonic() < deadline and count < max_events:
                if time.monotonic() >= next_ping:
                    await socket.send("PING")
                    next_ping = time.monotonic() + 8.0
                remaining = max(deadline - time.monotonic(), 0.05)
                raw = await asyncio.wait_for(socket.recv(), timeout=min(remaining, 2.0))
                if raw == "PONG":
                    continue
                parsed = parse_polymarket_message(json.loads(raw), received_ms=now_ms())
                events.extend(parsed)
                count += len(parsed)
    except TimeoutError:
        return
    except Exception as exc:
        errors.append(f"POLYMARKET:{type(exc).__name__}:{str(exc)[:160]}")


def annotate_optional_feed_gaps(
    events: list[V3Event],
    errors: list[str],
    *,
    include_futures: bool,
) -> list[str]:
    annotated = list(errors)
    if (
        include_futures
        and not any(event.source == "BINANCE_FUTURES" for event in events)
        and not any(error.startswith("BINANCE_FUTURES:") for error in annotated)
    ):
        annotated.append("BINANCE_FUTURES:NO_EVENTS")
    return annotated


async def capture_market_window(
    target: V3Target,
    *,
    duration_seconds: float = 15.0,
    max_events_per_source: int = 160,
    include_futures: bool = True,
) -> V3Capture:
    if not 2 <= duration_seconds <= 60:
        raise ValueError("duration_seconds must be between 2 and 60")
    if max_events_per_source <= 0:
        raise ValueError("max_events_per_source must be positive")
    events: list[V3Event] = []
    core_errors: list[str] = []
    optional_errors: list[str] = []
    deadline = time.monotonic() + duration_seconds
    coroutines = [
        _collect_binance(
            BINANCE_SPOT_WS,
            "BINANCE",
            deadline,
            events,
            core_errors,
            max_events_per_source,
        ),
        _collect_coinbase(deadline, events, core_errors, max_events_per_source),
        _collect_polymarket(target, deadline, events, core_errors, max_events_per_source),
    ]
    if include_futures:
        coroutines.append(
            _collect_binance(
                BINANCE_FUTURES_WS,
                "BINANCE_FUTURES",
                deadline,
                events,
                optional_errors,
                max_events_per_source,
            )
        )
    await asyncio.gather(*coroutines)
    optional_errors = annotate_optional_feed_gaps(
        events,
        optional_errors,
        include_futures=include_futures,
    )
    return V3Capture(
        events=tuple(
            sorted(
                events,
                key=lambda event: (
                    event.receive_timestamp_ms,
                    event.source_timestamp_ms or -1,
                    event.source,
                    event.event_type,
                    event.sequence or -1,
                ),
            )
        ),
        core_errors=tuple(core_errors),
        optional_errors=tuple(optional_errors),
    )


def source_counts(events: tuple[V3Event, ...]) -> dict[str, int]:
    output: dict[str, int] = {}
    for event in events:
        output[event.source] = output.get(event.source, 0) + 1
    return output
