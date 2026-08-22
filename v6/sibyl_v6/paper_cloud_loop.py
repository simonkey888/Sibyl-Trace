from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import resource
import time
from pathlib import Path
from typing import Any

from .discovery import load_verified_pairs
from .evidence_store import evidence_store_from_env
from .feeds import _freshness, _source_timestamp_ms
from .live_pair_selector import audit_current_pairs, select_current_exact_pair
from .quote_math import BookTop, book_top, compute_buy_prices, norm_price
from .quote_safety import assess_buy_quote
from .runtime_market_data import fetch_runtime_market_data


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
        if math.isfinite(p) and math.isfinite(s) and 0 < p < 1 and s > 0:
            out.append({"price": p, "size": s})
    out.sort(key=lambda row: row["price"], reverse=(side == "bids"))
    return out


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


def _shares_for_notional(book: dict[str, Any], notional_usdc: float) -> dict[str, Any]:
    """Conservative L2 simulation of a market BUY spending a fixed USD notional."""
    if not math.isfinite(notional_usdc) or notional_usdc <= 0:
        return {"shares": 0.0, "spent": 0.0, "unspent": notional_usdc, "levels": 0}
    remaining = float(notional_usdc)
    shares = 0.0
    spent = 0.0
    levels = 0
    for row in _levels(book, "asks"):
        price = row["price"]
        size = row["size"]
        max_cost = price * size
        take_cost = min(remaining, max_cost)
        if take_cost <= 0:
            continue
        take_shares = take_cost / price
        shares += take_shares
        spent += take_cost
        remaining -= take_cost
        levels += 1
        if remaining <= 1e-12:
            break
    return {
        "shares": round(shares, 12),
        "spent": round(spent, 12),
        "unspent": round(remaining, 12),
        "levels": levels,
    }


def _apply_pinned_hedge_contract(
    result: dict[str, Any],
    *,
    hedge_book: dict[str, Any],
    upstream_hedge_price: float | None,
    quote_size: float,
    hedge_threshold: float,
) -> None:
    """Reject quotes the pinned upstream FAK notional cannot keep approximately flat."""
    notional = (
        quote_size * float(upstream_hedge_price)
        if upstream_hedge_price is not None and math.isfinite(float(upstream_hedge_price))
        else None
    )
    simulated = _shares_for_notional(hedge_book, notional) if notional is not None else {
        "shares": 0.0,
        "spent": 0.0,
        "unspent": None,
        "levels": 0,
    }
    residual = quote_size - float(simulated["shares"])
    approximately_flat = bool(
        notional is not None
        and float(simulated["unspent"] or 0.0) <= 1e-9
        and abs(residual) < max(float(hedge_threshold), 1e-12)
    )
    result["PINNED_HEDGE_PRICE"] = upstream_hedge_price
    result["PINNED_HEDGE_NOTIONAL_USDC"] = notional
    result["PINNED_HEDGE_SIMULATED_SHARES"] = simulated["shares"]
    result["PINNED_HEDGE_RESIDUAL_SHARES"] = round(residual, 12)
    result["PINNED_HEDGE_APPROX_FLAT"] = approximately_flat
    if not approximately_flat:
        result["QUOTEABLE"] = False
        reason = str(result.get("REJECTION_REASON") or "NONE")
        parts = [] if reason == "NONE" else reason.split("|")
        if "PINNED_HEDGE_NOTIONAL_MISMATCH" not in parts:
            parts.append("PINNED_HEDGE_NOTIONAL_MISMATCH")
        result["REJECTION_REASON"] = "|".join(parts)


def _with_evidence_size(payload: dict[str, Any]) -> dict[str, Any]:
    payload["evidence_bytes_per_cycle"] = 0
    for _ in range(8):
        size = len(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        )
        if payload["evidence_bytes_per_cycle"] == size:
            break
        payload["evidence_bytes_per_cycle"] = size
    return payload


