from typing import Any

import pytest

from app.config import Settings
from app.polymarket import PolymarketClient, PolymarketError, taker_fee_rate_for_category


def test_activity_never_exceeds_current_offset_budget() -> None:
    client = PolymarketClient(Settings())
    offsets: list[int] = []

    def fake_get(_url: str, params: dict[str, Any]) -> list[dict]:
        offsets.append(int(params["offset"]))
        offset = int(params["offset"])
        return [
            {
                "transactionHash": f"tx-{offset + index}",
                "asset": "asset",
                "side": "BUY",
                "timestamp": offset + index,
            }
            for index in range(int(params["limit"]))
        ]

    client._get = fake_get
    try:
        rows = client.activity("0x" + "1" * 40, start=0, limit=10000)
    finally:
        client.close()

    assert offsets == list(range(0, 5000, 500))
    assert max(offsets) == 4500
    assert len(rows) == 5000


def test_closed_position_research_pages_timestamp_desc_explicitly() -> None:
    client = PolymarketClient(Settings())
    calls: list[dict[str, Any]] = []

    def fake_get(_url: str, params: dict[str, Any]) -> list[dict]:
        calls.append(params)
        return []

    client._get = fake_get
    try:
        assert client.research_closed_positions("0x" + "2" * 40, limit=1000) == []
    finally:
        client.close()

    assert calls[0]["sortBy"] == "TIMESTAMP"
    assert calls[0]["sortDirection"] == "DESC"
    assert calls[0]["limit"] <= 50


def test_btc_market_discovery_uses_documented_time_window_and_filters() -> None:
    client = PolymarketClient(Settings())
    captured: dict[str, Any] = {}

    def fake_get(_url: str, params: dict[str, Any]) -> list[dict]:
        captured.update(params)
        return [
            {
                "question": "Bitcoin Up or Down - 5 Minutes",
                "slug": "bitcoin-up-or-down-5m",
                "active": True,
                "closed": False,
                "acceptingOrders": True,
            },
            {
                "question": "Ethereum Up or Down - 5 Minutes",
                "slug": "eth-up-or-down-5m",
                "active": True,
                "closed": False,
                "acceptingOrders": True,
            },
        ]

    client._get = fake_get
    try:
        rows = client.active_btc_short_markets(horizon_minutes=20)
    finally:
        client.close()

    assert captured["closed"] is False
    assert captured["ascending"] is True
    assert "end_date_min" in captured and "end_date_max" in captured
    assert len(rows) == 1
    assert "Bitcoin" in rows[0]["question"]


def test_current_public_fee_table_is_explicit_by_category() -> None:
    assert taker_fee_rate_for_category("Crypto") == pytest.approx(0.07)
    assert taker_fee_rate_for_category("Sports") == pytest.approx(0.03)
    assert taker_fee_rate_for_category("Weather") == pytest.approx(0.05)
    assert taker_fee_rate_for_category("Geopolitics") == 0
    assert taker_fee_rate_for_category(None) == pytest.approx(0.05)


def test_order_book_fails_closed_on_asset_mismatch() -> None:
    client = PolymarketClient(Settings())
    client._get = lambda *_args, **_kwargs: {"asset_id": "wrong", "bids": [], "asks": []}
    try:
        with pytest.raises(PolymarketError):
            client.order_book("expected")
    finally:
        client.close()


def test_fee_rate_endpoint_is_kept_as_contract_metadata() -> None:
    client = PolymarketClient(Settings())
    client._get = lambda *_args, **_kwargs: {"base_fee": 30}
    try:
        assert client.fee_rate_bps("asset") == 30
    finally:
        client.close()
