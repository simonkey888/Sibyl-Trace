from __future__ import annotations

import time
from typing import Any

import httpx

from app.market_identity_v4 import MarketContract, title_similarity
from app.venue_v3 import NormalizedBook, PriceLevel, VenueCapabilities

KALSHI_PUBLIC_BASE = "https://external-api.kalshi.com/trade-api/v2"


def _float(value: object) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed


def _levels(raw: object) -> list[tuple[float, float]]:
    if not isinstance(raw, list):
        return []
    output: list[tuple[float, float]] = []
    for item in raw:
        if not isinstance(item, list) or len(item) < 2:
            continue
        price = _float(item[0])
        size = _float(item[1])
        if price is None or size is None or not 0 < price < 1 or size <= 0:
            continue
        output.append((price, size))
    return output


def normalize_kalshi_orderbook(
    payload: dict[str, Any],
    *,
    ticker: str,
    receive_timestamp_ms: int | None = None,
) -> NormalizedBook:
    raw_book = payload.get("orderbook_fp") or payload.get("orderbook") or {}
    if not isinstance(raw_book, dict):
        raw_book = {}
    yes_bids = _levels(raw_book.get("yes_dollars") or raw_book.get("yes"))
    no_bids = _levels(raw_book.get("no_dollars") or raw_book.get("no"))

    bids = tuple(
        PriceLevel(price, size)
        for price, size in sorted(yes_bids, key=lambda level: level[0], reverse=True)
    )
    asks = tuple(
        PriceLevel(1.0 - no_price, size)
        for no_price, size in sorted(no_bids, key=lambda level: 1.0 - level[0])
    )
    book = NormalizedBook(
        venue="KALSHI",
        asset_id=ticker,
        bids=bids,
        asks=asks,
        source_timestamp_ms=None,
        receive_timestamp_ms=receive_timestamp_ms or time.time_ns() // 1_000_000,
    )
    if book.best_bid and book.best_ask and book.best_bid.price >= book.best_ask.price:
        raise ValueError("crossed or locked Kalshi YES book")
    return book


def normalize_kalshi_contract(market: dict[str, Any]) -> MarketContract:
    ticker = str(market.get("ticker") or "")
    title = str(market.get("title") or market.get("subtitle") or ticker)
    strike = market.get("functional_strike")
    if strike is None:
        floor = market.get("floor_strike")
        cap = market.get("cap_strike")
        strike = f"floor={floor};cap={cap}" if floor is not None or cap is not None else None
    rules = market.get("rules_primary") or market.get("rules_secondary")
    return MarketContract(
        venue="KALSHI",
        market_id=ticker,
        title=title,
        underlying=str(market.get("event_ticker") or "") or None,
        event=str(market.get("event_ticker") or "") or None,
        outcome=str(market.get("yes_sub_title") or title) or None,
        strike=str(strike) if strike is not None else None,
        cutoff_iso=str(market.get("close_time") or "") or None,
        timezone="UTC" if market.get("close_time") else None,
        resolution_source=str(market.get("settlement_source_url") or "") or None,
        resolution_rule=str(rules or "") or None,
        exceptions=tuple(
            value
            for value in (
                str(market.get("early_close_condition") or ""),
                "provisional" if market.get("is_provisional") is True else "",
            )
            if value
        ),
    )


def rank_btc_markets(markets: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    candidates: list[tuple[float, dict[str, Any]]] = []
    for market in markets:
        text = " ".join(
            str(market.get(key) or "")
            for key in ("title", "subtitle", "yes_sub_title", "event_ticker", "ticker")
        )
        normalized = text.casefold()
        if "bitcoin" not in normalized and "btc" not in normalized:
            continue
        candidates.append((title_similarity(query, text), market))
    candidates.sort(key=lambda item: item[0], reverse=True)
    return [market for _, market in candidates]


class KalshiReadOnlyVenue:
    capabilities = VenueCapabilities(
        public_books=True,
        public_trades=True,
        order_placement=False,
        private_account_data=False,
    )

    def __init__(self, client: httpx.Client | None = None):
        self.client = client or httpx.Client(base_url=KALSHI_PUBLIC_BASE, timeout=10.0)

    def close(self) -> None:
        self.client.close()

    def list_markets(self, *, status: str = "open", limit: int = 1000) -> list[dict[str, Any]]:
        response = self.client.get("/markets", params={"status": status, "limit": limit})
        response.raise_for_status()
        payload = response.json()
        markets = payload.get("markets") if isinstance(payload, dict) else None
        if not isinstance(markets, list):
            return []
        return [market for market in markets if isinstance(market, dict)]

    def get_market(self, ticker: str) -> dict[str, Any]:
        response = self.client.get(f"/markets/{ticker}")
        response.raise_for_status()
        payload = response.json()
        market = payload.get("market")
        if not isinstance(market, dict):
            raise ValueError("Kalshi market response missing market")
        return market

    def get_book(self, asset_id: str) -> NormalizedBook:
        response = self.client.get(f"/markets/{asset_id}/orderbook")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError("Kalshi orderbook response must be an object")
        return normalize_kalshi_orderbook(payload, ticker=asset_id)
