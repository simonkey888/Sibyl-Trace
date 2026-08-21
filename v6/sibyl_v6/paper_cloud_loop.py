from __future__ import annotations

import argparse
import hashlib
import json
import os
import resource
import time
import urllib.parse
from pathlib import Path
from typing import Any

from .discovery import load_verified_pairs
from .evidence_store import evidence_store_from_env
from .feeds import _freshness, _get_json, _source_timestamp_ms
from .quote_math import BookTop, book_top, compute_buy_prices, norm_price


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
            p = norm_price(float(price))
            s = float(size)
        except (TypeError, ValueError):
            continue
        if 0 < p < 1 and s > 0:
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


def _book_summary(book: dict[str, Any], observed_at_ms: int) -> dict[str, Any]:
    bids = _levels(book, "bids")
    asks = _levels(book, "asks")
    top = book_top(book)
    source_ts = _source_timestamp_ms(book)
    age_ms, status = _freshness(source_ts, observed_at_ms, 15_000)
    return {
        "book_hash": _hash(book),
        "bid_levels": len(bids),
        "ask_levels": len(asks),
        "best_executable_bid": top.bid,
        "best_executable_ask": top.ask,
        "two_sided": top.bid is not None and top.ask is not None,
        "source_timestamp_ms": source_ts,
        "quote_age_ms": age_ms,
        "quote_age_status": status,
    }


