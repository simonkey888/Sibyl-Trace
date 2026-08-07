from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from app.venue_v3 import NormalizedBook

Side = Literal["BUY", "SELL"]


@dataclass(frozen=True)
class BookMetrics:
    best_bid: float
    best_ask: float
    bid_size: float
    ask_size: float
    spread: float
    midpoint: float
    imbalance: float
    microprice: float


@dataclass(frozen=True)
class QueuePrint:
    price: float
    size: float
    aggressor_side: Side


@dataclass(frozen=True)
class QueueFillResult:
    status: str
    requested: float
    filled: float
    remaining: float
    initial_queue_ahead: float
    residual_queue_ahead: float
    matched_volume: float
    reason: str


@dataclass(frozen=True)
class L1TakeResult:
    status: str
    requested: float
    filled: float
    unfilled: float
    average_price: float | None
    reason: str


@dataclass(frozen=True)
class LatencyBudget:
    source_to_receive_ms: int | None
    decision_delay_ms: int
    synthetic_order_delay_ms: int
    total_observed_to_order_ms: int | None


def book_metrics(book: NormalizedBook) -> BookMetrics:
    bid = book.best_bid
    ask = book.best_ask
    if bid is None or ask is None:
        raise ValueError("two-sided book required")
    if bid.price >= ask.price:
        raise ValueError("book is crossed or locked")
    total = bid.size + ask.size
    if total <= 0:
        raise ValueError("top-of-book depth must be positive")
    midpoint = (bid.price + ask.price) / 2.0
    imbalance = bid.size / total
    microprice = (ask.price * bid.size + bid.price * ask.size) / total
    return BookMetrics(
        best_bid=bid.price,
        best_ask=ask.price,
        bid_size=bid.size,
        ask_size=ask.size,
        spread=ask.price - bid.price,
        midpoint=midpoint,
        imbalance=imbalance,
        microprice=microprice,
    )


def signed_flow_ewma(values: Iterable[float], *, alpha: float = 0.25) -> float:
    if not 0 < alpha <= 1:
        raise ValueError("alpha must be in (0, 1]")
    state: float | None = None
    for raw in values:
        value = float(raw)
        state = value if state is None else alpha * value + (1.0 - alpha) * state
    return state or 0.0


def signed_markout(entry_price: float, later_midpoint: float, aggressor_side: Side) -> float:
    if not 0 < entry_price < 1 or not 0 < later_midpoint < 1:
        raise ValueError("prediction-market prices must be in (0, 1)")
    direction = 1.0 if aggressor_side == "BUY" else -1.0
    return direction * (later_midpoint - entry_price)


def adverse_selection_toxicity(
    observations: Iterable[tuple[float, float, Side]],
) -> float:
    adverse = [
        max(0.0, signed_markout(entry, later, side))
        for entry, later, side in observations
    ]
    return sum(adverse) / len(adverse) if adverse else 0.0


def simulate_queue_fill(
    *,
    side: Side,
    order_price: float,
    quantity: float,
    queue_ahead: float,
    prints: Iterable[QueuePrint],
    cancelled_ahead: float = 0.0,
) -> QueueFillResult:
    if not 0 < order_price < 1 or quantity <= 0 or queue_ahead < 0 or cancelled_ahead < 0:
        return QueueFillResult(
            status="INVALID",
            requested=max(quantity, 0.0),
            filled=0.0,
            remaining=max(quantity, 0.0),
            initial_queue_ahead=max(queue_ahead, 0.0),
            residual_queue_ahead=max(queue_ahead, 0.0),
            matched_volume=0.0,
            reason="invalid_inputs",
        )
    ahead = max(queue_ahead - cancelled_ahead, 0.0)
    initial_ahead = ahead
    filled = 0.0
    matched = 0.0
    for trade in prints:
        if trade.size <= 0:
            continue
        executable = (
            side == "BUY"
            and trade.aggressor_side == "SELL"
            and trade.price <= order_price
        ) or (
            side == "SELL"
            and trade.aggressor_side == "BUY"
            and trade.price >= order_price
        )
        if not executable:
            continue
        volume = trade.size
        matched += volume
        consumed_ahead = min(ahead, volume)
        ahead -= consumed_ahead
        volume -= consumed_ahead
        if volume <= 0:
            continue
        own_fill = min(quantity - filled, volume)
        filled += own_fill
        if filled >= quantity:
            break
    remaining = max(quantity - filled, 0.0)
    if filled >= quantity:
        status, reason = "FILLED", "queue_consumed_and_requested_quantity_matched"
    elif filled > 0:
        status, reason = "PARTIAL", "some_volume_reached_our_queue_position"
    else:
        status, reason = "NOT_FILLED", "queue_ahead_not_consumed_or_no_matching_prints"
    return QueueFillResult(
        status=status,
        requested=quantity,
        filled=filled,
        remaining=remaining,
        initial_queue_ahead=initial_ahead,
        residual_queue_ahead=ahead,
        matched_volume=matched,
        reason=reason,
    )


def simulate_l1_take(*, side: Side, quantity: float, book: NormalizedBook) -> L1TakeResult:
    if quantity <= 0:
        return L1TakeResult(
            "INVALID",
            max(quantity, 0.0),
            0.0,
            max(quantity, 0.0),
            None,
            "invalid_quantity",
        )
    level = book.best_ask if side == "BUY" else book.best_bid
    if level is None:
        return L1TakeResult(
            "NOT_FILLED", quantity, 0.0, quantity, None, "missing_top_of_book"
        )
    filled = min(quantity, level.size)
    unfilled = quantity - filled
    return L1TakeResult(
        status="FILLED" if unfilled == 0 else "PARTIAL",
        requested=quantity,
        filled=filled,
        unfilled=unfilled,
        average_price=level.price,
        reason="l1_depth_only_conservative_cap",
    )


def latency_budget(
    *,
    source_timestamp_ms: int | None,
    receive_timestamp_ms: int,
    decision_delay_ms: int,
    synthetic_order_delay_ms: int,
) -> LatencyBudget:
    if decision_delay_ms < 0 or synthetic_order_delay_ms < 0:
        raise ValueError("latency components cannot be negative")
    source_to_receive = (
        max(receive_timestamp_ms - source_timestamp_ms, 0)
        if source_timestamp_ms is not None
        else None
    )
    total = (
        source_to_receive + decision_delay_ms + synthetic_order_delay_ms
        if source_to_receive is not None
        else None
    )
    return LatencyBudget(
        source_to_receive_ms=source_to_receive,
        decision_delay_ms=decision_delay_ms,
        synthetic_order_delay_ms=synthetic_order_delay_ms,
        total_observed_to_order_ms=total,
    )
