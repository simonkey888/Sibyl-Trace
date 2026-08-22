from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import urllib.parse
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

from .feeds import _get_json
from .live_pair_selector import audit_current_pairs

RAW_6 = Decimal("1000000")
EVENT_PAGE_LIMIT = 100
MAX_EVENT_PAGES = 100


def _parse_utc(value: Any) -> dt.datetime:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("FLOW_EVENT_TIMESTAMP_REQUIRED")
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = dt.datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("FLOW_EVENT_TIMESTAMP_TZ_REQUIRED")
    return parsed.astimezone(dt.timezone.utc)


def _raw6(value: Any) -> Decimal:
    try:
        raw = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("FLOW_EVENT_RAW_AMOUNT_INVALID") from exc
    if not raw.is_finite() or raw < 0 or raw != raw.to_integral_value():
        raise ValueError("FLOW_EVENT_RAW_AMOUNT_INVALID")
    return raw / RAW_6


def _finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _reward_state(detail: dict[str, Any]) -> dict[str, Any]:
    settings = detail.get("settings") if isinstance(detail.get("settings"), dict) else {}
    rebate = _finite_number(settings.get("rebateRate"))
    daily = _finite_number(settings.get("dailyReward"))
    effective = _finite_number(settings.get("effectiveDailyReward"))
    multiplier = _finite_number(settings.get("currentRewardsMultiplier"))
    min_size_raw = _finite_number(settings.get("minSize"))
    return {
        "isRewardable": detail.get("isRewardable"),
        "rebateRate": rebate,
        "dailyReward": daily,
        "effectiveDailyReward": effective,
        "currentRewardsMultiplier": multiplier,
        "maxSpread": _finite_number(settings.get("maxSpread")),
        "minSizeContracts": (
            min_size_raw / 1_000_000.0 if min_size_raw is not None else None
        ),
        "rewardsEpoch": settings.get("rewardsEpoch"),
        "rebate_nonzero": bool(rebate is not None and rebate > 0),
        "lp_reward_nonzero": bool(
            (daily is not None and daily > 0)
            or (effective is not None and effective > 0)
        ),
    }


def _fetch_all_events(
    slug: str,
    *,
    getter: Callable[[str], tuple[int, Any]] = _get_json,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    encoded = urllib.parse.quote(slug, safe="")
    rows: list[dict[str, Any]] = []
    total_pages: int | None = None
    total_rows: int | None = None
    pages_fetched = 0
    for page in range(1, MAX_EVENT_PAGES + 1):
        url = (
            f"https://api.limitless.exchange/markets/{encoded}/events"
            f"?page={page}&limit={EVENT_PAGE_LIMIT}"
        )
        status, payload = getter(url)
        if status != 200 or not isinstance(payload, dict):
            raise RuntimeError(f"LIMITLESS_FLOW_EVENTS_HTTP_INVALID:{slug}:{status}")
        events = payload.get("events")
        if not isinstance(events, list):
            raise RuntimeError(f"LIMITLESS_FLOW_EVENTS_SCHEMA_INVALID:{slug}")
        try:
            reported_page = int(payload.get("page"))
            reported_limit = int(payload.get("limit"))
            reported_pages = int(payload.get("totalPages"))
            reported_rows = int(payload.get("totalRows"))
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"LIMITLESS_FLOW_PAGINATION_INVALID:{slug}") from exc
        if reported_page != page or reported_limit <= 0 or reported_pages < 0 or reported_rows < 0:
            raise RuntimeError(f"LIMITLESS_FLOW_PAGINATION_INVALID:{slug}")
        if total_pages is None:
            total_pages = reported_pages
            total_rows = reported_rows
        elif total_pages != reported_pages or total_rows != reported_rows:
            raise RuntimeError(f"LIMITLESS_FLOW_PAGINATION_DRIFT:{slug}")
        pages_fetched += 1
        for event in events:
            if not isinstance(event, dict):
                raise RuntimeError(f"LIMITLESS_FLOW_EVENT_SCHEMA_INVALID:{slug}")
            rows.append(event)
        if page >= max(1, reported_pages):
            break
    else:
        raise RuntimeError(f"LIMITLESS_FLOW_PAGE_LIMIT_EXCEEDED:{slug}")

    expected_rows = int(total_rows or 0)
    if len(rows) != expected_rows:
        raise RuntimeError(
            f"LIMITLESS_FLOW_INCOMPLETE:{slug}:expected={expected_rows}:got={len(rows)}"
        )
    return rows, {
        "pages_fetched": pages_fetched,
        "total_pages": int(total_pages or 0),
        "total_rows": expected_rows,
        "pagination_complete": True,
    }


