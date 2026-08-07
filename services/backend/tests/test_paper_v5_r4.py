from __future__ import annotations

import time

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db import Base
from app.models import Wallet
from app.models_v5 import PaperV5Execution, PaperV5ExecutionEvidence, PaperV5Prediction
from app.paper_v5_r4 import PaperEngineV5R4, _rules_from_official_metadata
from app.repository import initialize_state


def factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


def settings():
    return Settings(
        trading_mode="PAPER",
        paper_trading_enabled=True,
        initial_bankroll_usd=300,
        risk_max_signal_age_seconds=5400,
        activity_lookback_seconds=5400,
    )


def activity(tx="0xr4"):
    return {
        "type": "TRADE",
        "transactionHash": tx,
        "asset": "asset-r4",
        "conditionId": "condition-r4",
        "side": "BUY",
        "timestamp": int(time.time()),
        "price": 0.50,
        "size": 200,
        "usdcSize": 100,
        "title": "R4 market",
        "outcome": "Yes",
        "outcomeIndex": 0,
    }


def book(asks=(), bids=(), suffix="1"):
    return {
        "hash": f"r4-book-{suffix}",
        "timestamp": str(3000 + int(suffix)),
        "asks": [{"price": str(p), "size": str(s)} for p, s in asks],
        "bids": [{"price": str(p), "size": str(s)} for p, s in bids],
    }


def market(**kw):
    base = {
        "conditionId": "condition-r4",
        "active": True,
        "closed": False,
        "acceptingOrders": True,
        "enableOrderBook": True,
        "secondsDelay": 0,
    }
    base.update(kw)
    return base


class FakeClient:
    def __init__(self, books, market_data=None):
        self.books = list(books)
        self.market_data = market_data or market()

    def market_by_condition(self, _condition_id):
        return dict(self.market_data)

    def clob_market_info(self, _condition_id):
        return {
            "mts": "0.01",
            "mos": "1",
            "itode": True,
            "fd": {"r": "0.05", "e": 1, "to": True},
        }

    def fee_rate_bps(self, _asset_id):
        return 1000

    def order_book(self, _asset_id):
        if len(self.books) > 1:
            return self.books.pop(0)
        return self.books[0]


def add_wallet(db):
    wallet = Wallet(
        address="0x3333333333333333333333333333333333333333",
        username="r4",
        score=90,
        win_rate=0.7,
        profit_factor=2,
        realized_pnl=100,
        volume=1000,
        closed_count=100,
        concentration=0.2,
        selected=True,
    )
    db.add(wallet)
    db.commit()
    return wallet


def test_official_seconds_delay_overrides_itode_without_synthetic_proxy():
    info = {
        "mts": "0.01",
        "mos": "1",
        "itode": True,
        "fd": {"r": "0.07", "e": 1, "to": True},
    }
    assert _rules_from_official_metadata(info, market(secondsDelay=2)).order_delay_ms == 2000
    assert _rules_from_official_metadata(info, market(secondsDelay=0)).order_delay_ms == 0
    with pytest.raises(ValueError, match="official_seconds_delay_unavailable"):
        _rules_from_official_metadata(info, market(secondsDelay=None))


def test_active_market_404_is_data_failure_not_no_fill(monkeypatch):
    class Response:
        status_code = 404

    class MissingBook(Exception):
        response = Response()

    class Client(FakeClient):
        def order_book(self, _asset_id):
            raise MissingBook("missing")

    local = factory()
    monkeypatch.setattr("app.paper_v5_r4.time.sleep", lambda _seconds: None)
    with local() as db:
        initialize_state(db, settings())
        wallet = add_wallet(db)
        PaperEngineV5R4(settings(), Client([])).process(db, wallet, activity("0xactive404"))
        execution = db.scalar(select(PaperV5Execution))
        prediction = db.scalar(select(PaperV5Prediction))
        assert execution.status == "REJECTED"
        assert prediction.result == "REJECTED"
        assert "active_market_book_404" in execution.reason


def test_nontradable_market_is_no_fill():
    local = factory()
    client = FakeClient(
        [],
        market_data=market(closed=True, acceptingOrders=False, enableOrderBook=False),
    )
    with local() as db:
        initialize_state(db, settings())
        wallet = add_wallet(db)
        PaperEngineV5R4(settings(), client).process(db, wallet, activity("0xclosed"))
        execution = db.scalar(select(PaperV5Execution))
        assert execution.status == "NO_FILL"
        assert execution.reason == "market_not_trade_ready"


def test_fill_records_actual_gap_and_evidence_hash(monkeypatch):
    local = factory()
    client = FakeClient(
        [
            book(asks=[(0.51, 100)], bids=[(0.49, 100)], suffix="1"),
            book(asks=[(0.52, 100)], bids=[(0.48, 100)], suffix="2"),
        ]
    )
    monkeypatch.setattr("app.paper_v5_r4.time.sleep", lambda _seconds: None)
    with local() as db:
        initialize_state(db, settings())
        wallet = add_wallet(db)
        PaperEngineV5R4(settings(), client).process(db, wallet, activity("0xevidence"))
        execution = db.scalar(select(PaperV5Execution))
        evidence = db.scalar(select(PaperV5ExecutionEvidence))
        assert execution.status in {"FILLED", "PARTIAL_FILLED"}
        assert evidence is not None
        assert evidence.decision_received_at_ms is not None
        assert evidence.arrival_received_at_ms is not None
        assert evidence.actual_gap_ms >= 0
        assert len(evidence.execution_evidence_hash) == 64
        assert evidence.official_seconds_delay == 0


def test_atlanta_golden_accounting_identity():
    from app.execution_v5 import simulate_fak_fill

    fill = simulate_fak_fill(
        book(asks=[(0.48, 100)], suffix="1"),
        side="BUY",
        fee_rate=0.05,
        minimum_order_size=1,
        worst_price=0.50,
        requested_usd=5.99,
    )
    assert fill.gross_notional == pytest.approx(5.83821, abs=2e-5)
    assert fill.fee_usd == pytest.approx(0.15179, abs=2e-5)
    assert -fill.net_cash_delta == pytest.approx(5.99, abs=2e-5)
    mark = simulate_fak_fill(
        book(bids=[(0.47, 100)], suffix="2"),
        side="SELL",
        fee_rate=0.05,
        minimum_order_size=1,
        worst_price=0.001,
        requested_shares=fill.filled_shares,
    )
    assert mark.net_cash_delta == pytest.approx(5.5651, abs=2e-4)
    assert mark.net_cash_delta - (-fill.net_cash_delta) == pytest.approx(-0.4249, abs=3e-4)
