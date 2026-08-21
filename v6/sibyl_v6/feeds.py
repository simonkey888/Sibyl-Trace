from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class FeedObservation:
    venue: str
    endpoint: str
    observed_at_ms: int
    http_status: int
    ok: bool
    payload_hash: str | None
    market_id: str | None = None
    book_level_count: int = 0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _get_json(url: str, timeout: float = 12.0) -> tuple[int, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "sibyl-v6-r1-readonly/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return int(response.status), json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"HTTP_{exc.code}:{body[:200]}") from exc


def _digest(payload: Any) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def polymarket_public_book(timeout: float = 12.0) -> FeedObservation:
    now = int(time.time() * 1000)
    gamma = "https://gamma-api.polymarket.com/markets?active=true&closed=false&limit=20"
    try:
        status, markets = _get_json(gamma, timeout)
        if status != 200 or not isinstance(markets, list):
            raise RuntimeError("POLYMARKET_MARKET_LIST_INVALID")
        token_id = None
        market_id = None
        for market in markets:
            raw = market.get("clobTokenIds") if isinstance(market, dict) else None
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except json.JSONDecodeError:
                    raw = []
            if isinstance(raw, list) and raw:
                token_id = str(raw[0])
                market_id = str(market.get("id") or market.get("conditionId") or "")
                break
        if not token_id:
            raise RuntimeError("POLYMARKET_NO_PUBLIC_CLOB_TOKEN")
        url = "https://clob.polymarket.com/book?" + urllib.parse.urlencode({"token_id": token_id})
        book_status, book = _get_json(url, timeout)
        if book_status != 200 or not isinstance(book, dict):
            raise RuntimeError("POLYMARKET_BOOK_INVALID")
        levels = len(book.get("bids") or []) + len(book.get("asks") or [])
        return FeedObservation("POLYMARKET", url, now, book_status, levels > 0, _digest(book), market_id, levels)
    except Exception as exc:
        return FeedObservation("POLYMARKET", gamma, now, _status_from_error(exc), False, None, error=str(exc))


def limitless_public_book(timeout: float = 12.0) -> FeedObservation:
    now = int(time.time() * 1000)
    active = "https://api.limitless.exchange/markets/active?limit=25&page=1"
    try:
        status, payload = _get_json(active, timeout)
        rows = payload.get("data") if isinstance(payload, dict) else None
        if status != 200 or not isinstance(rows, list):
            raise RuntimeError("LIMITLESS_MARKET_LIST_INVALID")
        last_error: Exception | None = None
        for market in rows:
            slug = str(market.get("slug") or "") if isinstance(market, dict) else ""
            if not slug:
                continue
            url = "https://api.limitless.exchange/markets/" + urllib.parse.quote(slug, safe="") + "/orderbook"
            try:
                book_status, book = _get_json(url, timeout)
                if book_status != 200 or not isinstance(book, dict):
                    continue
                levels = len(book.get("bids") or []) + len(book.get("asks") or [])
                if levels:
                    return FeedObservation("LIMITLESS", url, now, book_status, True, _digest(book), slug, levels)
            except Exception as exc:
                last_error = exc
        if last_error:
            raise last_error
        raise RuntimeError("LIMITLESS_NO_PUBLIC_BOOK_WITH_DEPTH")
    except Exception as exc:
        return FeedObservation("LIMITLESS", active, now, _status_from_error(exc), False, None, error=str(exc))


def _status_from_error(exc: Exception) -> int:
    text = str(exc)
    if text.startswith("HTTP_"):
        try:
            return int(text.split(":", 1)[0].split("_", 1)[1])
        except ValueError:
            pass
    return 0


def public_feed_smoke() -> dict[str, Any]:
    poly = polymarket_public_book()
    lmts = limitless_public_book()
    return {
        "schema_version": "SIBYL_V6_PUBLIC_FEEDS_V1",
        "observed_at_ms": int(time.time() * 1000),
        "polymarket": poly.to_dict(),
        "limitless": lmts.to_dict(),
        "status": "PASS" if poly.ok and lmts.ok else "FAIL",
    }