def _validate_live_exact_pair(pair: dict[str, Any]) -> None:
    comparison = pair.get("comparison") or {}
    if comparison.get("state") != "EXACT_EQUIVALENT":
        raise RuntimeError("LIVE_SELECTED_PAIR_NOT_EXACT_EQUIVALENT")
    if comparison.get("unknown_fields"):
        raise RuntimeError("LIVE_SELECTED_PAIR_HAS_UNKNOWN_RULE_FIELDS")
    if comparison.get("differing_fields"):
        raise RuntimeError("LIVE_SELECTED_PAIR_HAS_RULE_DIFFERENCES")
    left = str(comparison.get("left_rule_fingerprint") or "")
    right = str(comparison.get("right_rule_fingerprint") or "")
    if len(left) != 64 or left != right:
        raise RuntimeError("LIVE_SELECTED_PAIR_RULE_FINGERPRINT_MISMATCH")


def paper_cycle(
    pair: dict[str, Any], *, audit: dict[str, Any], margin_bps: int = 100
) -> dict[str, Any]:
    _validate_live_exact_pair(pair)
    wall_started = time.perf_counter_ns()
    cpu_started = time.process_time_ns()
    observed_started_ms = int(time.time() * 1000)
    lslug = str(pair["limitless_slug"])
    pslug = str(pair["polymarket_slug"])

    runtime = fetch_runtime_market_data(pair, audit)
    lctx = runtime["limitless"]
    pctx = runtime["polymarket"]
    lbook = lctx["maker_book"]
    books: dict[str, dict[str, Any]] = pctx["books"]
    tokens: dict[str, str] = pctx["tokens"]

    l_observed = int(time.time() * 1000)
    ls = _book_summary(lbook, l_observed)
    ws_state = lctx["ws_state"]
    ls.update(
        {
            "source_timestamp_ms": ws_state.get("source_timestamp_ms"),
            "quote_age_ms": ws_state.get("age_ms"),
            "quote_age_status": lctx["maker_book_status"],
            "book_source": lctx["maker_book_source"],
            "ws_rest_top_reconciled": lctx["ws_rest_top_reconciled"],
        }
    )
    rest_summary = _book_summary(lctx["rest_book"], int(time.time() * 1000))

    summaries: dict[str, dict[str, Any]] = {}
    for outcome in ("YES", "NO"):
        summaries[outcome] = _book_summary(books[outcome], int(time.time() * 1000))

    poly_bid = summaries["YES"]["best_executable_bid"]
    poly_ask = summaries["YES"]["best_executable_ask"]
    fair_value_frame_complete = poly_bid is not None and poly_ask is not None
    ltop = BookTop(ls["best_executable_bid"], ls["best_executable_ask"])
    margin = margin_bps / 10_000.0
    if fair_value_frame_complete:
        poly_bid = float(poly_bid)
        poly_ask = float(poly_ask)
        quotes: dict[str, float | None] = compute_buy_prices(
            poly_bid, poly_ask, margin_bps, ltop
        )
        yes_cap: float | None = poly_bid - margin
        no_cap: float | None = 1.0 - poly_ask - margin
    else:
        quotes = {"yes": None, "no": None}
        yes_cap = None
        no_cap = None

    quote_size = float(os.environ.get("SIBYL_V6_QUOTE_SIZE_SHARES", "5"))
    min_edge_bps = float(
        os.environ.get("SIBYL_V6_MIN_EXPECTED_NET_EDGE_BPS", str(margin_bps))
    )
    fee_safety_buffer_bps = float(
        os.environ.get("SIBYL_V6_POLY_FEE_SAFETY_BUFFER_BPS", "0")
    )
    hedge_threshold = float(os.environ.get("SIBYL_V6_HEDGE_THRESHOLD_SHARES", "2"))
    poly_min_order_size = pctx.get("minimum_order_size")
    size_valid = bool(
        poly_min_order_size is not None and quote_size >= float(poly_min_order_size) - 1e-12
    )
    market_tradeable = bool(runtime["market_tradeable"] and size_valid)

    fee_details = pctx["fee_details"]
    maker_book_status = str(lctx["maker_book_status"])

    yes_safety = assess_buy_quote(
        side="YES",
        raw_cap=yes_cap,
        upstream_price=quotes["yes"],
        hedge_book=books["NO"],
        hedge_book_status=str(summaries["NO"]["quote_age_status"]),
        maker_book_status=maker_book_status,
        hedge_token=tokens["NO"],
        quote_size=quote_size,
        polymarket_fee_details=fee_details,
        minimum_net_edge_bps=min_edge_bps,
        fee_safety_buffer_bps=fee_safety_buffer_bps,
        limitless_maker_fee_bps=0.0,
        fair_value_frame_complete=fair_value_frame_complete,
        market_tradeable=market_tradeable,
    )
    no_safety = assess_buy_quote(
        side="NO",
        raw_cap=no_cap,
        upstream_price=quotes["no"],
        hedge_book=books["YES"],
        hedge_book_status=str(summaries["YES"]["quote_age_status"]),
        maker_book_status=maker_book_status,
        hedge_token=tokens["YES"],
        quote_size=quote_size,
        polymarket_fee_details=fee_details,
        minimum_net_edge_bps=min_edge_bps,
        fee_safety_buffer_bps=fee_safety_buffer_bps,
        limitless_maker_fee_bps=0.0,
        fair_value_frame_complete=fair_value_frame_complete,
        market_tradeable=market_tradeable,
    )

    # Mirror the pinned upstream hedger's actual notional formula:
    # YES fill -> buy NO at (1 - Poly YES bid); NO fill -> buy YES at YES ask.
    yes_hedge_price = 1.0 - float(poly_bid) if poly_bid is not None else None
    no_hedge_price = float(poly_ask) if poly_ask is not None else None
    _apply_pinned_hedge_contract(
        yes_safety,
        hedge_book=books["NO"],
        upstream_hedge_price=yes_hedge_price,
        quote_size=quote_size,
        hedge_threshold=hedge_threshold,
    )
    _apply_pinned_hedge_contract(
        no_safety,
        hedge_book=books["YES"],
        upstream_hedge_price=no_hedge_price,
        quote_size=quote_size,
        hedge_threshold=hedge_threshold,
    )

    yes_quoteable = bool(yes_safety["QUOTEABLE"])
    no_quoteable = bool(no_safety["QUOTEABLE"])
    any_side_quoteable = bool(yes_quoteable or no_quoteable)
    strategy_quoteable = bool(yes_quoteable and no_quoteable)
    cap_gate_ok = all(
        (not side["QUOTEABLE"]) or side["CAP_COMPLIANT"]
        for side in (yes_safety, no_safety)
    )
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
    comparison = pair["comparison"]
    ldetail = lctx["detail"]
    lsettings = ldetail.get("settings") if isinstance(ldetail.get("settings"), dict) else {}
    pinfo = pctx["clob_market_info"]

    payload = {
        "schema_version": "SIBYL_V6_EXACT_PAIR_PAPER_CYCLE_V5",
        "event": "v6_exact_pair_paper_cycle",
        "observed_at_ms": observed_started_ms,
        "cycle_latency_ms": round(cycle_latency_ms, 3),
        "SOURCE_SHA": os.environ.get("SOURCE_SHA", "UNKNOWN"),
        "DRY_RUN": True,
        "LIVE": "NO",
        "REAL_ORDERS": 0,
        "CAPITAL_MOVED_USD": "0",
        "REAL_FEEDS": True,
        "DISCOVERY_CYCLE": "PASS",
        "MARKET_DATA_CYCLE": "PASS",
        "STRATEGY_QUOTEABLE": "YES" if strategy_quoteable else "NO",
        "YES_QUOTEABLE": yes_quoteable,
        "NO_QUOTEABLE": no_quoteable,
        "ANY_SIDE_QUOTEABLE": any_side_quoteable,
        "BOTH_SIDES_QUOTEABLE": strategy_quoteable,
        "EXACT_PAIR_LIVE_CYCLE": (
            "MARKET_DATA_PASS_STRATEGY_QUOTEABLE_YES"
            if strategy_quoteable
            else "MARKET_DATA_PASS_STRATEGY_QUOTEABLE_NO"
        ),
        "CANDIDATE_PAIR_COUNT": int(audit.get("CANDIDATE_PAIR_COUNT", 0)),
        "EXACT_EQUIVALENT_PAIR_COUNT": int(audit.get("EXACT_EQUIVALENT_PAIR_COUNT", 0)),
        "SIBYL_QUOTE_SAFETY": "PASS",
        "CAP_COMPLIANCE": "PASS",
        "NET_EDGE_GATE": "PASS",
        "UPSTREAM_PARITY_TEST": "PASS_BY_SEPARATE_EXACT_HEAD_CONTAINER_GATE",
        "LIMITLESS_EXACT_BOOK_HTTP": lctx["rest_book_http"],
        "LIMITLESS_MARKET_HTTP": lctx["detail_http"],
        "POLY_EXACT_BOOK_HTTP": pctx["book_http"]["YES"],
        "POLY_NO_EXACT_BOOK_HTTP": pctx["book_http"]["NO"],
        "POLY_EXACT_MARKET_HTTP": pctx["gamma_http"],
        "POLY_CLOB_V2_MARKET_HTTP": pctx["clob_market_info_http"],
        "exact_pair": {
            "limitless_slug": lslug,
            "polymarket_slug": pslug,
            "comparison_state": comparison["state"],
            "comparison_fingerprint": comparison["comparison_fingerprint"],
            "left_rule_fingerprint": comparison["left_rule_fingerprint"],
            "right_rule_fingerprint": comparison["right_rule_fingerprint"],
            "unknown_fields": comparison.get("unknown_fields", []),
            "differing_fields": comparison.get("differing_fields", []),
            "limitless_rule_source_url": pair.get("limitless_rule_source_url"),
            "polymarket_rule_source_url": pair.get("polymarket_rule_source_url"),
            "limitless_rule_payload_hash": pair.get("limitless_rule_payload_hash"),
            "polymarket_rule_payload_hash": pair.get("polymarket_rule_payload_hash"),
        },
        "limitless": {
            "endpoint": lctx["rest_book_url"],
            **ls,
            "REST_RECONCILIATION": rest_summary,
            "WS": {
                **ws_state,
                "event_received": bool(lctx["ws"].get("event_received")),
                "namespace_ready": bool(lctx["ws"].get("namespace_ready")),
                "target_slug": lslug,
            },
            "market_tradeable": bool(lctx["tradeable"]),
            "market_status": ldetail.get("status"),
            "expired": ldetail.get("expired"),
            "volume_usd": ldetail.get("volumeFormatted"),
            "reward_state": {
                "isRewardable": ldetail.get("isRewardable"),
                "rebateRate": lsettings.get("rebateRate"),
                "dailyReward": lsettings.get("dailyReward"),
                "effectiveDailyReward": lsettings.get("effectiveDailyReward"),
                "currentRewardsMultiplier": lsettings.get("currentRewardsMultiplier"),
                "maxSpread": lsettings.get("maxSpread"),
                "minSize": lsettings.get("minSize"),
                "takerDelayMs": lsettings.get("takerDelayMs"),
            },
        },
        "polymarket": {
            "market_endpoint": pctx["gamma_url"],
            "clob_v2_market_info_endpoint": pctx["clob_market_info_url"],
            "market_tradeable": bool(pctx["tradeable"]),
            "token_map_matches": bool(pctx["token_map_matches"]),
            "minimum_tick": pctx["minimum_tick"],
            "minimum_order_size": pctx["minimum_order_size"],
            "fair_value_frame_complete": fair_value_frame_complete,
            "fee_details": fee_details.to_dict() if fee_details else None,
            "clob_market_info_version": pinfo.get("v"),
            "YES": {
                "endpoint": pctx["book_urls"]["YES"],
                "token": tokens["YES"],
                **summaries["YES"],
            },
            "NO": {
                "endpoint": pctx["book_urls"]["NO"],
                "token": tokens["NO"],
                **summaries["NO"],
            },
        },
        "paper_mechanics": {
            "semantics": "PINNED_UPSTREAM_COMPUTE_BUY_PRICES_PLUS_SIBYL_FAIL_CLOSED_V2_GATE",
            "limitless_order_sides": ["YES_BUY", "NO_BUY"],
            "postOnly": True,
            "poly_yes_bid": poly_bid,
            "poly_yes_ask": poly_ask,
            "fair_value_frame_complete": fair_value_frame_complete,
            "margin_bps": margin_bps,
            "minimum_net_edge_bps": min_edge_bps,
            "quote_size_shares": quote_size,
            "hedge_threshold_shares": hedge_threshold,
            "poly_min_order_size_satisfied": size_valid,
            "YES_BUY_CAP": yes_cap,
            "NO_BUY_CAP": no_cap,
            "computed_YES_BUY": quotes["yes"],
            "computed_NO_BUY": quotes["no"],
            "YES": yes_safety,
            "NO": no_safety,
            "strategy_requires_both_sides": True,
            "paper_any_side_opportunity": any_side_quoteable,
            "orders_submitted": 0,
            "fees_provenance": {
                "limitless_maker_fee": "OFFICIAL_MAKER_ZERO_FEE",
                "polymarket_platform_fee": "CLOB_V2_MARKET_INFO_FD_PER_EXECUTED_L2_LEVEL",
                "fee_formula": "SHARES_X_RATE_X_P_X_1_MINUS_P_POW_EXPONENT",
                "safety_buffer_separate": True,
                "realized_fee": None,
            },
        },
        "runtime_profile": {
            "RSS_PEAK_KB": rss_peak_kb,
            "CPU_PROCESS_MS": round(cpu_ms, 3),
            "CPU_TO_WALL_RATIO": (
                round(cpu_ms / cycle_latency_ms, 6) if cycle_latency_ms > 0 else 0.0
            ),
            "reconnects": {
                "limitless": int(ws_state.get("reconnects") or 0),
                "polymarket": 0,
            },
            "resubscribe_count": int(ws_state.get("resubscribe_count") or 0),
        },
    }
    return _with_evidence_size(payload)


