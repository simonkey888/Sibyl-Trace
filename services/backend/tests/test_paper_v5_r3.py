from __future__ import annotations

import time

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db import Base
from app.execution_v5 import market_rules_from_clob_info
from app.models import Wallet
from app.models_v5 import PaperV5Execution, PaperV5Prediction
from app.paper_v5 import current_portfolio_v5, execution_health_v5
from app.paper_v5_r3 import COHORT_ID, PaperEngineV5R3
from app.repository import initialize_state


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


def activity(*, tx="0xr3", asset="asset-r3"):
    return {
        "type": "TRADE",
        "transactionHash": tx,
        "asset": asset,
        "conditionId": "condition-r3",
        "side": "BUY",
        "timestamp": int(time.time()),
        "price": 0.50,
        "size": 200,
        "usdcSize": 100,
        "title": "R3 test market",
        "outcome": "Yes",
        "outcomeIndex": 0,
    }


def book(*, asks=(), bids=(), suffix="1"):
    return {
        "hash": f"r3-book-{suffix}",
        "timestamp": str(2000 + int(suffix)),
        "asks": [{"price": str(price), "size": str(size)} for price, size in asks],
        "bids": [{"price": str(price), "size": str(size)} for price, size in bids],
    }


class FakeClient:
    def __init__(self, books):
        self.books = list(books)

    def clob_market_info(self, _condition_id):
        return {
            "mts": "0.01",
            "mos": "1",
            "itode": False,
            "fd": {"r": "0.05", "e": 1, "to": True},
        }

    def order_book(self, _asset_id):
        if len(self.books) > 1:
            return self.books.pop(0)
        return self.books[0]


def add_wallet(db: Session) -> Wallet:
    wallet = Wallet(
        address="0x2222222222222222222222222222222222222222",
        username="r3-source",
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


def test_r3_cohort_is_distinct_from_r2() -> None:
    assert COHORT_ID == "PAPER_V5_R3_INTRACYCLE_MARK_2026_08_07"


def test_r3_marks_new_fill_immediately_to_executable_bid_value(monkeypatch) -> None:
    SessionLocal = factory()
    client = FakeClient(
        [
            book(asks=[(0.51, 100)], bids=[(0.49, 100)], suffix="1"),
            book(asks=[(0.52, 100)], bids=[(0.48, 100)], suffix="2"),
        ]
    )
    monkeypatch.setattr("app.paper_v5_r3.time.sleep", lambda _seconds: None)
    with SessionLocal() as db:
        initialize_state(db, settings())
        wallet = add_wallet(db)
        assert PaperEngineV5R3(settings(), client).process(db, wallet, activity()) is True

        execution = db.scalar(select(PaperV5Execution))
        assert execution is not None
        assert execution.status in {"FILLED", "PARTIAL_FILLED"}
        assert execution.arrival_book_hash == "r3-book-2"

        portfolio = current_portfolio_v5(db, 300)
        assert portfolio["exposure"] > 0
        assert portfolio["equity"] < 300
        assert portfolio["daily_pnl"] > -(portfolio["equity"] * 0.03)


def test_r3_book_404_is_no_fill_not_adapter_failure(monkeypatch) -> None:
    class Response404:
        status_code = 404

    class Book404Error(Exception):
        response = Response404()

    class MissingBookClient(FakeClient):
        def order_book(self, _asset_id):
            raise Book404Error("closed token has no book")

    SessionLocal = factory()
    client = MissingBookClient([])
    monkeypatch.setattr("app.paper_v5_r3.time.sleep", lambda _seconds: None)
    with SessionLocal() as db:
        initialize_state(db, settings())
        wallet = add_wallet(db)
        PaperEngineV5R3(settings(), client).process(db, wallet, activity(tx="0x404"))

        prediction = db.scalar(select(PaperV5Prediction))
        execution = db.scalar(select(PaperV5Execution))
        assert prediction is not None
        assert execution is not None
        assert prediction.result == "NO_FILL"
        assert execution.status == "NO_FILL"
        assert execution.reason == "decision_book_not_found"
        health = execution_health_v5(db)
        assert health["adapter_failures"] == 0
        assert health["state"] == "GREEN"


def test_unknown_delayed_market_schedule_fails_closed() -> None:
    with pytest.raises(ValueError, match="unsupported_delayed_market_schedule"):
        market_rules_from_clob_info(
            {
                "mts": "0.01",
                "mos": "5",
                "itode": True,
                "fd": {"r": "0.05", "e": 1, "to": True},
            }
        )

    crypto = market_rules_from_clob_info(
        {
            "mts": "0.001",
            "mos": "5",
            "itode": True,
            "fd": {"r": "0.07", "e": 1, "to": True},
        }
    )
    assert crypto.order_delay_ms == 250
