from __future__ import annotations

import inspect
import time

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db import Base
from app.models import Wallet
from app.models_v5 import PaperV5Execution, PaperV5Prediction
from app.paper_v5 import (
    PaperEngineV5,
    current_portfolio_v5,
    execution_health_v5,
    settle_v5,
)
from app.repository import initialize_state
from app.watchdogs import accounting_watchdog


def factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


def settings() -> Settings:
    return Settings(
        trading_mode="PAPER",
        paper_trading_enabled=True,
        initial_bankroll_usd=300,
        risk_max_signal_age_seconds=5400,
        activity_lookback_seconds=5400,
    )


def activity(*, tx="0xabc", side="BUY", price=0.5, usdc=100.0):
    return {
        "type": "TRADE",
        "transactionHash": tx,
        "asset": "asset-yes",
        "conditionId": "condition-1",
        "side": side,
        "timestamp": int(time.time()),
        "price": price,
        "size": usdc / price,
        "usdcSize": usdc,
        "title": "Will the test resolve YES?",
        "outcome": "Yes",
        "outcomeIndex": 0,
    }


class FakeClient:
    def __init__(self, books):
        self.books = list(books)
        self.closed = []

    def clob_market_info(self, _condition_id):
        return {
            "mts": "0.01",
            "mos": "1",
            "itode": False,
            "fd": {"r": "0.07", "e": 1, "to": True},
        }

    def order_book(self, _asset_id):
        if len(self.books) > 1:
            return self.books.pop(0)
        return self.books[0]

    def closed_markets(self, _condition_ids):
        return self.closed


def order_book(*, asks=(), bids=(), suffix="1"):
    return {
        "hash": f"book-{suffix}",
        "timestamp": str(1000 + int(suffix)),
        "asks": [{"price": str(price), "size": str(size)} for price, size in asks],
        "bids": [{"price": str(price), "size": str(size)} for price, size in bids],
    }


def add_wallet(db: Session) -> Wallet:
    wallet = Wallet(
        address="0x1111111111111111111111111111111111111111",
        username="truth-source",
        score=90,
        win_rate=0.7,
        profit_factor=2.0,
        realized_pnl=100,
        volume=1000,
        closed_count=100,
        concentration=0.2,
        selected=True,
    )
    db.add(wallet)
    db.commit()
    return wallet


def test_v5_engine_never_uses_midpoint_and_fills_against_arrival_asks(monkeypatch) -> None:
    source = inspect.getsource(PaperEngineV5.process)
    assert ".midpoint(" not in source

    SessionLocal = factory()
    client = FakeClient(
        [
            order_book(asks=[(0.51, 100)], bids=[(0.49, 100)], suffix="1"),
            order_book(asks=[(0.52, 4), (0.53, 100)], bids=[(0.48, 100)], suffix="2"),
        ]
    )
    monkeypatch.setattr("app.paper_v5.time.sleep", lambda _seconds: None)
    with SessionLocal() as db:
        initialize_state(db, settings())
        wallet = add_wallet(db)
        created = PaperEngineV5(settings(), client).process(db, wallet, activity())
        assert created is True

        prediction = db.scalar(select(PaperV5Prediction))
        execution = db.scalar(select(PaperV5Execution))
        assert prediction is not None
        assert execution is not None
        assert prediction.decision in {"FILLED", "PARTIAL_FILLED"}
        assert prediction.result == "UNRESOLVED"
        assert prediction.resolution_status == "OPEN"
        assert execution.decision_best_price == pytest.approx(0.51)
        assert execution.average_fill_price is not None
        assert execution.average_fill_price >= 0.52
        assert execution.arrival_book_hash == "book-2"
        assert execution.decision_book_hash == "book-1"
        assert execution.fee_usd > 0
        assert execution.filled_shares > 0
        assert execution.levels_consumed >= 1

        portfolio = current_portfolio_v5(db, 300)
        assert portfolio["cash"] < 300
        assert portfolio["exposure"] == 0
        assert portfolio["equity"] < 300


def test_v5_records_no_fill_instead_of_inventing_execution(monkeypatch) -> None:
    SessionLocal = factory()
    client = FakeClient(
        [
            order_book(asks=[(0.51, 100)], bids=[(0.49, 100)], suffix="1"),
            order_book(asks=[(0.54, 100)], bids=[(0.48, 100)], suffix="2"),
        ]
    )
    monkeypatch.setattr("app.paper_v5.time.sleep", lambda _seconds: None)
    with SessionLocal() as db:
        initialize_state(db, settings())
        wallet = add_wallet(db)
        PaperEngineV5(settings(), client).process(db, wallet, activity(tx="0xdef"))
        prediction = db.scalar(select(PaperV5Prediction))
        execution = db.scalar(select(PaperV5Execution))
        assert prediction is not None
        assert execution is not None
        assert prediction.decision == "NO_FILL"
        assert prediction.result == "NO_FILL"
        assert prediction.resolution_status == "NOT_APPLICABLE"
        assert execution.status == "NO_FILL"
        assert execution.filled_shares == 0
        assert execution.net_cash_delta == 0
        assert current_portfolio_v5(db, 300)["equity"] == 300


