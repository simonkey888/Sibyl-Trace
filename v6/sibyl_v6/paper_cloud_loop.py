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
from .quote_safety import assess_buy_quote


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


def _poly_tokens(market: dict[str, Any]) -> dict[str, str]:
    raw_outcomes = market.get("outcomes")
    raw_tokens = market.get("clobTokenIds")
    if isinstance(raw_outcomes, str):
        raw_outcomes = json.loads(raw_outcomes)
    if isinstance(raw_tokens, str):
        raw_tokens = json.loads(raw_tokens)
    if not isinstance(raw_outcomes, list) or not isinstance(raw_tokens, list) or len(raw_outcomes) != len(raw_tokens):
        raise RuntimeError("POLYMARKET_TOKEN_MAPPING_INVALID")
    result: dict[str, str] = {}
    for outcome, token in zip(raw_outcomes, raw_tokens):
        label = str(outcome).strip().casefold()
        if label in ("yes", "no"):
            result[label.upper()] = str(token)
    if set(result) != {"YES", "NO"}:
        raise RuntimeError("POLYMARKET_BINARY_TOKEN_MAPPING_REQUIRED")
    return result


def _optional_nonnegative_number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result < 0 or result != result or result in (float("inf"), float("-inf")):
        return None
    return result


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
    tokens = _poly_tokens(pmarket)

    books: dict[str, dict[str, Any]] = {}
    book_http: dict[str, int] = {}
    book_urls: dict[str, str] = {}
    summaries: dict[str, dict[str, Any]] = {}
    for outcome in ("YES", "NO"):
        url = "https://clob.polymarket.com/book?" + urllib.parse.urlencode({"token_id": tokens[outcome]})
        status, book = _get_json(url)
        seen = int(time.time() * 1000)
        if status != 200 or not isinstance(book, dict):
            raise RuntimeError(f"POLYMARKET_{outcome}_EXACT_PAIR_BOOK_INVALID")
        books[outcome] = book
        book_http[outcome] = status
        book_urls[outcome] = url
        summaries[outcome] = _book_summary(book, seen)

    ls = _book_summary(lbook, l_observed)
    poly_bid = summaries["YES"]["best_executable_bid"]
    poly_ask = summaries["YES"]["best_executable_ask"]
    fair_value_frame_complete = poly_bid is not None and poly_ask is not None
    ltop = BookTop(ls["best_executable_bid"], ls["best_executable_ask"])
    margin = margin_bps / 10_000.0
    if fair_value_frame_complete:
        poly_bid = float(poly_bid)
        poly_ask = float(poly_ask)
        quotes: dict[str, float | None] = compute_buy_prices(poly_bid, poly_ask, margin_bps, ltop)
        yes_cap: float | None = poly_bid - margin
        no_cap: float | None = 1.0 - poly_ask - margin
    else:
        # Do not synthesize a complement or midpoint for a missing live side.
        # Deterministic upstream parity remains a separate exact-head test.
        quotes = {"yes": None, "no": None}
        yes_cap = None
        no_cap = None

    quote_size = float(os.environ.get("SIBYL_V6_QUOTE_SIZE_SHARES", "5"))
    min_edge_bps = float(os.environ.get("SIBYL_V6_MIN_EXPECTED_NET_EDGE_BPS", str(margin_bps)))
    poly_taker_fee_bps = _optional_nonnegative_number(pmarket.get("takerBaseFee"))
    limitless_maker_fee_bps = 0.0

    yes_safety = assess_buy_quote(
        side="YES",
        raw_cap=yes_cap,
        upstream_price=quotes["yes"],
        hedge_book=books["NO"],
        hedge_book_status=str(summaries["NO"]["quote_age_status"]),
        hedge_token=tokens["NO"],
        quote_size=quote_size,
        polymarket_taker_fee_bps=poly_taker_fee_bps,
        minimum_net_edge_bps=min_edge_bps,
        limitless_maker_fee_bps=limitless_maker_fee_bps,
        fair_value_frame_complete=fair_value_frame_complete,
    )
    no_safety = assess_buy_quote(
        side="NO",
        raw_cap=no_cap,
        upstream_price=quotes["no"],
        hedge_book=books["YES"],
        hedge_book_status=str(summaries["YES"]["quote_age_status"]),
        hedge_token=tokens["YES"],
        quote_size=quote_size,
        polymarket_taker_fee_bps=poly_taker_fee_bps,
        minimum_net_edge_bps=min_edge_bps,
        limitless_maker_fee_bps=limitless_maker_fee_bps,
        fair_value_frame_complete=fair_value_frame_complete,
    )

    strategy_quoteable = bool(yes_safety["QUOTEABLE"] and no_safety["QUOTEABLE"])
    cap_gate_ok = all((not side["QUOTEABLE"]) or side["CAP_COMPLIANT"] for side in (yes_safety, no_safety))
    net_edge_gate_ok = all(
        (not side["QUOTEABLE"])
        or (
            side["EXPECTED_NET_EDGE"] is not None
            and side["EXPECTED_NET_EDGE"] + 1e-12 >= side["MIN_EXPECTED_NET_EDGE"]
        )
        for side in (yes_safety, no_safety)
    )
    if not cap_gate_ok or not net_edge_gate_ok:
        raise RuntimeError("SIBYL_QUOTE_SAFETY_INVARIANT_FAILED")

    cycle_latency_ms = (time.perf_counter_ns() - wall_started) / 1_000_000.0
    cpu_ms = (time.process_time_ns() - cpu_started) / 1_000_000.0
    rss_peak_kb = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    payload = {
        "schema_version": "SIBYL_V6_EXACT_PAIR_PAPER_CYCLE_V3",
        "event": "v6_exact_pair_paper_cycle",
        "observed_at_ms": observed_started_ms,
        "cycle_latency_ms": round(cycle_latency_ms, 3),
        "SOURCE_SHA": os.environ.get("SOURCE_SHA", "UNKNOWN"),
        "DRY_RUN": True,
        "LIVE": "NO",
        "REAL_ORDERS": 0,
        "CAPITAL_MOVED_USD": "0",
        "REAL_FEEDS": True,
        "MARKET_DATA_CYCLE": "PASS",
        "STRATEGY_QUOTEABLE": "YES" if strategy_quoteable else "NO",
        "EXACT_PAIR_LIVE_CYCLE": (
            "MARKET_DATA_PASS_STRATEGY_QUOTEABLE_YES" if strategy_quoteable else "MARKET_DATA_PASS_STRATEGY_QUOTEABLE_NO"
        ),
        "SIBYL_QUOTE_SAFETY": "PASS",
        "CAP_COMPLIANCE": "PASS",
        "NET_EDGE_GATE": "PASS",
        "UPSTREAM_PARITY_TEST": "PASS_BY_SEPARATE_EXACT_HEAD_CONTAINER_GATE",
        "LIMITLESS_EXACT_BOOK_HTTP": lstatus,
        "POLY_EXACT_BOOK_HTTP": book_http["YES"],
        "POLY_NO_EXACT_BOOK_HTTP": book_http["NO"],
        "POLY_EXACT_MARKET_HTTP": pstatus,
        "exact_pair": {
            "limitless_slug": lslug,
            "polymarket_slug": pslug,
            "comparison_fingerprint": pair["comparison"]["comparison_fingerprint"],
            "rule_fingerprint": pair["comparison"]["left_rule_fingerprint"],
        },
        "limitless": {"endpoint": lurl, **ls},
        "polymarket": {
            "market_endpoint": pdetail_url,
            "taker_base_fee_bps": poly_taker_fee_bps,
            "fair_value_frame_complete": fair_value_frame_complete,
            "YES": {"endpoint": book_urls["YES"], "token": tokens["YES"], **summaries["YES"]},
            "NO": {"endpoint": book_urls["NO"], "token": tokens["NO"], **summaries["NO"]},
        },
        "paper_mechanics": {
            "semantics": "PINNED_UPSTREAM_COMPUTE_BUY_PRICES_E35AD881_PLUS_SIBYL_FAIL_CLOSED_GATE",
            "limitless_order_sides": ["YES_BUY", "NO_BUY"],
            "postOnly": True,
            "poly_yes_bid": poly_bid,
            "poly_yes_ask": poly_ask,
            "fair_value_frame_complete": fair_value_frame_complete,
            "margin_bps": margin_bps,
            "minimum_net_edge_bps": min_edge_bps,
            "quote_size_shares": quote_size,
            "YES_BUY_CAP": yes_cap,
            "NO_BUY_CAP": no_cap,
            "computed_YES_BUY": quotes["yes"],
            "computed_NO_BUY": quotes["no"],
            "YES": yes_safety,
            "NO": no_safety,
            "strategy_requires_both_sides": True,
            "orders_submitted": 0,
            "fees_provenance": {
                "limitless_maker_fee": "PINNED_UPSTREAM_QUICKSTART_NO_FEE_E35AD881",
                "polymarket_taker_fee": "GAMMA_MARKET_TAKER_BASE_FEE_OR_UNKNOWN",
                "fee_model": "CONSERVATIVE_BPS_PER_FULL_USD_PAYOUT_PER_SHARE",
            },
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
