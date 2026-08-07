from app.journal_v3 import downsample_bucket_key
from app.market_data_v3 import V3Event


def test_book_and_trade_in_same_second_do_not_collide() -> None:
    book = V3Event(
        source="POLYMARKET",
        event_type="BOOK",
        source_timestamp_ms=1_100,
        receive_timestamp_ms=1_150,
        bid=0.49,
        ask=0.51,
        asset_id="YES",
    )
    trade = V3Event(
        source="POLYMARKET",
        event_type="TRADE",
        source_timestamp_ms=1_200,
        receive_timestamp_ms=1_250,
        price=0.50,
        size=2.0,
        aggressor_side="BUY",
        asset_id="YES",
    )
    assert downsample_bucket_key(book, bucket_ms=1_000) != downsample_bucket_key(
        trade, bucket_ms=1_000
    )