def _with_evidence_size(payload: dict[str, Any]) -> dict[str, Any]:
    payload["evidence_bytes_per_cycle"] = 0
    for _ in range(8):
        size = len(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"))
        if payload["evidence_bytes_per_cycle"] == size:
            break
        payload["evidence_bytes_per_cycle"] = size
    return payload


def paper_cycle(pair: dict[str, Any], *, margin_bps: int = 100) -> dict[str, Any]:
    wall_started = time.perf_counter_ns()
    cpu_started = time.process_time_ns()
    observed_started_ms = int(time.time() * 1000)
    lslug = str(pair["limitless_slug"])
    pslug = str(pair["polymarket_slug"])

    lurl = "https://api.limitless.exchange/markets/" + urllib.parse.quote(lslug, safe="") + "/orderbook"
    lstatus, lbook = _get_json(lurl)
    l_observed = int(time.time() * 1000)
    if lstatus != 200 or not isinstance(lbook, dict):
        raise RuntimeError("LIMITLESS_EXACT_PAIR_BOOK_INVALID")

    pdetail_url = "https://gamma-api.polymarket.com/markets/slug/" + urllib.parse.quote(pslug, safe="")
    pstatus, pmarket = _get_json(pdetail_url)
    if pstatus != 200 or not isinstance(pmarket, dict):
        raise RuntimeError("POLYMARKET_EXACT_PAIR_MARKET_INVALID")
    yes_token = _poly_yes_token(pmarket)
    pbook_url = "https://clob.polymarket.com/book?" + urllib.parse.urlencode({"token_id": yes_token})
    pbstatus, pbook = _get_json(pbook_url)
    p_observed = int(time.time() * 1000)
    if pbstatus != 200 or not isinstance(pbook, dict):
        raise RuntimeError("POLYMARKET_EXACT_PAIR_BOOK_INVALID")

    ls = _book_summary(lbook, l_observed)
    ps = _book_summary(pbook, p_observed)
    if not ls["two_sided"] or not ps["two_sided"]:
        raise RuntimeError("EXACT_PAIR_TWO_SIDED_BOOK_REQUIRED")

    poly_bid = float(ps["best_executable_bid"])
    poly_ask = float(ps["best_executable_ask"])
    ltop = BookTop(float(ls["best_executable_bid"]), float(ls["best_executable_ask"]))
    quotes = compute_buy_prices(poly_bid, poly_ask, margin_bps, ltop)
    margin = margin_bps / 10_000.0
    yes_cap = poly_bid - margin
    no_cap = 1.0 - poly_ask - margin

    cycle_latency_ms = (time.perf_counter_ns() - wall_started) / 1_000_000.0
    cpu_ms = (time.process_time_ns() - cpu_started) / 1_000_000.0
    rss_peak_kb = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    payload = {
        "schema_version": "SIBYL_V6_EXACT_PAIR_PAPER_CYCLE_V2",
        "event": "v6_exact_pair_paper_cycle",
        "observed_at_ms": observed_started_ms,
        "cycle_latency_ms": round(cycle_latency_ms, 3),
        "SOURCE_SHA": os.environ.get("SOURCE_SHA", "UNKNOWN"),
        "DRY_RUN": True,
        "LIVE": "NO",
        "REAL_ORDERS": 0,
        "CAPITAL_MOVED_USD": "0",
        "REAL_FEEDS": True,
        "EXACT_PAIR_LIVE_CYCLE": "PASS",
        "LIMITLESS_EXACT_BOOK_HTTP": lstatus,
        "POLY_EXACT_BOOK_HTTP": pbstatus,
        "POLY_EXACT_MARKET_HTTP": pstatus,
        "exact_pair": {
            "limitless_slug": lslug,
            "polymarket_slug": pslug,
            "comparison_fingerprint": pair["comparison"]["comparison_fingerprint"],
            "rule_fingerprint": pair["comparison"]["left_rule_fingerprint"],
        },
        "limitless": {"endpoint": lurl, **ls},
        "polymarket": {"endpoint": pbook_url, "market_endpoint": pdetail_url, "yes_token": yes_token, **ps},
        "paper_mechanics": {
            "semantics": "PINNED_UPSTREAM_COMPUTE_BUY_PRICES_E35AD881",
            "limitless_order_sides": ["YES_BUY", "NO_BUY"],
            "postOnly": True,
            "poly_yes_bid": poly_bid,
            "poly_yes_ask": poly_ask,
            "margin_bps": margin_bps,
            "YES_BUY_CAP": yes_cap,
            "NO_BUY_CAP": no_cap,
            "computed_YES_BUY": quotes["yes"],
            "computed_NO_BUY": quotes["no"],
            "orders_submitted": 0,
        },
        "runtime_profile": {
            "RSS_PEAK_KB": rss_peak_kb,
            "CPU_PROCESS_MS": round(cpu_ms, 3),
            "CPU_TO_WALL_RATIO": round(cpu_ms / cycle_latency_ms, 6) if cycle_latency_ms > 0 else 0.0,
            "reconnects": {"limitless": 0, "polymarket": 0},
        },
    }
    return _with_evidence_size(payload)


def _persist_cycle(store, key: str, path: Path, payload: dict[str, Any]) -> None:
    data = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    store.put_bytes(key, data)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--cycles", type=int, default=0, help="0 means continuous")
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
    if pair["limitless_slug"] != "62000-1786954111732" or pair["polymarket_slug"] != "will-bitcoin-dip-to-62k-august-17-23-2026":
        raise SystemExit("CLOUD_PAPER_EXPECTED_AUDITED_PAIR_NOT_SELECTED")

    store = evidence_store_from_env()
    source_sha = os.environ.get("SOURCE_SHA", "UNKNOWN")
    evidence = Path(args.evidence)
    target_cycles = 1 if args.once else max(0, args.cycles)
    completed = 0
    while True:
        cycle = paper_cycle(pair)
        completed += 1
        cycle["evidence_store"] = store.contract()
        cycle = _with_evidence_size(cycle)
        path = evidence if completed == 1 else evidence.with_name(f"cycle-{completed:06d}.json")
        key = f"paper/{source_sha}/cycle-{completed:06d}.json"
        _persist_cycle(store, key, path, cycle)
        print(json.dumps({"BOT_CLOUD_RUNNING": target_cycles == 0, **cycle}, sort_keys=True), flush=True)
        if target_cycles and completed >= target_cycles:
            return 0
        time.sleep(max(args.interval, 1.0))


if __name__ == "__main__":
    raise SystemExit(main())
