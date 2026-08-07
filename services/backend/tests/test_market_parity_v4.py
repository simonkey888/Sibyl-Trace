from app.kalshi_v4 import KalshiReadOnlyVenue, normalize_kalshi_orderbook
from app.market_identity_v4 import MarketContract, compare_contracts
from app.parity_v4 import compare_binary_parity
from app.venue_v3 import NormalizedBook, PriceLevel


def _contract(venue: str, market_id: str, *, rule: str = "official close") -> MarketContract:
    return MarketContract(
        venue=venue,
        market_id=market_id,
        title="Will BTC close above 100000 on Friday?",
        underlying="BTCUSD",
        event="BTC Friday close",
        outcome="above 100000",
        strike="100000",
        cutoff_iso="2026-08-07T20:00:00Z",
        timezone="UTC",
        resolution_source="official index",
        resolution_rule=rule,
        exceptions=(),
    )


def test_resolution_mismatch_blocks_parity_even_with_same_title() -> None:
    decision = compare_contracts(
        _contract("POLYMARKET", "p1"),
        _contract("KALSHI", "k1", rule="five minute average"),
    )
    assert decision.decision == "NON_EQUIVALENT"
    assert "resolution_rule" in decision.mismatches


def test_kalshi_yes_book_uses_no_bid_as_complementary_yes_ask() -> None:
    book = normalize_kalshi_orderbook(
        {
            "orderbook_fp": {
                "yes_dollars": [["0.5500", "10.00"], ["0.5400", "5.00"]],
                "no_dollars": [["0.4000", "8.00"], ["0.3900", "6.00"]],
            }
        },
        ticker="KXBTC",
        receive_timestamp_ms=100,
    )
    assert book.best_bid == PriceLevel(0.55, 10.0)
    assert book.best_ask == PriceLevel(0.60, 8.0)
    assert KalshiReadOnlyVenue.capabilities.order_placement is False
    assert KalshiReadOnlyVenue.capabilities.private_account_data is False


def test_exact_identity_allows_cost_adjusted_parity_only() -> None:
    identity = compare_contracts(_contract("POLYMARKET", "p1"), _contract("KALSHI", "k1"))
    assert identity.decision == "EXACT_EQUIVALENT"
    left = NormalizedBook(
        venue="POLYMARKET",
        asset_id="p1",
        bids=(PriceLevel(0.50, 20.0),),
        asks=(PriceLevel(0.52, 10.0),),
        source_timestamp_ms=None,
        receive_timestamp_ms=1,
    )
    right = NormalizedBook(
        venue="KALSHI",
        asset_id="k1",
        bids=(PriceLevel(0.56, 7.0),),
        asks=(PriceLevel(0.58, 10.0),),
        source_timestamp_ms=None,
        receive_timestamp_ms=1,
    )
    result = compare_binary_parity(
        left,
        right,
        identity,
        left_fee_rate=0.01,
        right_fee_rate=0.01,
        settlement_penalty=0.005,
    )
    assert result.status == "POSITIVE"
    assert result.direction == "BUY_LEFT_SELL_RIGHT"
    assert result.max_size == 7.0
    assert result.net_edge is not None and result.net_edge > 0
