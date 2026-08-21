from __future__ import annotations

import urllib.parse
from typing import Any

from .feeds import _get_json


def fetch_limitless() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page in range(1, 61):
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
                        ("title", "eventTitle"),
                        ("slug", "eventSlug"),
                        ("tradeType", "tradeType"),
                        ("expirationDate", "expirationDate"),
                        ("expirationTimestamp", "expirationTimestamp"),
                        ("startAt", "startAt"),
                    ):
                        merged.setdefault(dest_key, row.get(source_key))
                    seen.add(sub_slug)
                    out.append(merged)
    return out


def fetch_polymarket() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    empty_pages = 0
    for offset in range(0, 5000, 100):
        url = "https://gamma-api.polymarket.com/events?" + urllib.parse.urlencode({
            "active": "true",
            "closed": "false",
            "archived": "false",
            "limit": 100,
            "offset": offset,
        })
        _, events = _get_json(url)
        if not isinstance(events, list):
            raise RuntimeError("POLYMARKET_EVENT_CATALOG_INVALID")
        if not events:
            empty_pages += 1
            if empty_pages >= 2:
                break
            continue
        empty_pages = 0
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
                row["eventStartDate"] = event.get("startDate")
                row["eventEndDate"] = event.get("endDate")
                seen.add(slug)
                out.append(row)
    return out


def fetch_live_catalogs() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    return fetch_limitless(), fetch_polymarket()
