from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.features_v3 import build_cross_market_features
from app.journal_v3 import (
    export_journal,
    journal_row_count,
    persist_downsampled_capture,
    read_journal,
)
from app.market_data_v3 import (
    V3Capture,
    V3Event,
    V3Target,
    discover_btc_target,
    parse_binance_aggtrade,
    parse_coinbase_ticker,
    parse_polymarket_message,
)
from app.market_making_v3 import MakerConfig, decide_quotes
from app.microstructure_v3 import (
    QueuePrint,
    book_metrics,
    latency_budget,
    simulate_l1_take,
    simulate_queue_fill,
)
from app.replay_v3 import replay_capture, stable_replay_order
from app.research_models import ResearchObservation
from app.research_v3 import analyze_capture, validate_v2_source
from app.venue_v3 import (
    NormalizedBook,
    PolymarketReadOnlyVenue,
    PriceLevel,
    normalize_polymarket_book,
)


def session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def book(
    *,
    received: int = 1_000,
    bid: float = 0.48,
    ask: float = 0.52,
    bid_size: float = 20.0,
    ask_size: float = 10.0,
) -> NormalizedBook:
    return NormalizedBook(
        venue="POLYMARKET",
        asset_id="up",
        bids=(PriceLevel(bid, bid_size),),
        asks=(PriceLevel(ask, ask_size),),
        source_timestamp_ms=received - 5,
        receive_timestamp_ms=received,
    )


def target() -> V3Target:
    return V3Target(
        condition_id="condition",
        question="Bitcoin Up or Down",
        end_timestamp_ms=30_000,
        outcome_assets={"Up": "up", "Down": "down"},
        fee_rate=0.07,
        tick_size=0.01,
    )


def capture() -> V3Capture:
    events = (
        V3Event("BINANCE", "TRADE", 900, 1_000, price=100.0, size=1.0, aggressor_side="BUY"),
        V3Event("COINBASE", "TRADE", 900, 1_000, price=100.0, size=2.0, aggressor_side="BUY"),
        V3Event(
            "BINANCE_FUTURES",
            "TRADE",
            900,
            1_000,
            price=100.0,
            size=3.0,
            aggressor_side="SELL",
        ),
        V3Event(
            "POLYMARKET",
            "BOOK",
            995,
            1_000,
            bid=0.48,
            ask=0.52,
            bid_size=5.0,
            ask_size=4.0,
            asset_id="up",
        ),
        V3Event("BINANCE", "TRADE", 1_900, 2_000, price=100.1, size=1.5, aggressor_side="BUY"),
        V3Event(
            "COINBASE",
            "TRADE",
            1_900,
            2_000,
            price=100.1,
            size=2.5,
            aggressor_side="BUY",
        ),
        V3Event(
            "BINANCE_FUTURES",
            "TRADE",
            1_900,
            2_000,
            price=100.1,
            size=3.5,
            aggressor_side="BUY",
        ),
        V3Event(
            "POLYMARKET",
            "TRADE",
            2_050,
            2_100,
            price=0.48,
            size=7.0,
            aggressor_side="SELL",
            asset_id="up",
        ),
        V3Event(
            "POLYMARKET",
            "BOOK",
            2_150,
            2_200,
            bid=0.49,
            ask=0.53,
            bid_size=4.0,
            ask_size=6.0,
            asset_id="up",
        ),
    )
    return V3Capture(events=events, core_errors=(), optional_errors=())


def test_normalize_polymarket_book_and_read_only_adapter() -> None:
    payload = {
        "timestamp": "123",
        "asset_id": "up",
        "bids": [{"price": "0.47", "size": "2"}, {"price": "0.48", "size": "3"}],
        "asks": [{"price": "0.54", "size": "4"}, {"price": "0.53", "size": "5"}],
    }
    normalized = normalize_polymarket_book(payload, asset_id="up", receive_timestamp_ms=200)
    assert normalized.best_bid == PriceLevel(0.48, 3.0)
    assert normalized.best_ask == PriceLevel(0.53, 5.0)
    assert normalized.source_timestamp_ms == 123

    class Client:
        def order_book(self, asset_id: str) -> dict:
            assert asset_id == "up"
            return payload

    adapter = PolymarketReadOnlyVenue(Client())  # type: ignore[arg-type]
    assert adapter.capabilities.order_placement is False
    assert adapter.get_book("up").best_bid == PriceLevel(0.48, 3.0)


