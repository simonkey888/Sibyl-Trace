from __future__ import annotations

from collections.abc import Iterable
from math import log
from statistics import pstdev

from app.market_data_v3 import V3Event

HORIZONS_MS = {
    "1s": 1_000,
    "5s": 5_000,
    "10s": 10_000,
    "1m": 60_000,
    "5m": 300_000,
    "10m": 600_000,
    "30m": 1_800_000,
}


def _series(events: Iterable[V3Event], source: str) -> list[V3Event]:
    return sorted(
        [
            event
            for event in events
            if event.source == source and event.price is not None and event.price > 0
        ],
        key=lambda event: event.receive_timestamp_ms,
    )


def _price_at_or_before(events: list[V3Event], timestamp_ms: int) -> float | None:
    value: float | None = None
    for event in events:
        if event.receive_timestamp_ms > timestamp_ms:
            break
        value = event.price
    return value


def horizon_returns(events: list[V3Event]) -> dict[str, float | None]:
    if not events or events[-1].price is None:
        return {name: None for name in HORIZONS_MS}
    last = events[-1]
    output: dict[str, float | None] = {}
    for name, horizon_ms in HORIZONS_MS.items():
        prior = _price_at_or_before(events, last.receive_timestamp_ms - horizon_ms)
        output[name] = (
            (last.price / prior - 1.0) * 10_000.0
            if prior is not None and prior > 0
            else None
        )
    return output


def signed_volume(events: Iterable[V3Event]) -> float:
    total = 0.0
    for event in events:
        if event.size is None or event.size <= 0:
            continue
        if event.aggressor_side == "BUY":
            total += event.size
        elif event.aggressor_side == "SELL":
            total -= event.size
    return total


def cvd_velocity_acceleration(
    events: list[V3Event], *, window_ms: int = 60_000
) -> dict[str, float]:
    if not events:
        return {"velocity": 0.0, "acceleration": 0.0}
    end = events[-1].receive_timestamp_ms
    start = end - window_ms
    mid = start + window_ms // 2
    first = [event for event in events if start <= event.receive_timestamp_ms < mid]
    second = [event for event in events if mid <= event.receive_timestamp_ms <= end]
    half_seconds = max(window_ms / 2_000.0, 1e-9)
    first_velocity = signed_volume(first) / half_seconds
    second_velocity = signed_volume(second) / half_seconds
    return {
        "velocity": second_velocity,
        "acceleration": (second_velocity - first_velocity) / half_seconds,
    }


def realized_volatility_bps(events: list[V3Event], *, window_ms: int = 60_000) -> float | None:
    if len(events) < 3:
        return None
    end = events[-1].receive_timestamp_ms
    prices = [
        event.price
        for event in events
        if event.receive_timestamp_ms >= end - window_ms
        and event.price is not None
        and event.price > 0
    ]
    if len(prices) < 3:
        return None
    returns = [
        log(current / previous)
        for previous, current in zip(prices, prices[1:], strict=False)
    ]
    return pstdev(returns) * 10_000.0 if len(returns) >= 2 else None


def latest_basis_bps(spot: list[V3Event], futures: list[V3Event]) -> float | None:
    if not spot or not futures or spot[-1].price is None or futures[-1].price is None:
        return None
    if spot[-1].price <= 0:
        return None
    return (futures[-1].price / spot[-1].price - 1.0) * 10_000.0


def latest_divergence_bps(left: list[V3Event], right: list[V3Event]) -> float | None:
    if not left or not right or left[-1].price is None or right[-1].price is None:
        return None
    midpoint = (left[-1].price + right[-1].price) / 2.0
    if midpoint <= 0:
        return None
    return (left[-1].price - right[-1].price) / midpoint * 10_000.0


def response_lag_ms(
    driver: list[V3Event],
    responder: list[V3Event],
    *,
    driver_move_bps: float = 5.0,
    responder_move_bps: float = 5.0,
) -> int | None:
    if len(driver) < 2 or len(responder) < 2:
        return None
    base_driver = driver[0].price
    base_responder = responder[0].price
    if not base_driver or not base_responder:
        return None
    driver_ts = next(
        (
            event.receive_timestamp_ms
            for event in driver[1:]
            if event.price is not None
            and abs(event.price / base_driver - 1.0) * 10_000.0 >= driver_move_bps
        ),
        None,
    )
    if driver_ts is None:
        return None
    responder_ts = next(
        (
            event.receive_timestamp_ms
            for event in responder[1:]
            if event.receive_timestamp_ms >= driver_ts
            and event.price is not None
            and abs(event.price / base_responder - 1.0) * 10_000.0 >= responder_move_bps
        ),
        None,
    )
    return responder_ts - driver_ts if responder_ts is not None else None


def build_temporal_features(events: tuple[V3Event, ...]) -> dict:
    binance = _series(events, "BINANCE")
    coinbase = _series(events, "COINBASE")
    futures = _series(events, "BINANCE_FUTURES")
    sources = {
        "BINANCE": binance,
        "COINBASE": coinbase,
        "BINANCE_FUTURES": futures,
    }
    return {
        "status": "CAPTURED" if events else "NO_DATA",
        "sources": {
            source: {
                "events": len(series),
                "returns_bps": horizon_returns(series),
                "realized_vol_60s_bps": realized_volatility_bps(series),
                "signed_volume": signed_volume(series),
                "cvd": cvd_velocity_acceleration(series),
            }
            for source, series in sources.items()
        },
        "spot_futures_basis_bps": latest_basis_bps(binance, futures),
        "binance_coinbase_divergence_bps": latest_divergence_bps(binance, coinbase),
        "binance_to_coinbase_response_lag_ms": response_lag_ms(binance, coinbase),
        "horizons": tuple(HORIZONS_MS),
    }
