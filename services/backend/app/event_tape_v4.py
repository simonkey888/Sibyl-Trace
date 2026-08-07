from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.venue_v3 import NormalizedBook, PriceLevel

TapeKind = Literal["SNAPSHOT", "DELTA", "TRADE"]
BookSide = Literal["BID", "ASK"]


@dataclass(frozen=True)
class TapeLevel:
    side: BookSide
    price: float
    size: float


@dataclass(frozen=True)
class TapeEvent:
    schema_version: int
    venue: str
    asset_id: str
    kind: TapeKind
    receive_timestamp_ms: int
    source_timestamp_ms: int | None = None
    sequence: int | None = None
    levels: tuple[TapeLevel, ...] = ()
    trade_price: float | None = None
    trade_size: float | None = None
    aggressor_side: str | None = None


@dataclass(frozen=True)
class ReconstructionResult:
    status: str
    book: NormalizedBook | None
    applied_events: int
    gaps: tuple[str, ...]


def snapshot_event(book: NormalizedBook, *, sequence: int | None = None) -> TapeEvent:
    levels = tuple(
        [TapeLevel("BID", level.price, level.size) for level in book.bids]
        + [TapeLevel("ASK", level.price, level.size) for level in book.asks]
    )
    return TapeEvent(
        schema_version=1,
        venue=book.venue,
        asset_id=book.asset_id,
        kind="SNAPSHOT",
        source_timestamp_ms=book.source_timestamp_ms,
        receive_timestamp_ms=book.receive_timestamp_ms,
        sequence=sequence,
        levels=levels,
    )


def validate_event(event: TapeEvent) -> None:
    if event.schema_version != 1:
        raise ValueError("unsupported tape schema")
    if not event.venue or not event.asset_id or event.receive_timestamp_ms <= 0:
        raise ValueError("invalid tape identity")
    if event.kind in {"SNAPSHOT", "DELTA"}:
        for level in event.levels:
            if not 0 < level.price < 1 or level.size < 0:
                raise ValueError("invalid book level")
    elif event.kind == "TRADE":
        if event.trade_price is None or event.trade_size is None:
            raise ValueError("trade event requires price and size")
        if not 0 < event.trade_price < 1 or event.trade_size <= 0:
            raise ValueError("invalid trade")


def stable_tape_order(events: tuple[TapeEvent, ...]) -> tuple[TapeEvent, ...]:
    for event in events:
        validate_event(event)
    return tuple(
        sorted(
            events,
            key=lambda event: (
                event.receive_timestamp_ms,
                event.source_timestamp_ms or -1,
                event.sequence or -1,
                event.kind,
            ),
        )
    )


def _materialize(
    venue: str,
    asset_id: str,
    bids: dict[float, float],
    asks: dict[float, float],
    source_timestamp_ms: int | None,
    receive_timestamp_ms: int,
) -> NormalizedBook:
    bid_levels = tuple(
        PriceLevel(price, size)
        for price, size in sorted(bids.items(), reverse=True)
        if size > 0
    )
    ask_levels = tuple(
        PriceLevel(price, size) for price, size in sorted(asks.items()) if size > 0
    )
    if bid_levels and ask_levels and bid_levels[0].price >= ask_levels[0].price:
        raise ValueError("reconstructed book is crossed or locked")
    return NormalizedBook(
        venue=venue,
        asset_id=asset_id,
        bids=bid_levels,
        asks=ask_levels,
        source_timestamp_ms=source_timestamp_ms,
        receive_timestamp_ms=receive_timestamp_ms,
    )


def reconstruct_l2(events: tuple[TapeEvent, ...]) -> ReconstructionResult:
    ordered = stable_tape_order(events)
    if not ordered:
        return ReconstructionResult("NO_DATA", None, 0, ())
    venue = ordered[0].venue
    asset_id = ordered[0].asset_id
    if any(event.venue != venue or event.asset_id != asset_id for event in ordered):
        raise ValueError("one reconstruction may contain only one venue/asset")

    bids: dict[float, float] = {}
    asks: dict[float, float] = {}
    gaps: list[str] = []
    applied = 0
    seeded = False
    last_sequence: int | None = None
    source_ts: int | None = None
    receive_ts = ordered[0].receive_timestamp_ms

    for event in ordered:
        receive_ts = event.receive_timestamp_ms
        source_ts = event.source_timestamp_ms or source_ts
        if event.sequence is not None:
            if last_sequence is not None:
                if event.sequence <= last_sequence:
                    gaps.append(f"non_monotonic_sequence:{last_sequence}->{event.sequence}")
                elif event.sequence > last_sequence + 1:
                    gaps.append(f"sequence_gap:{last_sequence}->{event.sequence}")
            last_sequence = event.sequence

        if event.kind == "TRADE":
            continue
        if event.kind == "SNAPSHOT":
            bids.clear()
            asks.clear()
            seeded = True
        elif not seeded:
            gaps.append("delta_before_snapshot")
            continue

        for level in event.levels:
            target = bids if level.side == "BID" else asks
            if level.size == 0:
                target.pop(level.price, None)
            else:
                target[level.price] = level.size
        applied += 1

    if not seeded:
        return ReconstructionResult("UNSEEDED", None, applied, tuple(gaps))
    book = _materialize(venue, asset_id, bids, asks, source_ts, receive_ts)
    status = "DEGRADED" if gaps else "RECONSTRUCTED"
    return ReconstructionResult(status, book, applied, tuple(gaps))


def queue_ahead_at_price(book: NormalizedBook, *, side: BookSide, price: float) -> float | None:
    levels = book.bids if side == "BID" else book.asks
    for level in levels:
        if abs(level.price - price) < 1e-12:
            return level.size
    return None
