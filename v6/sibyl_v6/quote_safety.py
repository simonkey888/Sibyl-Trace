from __future__ import annotations

import math
from typing import Any

from .quote_math import TICK, norm_price


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def floor_buy_cap(value: float, tick: float = TICK) -> float | None:
    """Floor a maximum BUY price to the venue tick; never rounds upward."""
    if not _finite(value) or not _finite(tick) or tick <= 0:
        return None
    units = math.floor((float(value) + 1e-12) / float(tick))
    floored = units * float(tick)
    if floored < tick or floored > 1.0 - tick + 1e-12:
        return None
    return round(floored, 12)


def _asks(book: dict[str, Any] | None) -> list[tuple[float, float]]:
    if not isinstance(book, dict):
        return []
    out: list[tuple[float, float]] = []
    rows = book.get("asks") or []
    if not isinstance(rows, list):
        return out
    for row in rows:
        if isinstance(row, dict):
            raw_price = row.get("price")
            raw_size = row.get("size") or row.get("amount") or row.get("quantity")
        elif isinstance(row, (list, tuple)) and len(row) >= 2:
            raw_price, raw_size = row[0], row[1]
        else:
            continue
        try:
            price = norm_price(float(raw_price))
            size = float(raw_size)
        except (TypeError, ValueError):
            continue
        if math.isfinite(price) and math.isfinite(size) and 0 < price < 1 and size > 0:
            out.append((price, size))
    out.sort(key=lambda row: row[0])
    return out


def executable_buy_cost(book: dict[str, Any] | None, shares: float) -> dict[str, Any]:
    """Return actual ask-side VWAP needed to BUY ``shares`` of the hedge token."""
    if not _finite(shares) or float(shares) <= 0:
        return {
            "requested_shares": shares,
            "filled_shares": 0.0,
            "depth_sufficient": False,
            "vwap": None,
            "total_cost": None,
            "levels_consumed": 0,
        }
    remaining = float(shares)
    filled = 0.0
    total = 0.0
    consumed = 0
    for price, size in _asks(book):
        take = min(remaining, size)
        if take <= 0:
            continue
        total += take * price
        filled += take
        remaining -= take
        consumed += 1
        if remaining <= 1e-12:
            break
    sufficient = remaining <= 1e-12
    return {
        "requested_shares": shares,
        "filled_shares": round(filled, 12),
        "depth_sufficient": sufficient,
        "vwap": round(total / filled, 12) if sufficient and filled > 0 else None,
        "total_cost": round(total, 12) if sufficient else None,
        "levels_consumed": consumed,
    }


def _reason(reasons: list[str]) -> str:
    return "NONE" if not reasons else "|".join(dict.fromkeys(reasons))


