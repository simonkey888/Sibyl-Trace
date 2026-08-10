from typing import Any

from app.config import Settings
from app.polymarket import PolymarketClient, PolymarketError


def client_with_payload(payload: Any) -> PolymarketClient:
    client = PolymarketClient(Settings())
    client._get = lambda *_args, **_kwargs: payload
    return client


def test_midpoint_uses_current_mid_price_contract() -> None:
    client = client_with_payload({"mid_price": "0.45"})
    try:
        assert client.midpoint("asset") == 0.45
    finally:
        client.close()


def test_midpoint_keeps_legacy_fallback() -> None:
    client = client_with_payload({"mid": "0.52"})
    try:
        assert client.midpoint("asset") == 0.52
    finally:
        client.close()


def test_midpoint_fails_closed_without_price() -> None:
    client = client_with_payload({})
    try:
        try:
            client.midpoint("asset")
        except PolymarketError as error:
            assert "did not contain" in str(error)
        else:
            raise AssertionError("missing midpoint must fail closed")
    finally:
        client.close()


def test_closed_positions_are_requested_newest_first_for_short_horizon() -> None:
    client = PolymarketClient(Settings())
    calls: list[dict[str, Any]] = []

    def fake_get(url: str, params: dict[str, Any]) -> list[dict]:
        calls.append({"url": url, **params})
        return [{"realizedPnl": "1", "timestamp": 1234}]

    client._get = fake_get
    try:
        rows = client.closed_positions("0x" + "a" * 40, limit=50)
    finally:
        client.close()

    assert len(rows) == 1
    assert len(calls) == 1
    assert calls[0]["url"].endswith("/closed-positions")
    assert calls[0]["limit"] == 50
    assert calls[0]["offset"] == 0
    assert calls[0]["sortBy"] == "TIMESTAMP"
    assert calls[0]["sortDirection"] == "DESC"


def test_activity_paginates_with_documented_offset_and_deduplicates() -> None:
    client = PolymarketClient(Settings())
    calls: list[dict[str, Any]] = []

    def fake_get(_url: str, params: dict[str, Any]) -> list[dict]:
        calls.append(params)
        offset = int(params["offset"])
        if offset == 0:
            return [
                {
                    "transactionHash": f"tx-{index}",
                    "asset": "asset",
                    "side": "BUY",
                    "timestamp": 1000 + index,
                }
                for index in range(500)
            ]
        return [
            {
                "transactionHash": "tx-499",
                "asset": "asset",
                "side": "BUY",
                "timestamp": 1499,
            },
            {
                "transactionHash": "tx-500",
                "asset": "asset",
                "side": "BUY",
                "timestamp": 1500,
            },
        ]

    client._get = fake_get
    try:
        activity = client.activity("0x" + "1" * 40, start=900, limit=700)
    finally:
        client.close()

    assert [call["offset"] for call in calls] == [0, 500]
    assert [call["limit"] for call in calls] == [500, 200]
    assert len(activity) == 501
    assert activity[-1]["transactionHash"] == "tx-500"


def test_activity_stops_after_short_page() -> None:
    client = PolymarketClient(Settings())
    offsets: list[int] = []

    def fake_get(_url: str, params: dict[str, Any]) -> list[dict]:
        offsets.append(int(params["offset"]))
        return [
            {
                "transactionHash": "tx-1",
                "asset": "asset",
                "side": "SELL",
                "timestamp": 10,
            }
        ]

    client._get = fake_get
    try:
        activity = client.activity("0x" + "2" * 40, start=0, limit=2000)
    finally:
        client.close()

    assert offsets == [0]
    assert len(activity) == 1


def test_closed_markets_uses_condition_filter_and_accepts_keyset_shape() -> None:
    client = PolymarketClient(Settings())
    captured: dict[str, Any] = {}

    def fake_get(url: str, params: dict[str, Any]) -> dict:
        captured["url"] = url
        captured["params"] = params
        return {
            "markets": [
                {
                    "conditionId": "condition",
                    "closed": True,
                    "clobTokenIds": '["asset", "other"]',
                    "outcomePrices": '["1", "0"]',
                }
            ],
            "next_cursor": "LTE=",
        }

    client._get = fake_get
    try:
        markets = client.closed_markets(["condition", "condition", ""])
    finally:
        client.close()

    assert captured["url"].endswith("/markets")
    assert captured["params"]["condition_ids"] == ["condition"]
    assert captured["params"]["closed"] == "true"
    assert markets[0]["conditionId"] == "condition"


def test_closed_markets_accepts_legacy_list_shape() -> None:
    client = client_with_payload([{"conditionId": "condition", "closed": True}])
    try:
        markets = client.closed_markets(["condition"])
    finally:
        client.close()

    assert len(markets) == 1
