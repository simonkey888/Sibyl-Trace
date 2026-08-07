from app.market_data_v4 import normalize_polymarket_v4


def test_book_message_preserves_full_l2_snapshot() -> None:
    events = normalize_polymarket_v4(
        {
            "event_type": "book",
            "asset_id": "YES",
            "timestamp": "1000",
            "bids": [
                {"price": "0.49", "size": "10"},
                {"price": "0.48", "size": "8"},
            ],
            "asks": [
                {"price": "0.51", "size": "7"},
                {"price": "0.52", "size": "6"},
            ],
        },
        received_ms=1010,
    )
    assert len(events) == 1
    event = events[0]
    assert event.kind == "SNAPSHOT"
    assert event.sequence is None
    assert [(level.side, level.price, level.size) for level in event.levels] == [
        ("BID", 0.49, 10.0),
        ("BID", 0.48, 8.0),
        ("ASK", 0.51, 7.0),
        ("ASK", 0.52, 6.0),
    ]


def test_price_change_zero_size_is_a_level_removal_delta() -> None:
    events = normalize_polymarket_v4(
        {
            "event_type": "price_change",
            "timestamp": "1100",
            "price_changes": [
                {"asset_id": "YES", "price": "0.49", "size": "0", "side": "BUY"},
                {"asset_id": "YES", "price": "0.50", "size": "12", "side": "BUY"},
            ],
        },
        received_ms=1110,
    )
    assert len(events) == 2
    assert events[0].kind == "DELTA"
    assert events[0].levels[0].size == 0.0
    assert events[1].levels[0].price == 0.50


def test_last_trade_price_becomes_trade_tape_event() -> None:
    events = normalize_polymarket_v4(
        {
            "event_type": "last_trade_price",
            "asset_id": "YES",
            "timestamp": "1200",
            "price": "0.505",
            "size": "3.5",
            "side": "BUY",
        },
        received_ms=1210,
    )
    assert len(events) == 1
    assert events[0].kind == "TRADE"
    assert events[0].trade_price == 0.505
    assert events[0].trade_size == 3.5
    assert events[0].aggressor_side == "BUY"


def test_list_payload_preserves_source_order_without_fake_sequence() -> None:
    events = normalize_polymarket_v4(
        [
            {
                "event_type": "book",
                "asset_id": "YES",
                "timestamp": "1000",
                "bids": [{"price": "0.49", "size": "10"}],
                "asks": [{"price": "0.51", "size": "10"}],
            },
            {
                "event_type": "price_change",
                "timestamp": "1000",
                "price_changes": [
                    {"asset_id": "YES", "price": "0.49", "size": "8", "side": "BUY"}
                ],
            },
        ],
        received_ms=1010,
    )
    assert [event.kind for event in events] == ["SNAPSHOT", "DELTA"]
    assert all(event.sequence is None for event in events)