def audit_pair_flow(
    slug: str,
    *,
    now: dt.datetime,
    getter: Callable[[str], tuple[int, Any]] = _get_json,
) -> dict[str, Any]:
    if now.tzinfo is None:
        raise ValueError("FLOW_AUDIT_NOW_TZ_REQUIRED")
    now = now.astimezone(dt.timezone.utc)
    encoded = urllib.parse.quote(slug, safe="")
    detail_url = f"https://api.limitless.exchange/markets/{encoded}"
    status, detail = getter(detail_url)
    if status != 200 or not isinstance(detail, dict):
        raise RuntimeError(f"LIMITLESS_FLOW_MARKET_HTTP_INVALID:{slug}:{status}")

    events, pagination = _fetch_all_events(slug, getter=getter)
    cutoff = now - dt.timedelta(hours=24)
    normalized: list[dict[str, Any]] = []
    for event in events:
        created = _parse_utc(event.get("createdAt"))
        tx_hash = str(event.get("txHash") or "").strip()
        if not tx_hash.startswith("0x") or len(tx_hash) < 10:
            raise RuntimeError(f"LIMITLESS_FLOW_FINALITY_EVIDENCE_INVALID:{slug}")
        taker_usd = _raw6(event.get("takerAmount"))
        matched_contracts = _raw6(event.get("matchedSize"))
        price = _finite_number(event.get("price"))
        if price is None or not 0 < price < 1:
            raise RuntimeError(f"LIMITLESS_FLOW_PRICE_INVALID:{slug}")
        normalized.append(
            {
                "created_at": created,
                "tx_hash": tx_hash,
                "taker_notional_usd": taker_usd,
                "matched_contracts": matched_contracts,
                "price": price,
            }
        )

    recent = [row for row in normalized if cutoff <= row["created_at"] <= now]
    recent_notional = sum((row["taker_notional_usd"] for row in recent), Decimal(0))
    recent_contracts = sum((row["matched_contracts"] for row in recent), Decimal(0))
    all_notional = sum((row["taker_notional_usd"] for row in normalized), Decimal(0))
    reward = _reward_state(detail)
    return {
        "limitless_slug": slug,
        "market_status": detail.get("status"),
        "expired": detail.get("expired"),
        "volume_formatted": detail.get("volumeFormatted"),
        "event_source_url": (
            f"https://api.limitless.exchange/markets/{encoded}/events"
        ),
        "market_source_url": detail_url,
        "window_hours": 24,
        "window_start_utc": cutoff.isoformat().replace("+00:00", "Z"),
        "window_end_utc": now.isoformat().replace("+00:00", "Z"),
        "finalized_trade_count_24h": len(recent),
        "taker_notional_24h_usd": float(recent_notional),
        "matched_contracts_24h": float(recent_contracts),
        "observed_trade_rate_per_hour": len(recent) / 24.0,
        "all_time_event_count": len(normalized),
        "all_time_taker_notional_usd": float(all_notional),
        "last_trade_utc": (
            max(row["created_at"] for row in normalized)
            .isoformat()
            .replace("+00:00", "Z")
            if normalized
            else None
        ),
        "reward_state": reward,
        **pagination,
    }


def run_flow_audit(
    *,
    now: dt.datetime | None = None,
    getter: Callable[[str], tuple[int, Any]] = _get_json,
) -> dict[str, Any]:
    observed = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc)
    rule_audit = audit_current_pairs()
    exact = [row for row in (rule_audit.get("exact_pairs") or []) if isinstance(row, dict)]
    if not exact:
        raise RuntimeError("FLOW_AUDIT_NO_CURRENT_EXACT_PAIRS")
    slugs = sorted({str(row.get("limitless_slug") or "") for row in exact if row.get("limitless_slug")})
    pairs = [audit_pair_flow(slug, now=observed, getter=getter) for slug in slugs]
    total_trades = sum(int(row["finalized_trade_count_24h"]) for row in pairs)
    total_notional = sum(float(row["taker_notional_24h_usd"]) for row in pairs)
    nonzero_rebate = [row["limitless_slug"] for row in pairs if row["reward_state"]["rebate_nonzero"]]
    nonzero_lp = [row["limitless_slug"] for row in pairs if row["reward_state"]["lp_reward_nonzero"]]
    return {
        "schema_version": "SIBYL_V6_LIMITLESS_FLOW_AUDIT_V1",
        "observed_at_utc": observed.isoformat().replace("+00:00", "Z"),
        "candidate_pair_count": int(rule_audit.get("CANDIDATE_PAIR_COUNT", 0)),
        "exact_pair_count": len(pairs),
        "exact_limitless_slugs": slugs,
        "window_hours": 24,
        "finalized_trade_count_24h": total_trades,
        "taker_notional_24h_usd": round(total_notional, 6),
        "observed_trade_rate_per_hour": total_trades / 24.0,
        "nonzero_rebate_markets": nonzero_rebate,
        "nonzero_lp_reward_markets": nonzero_lp,
        "all_pages_complete": all(bool(row["pagination_complete"]) for row in pairs),
        "pairs": pairs,
        "target_80_interpretation": (
            "NO_OBSERVED_FILL_FLOW_24H"
            if total_trades == 0
            else "OBSERVED_FLOW_NONZERO_REQUIRES_QUEUE_AND_EDGE_MODEL"
        ),
        "profitability_claim": False,
        "real_orders": 0,
        "capital_moved_usd": 0,
        "live": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="/tmp/sibyl-v6-flow-audit.json")
    args = parser.parse_args()
    payload = run_flow_audit()
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    print(json.dumps(payload, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
