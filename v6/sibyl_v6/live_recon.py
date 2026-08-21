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
    for key in (
        "title", "question", "description", "slug", "rules", "resolutionSource",
        "eventTitle", "eventSlug", "groupItemTitle",
    ):
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
    if any(x in text for x in ("above", "greater than", "higher than", "over $", "over ", "hit $", "hit  $")):
        return "ABOVE"
    if any(x in text for x in ("below", "less than", "lower than", "under $", "under ")):
        return "BELOW"
    title = str(row.get("title") or row.get("question") or "")
    if title.lstrip().startswith("↑"):
        return "ABOVE"
    if title.lstrip().startswith("↓"):
        return "BELOW"
    return "OTHER"


def _parse_number(token: str) -> int | None:
    text = token.strip().lower().replace("$", "").replace(",", "")
    mult = 1
    if text.endswith("k"):
        mult, text = 1000, text[:-1]
    elif text.endswith("m"):
        mult, text = 1_000_000, text[:-1]
    try:
        value = float(text) * mult
    except ValueError:
        return None
    if value < 100:
        return None
    return int(round(value))


def _threshold(row: dict[str, Any]) -> int | None:
    primary = " ".join(str(row.get(k) or "") for k in ("groupItemTitle", "title", "question"))
    for token in re.findall(r"\$?\d[\d,]*(?:\.\d+)?[kKmM]?", primary):
        value = _parse_number(token)
        if value is not None:
            return value
    return None


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
        pass
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return dt.datetime.strptime(text, fmt).replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
    return None


def _end(row: dict[str, Any]) -> dt.datetime | None:
    for key in (
        "endDate", "end_date", "expirationTimestamp", "expirationDate", "expiration_date",
        "expiresAt", "expires_at", "closeTime", "close_time", "resolutionDate", "resolution_date",
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
        "expirationDate", "expirationTimestamp", "startAt", "closeTime", "resolutionDate",
        "eventId", "eventSlug", "eventTitle", "marketType", "tradeType", "outcomes",
        "outcomePrices", "groupItemTitle", "umaResolutionStatus",
    )
    out: dict[str, Any] = {
        "venue": venue,
        "asset": _asset(row),
        "family": _family(row),
        "derived_threshold": _threshold(row),
    }
    ending = _end(row)
    out["derived_end_utc"] = ending.isoformat() if ending else None
    for key in preferred:
        if key in row and row[key] not in (None, "", [], {}):
            value = row[key]
            if isinstance(value, str) and len(value) > 3000:
                value = value[:3000] + "…"
            out[key] = value
    out["available_keys"] = sorted(row.keys())
    return out


def _fetch_limitless() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page in range(1, 41):
        url = "https://api.limitless.exchange/markets/active?" + urllib.parse.urlencode({"limit": 25, "page": page})
        _, payload = _get_json(url)
        rows = payload.get("data", []) if isinstance(payload, dict) else []
        if not isinstance(rows, list) or not rows:
            break
        for row in rows:
            if not isinstance(row, dict):
                continue
            slug = str(row.get("slug") or "")
            if slug and slug not in seen:
                seen.add(slug)
                out.append(row)
            subs = row.get("markets") or row.get("outcomeTokens") or []
            if isinstance(subs, list):
                for sub in subs:
                    if not isinstance(sub, dict):
                        continue
                    sub_slug = str(sub.get("slug") or "")
                    if not sub_slug or sub_slug in seen:
                        continue
                    merged = dict(sub)
                    for source_key, dest_key in (
                        ("title", "eventTitle"), ("slug", "eventSlug"), ("tradeType", "tradeType"),
                        ("expirationDate", "expirationDate"), ("expirationTimestamp", "expirationTimestamp"),
                        ("startAt", "startAt"),
                    ):
                        merged.setdefault(dest_key, row.get(source_key))
                    seen.add(sub_slug)
                    out.append(merged)
        if len(rows) < 25:
            break
    return out