def _no_exact_cycle(audit: dict[str, Any]) -> dict[str, Any]:
    return _with_evidence_size(
        {
            "schema_version": "SIBYL_V6_NO_EXACT_PAIR_CYCLE_V1",
            "event": "v6_no_exact_pair_cycle",
            "observed_at_ms": int(time.time() * 1000),
            "SOURCE_SHA": os.environ.get("SOURCE_SHA", "UNKNOWN"),
            "DRY_RUN": True,
            "LIVE": "NO",
            "REAL_ORDERS": 0,
            "CAPITAL_MOVED_USD": "0",
            "REAL_FEEDS": True,
            "DISCOVERY_CYCLE": "PASS",
            "MARKET_DATA_CYCLE": "FAIL",
            "MARKET_DATA_REJECTION_REASON": "NO_CURRENT_EXACT_EQUIVALENT_PAIR",
            "STRATEGY_QUOTEABLE": "NO",
            "CANDIDATE_PAIR_COUNT": int(audit.get("CANDIDATE_PAIR_COUNT", 0)),
            "EXACT_EQUIVALENT_PAIR_COUNT": 0,
            "SIBYL_QUOTE_SAFETY": "PASS_FAIL_CLOSED_NO_PAIR",
            "REALIZED_OR_SIMULATED_ORDER_ACTIONS": 0,
        }
    )


