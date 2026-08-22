from __future__ import annotations

import datetime as dt
import urllib.parse
from typing import Any

from .feeds import _get_json

SEARCH_ENTITIES = ("Bitcoin", "Ethereum", "Solana", "XRP", "Dogecoin")


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


def _polymarket_entity_events(entity: str, now_iso: str) -> list[dict[str, Any]]:
    events_out: list[dict[str, Any]] = []
    cursor: str | None = None
    seen_cursors: set[str] = set()
    for _ in range(20):
        params: dict[str, Any] = {
            "limit": 500,
            "closed": "false",
            "start_date_max": now_iso,
            "end_date_min": now_iso,
            "title_search": entity,
        }
        if cursor:
            params["after_cursor"] = cursor
        url = "https://gamma-api.polymarket.com/events/keyset?" + urllib.parse.urlencode(params)
        _, payload = _get_json(url)
        if not isinstance(payload, dict):
            raise RuntimeError(f"POLYMARKET_KEYSET_CATALOG_INVALID:{entity}")
        events = payload.get("events", [])
        if not isinstance(events, list):
            raise RuntimeError(f"POLYMARKET_KEYSET_EVENTS_INVALID:{entity}")
        events_out.extend(event for event in events if isinstance(event, dict))
        next_cursor = str(payload.get("next_cursor") or "").strip()
        if not next_cursor:
            break
        if next_cursor in seen_cursors:
            raise RuntimeError(f"POLYMARKET_KEYSET_CURSOR_LOOP:{entity}")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    else:
        raise RuntimeError(f"POLYMARKET_KEYSET_PAGE_CAP_EXCEEDED:{entity}")
    return events_out


def fetch_polymarket() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    seen_markets: set[str] = set()
    seen_events: set[str] = set()
    now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    now_iso = now.isoformat().replace("+00:00", "Z")
    for entity in SEARCH_ENTITIES:
        for event in _polymarket_entity_events(entity, now_iso):
            event_slug = str(event.get("slug") or event.get("id") or "")
            if event_slug and event_slug in seen_events:
                continue
            if event_slug:
                seen_events.add(event_slug)
            if event.get("closed") is True or event.get("archived") is True or event.get("active") is False:
                continue
            for market in event.get("markets") or []:
                if not isinstance(market, dict):
                    continue
                if market.get("archived") or market.get("closed") or market.get("active") is False:
                    continue
                slug = str(market.get("slug") or "")
                if not slug or slug in seen_markets:
                    continue
                row = dict(market)
                row["eventSlug"] = event.get("slug")
                row["eventTitle"] = event.get("title")
                row["eventStartDate"] = event.get("startDate")
                row["eventEndDate"] = event.get("endDate")
                seen_markets.add(slug)
                out.append(row)
    return out


def fetch_live_catalogs() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    return fetch_limitless(), fetch_polymarket()
