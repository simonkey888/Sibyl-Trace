from __future__ import annotations

import json
import time
import urllib.parse
from typing import Any

from .feeds import _get_json
from .limitless_ws import (
    classify_ws_snapshot,
    desired_subscription_slugs,
    fetch_limitless_ws_snapshot,
)
from .poly_fee import PolyFeeDetails, parse_clob_fee_details
from .quote_math import book_top


def _poly_tokens_from_gamma(market: dict[str, Any]) -> dict[str, str]:
    raw_outcomes = market.get("outcomes")
    raw_tokens = market.get("clobTokenIds")
    if isinstance(raw_outcomes, str):
        raw_outcomes = json.loads(raw_outcomes)
    if isinstance(raw_tokens, str):
        raw_tokens = json.loads(raw_tokens)
    if (
        not isinstance(raw_outcomes, list)
        or not isinstance(raw_tokens, list)
        or len(raw_outcomes) != len(raw_tokens)
    ):
        raise RuntimeError("POLYMARKET_TOKEN_MAPPING_INVALID")
    out: dict[str, str] = {}
    for outcome, token in zip(raw_outcomes, raw_tokens):
        label = str(outcome).strip().casefold()
        if label in ("yes", "no"):
            out[label.upper()] = str(token)
    if set(out) != {"YES", "NO"}:
        raise RuntimeError("POLYMARKET_BINARY_TOKEN_MAPPING_REQUIRED")
    return out


