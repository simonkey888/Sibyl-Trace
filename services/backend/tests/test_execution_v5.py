from __future__ import annotations

import pytest

from app.execution_v5 import (
    best_executable_price,
    market_rules_from_clob_info,
    simulate_fak_fill,
    taker_fee_usd,
    worst_price_limit,
)


def book(*, bids=(), asks=(), hash_value="h1", timestamp="1000"):
    return {
        "hash": hash_value,
        "timestamp": timestamp,
        "bids": [{"price": str(price), "size": str(size)} for price, size in bids],
        "asks": [{"price": str(price), "size": str(size)} for price, size in asks],
    }


def test_fee_formula_matches_current_polymarket_taker_schedule() -> None:
    assert taker_fee_usd(100, 0.5, 0.07) == pytest.approx(1.75)
    assert taker_fee_usd(100, 0.9, 0.07) == pytest.approx(0.63)
    assert taker_fee_usd(100, 0.5, 0.0) == 0


def test_market_rules_fail_closed_without_supported_per_market_fee_schedule() -> None:
    rules = market_rules_from_clob_info(
        {"mts": "0.01", "mos": "5", "fd": {"r": "0.07", "e": 1, "to": True}}
    )
    assert rules.tick_size == pytest.approx(0.01)
    assert rules.minimum_order_size == pytest.approx(5)
    assert rules.fee_rate == pytest.approx(0.07)
    assert rules.order_delay_ms == 0

    delayed = market_rules_from_clob_info(
        {
            "mts": "0.001",
            "mos": "1",
            "itode": True,
            "fd": {"r": "0.07", "e": 1, "to": True},
        }
    )
    assert delayed.order_delay_ms == 250

    with pytest.raises(ValueError, match="fee_schedule_unavailable"):
        market_rules_from_clob_info({"mts": "0.01", "mos": "5"})
    with pytest.raises(ValueError, match="unsupported_fee_schedule"):
        market_rules_from_clob_info(
            {"mts": "0.01", "mos": "5", "fd": {"r": "0.07", "e": 2, "to": True}}
        )


def test_best_executable_price_uses_ask_for_buy_and_bid_for_sell() -> None:
    current = book(bids=[(0.46, 20), (0.47, 10)], asks=[(0.52, 10), (0.51, 5)])
    assert best_executable_price(current, "BUY") == pytest.approx(0.51)
    assert best_executable_price(current, "SELL") == pytest.approx(0.47)


def test_buy_fak_consumes_asks_in_price_order_and_includes_fees() -> None:
    current = book(asks=[(0.52, 4), (0.50, 5), (0.51, 5)])
    fill = simulate_fak_fill(
        current,
        side="BUY",
        fee_rate=0.07,
        minimum_order_size=1,
        worst_price=0.51,
        requested_usd=5.2,
    )
    assert fill.status in {"FILLED", "PARTIAL_FILLED"}
    assert fill.filled_shares > 9
    assert fill.levels_consumed == 2
    assert fill.average_fill_price is not None
    assert 0.50 <= fill.average_fill_price <= 0.51
    assert fill.fee_usd > 0
    assert fill.net_cash_delta < 0
    assert fill.effective_price is not None
    assert fill.effective_price > fill.average_fill_price


def test_buy_never_consumes_ask_above_worst_price() -> None:
    current = book(asks=[(0.49, 2), (0.51, 50)])
    fill = simulate_fak_fill(
        current,
        side="BUY",
        fee_rate=0,
        minimum_order_size=1,
        worst_price=0.50,
        requested_usd=10,
    )
    assert fill.status == "PARTIAL_FILLED"
    assert fill.filled_shares == pytest.approx(2)
    assert fill.gross_notional == pytest.approx(0.98)
    assert fill.levels_consumed == 1


def test_sell_fak_consumes_highest_bids_and_cancels_remainder() -> None:
    current = book(bids=[(0.47, 2), (0.49, 3), (0.48, 2)])
    fill = simulate_fak_fill(
        current,
        side="SELL",
        fee_rate=0.07,
        minimum_order_size=1,
        worst_price=0.48,
        requested_shares=10,
    )
    assert fill.status == "PARTIAL_FILLED"
    assert fill.filled_shares == pytest.approx(5)
    assert fill.levels_consumed == 2
    assert fill.gross_notional == pytest.approx(2.43)
    assert fill.fee_usd > 0
    assert fill.net_cash_delta < fill.gross_notional


def test_no_fill_is_explicit_when_liquidity_or_minimum_is_missing() -> None:
    empty = simulate_fak_fill(
        book(asks=[]),
        side="BUY",
        fee_rate=0.07,
        minimum_order_size=1,
        worst_price=0.7,
        requested_usd=5,
    )
    assert empty.status == "NO_FILL"
    assert empty.reason == "empty_executable_book"

    too_small = simulate_fak_fill(
        book(asks=[(0.5, 100)]),
        side="BUY",
        fee_rate=0,
        minimum_order_size=10,
        worst_price=0.6,
        requested_usd=4,
    )
    assert too_small.status == "NO_FILL"
    assert too_small.reason == "below_min_order_or_price_limit"


def test_worst_price_limit_is_tick_aligned_and_directional() -> None:
    assert worst_price_limit(
        source_price=0.501,
        side="BUY",
        tick_size=0.01,
        maximum_absolute_slippage=0.03,
    ) == pytest.approx(0.53)
    assert worst_price_limit(
        source_price=0.501,
        side="SELL",
        tick_size=0.01,
        maximum_absolute_slippage=0.03,
    ) == pytest.approx(0.48)
