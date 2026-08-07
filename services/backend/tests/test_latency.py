import pytest

from app.latency import (
    CaptureResult,
    FeedEvent,
    LatencyTarget,
    analyze_latency_opportunities,
    detect_consensus_impulses,
    parse_binance_message,
    parse_coinbase_message,
    parse_polymarket_message,
)


def target() -> LatencyTarget:
    return LatencyTarget(
        condition_id="0x" + "1" * 64,
        question="BTC Up or Down - test",
        end_timestamp_ms=10_000,
        outcome_assets={"Up": "up-asset", "Down": "down-asset"},
        fee_rate=0.07,
        tick_size=0.01,
    )


def test_public_feed_parsers_preserve_source_and_receive_timestamps() -> None:
    binance = parse_binance_message(
        {"e": "aggTrade", "T": 1000, "E": 1001, "p": "65000", "a": 7},
        1100,
    )
    coinbase = parse_coinbase_message(
        {
            "type": "ticker",
            "product_id": "BTC-USD",
            "price": "65001",
            "best_bid": "65000",
            "best_ask": "65002",
            "time": "2026-08-07T00:00:01Z",
            "sequence": 8,
        },
        1200,
    )
    polymarket = parse_polymarket_message(
        {
            "event_type": "book",
            "timestamp": "1300",
            "asset_id": "up-asset",
            "bids": [{"price": "0.49", "size": "8"}],
            "asks": [{"price": "0.51", "size": "7"}],
        },
        1400,
    )[0]
    assert binance and binance.source == "BINANCE" and binance.receive_timestamp_ms == 1100
    assert coinbase and coinbase.source == "COINBASE" and coinbase.bid == 65000
    assert polymarket.source == "POLYMARKET"
    assert polymarket.ask == 0.51
    assert polymarket.ask_size == 7


def test_consensus_impulse_requires_both_exchanges_same_direction() -> None:
    events = (
        FeedEvent("BINANCE", 1000, 1000, price=100.0),
        FeedEvent("COINBASE", 1000, 1000, price=100.0),
        FeedEvent("BINANCE", 2000, 2000, price=100.1),
        FeedEvent("COINBASE", 2000, 2000, price=100.1),
    )
    triggers = detect_consensus_impulses(events, threshold_bps=2)
    assert triggers
    assert triggers[-1][1] == "UP"
    assert triggers[-1][2] == pytest.approx(10.0)


def test_disagreeing_exchanges_do_not_create_latency_signal() -> None:
    events = (
        FeedEvent("BINANCE", 1000, 1000, price=100.0),
        FeedEvent("COINBASE", 1000, 1000, price=100.0),
        FeedEvent("BINANCE", 2000, 2000, price=100.1),
        FeedEvent("COINBASE", 2000, 2000, price=99.9),
    )
    assert detect_consensus_impulses(events, threshold_bps=2) == []


def test_executable_edge_requires_depth_bid_ask_and_fees() -> None:
    events = (
        FeedEvent("BINANCE", 1000, 1000, price=100.0),
        FeedEvent("COINBASE", 1000, 1000, price=100.0),
        FeedEvent(
            "POLYMARKET",
            1900,
            1900,
            bid=0.49,
            ask=0.50,
            bid_size=20,
            ask_size=20,
            asset_id="up-asset",
        ),
        FeedEvent("BINANCE", 2000, 2000, price=100.1),
        FeedEvent("COINBASE", 2000, 2000, price=100.1),
        FeedEvent(
            "POLYMARKET",
            2250,
            2250,
            bid=0.56,
            ask=0.57,
            bid_size=20,
            ask_size=20,
            asset_id="up-asset",
        ),
    )
    result = analyze_latency_opportunities(target(), CaptureResult(events, ()), requested_shares=5)
    assert result
    opportunity = result[-1]
    assert opportunity.entry_ask == 0.50
    assert opportunity.exit_bid == 0.56
    assert opportunity.net_edge_per_share is not None
    assert opportunity.net_edge_per_share > 0
    assert opportunity.executable


def test_midpoint_like_quote_without_depth_never_counts_as_executable() -> None:
    events = (
        FeedEvent("BINANCE", 1000, 1000, price=100.0),
        FeedEvent("COINBASE", 1000, 1000, price=100.0),
        FeedEvent("POLYMARKET", 1900, 1900, bid=0.49, ask=0.50, asset_id="up-asset"),
        FeedEvent("BINANCE", 2000, 2000, price=100.1),
        FeedEvent("COINBASE", 2000, 2000, price=100.1),
        FeedEvent("POLYMARKET", 2250, 2250, bid=0.60, ask=0.61, asset_id="up-asset"),
    )
    result = analyze_latency_opportunities(target(), CaptureResult(events, ()), requested_shares=5)
    assert result
    assert not result[-1].executable
