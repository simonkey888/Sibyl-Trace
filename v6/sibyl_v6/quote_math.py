from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

TICK = 0.01


@dataclass(frozen=True)
class BookTop:
    bid: float | None
    ask: float | None


def norm_price(value: float) -> float:
    return value / 100.0 if value > 1 else value


def clip_price(value: float) -> float:
    # JS Math.round for positive prediction-market prices: round half upward.
    rounded = math.floor(value * 100.0 + 0.5) / 100.0
    return max(TICK, min(1.0 - TICK, rounded))


def book_top(book: dict[str, Any] | None) -> BookTop:
    if not book:
        return BookTop(None, None)

    def prices(side: str) -> list[float]:
        out: list[float] = []
        rows = book.get(side) or []
        if not isinstance(rows, list):
            return out
        for row in rows:
            raw = row.get("price") if isinstance(row, dict) else row[0] if isinstance(row, (list, tuple)) and row else None
            try:
                price = norm_price(float(raw))
            except (TypeError, ValueError):
                continue
            if math.isfinite(price) and 0 < price < 1:
                out.append(price)
        return out

    bids = prices("bids")
    asks = prices("asks")
    return BookTop(max(bids) if bids else None, min(asks) if asks else None)


def compute_buy_prices(
    poly_bid: float,
    poly_ask: float,
    margin_bps: int,
    yes_book: BookTop | None = None,
) -> dict[str, float]:
    """Literal PAPER mirror of pinned upstream computeBuyPrices at e35ad881.

    Both Limitless sides are BUY. The Limitless YES book can only lower the
    fair-value caps so postOnly quotes do not cross the resting book.
    """
    margin = margin_bps / 10_000.0
    fair_yes = poly_bid - margin
    fair_no = 1.0 - poly_ask - margin
    yes = fair_yes
    no = fair_no
    if yes_book is not None and yes_book.ask is not None:
        yes = min(fair_yes, yes_book.ask - TICK)
    if yes_book is not None and yes_book.bid is not None:
        no = min(fair_no, 1.0 - yes_book.bid - TICK)
    return {"yes": clip_price(yes), "no": clip_price(no)}
