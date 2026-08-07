from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any

from websockets.asyncio.client import connect

from app.event_tape_v4 import TapeEvent, TapeLevel
from app.market_data_v3 import POLYMARKET_MARKET_WS, V3Target


@dataclass(frozen=True)
class V4Capture:
    events: tuple[TapeEvent, ...]
    raw_records: tuple[dict[str, Any], ...]
    errors: tuple[str, ...]
    continuity: str


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def _float(value: object) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: object) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _level(side: str, item: object) -> TapeLevel | None:
    if not isinstance(item, dict):
        return None
    price = _float(item.get("price"))
    size = _float(item.get("size"))
    if price is None or size is None or not 0 < price < 1 or size < 0:
        return None
    return TapeLevel("BID" if side == "BUY" else "ASK", price, size)


def _snapshot_levels(payload: dict[str, Any]) -> tuple[TapeLevel, ...]:
    output: list[TapeLevel] = []
    for item in payload.get("bids") or []:
        level = _level("BUY", item)
        if level is not None:
            output.append(level)
    for item in payload.get("asks") or []:
        level = _level("SELL", item)
        if level is not None:
            output.append(level)
    return tuple(output)


def normalize_polymarket_v4(
    payload: Any,
    *,
    received_ms: int,
    sequence_start: int,
) -> tuple[list[TapeEvent], int]:
    sequence = sequence_start
    if isinstance(payload, list):
        output: list[TapeEvent] = []
        for item in payload:
            events, sequence = normalize_polymarket_v4(
                item,
                received_ms=received_ms,
                sequence_start=sequence,
            )
            output.extend(events)
        return output, sequence
    if not isinstance(payload, dict):
        return [], sequence

    event_type = str(payload.get("event_type") or "")
    timestamp = _int(payload.get("timestamp"))
    if event_type == "book":
        asset_id = str(payload.get("asset_id") or "")
        if not asset_id:
            return [], sequence
        levels = _snapshot_levels(payload)
        if not levels:
            return [], sequence
        sequence += 1
        return [
            TapeEvent(
                schema_version=1,
                venue="POLYMARKET",
                asset_id=asset_id,
                kind="SNAPSHOT",
                source_timestamp_ms=timestamp,
                receive_timestamp_ms=received_ms,
                sequence=sequence,
                levels=levels,
            )
        ], sequence

    if event_type == "price_change":
        output = []
        changes = payload.get("price_changes")
        if not isinstance(changes, list):
            return output, sequence
        for change in changes:
            if not isinstance(change, dict):
                continue
            asset_id = str(change.get("asset_id") or "")
            side = str(change.get("side") or "").upper()
            level = _level(side, change) if side in {"BUY", "SELL"} else None
            if not asset_id or level is None:
                continue
            sequence += 1
            output.append(
                TapeEvent(
                    schema_version=1,
                    venue="POLYMARKET",
                    asset_id=asset_id,
                    kind="DELTA",
                    source_timestamp_ms=timestamp,
                    receive_timestamp_ms=received_ms,
                    sequence=sequence,
                    levels=(level,),
                )
            )
        return output, sequence

    if event_type == "last_trade_price":
        asset_id = str(payload.get("asset_id") or "")
        price = _float(payload.get("price"))
        size = _float(payload.get("size"))
        side = str(payload.get("side") or "").upper()
        if (
            not asset_id
            or price is None
            or size is None
            or not 0 < price < 1
            or size <= 0
        ):
            return [], sequence
        sequence += 1
        return [
            TapeEvent(
                schema_version=1,
                venue="POLYMARKET",
                asset_id=asset_id,
                kind="TRADE",
                source_timestamp_ms=timestamp,
                receive_timestamp_ms=received_ms,
                sequence=sequence,
                trade_price=price,
                trade_size=size,
                aggressor_side=side if side in {"BUY", "SELL"} else None,
            )
        ], sequence
    return [], sequence


async def capture_polymarket_l2(
    target: V3Target,
    *,
    duration_seconds: float = 15.0,
    max_messages: int = 240,
) -> V4Capture:
    if not 2 <= duration_seconds <= 60:
        raise ValueError("duration_seconds must be between 2 and 60")
    if max_messages <= 0:
        raise ValueError("max_messages must be positive")

    subscription = {
        "assets_ids": list(target.outcome_assets.values()),
        "type": "market",
        "custom_feature_enabled": True,
    }
    events: list[TapeEvent] = []
    raw_records: list[dict[str, Any]] = []
    errors: list[str] = []
    sequence = 0
    deadline = time.monotonic() + duration_seconds
    try:
        async with connect(POLYMARKET_MARKET_WS, open_timeout=8, close_timeout=2) as socket:
            await socket.send(json.dumps(subscription))
            messages = 0
            next_ping = time.monotonic() + 8.0
            while time.monotonic() < deadline and messages < max_messages:
                if time.monotonic() >= next_ping:
                    await socket.send("PING")
                    next_ping = time.monotonic() + 8.0
                remaining = max(deadline - time.monotonic(), 0.05)
                raw = await asyncio.wait_for(socket.recv(), timeout=min(remaining, 2.0))
                if raw == "PONG":
                    continue
                received = now_ms()
                payload = json.loads(raw)
                raw_records.append({"receive_timestamp_ms": received, "payload": payload})
                normalized, sequence = normalize_polymarket_v4(
                    payload,
                    received_ms=received,
                    sequence_start=sequence,
                )
                events.extend(normalized)
                messages += 1
    except TimeoutError:
        pass
    except Exception as exc:
        errors.append(f"POLYMARKET_V4:{type(exc).__name__}:{str(exc)[:160]}")

    return V4Capture(
        events=tuple(events),
        raw_records=tuple(raw_records),
        errors=tuple(errors),
        continuity="L2_AGGREGATE_NO_SERVER_SEQUENCE",
    )
