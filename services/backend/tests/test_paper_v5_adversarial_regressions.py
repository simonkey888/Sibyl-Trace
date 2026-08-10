from __future__ import annotations

import time
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db import Base
from app.models import Wallet
from app.models_v5 import PaperV5Position, PaperV5Settlement
from app.paper_v5 import PaperEngineV5, current_portfolio_v5, mark_positions_v5, settle_v5
from app.paper_v5_r42 import _R42TruthClient
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
        initial_bankroll_usd=1000,
        risk_max_signal_age_seconds=5400,
        activity_lookback_seconds=5400,
    )


def book(*, asks=((0.50, 1000),), bids=((0.49, 1000),), suffix="1"):
    return {
        "hash": f"adversarial-book-{suffix}",
        "timestamp": str(7000 + int(suffix)),
        "asks": [{"price": str(price), "size": str(size)} for price, size in asks],
        "bids": [{"price": str(price), "size": str(size)} for price, size in bids],
    }


def activity(tx: str, *, side: str, source_usdc: float = 50.0):
    price = 0.50 if side == "BUY" else 0.49
    return {
        "type": "TRADE",
        "transactionHash": tx,
        "asset": "asset-adversarial",
        "conditionId": "condition-adversarial",
        "side": side,
        "timestamp": int(time.time()),
        "price": price,
        "size": source_usdc / price,
        "usdcSize": source_usdc,
        "title": "Adversarial accounting market",
        "outcome": "Yes",
        "outcomeIndex": 0,
    }


class ExecutionClient:
    def __init__(self):
        self.closed = []
        self.counter = 0

    def clob_market_info(self, _condition_id):
        return {
            "mts": "0.01",
            "mos": "1",
            "itode": False,
            "fd": {"r": "0.07", "e": 1, "to": True},
        }

    def order_book(self, _asset_id):
        self.counter += 1
        return book(suffix=str(self.counter))

    def closed_markets(self, _condition_ids):
        return self.closed


class ShadowClient:
    def __init__(self, raw_book):
        self.raw_book = raw_book
        self.settings = SimpleNamespace(gamma_api_base="https://gamma.test")

    def order_book(self, _asset_id):
        return self.raw_book

    def _get(self, _url, params=None):
        assert params is None
        return {
            "conditionId": "condition-shadow",
            "active": True,
            "closed": False,
            "acceptingOrders": True,
            "enableOrderBook": True,
            "secondsDelay": 0,
        }


def add_wallet(db: Session) -> Wallet:
    wallet = Wallet(
        address="0x9999999999999999999999999999999999999999",
        username="adversarial-source",
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


def test_multiple_buys_partial_sell_then_settlement_reconciles_once(monkeypatch):
    local = factory()
    client = ExecutionClient()
    monkeypatch.setattr("app.paper_v5.time.sleep", lambda _seconds: None)

    with local() as db:
        initialize_state(db, settings())
        wallet = add_wallet(db)
        engine = PaperEngineV5(settings(), client)

        for index in range(3):
            assert engine.process(
                db,
                wallet,
                activity(f"0xbuy-{index}", side="BUY"),
            ) is True

        marked, mark_errors = mark_positions_v5(db, client)
        assert marked == 1
        assert mark_errors == []

        before_sell = db.get(PaperV5Position, "asset-adversarial")
        assert before_sell is not None
        shares_before_sell = before_sell.shares
        cost_before_sell = before_sell.cost_basis_usd
        assert shares_before_sell > 0
        assert cost_before_sell > 0

        assert engine.process(
            db,
            wallet,
            activity("0xpartial-sell", side="SELL"),
        ) is True

        after_sell = db.get(PaperV5Position, "asset-adversarial")
        assert after_sell is not None
        assert 0 < after_sell.shares < shares_before_sell
        expected_remaining_cost = cost_before_sell * (after_sell.shares / shares_before_sell)
        assert after_sell.cost_basis_usd == pytest.approx(
            expected_remaining_cost, rel=1e-9, abs=1e-9
        )
        assert after_sell.realized_pnl != 0

        portfolio_before_settlement = current_portfolio_v5(db, 1000)
        watchdog_before = accounting_watchdog(
            cash=portfolio_before_settlement["cash"],
            open_market_value=portfolio_before_settlement["exposure"],
            equity=portfolio_before_settlement["equity"],
            initial_bankroll=portfolio_before_settlement["initial_bankroll"],
            realized_pnl=portfolio_before_settlement["realized_pnl"],
            unrealized_pnl=portfolio_before_settlement["unrealized_pnl"],
            tolerance=0.02,
        )
        assert watchdog_before.state == "GREEN"

        client.closed = [{
            "closed": True,
            "conditionId": "condition-adversarial",
            "clobTokenIds": ["asset-adversarial", "asset-no"],
            "outcomePrices": ["1", "0"],
        }]
        resolved, settled, errors = settle_v5(db, client)
        assert errors == []
        assert resolved >= 1
        assert settled == 1

        portfolio_after = current_portfolio_v5(db, 1000)
        position_after = db.get(PaperV5Position, "asset-adversarial")
        assert position_after is not None
        assert position_after.shares == 0
        assert position_after.cost_basis_usd == 0
        assert position_after.mark_value_usd == 0
        assert db.scalar(select(func.count()).select_from(PaperV5Settlement)) == 1

        watchdog_after = accounting_watchdog(
            cash=portfolio_after["cash"],
            open_market_value=portfolio_after["exposure"],
            equity=portfolio_after["equity"],
            initial_bankroll=portfolio_after["initial_bankroll"],
            realized_pnl=portfolio_after["realized_pnl"],
            unrealized_pnl=portfolio_after["unrealized_pnl"],
            tolerance=0.02,
        )
        assert watchdog_after.state == "GREEN"

        frozen = dict(portfolio_after)
        resolved_again, settled_again, errors_again = settle_v5(db, client)
        assert errors_again == []
        assert settled_again == 0
        assert resolved_again >= 0
        assert current_portfolio_v5(db, 1000) == frozen
        assert db.scalar(select(func.count()).select_from(PaperV5Settlement)) == 1


def test_shadow_self_impact_accumulates_across_multiple_fills_same_price():
    raw = {
        "hash": "shadow-source-book",
        "timestamp": "9000",
        "asks": [{"price": "0.51", "size": "10"}],
        "bids": [{"price": "0.49", "size": "10"}],
    }
    proxy = _R42TruthClient(ShadowClient(raw))

    for filled in (3.0, 5.0):
        proxy.start_signal("condition-shadow", "shadow-market")
        proxy.order_book("asset-shadow")
        proxy.order_book("asset-shadow")
        proxy.record_fill(
            "asset-shadow",
            "BUY",
            SimpleNamespace(
                status="FILLED",
                filled_shares=filled,
                worst_price_limit=0.60,
            ),
        )
        proxy.finish_signal()

    proxy.start_signal("condition-shadow", "shadow-market")
    adjusted = proxy.order_book("asset-shadow")
    assert float(adjusted["asks"][0]["size"]) == pytest.approx(2.0)
    assert adjusted["source_hash"] == "shadow-source-book"
    assert str(adjusted["hash"]).startswith("shadow-")
