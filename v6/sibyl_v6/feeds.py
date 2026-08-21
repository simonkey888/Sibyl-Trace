from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Callable

from .probe import TARGETS, _probe


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
    source_timestamp_ms: int | None = None
    source_age_ms: int | None = None
    staleness_status: str = "UNKNOWN"
    reconnect_attempts: int = 0
    recovered_after_reconnect: bool = False
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class WebSocketTelemetry:
    venue: str
    endpoint: str
    attempts: int
    successful_handshakes: int
    reconnect_attempts: int
    recovered_after_reconnect: bool
    statuses: tuple[int, ...]
    connect_ms: tuple[float, ...]
    tls_ms: tuple[float, ...]
    ttfb_ms: tuple[float, ...]
    ws_connect_ms: tuple[float, ...]
    errors: tuple[str, ...]

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


def _timestamp_ms(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            try:
                parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            except ValueError:
                return None
            return int(parsed.timestamp() * 1000)
    if number <= 0:
        return None
    # Normalize seconds/milliseconds/microseconds without inventing a timestamp.
    if number < 10_000_000_000:
        number *= 1000
    elif number > 10_000_000_000_000:
        number /= 1000
    return int(number)


def _source_timestamp_ms(payload: Any) -> int | None:
    if not isinstance(payload, dict):
        return None
    for key in (
        "timestamp",
        "ts",
        "updatedAt",
        "updated_at",
        "lastUpdated",
        "last_updated",
    ):
        parsed = _timestamp_ms(payload.get(key))
        if parsed is not None:
            return parsed
    return None


def _freshness(source_timestamp_ms: int | None, observed_at_ms: int, max_age_ms: int) -> tuple[int | None, str]:
    if source_timestamp_ms is None:
        return None, "UNKNOWN"
    age = observed_at_ms - source_timestamp_ms
    if age < 0:
        return age, "CLOCK_SKEW"
    return age, "FRESH" if age <= max_age_ms else "STALE"


def _with_reconnect(
    fetcher: Callable[[float], FeedObservation],
    *,
    timeout: float,
    max_reconnects: int = 1,
) -> FeedObservation:
    first: FeedObservation | None = None
    for attempt in range(max_reconnects + 1):
        observation = fetcher(timeout)
        if first is None:
            first = observation
        if observation.ok:
            return FeedObservation(
                **{
                    **observation.to_dict(),
                    "reconnect_attempts": attempt,
                    "recovered_after_reconnect": attempt > 0,
                }
            )
    assert first is not None
    return FeedObservation(
        **{
            **first.to_dict(),
            "reconnect_attempts": max_reconnects,
            "recovered_after_reconnect": False,
        }
    )


def polymarket_public_book(timeout: float = 12.0) -> FeedObservation:
    observed_at = int(time.time() * 1000)
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
        source_ts = _source_timestamp_ms(book)
        age, stale = _freshness(source_ts, observed_at, 15_000)
        return FeedObservation(
            "POLYMARKET",
            url,
            observed_at,
            book_status,
            levels > 0,
            _digest(book),
            market_id,
            levels,
            source_ts,
            age,
            stale,
        )
    except Exception as exc:
        return FeedObservation(
            "POLYMARKET",
            gamma,
            observed_at,
            _status_from_error(exc),
            False,
            None,
            error=str(exc),
        )


def limitless_public_book(timeout: float = 12.0) -> FeedObservation:
    observed_at = int(time.time() * 1000)
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
            url = (
                "https://api.limitless.exchange/markets/"
                + urllib.parse.quote(slug, safe="")
                + "/orderbook"
            )
            try:
                book_status, book = _get_json(url, timeout)
                if book_status != 200 or not isinstance(book, dict):
                    continue
                levels = len(book.get("bids") or []) + len(book.get("asks") or [])
                if levels:
                    source_ts = _source_timestamp_ms(book)
                    age, stale = _freshness(source_ts, observed_at, 15_000)
                    return FeedObservation(
                        "LIMITLESS",
                        url,
                        observed_at,
                        book_status,
                        True,
                        _digest(book),
                        slug,
                        levels,
                        source_ts,
                        age,
                        stale,
                    )
            except Exception as exc:
                last_error = exc
        if last_error:
            raise last_error
        raise RuntimeError("LIMITLESS_NO_PUBLIC_BOOK_WITH_DEPTH")
    except Exception as exc:
        return FeedObservation(
            "LIMITLESS",
            active,
            observed_at,
            _status_from_error(exc),
            False,
            None,
            error=str(exc),
        )


def _ws_telemetry(venue: str, target_name: str, attempts: int = 2) -> WebSocketTelemetry:
    statuses: list[int] = []
    connects: list[float] = []
    tls_values: list[float] = []
    ttfb_values: list[float] = []
    ws_values: list[float] = []
    errors: list[str] = []
    first_success: int | None = None
    endpoint = TARGETS[target_name]
    for index in range(attempts):
        try:
            connect, tls, ttfb, ws_connect, status = _probe(endpoint)
            statuses.append(status)
            connects.append(connect)
            tls_values.append(tls)
            ttfb_values.append(ttfb)
            if ws_connect is not None:
                ws_values.append(ws_connect)
            if status == 101 and first_success is None:
                first_success = index
        except Exception as exc:
            errors.append(str(exc))
    successful = sum(status == 101 for status in statuses)
    return WebSocketTelemetry(
        venue=venue,
        endpoint=endpoint,
        attempts=attempts,
        successful_handshakes=successful,
        reconnect_attempts=max(0, attempts - 1),
        recovered_after_reconnect=first_success is not None and first_success > 0,
        statuses=tuple(statuses),
        connect_ms=tuple(connects),
        tls_ms=tuple(tls_values),
        ttfb_ms=tuple(ttfb_values),
        ws_connect_ms=tuple(ws_values),
        errors=tuple(errors),
    )


def _status_from_error(exc: Exception) -> int:
    text = str(exc)
    if text.startswith("HTTP_"):
        try:
            return int(text.split(":", 1)[0].split("_", 1)[1])
        except ValueError:
            pass
    return 0


def public_feed_smoke() -> dict[str, Any]:
    poly = _with_reconnect(polymarket_public_book, timeout=12.0)
    lmts = _with_reconnect(limitless_public_book, timeout=12.0)
    poly_ws = _ws_telemetry("POLYMARKET", "polymarket_ws")
    lmts_ws = _ws_telemetry("LIMITLESS", "limitless_ws")
    return {
        "schema_version": "SIBYL_V6_PUBLIC_FEEDS_V2",
        "evidence_class": "REAL_PUBLIC_VENUE_FEEDS_NOT_FIXTURES",
        "observed_at_ms": int(time.time() * 1000),
        "polymarket": poly.to_dict(),
        "limitless": lmts.to_dict(),
        "websocket_telemetry": {
            "polymarket": poly_ws.to_dict(),
            "limitless": lmts_ws.to_dict(),
        },
        "staleness_semantics": "SOURCE_TIMESTAMP_UNKNOWN_IS_NEVER_INFERRED_FRESH",
        "status": "PASS" if poly.ok and lmts.ok else "FAIL",
    }
