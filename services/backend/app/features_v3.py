from __future__ import annotations

from collections.abc import Iterable
from math import log
from statistics import pstdev

from app.market_data_v3 import V3Event


def _price_events(events: Iterable[V3Event], source: str) -> list[V3Event]:
    return [
        event
        for event in events
        if event.source == source and event.price is not None and event.price > 0
    ]


def _price_at_or_before(events: list[V3Event], timestamp_ms: int) -> float | None:
    result: float | None = None
    for event in events:
        if event.receive_timestamp_ms > timestamp_ms:
            break
        if event.price is not None:
            result = event.price
    return result


def _returns_bps(events: list[V3Event], horizon_ms: int) -> float | None:
    if len(events) < 2:
        return None
    last = events[-1]
    if last.price is None:
        return None
    prior = _price_at_or_before(events, last.receive_timestamp_ms - horizon_ms)
    if prior is None or prior <= 0:
        return None
    return (last.price / prior - 1.0) * 10_000.0


def _realized_vol_bps(events: list[V3Event]) -> float | None:
    prices = [event.price for event in events if event.price is not None and event.price > 0]
    if len(prices) < 3:
        return None
    log_returns = [
        log(current / previous)
        for previous, current in zip(prices, prices[1:], strict=False)
    ]
    return pstdev(log_returns) * 10_000.0 if len(log_returns) >= 2 else None


def _signed_volume(events: Iterable[V3Event]) -> float:
    total = 0.0
    for event in events:
        if event.size is None or event.size <= 0 or event.aggressor_side not in {"BUY", "SELL"}:
            continue
        total += event.size if event.aggressor_side == "BUY" else -event.size
    return total


def _latest_poly_books(events: Iterable[V3Event]) -> dict[str, dict[str, float | None]]:
    latest: dict[str, V3Event] = {}
    histories: dict[str, list[float]] = {}
    for event in events:
        if (
            event.source != "POLYMARKET"
            or event.event_type != "BOOK"
            or not event.asset_id
            or event.bid is None
            or event.ask is None
            or event.bid >= event.ask
        ):
            continue
        latest[event.asset_id] = event
        histories.setdefault(event.asset_id, []).append((event.bid + event.ask) / 2.0)
    output: dict[str, dict[str, float | None]] = {}
    for asset_id, event in latest.items():
        bid_size = event.bid_size
        ask_size = event.ask_size
        total = (bid_size or 0.0) + (ask_size or 0.0)
        imbalance = bid_size / total if bid_size is not None and total > 0 else None
        microprice = (
            (event.ask * bid_size + event.bid * ask_size) / total
            if bid_size is not None and ask_size is not None and total > 0
            else None
        )
        mids = histories.get(asset_id, [])
        log_returns = [
            log(current / previous)
            for previous, current in zip(mids, mids[1:], strict=False)
            if previous > 0 and current > 0
        ]
        volatility = pstdev(log_returns) if len(log_returns) >= 2 else None
        output[asset_id] = {
            "best_bid": event.bid,
            "best_ask": event.ask,
            "bid_size": bid_size,
            "ask_size": ask_size,
            "spread": event.ask - event.bid,
            "midpoint": (event.bid + event.ask) / 2.0,
            "imbalance": imbalance,
            "microprice": microprice,
            "volatility": volatility,
            "receive_timestamp_ms": float(event.receive_timestamp_ms),
        }
    return output


def build_cross_market_features(events: tuple[V3Event, ...]) -> dict:
    sources = ("BINANCE", "COINBASE", "BINANCE_FUTURES")
    source_features: dict[str, dict] = {}
    for source in sources:
        series = _price_events(events, source)
        source_features[source] = {
            "events": len(series),
            "latest_price": series[-1].price if series else None,
            "return_1s_bps": _returns_bps(series, 1_000),
            "return_5s_bps": _returns_bps(series, 5_000),
            "return_10s_bps": _returns_bps(series, 10_000),
            "realized_vol_bps": _realized_vol_bps(series),
            "signed_volume": _signed_volume(series),
        }

    consensus_values = [
        feature["return_1s_bps"]
        for feature in source_features.values()
        if feature["return_1s_bps"] is not None
    ]
    consensus_1s = (
        sum(float(value) for value in consensus_values) / len(consensus_values)
        if consensus_values
        else None
    )
    signs = {1 if value > 0 else -1 if value < 0 else 0 for value in consensus_values}
    consensus_aligned = bool(consensus_values) and len(signs - {0}) <= 1

    return {
        "status": "CAPTURED" if events else "NO_DATA",
        "sources": source_features,
        "consensus_return_1s_bps": consensus_1s,
        "consensus_direction_aligned": consensus_aligned,
        "polymarket_books": _latest_poly_books(events),
    }
