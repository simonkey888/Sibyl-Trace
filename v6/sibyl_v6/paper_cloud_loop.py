from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
import urllib.parse
from pathlib import Path
from typing import Any

from .discovery import load_verified_pairs
from .feeds import _get_json


def _hash(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _levels(book: dict[str, Any], side: str) -> list[dict[str, float]]:
    rows = book.get(side) or []
    out: list[dict[str, float]] = []
    if not isinstance(rows, list):
        return out
    for row in rows:
        price = size = None
        if isinstance(row, dict):
            price = row.get("price")
            size = row.get("size") or row.get("amount") or row.get("quantity")
        elif isinstance(row, (list, tuple)) and len(row) >= 2:
            price, size = row[0], row[1]
        try:
            p = float(price)
            s = float(size)
        except (TypeError, ValueError):
            continue
        if p > 0 and s > 0:
            out.append({"price": p, "size": s})
    return out


def _poly_yes_token(market: dict[str, Any]) -> str:
    raw_outcomes = market.get("outcomes")
    raw_tokens = market.get("clobTokenIds")
    if isinstance(raw_outcomes, str):
        raw_outcomes = json.loads(raw_outcomes)
    if isinstance(raw_tokens, str):
        raw_tokens = json.loads(raw_tokens)
    if not isinstance(raw_outcomes, list) or not isinstance(raw_tokens, list) or len(raw_outcomes) != len(raw_tokens):
        raise RuntimeError("POLYMARKET_TOKEN_MAPPING_INVALID")
    for outcome, token in zip(raw_outcomes, raw_tokens):
        if str(outcome).strip().casefold() == "yes":
            return str(token)
    raise RuntimeError("POLYMARKET_YES_TOKEN_MISSING")


def _book_summary(book: dict[str, Any]) -> dict[str, Any]:
    bids = _levels(book, "bids")
    asks = _levels(book, "asks")
    best_bid = max((x["price"] for x in bids), default=None)
    best_ask = min((x["price"] for x in asks), default=None)
    return {
        "book_hash": _hash(book),
        "bid_levels": len(bids),
        "ask_levels": len(asks),
        "best_bid": best_bid,
        "best_ask": best_ask,
        "two_sided": best_bid is not None and best_ask is not None,
    }


def paper_cycle(pair: dict[str, Any]) -> dict[str, Any]:
    started = int(time.time() * 1000)
    lslug = str(pair["limitless_slug"])
    pslug = str(pair["polymarket_slug"])

    lurl = "https://api.limitless.exchange/markets/" + urllib.parse.quote(lslug, safe="") + "/orderbook"
    lstatus, lbook = _get_json(lurl)
    if lstatus != 200 or not isinstance(lbook, dict):
        raise RuntimeError("LIMITLESS_EXACT_PAIR_BOOK_INVALID")

    pdetail_url = "https://gamma-api.polymarket.com/markets/slug/" + urllib.parse.quote(pslug, safe="")
    pstatus, pmarket = _get_json(pdetail_url)
    if pstatus != 200 or not isinstance(pmarket, dict):
        raise RuntimeError("POLYMARKET_EXACT_PAIR_MARKET_INVALID")
    yes_token = _poly_yes_token(pmarket)
    pbook_url = "https://clob.polymarket.com/book?" + urllib.parse.urlencode({"token_id": yes_token})
    pbstatus, pbook = _get_json(pbook_url)
    if pbstatus != 200 or not isinstance(pbook, dict):
        raise RuntimeError("POLYMARKET_EXACT_PAIR_BOOK_INVALID")

    ls = _book_summary(lbook)
    ps = _book_summary(pbook)
    real_feeds = (ls["bid_levels"] + ls["ask_levels"] > 0) and (ps["bid_levels"] + ps["ask_levels"] > 0)
    if not real_feeds:
        raise RuntimeError("EXACT_PAIR_BOOK_DEPTH_EMPTY")

    # Pure PAPER mechanics: derive a hypothetical Limitless maker fair/quote from
    # the live Polymarket YES book. Nothing is signed, submitted, or funded.
    poly_mid = None
    if ps["best_bid"] is not None and ps["best_ask"] is not None:
        poly_mid = (ps["best_bid"] + ps["best_ask"]) / 2.0
    margin_bps = 100
    maker_bid = maker_ask = None
    if poly_mid is not None:
        margin = margin_bps / 10000.0
        maker_bid = max(0.001, poly_mid - margin)
        maker_ask = min(0.999, poly_mid + margin)

    return {
        "schema_version": "SIBYL_V6_CLOUD_PAPER_CYCLE_V1",
        "event": "v6_cloud_paper_cycle",
        "observed_at_ms": int(time.time() * 1000),
        "cycle_latency_ms": int(time.time() * 1000) - started,
        "SOURCE_SHA": os.environ.get("SOURCE_SHA", "UNKNOWN"),
        "DRY_RUN": True,
        "LIVE": "NO",
        "REAL_ORDERS": 0,
        "CAPITAL_MOVED_USD": "0",
        "REAL_FEEDS": real_feeds,
        "exact_pair": {
            "limitless_slug": lslug,
            "polymarket_slug": pslug,
            "comparison_fingerprint": pair["comparison"]["comparison_fingerprint"],
            "rule_fingerprint": pair["comparison"]["left_rule_fingerprint"],
        },
        "limitless": {"endpoint": lurl, **ls},
        "polymarket": {"endpoint": pbook_url, "yes_token": yes_token, **ps},
        "paper_mechanics": {
            "poly_yes_mid": poly_mid,
            "margin_bps": margin_bps,
            "hypothetical_limitless_postonly_bid": maker_bid,
            "hypothetical_limitless_postonly_ask": maker_ask,
            "orders_submitted": 0,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=float(os.environ.get("SIBYL_V6_PAPER_INTERVAL_SECONDS", "60")))
    parser.add_argument("--evidence", default="/tmp/sibyl-v6-paper/startup.json")
    args = parser.parse_args()

    if os.environ.get("DRY_RUN", "true").casefold() != "true":
        raise SystemExit("CLOUD_PAPER_REQUIRES_DRY_RUN_TRUE")
    if os.environ.get("SIBYL_V6_LIVE_ALLOWED", "false").casefold() != "false":
        raise SystemExit("CLOUD_PAPER_LIVE_MUST_BE_FALSE")
    if os.environ.get("SIBYL_V6_RUN_UPSTREAM", "0") != "0":
        raise SystemExit("CLOUD_PAPER_UPSTREAM_WRITES_FORBIDDEN")

    pair_path = Path(os.environ.get("SIBYL_V6_VERIFIED_PAIRS", "v6/config/verified_pairs.json"))
    exact = load_verified_pairs(pair_path)
    if not exact:
        raise SystemExit("CLOUD_PAPER_NO_EXACT_PAIR")
    pair = exact[0]

    first = paper_cycle(pair)
    evidence = Path(args.evidence)
    evidence.parent.mkdir(parents=True, exist_ok=True)
    evidence.write_text(json.dumps(first, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"BOT_CLOUD_RUNNING": True, **first}, sort_keys=True), flush=True)
    if args.once:
        return 0

    while True:
        time.sleep(max(args.interval, 10.0))
        try:
            cycle = paper_cycle(pair)
            print(json.dumps(cycle, sort_keys=True), flush=True)
        except Exception as exc:
            print(json.dumps({
                "event": "v6_cloud_paper_cycle_failed",
                "DRY_RUN": True,
                "LIVE": "NO",
                "REAL_ORDERS": 0,
                "error": str(exc),
            }, sort_keys=True), flush=True)


if __name__ == "__main__":
    raise SystemExit(main())