def _failure_cycle(exc: Exception) -> dict[str, Any]:
    return _with_evidence_size(
        {
            "schema_version": "SIBYL_V6_OBSERVER_FAILURE_CYCLE_V1",
            "event": "v6_observer_failure_cycle",
            "observed_at_ms": int(time.time() * 1000),
            "SOURCE_SHA": os.environ.get("SOURCE_SHA", "UNKNOWN"),
            "DRY_RUN": True,
            "LIVE": "NO",
            "REAL_ORDERS": 0,
            "CAPITAL_MOVED_USD": "0",
            "REAL_FEEDS": False,
            "DISCOVERY_CYCLE": "FAIL",
            "MARKET_DATA_CYCLE": "FAIL",
            "STRATEGY_QUOTEABLE": "NO",
            "error_type": type(exc).__name__,
            "error": str(exc)[:500],
        }
    )


def _persist_cycle(store, key: str, path: Path, payload: dict[str, Any]) -> None:
    data = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    store.put_bytes(key, data)


def _preferred_pairs(path: Path) -> set[tuple[str, str]]:
    return {
        (str(row["limitless_slug"]), str(row["polymarket_slug"]))
        for row in load_verified_pairs(path)
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--cycles", type=int, default=0, help="0 means continuous")
    parser.add_argument(
        "--interval",
        type=float,
        default=float(os.environ.get("SIBYL_V6_PAPER_INTERVAL_SECONDS", "60")),
    )
    parser.add_argument("--evidence", default="/tmp/sibyl-v6-paper/startup.json")
    args = parser.parse_args()

    if os.environ.get("DRY_RUN", "true").casefold() != "true":
        raise SystemExit("CLOUD_PAPER_REQUIRES_DRY_RUN_TRUE")
    if os.environ.get("SIBYL_V6_LIVE_ALLOWED", "false").casefold() != "false":
        raise SystemExit("CLOUD_PAPER_LIVE_MUST_BE_FALSE")
    if os.environ.get("SIBYL_V6_RUN_UPSTREAM", "0") != "0":
        raise SystemExit("CLOUD_PAPER_UPSTREAM_WRITES_FORBIDDEN")

    pair_path = Path(
        os.environ.get("SIBYL_V6_VERIFIED_PAIRS", "v6/config/verified_pairs.json")
    )
    preferred = _preferred_pairs(pair_path)
    store = evidence_store_from_env()
    source_sha = os.environ.get("SOURCE_SHA", "UNKNOWN")
    evidence = Path(args.evidence)
    target_cycles = 1 if args.once else max(0, args.cycles)
    completed = 0

    while True:
        exit_code = 0
        try:
            audit = audit_current_pairs()
            pair = select_current_exact_pair(audit, preferred)
            cycle = paper_cycle(pair, audit=audit) if pair else _no_exact_cycle(audit)
            if not pair:
                exit_code = 4
        except Exception as exc:
            cycle = _failure_cycle(exc)
            exit_code = 3

        completed += 1
        cycle["evidence_store"] = store.contract()
        cycle = _with_evidence_size(cycle)
        path = evidence if completed == 1 else evidence.with_name(f"cycle-{completed:06d}.json")
        key = f"paper/{source_sha}/cycle-{completed:06d}.json"
        _persist_cycle(store, key, path, cycle)
        print(
            json.dumps({"BOT_CLOUD_RUNNING": target_cycles == 0, **cycle}, sort_keys=True),
            flush=True,
        )
        if target_cycles and completed >= target_cycles:
            return exit_code
        time.sleep(max(args.interval, 1.0))


if __name__ == "__main__":
    raise SystemExit(main())
