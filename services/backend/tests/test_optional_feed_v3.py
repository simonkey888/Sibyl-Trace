from __future__ import annotations

from app.market_data_v3 import V3Event, annotate_optional_feed_gaps


def _event(source: str) -> V3Event:
    return V3Event(
        source=source,
        event_type="TRADE",
        source_timestamp_ms=100,
        receive_timestamp_ms=110,
        price=100.0,
    )


def test_missing_optional_futures_feed_is_explicit() -> None:
    errors = annotate_optional_feed_gaps(
        [_event("BINANCE"), _event("COINBASE")],
        [],
        include_futures=True,
    )
    assert errors == ["BINANCE_FUTURES:NO_EVENTS"]


def test_present_optional_futures_feed_needs_no_gap_marker() -> None:
    errors = annotate_optional_feed_gaps(
        [_event("BINANCE_FUTURES")],
        [],
        include_futures=True,
    )
    assert errors == []


def test_existing_optional_futures_error_is_not_duplicated() -> None:
    errors = annotate_optional_feed_gaps(
        [],
        ["BINANCE_FUTURES:TimeoutError:timed out"],
        include_futures=True,
    )
    assert errors == ["BINANCE_FUTURES:TimeoutError:timed out"]