def test_normalize_rejects_crossed_book() -> None:
    with pytest.raises(ValueError, match="crossed"):
        normalize_polymarket_book(
            {
                "bids": [{"price": 0.55, "size": 1}],
                "asks": [{"price": 0.54, "size": 1}],
            },
            asset_id="up",
        )


def test_microstructure_metrics_queue_and_l1_depth() -> None:
    metrics = book_metrics(book())
    assert metrics.spread == pytest.approx(0.04)
    assert metrics.imbalance == pytest.approx(2 / 3)
    assert 0.48 < metrics.microprice < 0.52

    result = simulate_queue_fill(
        side="BUY",
        order_price=0.48,
        quantity=5,
        queue_ahead=10,
        prints=[
            QueuePrint(0.48, 8, "SELL"),
            QueuePrint(0.50, 100, "BUY"),
            QueuePrint(0.47, 7, "SELL"),
        ],
    )
    assert result.status == "FILLED"
    assert result.filled == 5
    assert result.residual_queue_ahead == 0

    partial = simulate_l1_take(side="BUY", quantity=15, book=book())
    assert partial.status == "PARTIAL"
    assert partial.filled == 10
    assert partial.unfilled == 5


def test_queue_invalid_and_latency_budget() -> None:
    invalid = simulate_queue_fill(
        side="SELL",
        order_price=2.0,
        quantity=1,
        queue_ahead=0,
        prints=[],
    )
    assert invalid.status == "INVALID"
    budget = latency_budget(
        source_timestamp_ms=100,
        receive_timestamp_ms=125,
        decision_delay_ms=5,
        synthetic_order_delay_ms=20,
    )
    assert budget.source_to_receive_ms == 25
    assert budget.total_observed_to_order_ms == 50


def test_v3_public_feed_parsers() -> None:
    spot = parse_binance_aggtrade(
        {"e": "aggTrade", "p": "100", "q": "0.5", "m": True, "T": 10, "a": 7},
        source="BINANCE",
        received_ms=20,
    )
    assert spot is not None
    assert spot.aggressor_side == "SELL"
    assert spot.size == 0.5

    coinbase = parse_coinbase_ticker(
        {
            "type": "ticker",
            "product_id": "BTC-USD",
            "price": "101",
            "best_bid": "100",
            "best_ask": "102",
            "last_size": "0.2",
            "side": "buy",
            "time": "2026-08-07T08:00:00Z",
            "sequence": 9,
        },
        received_ms=30,
    )
    assert coinbase is not None
    assert coinbase.aggressor_side == "BUY"

    poly = parse_polymarket_message(
        {
            "event_type": "book",
            "asset_id": "up",
            "timestamp": 40,
            "bids": [{"price": "0.49", "size": "5"}],
            "asks": [{"price": "0.51", "size": "6"}],
        },
        received_ms=50,
    )
    assert len(poly) == 1
    assert poly[0].bid_size == 5

    trade = parse_polymarket_message(
        {
            "event_type": "last_trade_price",
            "asset_id": "up",
            "timestamp": 55,
            "price": "0.51",
            "size": "4",
            "side": "BUY",
        },
        received_ms=60,
    )
    assert trade[0].event_type == "TRADE"
    assert trade[0].size == 4


def test_target_discovery_uses_public_clob_metadata() -> None:
    class Client:
        def active_btc_short_markets(self, *, horizon_minutes: int) -> list[dict]:
            assert horizon_minutes == 30
            return [
                {
                    "conditionId": "condition",
                    "question": "Bitcoin Up or Down",
                    "endDate": "2026-08-07T09:00:00Z",
                    "category": "Crypto",
                }
            ]

        def clob_market_info(self, condition_id: str) -> dict:
            assert condition_id == "condition"
            return {
                "t": [{"o": "Up", "t": "up"}, {"o": "Down", "t": "down"}],
                "mts": 0.01,
                "fd": {"r": 0.07},
            }

    found = discover_btc_target(Client())  # type: ignore[arg-type]
    assert found is not None
    assert found.outcome_assets == {"Up": "up", "Down": "down"}
    assert found.fee_rate == 0.07


