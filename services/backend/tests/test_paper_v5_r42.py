from __future__ import annotations

import json
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import paper_v5 as legacy
from app import paper_v5_r4 as r4
from app.config import Settings
from app.db import Base
from app.models import Wallet
from app.models_v5 import PaperV5Execution
from app.paper_v5_r42 import (
    COHORT_ID,
    EXECUTION_MODEL,
    PaperEngineV5R42,
    _apply_r42_report,
    _R42TruthClient,
    _write_ledger_r42,
)
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


def activity(tx="0xr42"):
    return {
        "type": "TRADE",
        "transactionHash": tx,
        "asset": "asset-r42",
        "conditionId": "condition-r42",
        "side": "BUY",
        "timestamp": int(time.time()),
        "price": 0.50,
        "size": 200,
        "usdcSize": 100,
        "title": "R4.2 market",
        "outcome": "Yes",
        "outcomeIndex": 0,
        "slug": "r42-market",
    }


def book(asks=(), bids=(), suffix="1"):
    return {
        "hash": f"r42-book-{suffix}",
        "timestamp": str(5000 + int(suffix)),
        "asks": [{"price": str(p), "size": str(s)} for p, s in asks],
        "bids": [{"price": str(p), "size": str(s)} for p, s in bids],
    }


def market(**kw):
    base = {
        "conditionId": "condition-r42",
        "active": True,
        "closed": False,
        "acceptingOrders": True,
        "enableOrderBook": True,
        "secondsDelay": 0,
    }
    base.update(kw)
    return base


class FakeClient:
    def __init__(self, books, markets=None):
        self.books = list(books)
        self.markets = list(markets or [market()])
        self.settings = SimpleNamespace(gamma_api_base="https://gamma.test")
        self.order_book_calls = 0
        self.market_calls = 0

    def _get(self, url, params=None):
        assert url == "https://gamma.test/markets/slug/r42-market"
        assert params is None
        self.market_calls += 1
        index = min(self.market_calls - 1, len(self.markets) - 1)
        return dict(self.markets[index])

    def clob_market_info(self, _condition_id):
        return {
            "mts": "0.01",
            "mos": "1",
            "itode": False,
            "fd": {"r": "0.05", "e": 1, "to": True},
        }

    def fee_rate_bps(self, _asset_id):
        return 1000

    def order_book(self, _asset_id):
        self.order_book_calls += 1
        if len(self.books) > 1:
            return self.books.pop(0)
        return self.books[0]


