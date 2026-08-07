from app.watchdogs import (
    accounting_watchdog,
    edge_decay_watchdog,
    feed_watchdog,
    global_watchdog_state,
    sample_watchdog,
)


def test_accounting_mismatch_is_red() -> None:
    result = accounting_watchdog(
        cash=290,
        open_market_value=5,
        equity=300,
        initial_bankroll=300,
        realized_pnl=0,
        unrealized_pnl=0,
    )
    assert result.state == "RED"


def test_accounting_reconciliation_is_green() -> None:
    result = accounting_watchdog(
        cash=290,
        open_market_value=12,
        equity=302,
        initial_bankroll=300,
        realized_pnl=1,
        unrealized_pnl=1,
    )
    assert result.state == "GREEN"


def test_feed_watchdog_degrades_without_inventing_data() -> None:
    yellow = feed_watchdog({"BINANCE": 20, "COINBASE": 20, "POLYMARKET": 0}, ())
    red = feed_watchdog({"BINANCE": 20}, ("COINBASE:timeout",))
    assert yellow.state == "YELLOW"
    assert red.state == "RED"


def test_sample_watchdog_never_calls_small_sample_green() -> None:
    assert sample_watchdog("EDGE_SCORE_DROP", 2, minimum=30).state == "YELLOW"
    assert sample_watchdog("EDGE_SCORE_DROP", 30, minimum=30).state == "GREEN"


def test_edge_decay_and_global_state() -> None:
    green = edge_decay_watchdog(0.03, 0.03)
    red = edge_decay_watchdog(-0.01, 0.03)
    assert red.state == "RED"
    assert global_watchdog_state([green, red]) == "RED"