def test_cross_market_features_are_additive_and_include_futures() -> None:
    features = build_cross_market_features(capture().events)
    assert features["status"] == "CAPTURED"
    assert features["sources"]["BINANCE"]["events"] == 2
    assert features["sources"]["BINANCE_FUTURES"]["events"] == 2
    assert features["sources"]["BINANCE"]["signed_volume"] == pytest.approx(2.5)
    assert "up" in features["polymarket_books"]


def test_market_making_lab_has_safe_regimes() -> None:
    quiet = decide_quotes(
        book=book(received=1_000),
        tick_size=0.01,
        now_ms=1_100,
        end_timestamp_ms=300_000,
    )
    assert quiet.regime == "QUIET"
    assert quiet.bid_price is not None
    assert quiet.ask_price is not None
    assert quiet.bid_price < quiet.ask_price

    event = decide_quotes(
        book=book(received=1_000),
        tick_size=0.01,
        now_ms=1_100,
        end_timestamp_ms=300_000,
        toxicity=0.02,
    )
    assert event.regime == "EVENT"
    assert event.bid_price is None

    reduce_only = decide_quotes(
        book=book(received=1_000),
        tick_size=0.01,
        now_ms=1_100,
        end_timestamp_ms=300_000,
        inventory=25,
    )
    assert reduce_only.regime == "REDUCE_ONLY"
    assert reduce_only.bid_size == 0
    assert reduce_only.ask_size > 0

    stale = decide_quotes(
        book=book(received=1_000),
        tick_size=0.01,
        now_ms=10_000,
        end_timestamp_ms=300_000,
        config=MakerConfig(stale_after_ms=1_000),
    )
    assert stale.regime == "HALTED"


def test_replay_is_deterministic_and_queue_aware() -> None:
    unordered = tuple(reversed(capture().events))
    ordered = stable_replay_order(unordered)
    assert ordered[0].receive_timestamp_ms <= ordered[-1].receive_timestamp_ms
    replay = replay_capture(unordered)
    assert replay["status"] == "REPLAYED"
    assert replay["queue_probes"] >= 1
    assert replay["invariant_violations"] == []
    assert "after the seed book" in replay["no_lookahead_rule"]


def test_journal_is_append_only_deduped_and_compressed(tmp_path: Path) -> None:
    with session() as db:
        first = persist_downsampled_capture(
            db,
            experiment_id="RESEARCH_LAB_V3",
            market_id="condition",
            run_id="run-1",
            events=capture().events,
        )
        second = persist_downsampled_capture(
            db,
            experiment_id="RESEARCH_LAB_V3",
            market_id="condition",
            run_id="run-2",
            events=capture().events,
        )
        count = journal_row_count(db)
        path = tmp_path / "journal.jsonl.gz"
        exported = export_journal(db, path)
        rows = read_journal(path)
        actual_rows = int(
            db.scalar(
                select(func.count())
                .select_from(ResearchObservation)
                .where(ResearchObservation.source.like("V3_%"))
            )
            or 0
        )
    assert first > 0
    assert second == 0
    assert count == first == exported == actual_rows == len(rows)
    assert rows[0]["payload"]["schema_version"] == 1


def test_v2_source_validation_fails_closed() -> None:
    good = {
        "run": {"status": "PASS", "github_run_id": "1", "github_sha": "abc"},
        "safety": {
            "trading_mode": "PAPER",
            "live_available": False,
            "cost_authorized_usd": 0,
        },
    }
    assert validate_v2_source(good)["github_run_id"] == "1"
    bad = {
        **good,
        "safety": {**good["safety"], "live_available": True},
    }
    with pytest.raises(ValueError, match="invariants"):
        validate_v2_source(bad)


def test_capture_analysis_keeps_optional_feed_errors_nonfatal() -> None:
    base = capture()
    degraded_optional = V3Capture(
        events=base.events,
        core_errors=(),
        optional_errors=("BINANCE_FUTURES:HTTP451",),
    )
    result = analyze_capture(target(), degraded_optional, analysis_now_ms=2_500)
    assert result["watchdog"]["state"] == "GREEN"
    assert result["optional_errors"] == ["BINANCE_FUTURES:HTTP451"]
    assert result["microstructure_v3"]["legacy_fills_rewritten"] is False
    assert result["market_making_v3"]["execution_enabled"] is False
    assert result["replay_v3"]["event_count"] == len(base.events)
