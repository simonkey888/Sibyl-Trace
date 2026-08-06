from app.config import Settings
from app.polymarket import PolymarketClient, PolymarketError


def client_with_payload(payload: dict) -> PolymarketClient:
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
