from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.source_strategy import (
    DIRECTIONAL_CANDIDATE,
    SourceStrategyPolicy,
    classify_source_strategy,
    fetch_public_activity_events,
)

WALLET = "0x" + "c" * 40


def _event(event_type: str, timestamp: object, suffix: str = "1") -> dict:
    return {
        "type": event_type,
        "timestamp": timestamp,
        "transactionHash": f"0x{event_type.lower()}-{suffix}",
        "conditionId": f"condition-{suffix}",
        "asset": f"asset-{suffix}",
        "side": "BUY",
        "outcomeIndex": 0,
        "outcome": "Yes",
        "price": 0.5,
        "size": 2,
        "usdcSize": 1,
    }


def test_activity_fetch_zero_limit_does_not_call_remote() -> None:
    class Client:
        settings = SimpleNamespace(data_api_base="https://data-api.polymarket.com")

        def _get(self, _url: str, _params: dict):
            raise AssertionError("zero-limit fetch must not call remote")

    assert fetch_public_activity_events(Client(), WALLET, cutoff_at=1_000, limit=0) == []


def test_activity_fetch_rejects_non_list_shape() -> None:
    class Client:
        settings = SimpleNamespace(data_api_base="https://data-api.polymarket.com")

        def _get(self, _url: str, _params: dict):
            return {"unexpected": "shape"}

    with pytest.raises(ValueError, match="public_activity_response_not_list"):
        fetch_public_activity_events(Client(), WALLET, cutoff_at=1_000, limit=10)


def test_activity_fetch_fails_closed_on_bad_rows_and_deduplicates() -> None:
    valid = _event("TRADE", 900)

    class Client:
        settings = SimpleNamespace(data_api_base="https://data-api.polymarket.com")

        def _get(self, _url: str, _params: dict):
            return [valid, dict(valid), "not-a-row", _event("TRADE", "bad", "2")]

    rows = fetch_public_activity_events(Client(), WALLET, cutoff_at=1_000, limit=10)
    assert rows == [valid]


def test_taker_rebate_is_counted_without_becoming_directional_alpha() -> None:
    events = [_event("TRADE", 900, str(index)) for index in range(5)]
    events.append(_event("TAKER_REBATE", 950, "rebate"))
    profile = classify_source_strategy(
        WALLET,
        events,
        cutoff_at=1_000,
        policy=SourceStrategyPolicy(min_trade_count=5),
    )
    assert profile.taker_rebate_count == 1
    assert profile.maker_rebate_count == 0
    assert profile.classification == DIRECTIONAL_CANDIDATE