def test_v5_buy_fill_resolves_win_only_from_terminal_market_result(monkeypatch) -> None:
    SessionLocal = factory()
    client = FakeClient(
        [
            order_book(asks=[(0.51, 100)], bids=[(0.49, 100)], suffix="1"),
            order_book(asks=[(0.51, 100)], bids=[(0.49, 100)], suffix="2"),
        ]
    )
    monkeypatch.setattr("app.paper_v5.time.sleep", lambda _seconds: None)
    with SessionLocal() as db:
        initialize_state(db, settings())
        wallet = add_wallet(db)
        PaperEngineV5(settings(), client).process(db, wallet, activity(tx="0xwin"))
        prediction = db.scalar(select(PaperV5Prediction))
        assert prediction is not None
        assert prediction.result == "UNRESOLVED"

        client.closed = [
            {
                "closed": True,
                "conditionId": "condition-1",
                "clobTokenIds": ["asset-yes", "asset-no"],
                "outcomePrices": ["1", "0"],
            }
        ]
        resolved, settled, errors = settle_v5(db, client)
        assert errors == []
        assert resolved == 1
        assert settled == 1
        db.refresh(prediction)
        assert prediction.result == "WIN"
        assert prediction.resolution_status == "RESOLVED"
        assert prediction.resolution_price == 1.0

        portfolio = current_portfolio_v5(db, 300)
        watchdog = accounting_watchdog(
            cash=portfolio["cash"],
            open_market_value=portfolio["exposure"],
            equity=portfolio["equity"],
            initial_bankroll=portfolio["initial_bankroll"],
            realized_pnl=portfolio["realized_pnl"],
            unrealized_pnl=portfolio["unrealized_pnl"],
            tolerance=0.02,
        )
        assert watchdog.state == "GREEN"
        assert portfolio["exposure"] == 0
        assert portfolio["realized_pnl"] > 0


def test_v5_buy_fill_resolves_loss_from_terminal_zero(monkeypatch) -> None:
    SessionLocal = factory()
    client = FakeClient(
        [
            order_book(asks=[(0.51, 100)], bids=[(0.49, 100)], suffix="1"),
            order_book(asks=[(0.51, 100)], bids=[(0.49, 100)], suffix="2"),
        ]
    )
    monkeypatch.setattr("app.paper_v5.time.sleep", lambda _seconds: None)
    with SessionLocal() as db:
        initialize_state(db, settings())
        wallet = add_wallet(db)
        PaperEngineV5(settings(), client).process(db, wallet, activity(tx="0xloss"))
        client.closed = [
            {
                "closed": True,
                "conditionId": "condition-1",
                "clobTokenIds": ["asset-yes", "asset-no"],
                "outcomePrices": ["0", "1"],
            }
        ]
        resolved, settled, errors = settle_v5(db, client)
        assert errors == []
        assert resolved == 1
        assert settled == 1
        prediction = db.scalar(select(PaperV5Prediction))
        assert prediction is not None
        assert prediction.result == "LOSS"
        assert current_portfolio_v5(db, 300)["realized_pnl"] < 0


def test_rejected_prediction_never_enters_accuracy_denominator(monkeypatch) -> None:
    SessionLocal = factory()
    client = FakeClient([order_book(asks=[(0.51, 100)], bids=[(0.49, 100)])])
    monkeypatch.setattr("app.paper_v5.time.sleep", lambda _seconds: None)
    with SessionLocal() as db:
        initialize_state(db, settings())
        wallet = add_wallet(db)
        wallet.score = 10
        db.commit()
        PaperEngineV5(settings(), client).process(db, wallet, activity(tx="0xreject"))
        prediction = db.scalar(select(PaperV5Prediction))
        execution = db.scalar(select(PaperV5Execution))
        assert prediction is not None
        assert execution is not None
        assert prediction.result == "REJECTED"
        assert prediction.resolution_status == "NOT_APPLICABLE"
        assert execution.status == "REJECTED"


def test_systemic_adapter_failure_is_red(monkeypatch) -> None:
    class BadRulesClient(FakeClient):
        def clob_market_info(self, _condition_id):
            return {
                "mts": "0.01",
                "mos": "1",
                "fd": {"r": "0.07", "e": 2, "to": True},
            }

    SessionLocal = factory()
    client = BadRulesClient([order_book(asks=[(0.51, 100)], bids=[(0.49, 100)])])
    monkeypatch.setattr("app.paper_v5.time.sleep", lambda _seconds: None)
    with SessionLocal() as db:
        initialize_state(db, settings())
        wallet = add_wallet(db)
        PaperEngineV5(settings(), client).process(db, wallet, activity(tx="0xbad-rules"))
        health = execution_health_v5(db)
        assert health["state"] == "RED"
        assert health["adapter_failures"] == 1
        assert health["decision_books_reached"] == 0
        assert health["errors"] == ["systemic_market_data_adapter_failure:1"]
