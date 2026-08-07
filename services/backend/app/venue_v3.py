from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from app.polymarket import PolymarketClient


@dataclass(frozen=True)
class VenueCapabilities:
    public_books: bool = True
    public_trades: bool = True
    order_placement: bool = False
    private_account_data: bool = False


@dataclass(frozen=True)
class PriceLevel:
    price: float
    size: float


@dataclass(frozen=True)
class NormalizedBook:
    venue: str
    asset_id: str
    bids: tuple[PriceLevel, ...]
    asks: tuple[PriceLevel, ...]
    source_timestamp_ms: int | None
    receive_timestamp_ms: int

    @property
    def best_bid(self) -> PriceLevel | None:
        return self.bids[0] if self.bids else None

    @property
    def best_ask(self) -> PriceLevel | None:
        return self.asks[0] if self.asks else None


@dataclass(frozen=True)
class NormalizedTrade:
    venue: str
    asset_id: str
    price: float
    size: float
    aggressor_side: str | None
    source_timestamp_ms: int | None
    receive_timestamp_ms: int


class ReadOnlyVenueAdapter(Protocol):
    capabilities: VenueCapabilities

    def get_book(self, asset_id: str) -> NormalizedBook: ...


def _float(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed


def _timestamp_ms(value: object) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    if parsed <= 0:
        return None
    return parsed


def _levels(raw: object, *, reverse: bool) -> tuple[PriceLevel, ...]:
    if not isinstance(raw, list):
        return ()
    levels: list[PriceLevel] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        price = _float(item.get("price"))
        size = _float(item.get("size"))
        if price is None or size is None or not 0 < price < 1 or size <= 0:
            continue
        levels.append(PriceLevel(price=price, size=size))
    levels.sort(key=lambda level: level.price, reverse=reverse)
    return tuple(levels)


def normalize_polymarket_book(
    payload: dict,
    *,
    asset_id: str,
    receive_timestamp_ms: int | None = None,
) -> NormalizedBook:
    bids = _levels(payload.get("bids"), reverse=True)
    asks = _levels(payload.get("asks"), reverse=False)
    book = NormalizedBook(
        venue="POLYMARKET",
        asset_id=asset_id,
        bids=bids,
        asks=asks,
        source_timestamp_ms=_timestamp_ms(payload.get("timestamp")),
        receive_timestamp_ms=receive_timestamp_ms or time.time_ns() // 1_000_000,
    )
    if book.best_bid and book.best_ask and book.best_bid.price >= book.best_ask.price:
        raise ValueError("crossed or locked Polymarket book")
    return book


class PolymarketReadOnlyVenue:
    capabilities = VenueCapabilities()

    def __init__(self, client: PolymarketClient):
        self.client = client

    def get_book(self, asset_id: str) -> NormalizedBook:
        payload = self.client.order_book(asset_id)
        return normalize_polymarket_book(payload, asset_id=asset_id)
