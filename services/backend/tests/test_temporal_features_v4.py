from app.market_data_v3 import V3Event
from app.temporal_features_v4 import build_temporal_features, response_lag_ms


def _trade(source: str, ts: int, price: float, size: float, side: str) -> V3Event:
    return V3Event(
        source=source,
        event_type="TRADE",
        source_timestamp_ms=ts,
        receive_timestamp_ms=ts,
        price=price,
        size=size,
        aggressor_side=side,
    )


def test_features_reach_minute_horizons_from_persisted_history() -> None:
    now = 2_000_000
    events = (
        _trade("BINANCE", now - 1_800_000, 90.0, 1.0, "SELL"),
        _trade("BINANCE", now - 600_000, 95.0, 2.0, "BUY"),
        _trade("BINANCE", now - 300_000, 98.0, 1.0, "BUY"),
        _trade("BINANCE", now - 60_000, 99.0, 3.0, "SELL"),
        _trade("BINANCE", now, 100.0, 5.0, "BUY"),
        _trade("COINBASE", now - 60_000, 99.1, 1.0, "BUY"),
        _trade("COINBASE", now, 100.1, 1.0, "BUY"),
        _trade("BINANCE_FUTURES", now - 60_000, 99.2, 1.0, "BUY"),
        _trade("BINANCE_FUTURES", now, 100.2, 1.0, "BUY"),
    )
    result = build_temporal_features(events)
    returns = result["sources"]["BINANCE"]["returns_bps"]
    assert returns["1m"] is not None
    assert returns["5m"] is not None
    assert returns["10m"] is not None
    assert returns["30m"] is not None
    assert result["spot_futures_basis_bps"] is not None
    assert result["binance_coinbase_divergence_bps"] is not None
    assert result["horizons"] == ("1s", "5s", "10s", "1m", "5m", "10m", "30m")


def test_response_lag_requires_responder_move_after_driver() -> None:
    driver = [
        _trade("BINANCE", 1_000, 100.0, 1.0, "BUY"),
        _trade("BINANCE", 2_000, 100.1, 1.0, "BUY"),
    ]
    responder = [
        _trade("COINBASE", 1_000, 100.0, 1.0, "BUY"),
        _trade("COINBASE", 2_500, 100.1, 1.0, "BUY"),
    ]
    assert response_lag_ms(driver, responder, driver_move_bps=5, responder_move_bps=5) == 500