def _poly_tokens_from_clob(info: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    rows = info.get("t")
    if not isinstance(rows, list):
        return out
    for row in rows:
        if not isinstance(row, dict):
            continue
        label = str(row.get("o") or "").strip().casefold()
        token = row.get("t")
        if label in ("yes", "no") and token is not None:
            out[label.upper()] = str(token)
    return out


def _finite_positive(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number <= 0 or number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _limitless_tradeable(detail: dict[str, Any]) -> bool:
    return bool(
        str(detail.get("status") or "") in {"FUNDED", "FUNDED_FLAGGED"}
        and detail.get("expired") is False
        and detail.get("hidden") is not True
        and str(detail.get("tradeType") or "").casefold() == "clob"
        and str(detail.get("marketType") or "single").casefold() == "single"
        and isinstance(detail.get("tokens"), dict)
        and detail["tokens"].get("yes")
        and detail["tokens"].get("no")
    )


def _polymarket_tradeable(gamma: dict[str, Any], clob: dict[str, Any]) -> bool:
    return bool(
        gamma.get("active") is True
        and gamma.get("closed") is False
        and gamma.get("archived") is not True
        and gamma.get("acceptingOrders") is True
        and gamma.get("enableOrderBook") is True
        and gamma.get("negRisk") is False
        and clob.get("ao") is True
        and clob.get("cbos") is True
        and _finite_positive(clob.get("mts")) is not None
        and _finite_positive(clob.get("mos")) is not None
    )


def _top_matches(left: dict[str, Any], right: dict[str, Any], tolerance: float = 1e-12) -> bool:
    a = book_top(left)
    b = book_top(right)
    for x, y in ((a.bid, b.bid), (a.ask, b.ask)):
        if x is None or y is None:
            if x is not y:
                return False
        elif abs(float(x) - float(y)) > tolerance:
            return False
    return True


def fetch_runtime_market_data(
    pair: dict[str, Any],
    audit: dict[str, Any],
    *,
    ws_timeout_ms: int = 5_000,
    ws_max_age_ms: int = 15_000,
) -> dict[str, Any]:
    """Fetch quote-decision data with fail-closed venue state/provenance.

    Limitless REST is reconciliation only. Only a timestamped orderbookUpdate
    can prove maker-book freshness; a quiet/disconnected stream stays non-fresh.
    Polymarket uses current CLOB V2 market info for tick/min-size/fees/token map.
    """
    lslug = str(pair["limitless_slug"])
    pslug = str(pair["polymarket_slug"])

    ldetail_url = "https://api.limitless.exchange/markets/" + urllib.parse.quote(lslug, safe="")
    ldetail_status, ldetail = _get_json(ldetail_url)
    if ldetail_status != 200 or not isinstance(ldetail, dict):
        raise RuntimeError("LIMITLESS_MARKET_DETAIL_INVALID")

    desired_slugs = desired_subscription_slugs(audit)
    ws = fetch_limitless_ws_snapshot(
        target_slug=lslug,
        desired_slugs=desired_slugs,
        timeout_ms=ws_timeout_ms,
        max_reconnects=1,
    )
    ws_observed = int(time.time() * 1000)
    ws_state = classify_ws_snapshot(ws, observed_at_ms=ws_observed, max_age_ms=ws_max_age_ms)

    # Fetch REST after the WS observation. It is a current untimestamped snapshot
    # used only to detect obvious local-stream desynchronization.
    lbook_url = ldetail_url + "/orderbook"
    lbook_status, lbook_rest = _get_json(lbook_url)
    if lbook_status != 200 or not isinstance(lbook_rest, dict):
        raise RuntimeError("LIMITLESS_RECONCILIATION_BOOK_INVALID")

    ws_book = ws.get("orderbook") if isinstance(ws, dict) else None
    reconciled = bool(isinstance(ws_book, dict) and _top_matches(ws_book, lbook_rest))
    maker_status = str(ws_state.get("status") or "UNKNOWN")
    if maker_status == "FRESH" and not reconciled:
        maker_status = "DESYNC"
    maker_book = ws_book if isinstance(ws_book, dict) else lbook_rest

    pdetail_url = "https://gamma-api.polymarket.com/markets/slug/" + urllib.parse.quote(pslug, safe="")
    pdetail_status, pmarket = _get_json(pdetail_url)
    if pdetail_status != 200 or not isinstance(pmarket, dict):
        raise RuntimeError("POLYMARKET_MARKET_DETAIL_INVALID")
    condition_id = str(pmarket.get("conditionId") or "").strip()
    if not condition_id:
        raise RuntimeError("POLYMARKET_CONDITION_ID_REQUIRED")

    pclob_url = "https://clob.polymarket.com/clob-markets/" + urllib.parse.quote(condition_id, safe="")
    pclob_status, pclob = _get_json(pclob_url)
    if pclob_status != 200 or not isinstance(pclob, dict):
        raise RuntimeError("POLYMARKET_CLOB_V2_MARKET_INFO_INVALID")

    gamma_tokens = _poly_tokens_from_gamma(pmarket)
    clob_tokens = _poly_tokens_from_clob(pclob)
    token_map_matches = gamma_tokens == clob_tokens and set(clob_tokens) == {"YES", "NO"}
    fee_details: PolyFeeDetails | None = parse_clob_fee_details(pclob)

    books: dict[str, dict[str, Any]] = {}
    book_http: dict[str, int] = {}
    book_urls: dict[str, str] = {}
    for outcome in ("YES", "NO"):
        url = "https://clob.polymarket.com/book?" + urllib.parse.urlencode(
            {"token_id": gamma_tokens[outcome]}
        )
        status, book = _get_json(url)
        if status != 200 or not isinstance(book, dict):
            raise RuntimeError(f"POLYMARKET_{outcome}_BOOK_INVALID")
        books[outcome] = book
        book_http[outcome] = status
        book_urls[outcome] = url

    limitless_tradeable = _limitless_tradeable(ldetail)
    polymarket_tradeable = _polymarket_tradeable(pmarket, pclob)
    market_tradeable = bool(
        limitless_tradeable and polymarket_tradeable and token_map_matches and fee_details is not None
    )

    return {
        "limitless": {
            "detail_url": ldetail_url,
            "detail_http": ldetail_status,
            "detail": ldetail,
            "rest_book_url": lbook_url,
            "rest_book_http": lbook_status,
            "rest_book": lbook_rest,
            "ws": ws,
            "ws_state": ws_state,
            "ws_rest_top_reconciled": reconciled,
            "maker_book_status": maker_status,
            "maker_book": maker_book,
            "maker_book_source": (
                "WS_ORDERBOOK_UPDATE" if isinstance(ws_book, dict) else "REST_RECONCILIATION_ONLY"
            ),
            "tradeable": limitless_tradeable,
        },
        "polymarket": {
            "gamma_url": pdetail_url,
            "gamma_http": pdetail_status,
            "gamma": pmarket,
            "clob_market_info_url": pclob_url,
            "clob_market_info_http": pclob_status,
            "clob_market_info": pclob,
            "tokens": gamma_tokens,
            "clob_tokens": clob_tokens,
            "token_map_matches": token_map_matches,
            "fee_details": fee_details,
            "books": books,
            "book_http": book_http,
            "book_urls": book_urls,
            "tradeable": polymarket_tradeable,
            "minimum_tick": _finite_positive(pclob.get("mts")),
            "minimum_order_size": _finite_positive(pclob.get("mos")),
        },
        "market_tradeable": market_tradeable,
    }
