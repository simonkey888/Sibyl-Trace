from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict

from app.market_data_v3 import V3Event
from app.microstructure_v3 import QueuePrint, simulate_queue_fill


def stable_replay_order(events: Iterable[V3Event]) -> list[V3Event]:
    return sorted(
        events,
        key=lambda event: (
            event.receive_timestamp_ms,
            event.source_timestamp_ms or -1,
            event.source,
            event.event_type,
            event.asset_id or "",
            event.sequence or -1,
        ),
    )


def _queue_probe(events: list[V3Event], asset_id: str, side: str) -> dict | None:
    book = next(
        (
            event
            for event in events
            if event.source == "POLYMARKET"
            and event.event_type == "BOOK"
            and event.asset_id == asset_id
            and event.bid is not None
            and event.ask is not None
            and event.bid_size is not None
            and event.ask_size is not None
        ),
        None,
    )
    if book is None:
        return None
    if side == "BUY":
        order_price = book.bid
        queue_ahead = book.bid_size
        needed_aggressor = "SELL"
    else:
        order_price = book.ask
        queue_ahead = book.ask_size
        needed_aggressor = "BUY"
    if order_price is None or queue_ahead is None:
        return None
    prints = [
        QueuePrint(
            price=event.price,
            size=event.size,
            aggressor_side=needed_aggressor,
        )
        for event in events
        if event.receive_timestamp_ms > book.receive_timestamp_ms
        and event.source == "POLYMARKET"
        and event.event_type == "TRADE"
        and event.asset_id == asset_id
        and event.price is not None
        and event.size is not None
        and event.size > 0
        and event.aggressor_side == needed_aggressor
    ]
    result = simulate_queue_fill(
        side=side,  # type: ignore[arg-type]
        order_price=order_price,
        quantity=5.0,
        queue_ahead=queue_ahead,
        prints=prints,
    )
    return {
        "asset_id": asset_id,
        "side": side,
        "book_timestamp_ms": book.receive_timestamp_ms,
        "prints_after_book": len(prints),
        "result": asdict(result),
    }


def replay_capture(events: tuple[V3Event, ...]) -> dict:
    ordered = stable_replay_order(events)
    violations: list[str] = []
    if any(
        current.receive_timestamp_ms < previous.receive_timestamp_ms
        for previous, current in zip(ordered, ordered[1:], strict=False)
    ):
        violations.append("receive_timestamp_regression")

    poly_assets = sorted(
        {
            event.asset_id
            for event in ordered
            if event.source == "POLYMARKET" and event.asset_id
        }
    )
    probes = [
        probe
        for asset_id in poly_assets
        for side in ("BUY", "SELL")
        if (probe := _queue_probe(ordered, asset_id, side)) is not None
    ]
    filled = sum(1 for probe in probes if probe["result"]["status"] == "FILLED")
    partial = sum(1 for probe in probes if probe["result"]["status"] == "PARTIAL")
    return {
        "status": "REPLAYED" if ordered else "NO_DATA",
        "event_count": len(ordered),
        "book_events": sum(
            event.source == "POLYMARKET" and event.event_type == "BOOK"
            for event in ordered
        ),
        "trade_events": sum(event.event_type == "TRADE" for event in ordered),
        "queue_probes": len(probes),
        "queue_filled": filled,
        "queue_partial": partial,
        "invariant_violations": violations,
        "no_lookahead_rule": "queue probes consume only prints received after the seed book",
        "probes": probes,
    }
