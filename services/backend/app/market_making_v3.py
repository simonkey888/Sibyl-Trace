from __future__ import annotations

from dataclasses import asdict, dataclass
from math import ceil, floor

from app.microstructure_v3 import book_metrics
from app.venue_v3 import NormalizedBook


@dataclass(frozen=True)
class MakerConfig:
    base_half_spread: float = 0.01
    inventory_skew: float = 0.02
    base_size: float = 5.0
    hard_inventory: float = 25.0
    trend_flow_threshold: float = 3.0
    trend_volatility_threshold: float = 0.004
    event_toxicity_threshold: float = 0.015
    event_volatility_threshold: float = 0.012
    halt_before_expiry_ms: int = 30_000
    reduce_before_expiry_ms: int = 180_000
    stale_after_ms: int = 5_000


@dataclass(frozen=True)
class MakerDecision:
    regime: str
    fair_value: float | None
    reservation_price: float | None
    bid_price: float | None
    ask_price: float | None
    bid_size: float
    ask_size: float
    spread: float | None
    inventory_fraction: float
    reason_codes: tuple[str, ...]


def _floor_tick(value: float, tick: float) -> float:
    return floor((value + 1e-12) / tick) * tick


def _ceil_tick(value: float, tick: float) -> float:
    return ceil((value - 1e-12) / tick) * tick


def decide_quotes(
    *,
    book: NormalizedBook,
    tick_size: float,
    now_ms: int,
    end_timestamp_ms: int,
    inventory: float = 0.0,
    volatility: float = 0.0,
    toxicity: float = 0.0,
    signed_flow_ewma: float = 0.0,
    config: MakerConfig | None = None,
) -> MakerDecision:
    config = config or MakerConfig()
    reasons: list[str] = []
    inventory_fraction = (
        max(-1.0, min(1.0, inventory / config.hard_inventory))
        if config.hard_inventory > 0
        else 0.0
    )

    if tick_size <= 0 or tick_size >= 0.5:
        return MakerDecision(
            "HALTED",
            None,
            None,
            None,
            None,
            0.0,
            0.0,
            None,
            inventory_fraction,
            ("invalid_tick",),
        )

    age_ms = max(now_ms - book.receive_timestamp_ms, 0)
    remaining_ms = end_timestamp_ms - now_ms if end_timestamp_ms > 0 else 10**12
    try:
        metrics = book_metrics(book)
    except ValueError:
        return MakerDecision(
            "HALTED",
            None,
            None,
            None,
            None,
            0.0,
            0.0,
            None,
            inventory_fraction,
            ("invalid_book",),
        )

    if age_ms > config.stale_after_ms:
        reasons.append("stale_book")
    if remaining_ms <= config.halt_before_expiry_ms:
        reasons.append("expiry_imminent")
    if reasons:
        return MakerDecision(
            "HALTED",
            metrics.microprice,
            None,
            None,
            None,
            0.0,
            0.0,
            None,
            inventory_fraction,
            tuple(reasons),
        )

    if toxicity >= config.event_toxicity_threshold:
        reasons.append("toxicity_event")
    if volatility >= config.event_volatility_threshold:
        reasons.append("volatility_event")
    if reasons:
        return MakerDecision(
            "EVENT",
            metrics.microprice,
            metrics.microprice,
            None,
            None,
            0.0,
            0.0,
            None,
            inventory_fraction,
            tuple(reasons),
        )

    reduce_only = (
        abs(inventory) >= config.hard_inventory
        or remaining_ms <= config.reduce_before_expiry_ms
    )
    trending = (
        abs(signed_flow_ewma) >= config.trend_flow_threshold
        or volatility >= config.trend_volatility_threshold
    )
    if reduce_only:
        regime = "REDUCE_ONLY"
        reasons.append("inventory_or_expiry_reduce_only")
    elif trending:
        regime = "TRENDING"
        reasons.append("flow_or_volatility_trending")
    else:
        regime = "QUIET"
        reasons.append("normal_two_sided_quote")

    fair_value = metrics.microprice
    reservation = fair_value - inventory_fraction * config.inventory_skew
    half_spread = config.base_half_spread + min(volatility, 0.05) + min(toxicity, 0.05)
    if regime == "TRENDING":
        half_spread *= 1.5

    lower = tick_size
    upper = 1.0 - tick_size
    raw_bid = max(lower, min(upper, reservation - half_spread))
    raw_ask = max(lower, min(upper, reservation + half_spread))
    bid = _floor_tick(raw_bid, tick_size)
    ask = _ceil_tick(raw_ask, tick_size)
    bid = max(lower, min(upper, bid))
    ask = max(lower, min(upper, ask))

    if bid >= ask:
        return MakerDecision(
            "HALTED",
            fair_value,
            reservation,
            None,
            None,
            0.0,
            0.0,
            None,
            inventory_fraction,
            ("quote_cross_after_rounding",),
        )

    size_scale = max(0.2, 1.0 - abs(inventory_fraction))
    if regime == "TRENDING":
        size_scale *= 0.5
    size = config.base_size * size_scale
    bid_size = size
    ask_size = size
    if regime == "REDUCE_ONLY":
        if inventory > 0:
            bid_size = 0.0
        elif inventory < 0:
            ask_size = 0.0
        else:
            bid_size = 0.0
            ask_size = 0.0

    return MakerDecision(
        regime=regime,
        fair_value=fair_value,
        reservation_price=reservation,
        bid_price=bid if bid_size > 0 else None,
        ask_price=ask if ask_size > 0 else None,
        bid_size=bid_size,
        ask_size=ask_size,
        spread=(ask - bid) if bid_size > 0 and ask_size > 0 else None,
        inventory_fraction=inventory_fraction,
        reason_codes=tuple(reasons),
    )


def decision_payload(decision: MakerDecision) -> dict:
    return asdict(decision)
