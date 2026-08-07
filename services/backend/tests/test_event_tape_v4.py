from app.event_tape_v4 import (
    TapeEvent,
    TapeLevel,
    queue_ahead_at_price,
    reconstruct_l2,
    snapshot_event,
)
from app.venue_v3 import NormalizedBook, PriceLevel


def _book() -> NormalizedBook:
    return NormalizedBook(
        venue="POLYMARKET",
        asset_id="YES",
        bids=(PriceLevel(0.50, 10.0), PriceLevel(0.49, 5.0)),
        asks=(PriceLevel(0.52, 8.0), PriceLevel(0.53, 7.0)),
        source_timestamp_ms=100,
        receive_timestamp_ms=110,
    )


def test_snapshot_delta_reconstructs_full_depth_and_surfaces_gap() -> None:
    snapshot = snapshot_event(_book(), sequence=10)
    delta = TapeEvent(
        schema_version=1,
        venue="POLYMARKET",
        asset_id="YES",
        kind="DELTA",
        source_timestamp_ms=120,
        receive_timestamp_ms=130,
        sequence=12,
        levels=(
            TapeLevel("BID", 0.50, 6.0),
            TapeLevel("BID", 0.48, 4.0),
            TapeLevel("ASK", 0.52, 0.0),
            TapeLevel("ASK", 0.515, 3.0),
        ),
    )
    result = reconstruct_l2((delta, snapshot))
    assert result.status == "DEGRADED"
    assert result.gaps == ("sequence_gap:10->12",)
    assert result.book is not None
    assert [level.price for level in result.book.bids] == [0.50, 0.49, 0.48]
    assert [level.price for level in result.book.asks] == [0.515, 0.53]
    assert queue_ahead_at_price(result.book, side="BID", price=0.50) == 6.0


def test_delta_before_snapshot_is_never_treated_as_book() -> None:
    delta = TapeEvent(
        schema_version=1,
        venue="POLYMARKET",
        asset_id="YES",
        kind="DELTA",
        receive_timestamp_ms=100,
        sequence=1,
        levels=(TapeLevel("BID", 0.50, 10.0),),
    )
    result = reconstruct_l2((delta,))
    assert result.status == "UNSEEDED"
    assert result.book is None
    assert "delta_before_snapshot" in result.gaps