def _fetch_polymarket() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for offset in range(0, 2000, 500):
        url = "https://gamma-api.polymarket.com/events?" + urllib.parse.urlencode({
            "active": "true", "closed": "false", "archived": "false", "limit": 500, "offset": offset,
        })
        _, events = _get_json(url)
        if not isinstance(events, list) or not events:
            break
        for event in events:
            if not isinstance(event, dict):
                continue
            for market in event.get("markets") or []:
                if not isinstance(market, dict):
                    continue
                if market.get("archived") or market.get("closed") or market.get("active") is False:
                    continue
                slug = str(market.get("slug") or "")
                if not slug or slug in seen:
                    continue
                row = dict(market)
                row["eventSlug"] = event.get("slug")
                row["eventTitle"] = event.get("title")
                seen.add(slug)
                out.append(row)
        if len(events) < 500:
            break
    return out


def fetch_live_catalogs() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    return _fetch_limitless(), _fetch_polymarket()


def _event_tokens(row: dict[str, Any]) -> set[str]:
    text = " ".join(str(row.get(k) or "") for k in ("eventTitle", "eventSlug", "title", "question")).casefold()
    stop = {"will", "the", "what", "price", "hit", "by", "or", "and", "to", "of", "in", "on"}
    return {t for t in re.findall(r"[a-z0-9]+", text) if len(t) > 1 and t not in stop}


def semantic_candidates() -> dict[str, Any]:
    limitless, polymarket = fetch_live_catalogs()
    candidates: list[dict[str, Any]] = []
    for left in limitless:
        la = _asset(left)
        if not la:
            continue
        lf = _family(left)
        lt = _threshold(left)
        lend = _end(left)
        for right in polymarket:
            if _asset(right) != la:
                continue
            rf = _family(right)
            if lf == "OTHER" or rf == "OTHER" or lf != rf:
                continue
            rt = _threshold(right)
            if lf in ("ABOVE", "BELOW") and lt is not None and rt is not None and lt != rt:
                continue
            rend = _end(right)
            time_delta_s = abs((lend - rend).total_seconds()) if lend and rend else None
            token_overlap = len(_event_tokens(left) & _event_tokens(right))
            if time_delta_s is None and token_overlap < 2:
                continue
            if time_delta_s is not None and time_delta_s > 86400 and token_overlap < 2:
                continue
            score = 10
            if lt is not None and rt is not None and lt == rt:
                score += 12
            if time_delta_s is not None:
                if time_delta_s == 0:
                    score += 20
                elif time_delta_s <= 300:
                    score += 16
                elif time_delta_s <= 3600:
                    score += 10
                elif time_delta_s <= 14400:
                    score += 6
                elif time_delta_s <= 86400:
                    score += 2
            score += min(token_overlap, 6)
            candidates.append({
                "score": score,
                "asset": la,
                "family": [lf, rf],
                "thresholds": [lt, rt],
                "end_delta_seconds": time_delta_s,
                "event_token_overlap": token_overlap,
                "limitless": _compact(left, "LIMITLESS"),
                "polymarket": _compact(right, "POLYMARKET"),
            })
    candidates.sort(key=lambda x: (-x["score"], x["end_delta_seconds"] if x["end_delta_seconds"] is not None else 10**18))
    focused_limitless = [_compact(r, "LIMITLESS") for r in limitless if _asset(r) and _family(r) != "OTHER"][:160]
    focused_poly = [_compact(r, "POLYMARKET") for r in polymarket if _asset(r) and _family(r) != "OTHER"][:240]
    return {
        "limitless_market_count": len(limitless),
        "polymarket_market_count": len(polymarket),
        "semantic_candidate_count": len(candidates),
        "top_candidates": candidates[:160],
        "focused_limitless": focused_limitless,
        "focused_polymarket": focused_poly,
    }


if __name__ == "__main__":
    print(json.dumps(semantic_candidates(), indent=2, sort_keys=True, default=str))