def assess_buy_quote(
    *,
    side: str,
    raw_cap: float | None,
    upstream_price: float | None,
    hedge_book: dict[str, Any] | None,
    hedge_book_status: str,
    maker_book_status: str,
    hedge_token: str,
    quote_size: float,
    polymarket_taker_fee_bps: float | None,
    minimum_net_edge_bps: float,
    tick: float = TICK,
    limitless_maker_fee_bps: float | None = 0.0,
    fair_value_frame_complete: bool = True,
) -> dict[str, Any]:
    """Fail-closed Sibyl gate applied after pinned upstream quote math.

    A quote can be approved only when every market-data input used by the
    pinned upstream calculation is fresh. In particular, Limitless book
    competition is part of ``computeBuyPrices``; an unavailable/UNKNOWN venue
    timestamp therefore cannot be silently treated as fresh.

    The hedge is an actual executable BUY of the opposite Polymarket token:
      Limitless YES fill -> BUY Polymarket NO
      Limitless NO fill  -> BUY Polymarket YES
    """
    reasons: list[str] = []
    raw_cap_ok = _finite(raw_cap)
    upstream_ok = _finite(upstream_price)
    if not fair_value_frame_complete:
        reasons.append("FAIR_VALUE_FRAME_INCOMPLETE")
    if not raw_cap_ok:
        reasons.append("RAW_CAP_INVALID")
    if not upstream_ok:
        reasons.append("UPSTREAM_PRICE_INVALID")
    if maker_book_status != "FRESH":
        reasons.append("LIMITLESS_BOOK_NOT_FRESH")

    safe_quote = None
    if raw_cap_ok and float(raw_cap) < tick:
        reasons.append("RAW_CAP_BELOW_MIN_TICK")
    if raw_cap_ok and upstream_ok and float(raw_cap) >= tick:
        safe_quote = floor_buy_cap(min(float(upstream_price), float(raw_cap)), tick)
        if safe_quote is None:
            reasons.append("NO_VALID_FLOOR_TICK")

    upstream_cap_compliant = bool(
        raw_cap_ok and upstream_ok and float(upstream_price) <= float(raw_cap) + 1e-12
    )
    cap_compliant = bool(
        safe_quote is not None and raw_cap_ok and safe_quote <= float(raw_cap) + 1e-12
    )
    if safe_quote is not None and not cap_compliant:
        reasons.append("CAP_BREACH")

    tick_quoteable = bool(
        safe_quote is not None
        and safe_quote >= tick - 1e-12
        and safe_quote <= 1.0 - tick + 1e-12
        and abs((safe_quote / tick) - round(safe_quote / tick)) <= 1e-9
    )
    if safe_quote is not None and not tick_quoteable:
        reasons.append("INVALID_VENUE_TICK")

    hedge = executable_buy_cost(hedge_book, quote_size)
    if not hedge["depth_sufficient"]:
        reasons.append("INSUFFICIENT_HEDGE_DEPTH")
    if hedge_book_status != "FRESH":
        reasons.append("HEDGE_BOOK_NOT_FRESH")

    fee_known = (
        polymarket_taker_fee_bps is not None
        and _finite(polymarket_taker_fee_bps)
        and float(polymarket_taker_fee_bps) >= 0
        and limitless_maker_fee_bps is not None
        and _finite(limitless_maker_fee_bps)
        and float(limitless_maker_fee_bps) >= 0
    )
    if not fee_known:
        reasons.append("FEE_UNKNOWN")

    fees_per_share = None
    expected_net_edge = None
    min_edge = float(minimum_net_edge_bps) / 10_000.0
    if safe_quote is not None and hedge["vwap"] is not None and fee_known:
        fees_per_share = (
            float(polymarket_taker_fee_bps) + float(limitless_maker_fee_bps)
        ) / 10_000.0
        expected_net_edge = 1.0 - safe_quote - float(hedge["vwap"]) - fees_per_share
        if expected_net_edge + 1e-12 < min_edge:
            reasons.append("NET_EDGE_BELOW_MINIMUM")

    quoteable = bool(
        fair_value_frame_complete
        and maker_book_status == "FRESH"
        and cap_compliant
        and tick_quoteable
        and hedge["depth_sufficient"]
        and hedge_book_status == "FRESH"
        and fee_known
        and expected_net_edge is not None
        and expected_net_edge + 1e-12 >= min_edge
    )
    return {
        "SIDE": side,
        "RAW_CAP": raw_cap,
        "UPSTREAM_COMPUTED_PRICE": upstream_price,
        "UPSTREAM_CAP_COMPLIANT": upstream_cap_compliant,
        "SAFE_QUOTE_PRICE": safe_quote,
        "CAP_COMPLIANT": cap_compliant,
        "TICK_QUOTEABLE": tick_quoteable,
        "LIMITLESS_BOOK_STATUS": maker_book_status,
        "HEDGE_TOKEN": hedge_token,
        "HEDGE_BOOK_STATUS": hedge_book_status,
        "EXECUTABLE_HEDGE_COST": hedge["vwap"],
        "HEDGE_TOTAL_COST": hedge["total_cost"],
        "HEDGE_DEPTH_SUFFICIENT": hedge["depth_sufficient"],
        "HEDGE_LEVELS_CONSUMED": hedge["levels_consumed"],
        "QUOTE_SIZE_SHARES": quote_size,
        "FEES": {
            "LIMITLESS_MAKER_FEE_BPS": limitless_maker_fee_bps,
            "POLYMARKET_TAKER_FEE_BPS": polymarket_taker_fee_bps,
            "FEE_PER_SHARE_CONSERVATIVE": fees_per_share,
        },
        "MIN_EXPECTED_NET_EDGE": min_edge,
        "EXPECTED_NET_EDGE": expected_net_edge,
        "QUOTEABLE": quoteable,
        "REJECTION_REASON": _reason(reasons),
    }
