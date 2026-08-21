from __future__ import annotations

import datetime as dt
import json
import re
import urllib.parse
from typing import Any

from .feeds import _get_json

ASSETS = {
    "BTC": ("btc", "bitcoin"),
    "ETH": ("eth", "ethereum", "ether"),
    "SOL": ("sol", "solana"),
    "XRP": ("xrp", "ripple"),
    "DOGE": ("doge", "dogecoin"),
}


def _text(row: dict[str, Any]) -> str:
    parts = []
    for key in ("title", "question", "description", "slug", "rules", "resolutionSource"):
        value = row.get(key)
        if value is not None:
            parts.append(str(value))
    return " ".join(parts).casefold()


def _asset(row: dict[str, Any]) -> str | None:
    text = _text(row)
    for symbol, aliases in ASSETS.items():
        for alias in aliases:
            if re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", text):
                return symbol
    return None


def _family(row: dict[str, Any]) -> str:
    text = _text(row)
    if "up or down" in text or ("higher" in text and "lower" in text):
        return "UP_DOWN"
    if any(x in text for x in ("above", "greater than", "higher than", "over $", "over ")):
        return "ABOVE"
    if any(x in text for x in ("below", "less than", "lower than", "under $", "under ")):
        return "BELOW"
    return "OTHER"


def _parse_time(value: Any) -> dt.datetime | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        n = float(value)
        if n > 10_000_000_000:
            n /= 1000.0
        try:
            return dt.datetime.fromtimestamp(n, tz=dt.timezone.utc)
        except (ValueError, OSError):
            return None
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed.astimezone(dt.timezone.utc)
    except ValueError:
        return None


def _end(row: dict[str, Any]) -> dt.datetime | None:
    for key in (
        "endDate", "end_date", "expirationDate", "expiration_date", "expiresAt",
        "expires_at", "closeTime", "close_time", "resolutionDate", "resolution_date",
    ):
        parsed = _parse_time(row.get(key))
        if parsed:
            return parsed
    slug = str(row.get("slug") or "")
    match = re.search(r"(?:^|-)(1[7-9]\d{8})(?:-|$)", slug)
    if match:
        return _parse_time(int(match.group(1)))
    return None


def _compact(row: dict[str, Any], venue: str) -> dict[str, Any]:
    preferred = (
        "id", "conditionId", "slug", "title", "question", "description", "rules",
        "resolutionSource", "oracle", "oracleSource", "oracleAddress", "endDate",
        "expirationDate", "closeTime", "resolutionDate", "eventId", "marketType",
        "outcomes", "outcomePrices", "groupItemTitle", "umaResolutionStatus",
    )
    out: dict[str, Any] = {"venue": venue, "asset": _asset(row), "family": _family(row)}
    ending = _end(row)
    out["derived_end_utc"] = ending.isoformat() if ending else None
    for key in preferred:
        if key in row and row[key] not in (None, "", [], {}):
            value = row[key]
            if isinstance(value, str) and len(value) > 1600:
                value = value[:1600] + "…"
            out[key] = value
    out["available_keys"] = sorted(row.keys())
    return out


def fetch_live_catalogs(limitless_pages: int = 12, polymarket_limit: int = 1000) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    limitless: list[dict[str, Any]] = []
    for page in range(1, limitless_pages + 1):
        url = "https://api.limitless.exchange/markets/active?" + urllib.parse.urlencode({"limit": 50, "page": page})
        _, payload = _get_json(url)
        rows = payload.get("data", []) if isinstance(payload, dict) else []
        if not isinstance(rows, list) or not rows:
            break
        limitless.extend(row for row in rows if isinstance(row, dict))
        if len(rows) < 50:
            break

    _, poly_payload = _get_json(
        "https://gamma-api.polymarket.com/markets?" + urllib.parse.urlencode(
            {"active": "true", "closed": "false", "limit": polymarket_limit}
        )
    )
    polymarket = [row for row in poly_payload if isinstance(row, dict)] if isinstance(poly_payload, list) else []
    return limitless, polymarket


def semantic_candidates() -> dict[str, Any]:
    limitless, polymarket = fetch_live_catalogs()
    candidates: list[dict[str, Any]] = []
    for left in limitless:
        la = _asset(left)
        if not la:
            continue
        lf = _family(left)
        lend = _end(left)
        for right in polymarket:
            if _asset(right) != la:
                continue
            rf = _family(right)
            rend = _end(right)
            time_delta_s = None
            if lend and rend:
                time_delta_s = abs((lend - rend).total_seconds())
            same_family = lf == rf and lf != "OTHER"
            if not same_family and (time_delta_s is None or time_delta_s > 3600):
                continue
            score = 0
            if same_family:
                score += 5
            if time_delta_s is not None:
                if time_delta_s == 0:
                    score += 10
                elif time_delta_s <= 300:
                    score += 8
                elif time_delta_s <= 900:
                    score += 5
                elif time_delta_s <= 3600:
                    score += 2
            candidates.append({
                "score": score,
                "asset": la,
                "family": [lf, rf],
                "end_delta_seconds": time_delta_s,
                "limitless": _compact(left, "LIMITLESS"),
                "polymarket": _compact(right, "POLYMARKET"),
            })
    candidates.sort(key=lambda x: (-x["score"], x["end_delta_seconds"] if x["end_delta_seconds"] is not None else 10**18))
    focused_limitless = [_compact(r, "LIMITLESS") for r in limitless if _asset(r) and _family(r) != "OTHER"][:40]
    focused_poly = [_compact(r, "POLYMARKET") for r in polymarket if _asset(r) and _family(r) != "OTHER"][:80]
    return {
        "limitless_market_count": len(limitless),
        "polymarket_market_count": len(polymarket),
        "semantic_candidate_count": len(candidates),
        "top_candidates": candidates[:60],
        "focused_limitless": focused_limitless,
        "focused_polymarket": focused_poly,
    }


if __name__ == "__main__":
    print(json.dumps(semantic_candidates(), indent=2, sort_keys=True, default=str))