def add_wallet(db):
    wallet = Wallet(
        address="0x4444444444444444444444444444444444444444",
        username="r42",
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


def test_post_delay_market_state_is_revalidated_before_arrival(monkeypatch):
    local = factory()
    client = FakeClient(
        [book(asks=[(0.51, 100)], bids=[(0.49, 100)], suffix="1")],
        markets=[
            market(secondsDelay=1),
            market(secondsDelay=1, active=False, closed=True, acceptingOrders=False),
            market(secondsDelay=1, active=False, closed=True, acceptingOrders=False),
        ],
    )
    monkeypatch.setattr("app.paper_v5_r4.time.sleep", lambda _seconds: None)
    with local() as db:
        initialize_state(db, settings())
        wallet = add_wallet(db)
        assert PaperEngineV5R42(settings(), client).process(db, wallet, activity("0xdelay")) is True
        execution = db.scalar(select(PaperV5Execution))
        assert execution is not None
        assert execution.status == "NO_FILL"
        assert execution.reason == "arrival_book_unavailable_nontradable"
        assert client.order_book_calls == 1
        assert client.market_calls >= 3


def test_shadow_self_impact_depletes_subsequent_books():
    raw = book(asks=[(0.51, 10)], bids=[(0.49, 10)], suffix="1")
    client = FakeClient([raw, raw, raw])
    proxy = _R42TruthClient(client)
    proxy.start_signal("condition-r42", "r42-market")
    proxy._get("https://gamma.test/markets/slug/r42-market")
    proxy.order_book("asset-r42")
    arrival = proxy.order_book("asset-r42")
    assert float(arrival["asks"][0]["size"]) == 10
    proxy.record_fill(
        "asset-r42",
        "BUY",
        SimpleNamespace(
            status="FILLED",
            filled_shares=6.0,
            worst_price_limit=0.60,
        ),
    )
    proxy.finish_signal()

    proxy.start_signal("condition-r42", "r42-market")
    next_book = proxy.order_book("asset-r42")
    assert float(next_book["asks"][0]["size"]) == pytest.approx(4.0)
    assert str(next_book["hash"]).startswith("shadow-")
    assert next_book["source_hash"] == "r42-book-1"


def test_shadow_self_impact_tolerates_malformed_price_levels():
    raw = {
        "hash": "r42-book-malformed",
        "timestamp": "5001",
        "asks": [
            {"price": None, "size": "5"},
            {"price": "not-a-number", "size": "5"},
            {"price": "0.51", "size": "10"},
        ],
        "bids": [{"price": "0.49", "size": "10"}],
    }
    client = FakeClient([raw])
    proxy = _R42TruthClient(client)
    proxy._shadow[("asset-r42", "BUY", "0.51")] = 4.0
    proxy.start_signal("condition-r42", "r42-market")
    adjusted = proxy.order_book("asset-r42")

    assert adjusted["asks"][0]["price"] is None
    assert adjusted["asks"][1]["price"] == "not-a-number"
    assert float(adjusted["asks"][2]["size"]) == pytest.approx(6.0)
    assert adjusted["hash"].startswith("shadow-")


def test_r42_ledger_emits_copy_decay_fee_and_book_provenance(monkeypatch, tmp_path: Path):
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
        engine = PaperEngineV5R42(settings(), client)
        engine._truth_client._shadow[("asset-r42", "BUY", "0.52")] = 10.0
        engine.process(db, wallet, activity("0xledger"))
        path = tmp_path / "ledger.jsonl"
        _write_ledger_r42(legacy._write_ledger, db, path)
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        assert len(rows) == 1
        row = rows[0]
        assert row["copy_decay"]["direction"] == "positive_is_worse_than_source"
        assert row["copy_decay"]["decision_vs_source"] == pytest.approx(0.01)
        assert row["copy_decay"]["raw_fill_vs_source"] == pytest.approx(0.02)
        assert row["copy_decay"]["effective_fill_vs_source"] > 0.02
        assert row["copy_decay"]["fee_per_filled_share"] > 0
        assert row["fee_provenance"]["primary"] == "CLOB getClobMarketInfo fd"
        assert row["fee_provenance"]["fee_rate"] == pytest.approx(0.05)
        assert row["fee_provenance"]["fee_exponent"] == pytest.approx(1.0)
        assert row["fee_provenance"]["fee_rate_bps_crosscheck"] == 1000
        provenance = row["book_provenance"]
        assert provenance["decision_public_book_hash"] == "r42-book-1"
        assert provenance["decision_execution_book_hash"] == "r42-book-1"
        assert provenance["decision_shadow_adjusted"] is False
        assert provenance["arrival_public_book_hash"] == "r42-book-2"
        assert provenance["arrival_execution_book_hash"].startswith("shadow-")
        assert provenance["arrival_shadow_adjusted"] is True
        assert row["shadow_self_impact_applied"] is True
        assert len(row["execution_evidence"]["execution_evidence_hash"]) == 64


def test_r42_report_declares_only_truthful_corrections(monkeypatch):
    local = factory()
    with local() as db:
        initialize_state(db, settings())
        baseline = r4._status_counts(db)
        report = {
            "status": "PASS",
            "run": {"errors": []},
            "methodology": {},
            "cycle": {"signals_processed": 0},
        }
        monkeypatch.setattr(r4, "COHORT_ID", COHORT_ID)
        monkeypatch.setattr(r4, "EXECUTION_MODEL", EXECUTION_MODEL)
        result = _apply_r42_report(report, db, baseline)
        method = result["methodology"]
        assert result["cohort_id"] == COHORT_ID
        assert method["execution_model"] == EXECUTION_MODEL
        assert method["post_delay_market_state_revalidation"] is True
        assert method["shadow_self_impact"] is True
        assert method["shadow_self_impact_live_claim"] is False
        assert method["public_book_hash_bridge_persisted"] is True
        assert method["execution_evidence_hash_includes_book_provenance"] is True
        assert method["copy_decay_metrics_in_ledger"] is True
        assert method["fee_provenance_in_ledger"] is True
