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
from app import paper_v5_r43 as r43
from app.config import Settings
from app.db import Base
from app.models import AuditEvent, Wallet
from app.models_v5 import PaperV5ExecutionEvidence, PaperV5Position, PaperV5Prediction
from app.paper_v5_r43 import (
    CYCLE_SELECTION_EFFECTIVE_STATE,
    SELECTION_EVENT,
    PaperEngineV5R43,
    _selection_evidence_binding_valid,
    _selection_payload_hash_valid,
    _write_ledger_r43,
)
from app.repository import get_state, initialize_state, set_state
from app.scanner import scan_wallets


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
        candidate_limit=6,
        tracked_wallet_limit=3,
        risk_max_signal_age_seconds=5400,
        activity_lookback_seconds=5400,
    )


def activity(timestamp: int, tx: str = "0xr43"):
    return {
        "type": "TRADE",
        "transactionHash": tx,
        "asset": "asset-r43",
        "conditionId": "condition-r43",
        "side": "BUY",
        "timestamp": timestamp,
        "price": 0.50,
        "size": 200,
        "usdcSize": 100,
        "title": "R4.3 prospective market",
        "outcome": "Yes",
        "outcomeIndex": 0,
        "slug": "r43-market",
    }


def book(*, asks=(), bids=(), suffix="1"):
    return {
        "hash": f"r43-book-{suffix}",
        "timestamp": str(1_800_000_000_000 + int(suffix)),
        "asset_id": "asset-r43",
        "asks": [{"price": str(p), "size": str(s)} for p, s in asks],
        "bids": [{"price": str(p), "size": str(s)} for p, s in bids],
    }


def market():
    return {
        "conditionId": "condition-r43",
        "active": True,
        "closed": False,
        "acceptingOrders": True,
        "enableOrderBook": True,
        "secondsDelay": 0,
    }


class ExecutionClient:
    def __init__(self, books):
        self.books = list(books)
        self.settings = SimpleNamespace(gamma_api_base="https://gamma.test")

    def _get(self, url, params=None):
        assert url == "https://gamma.test/markets/slug/r43-market"
        return market()

    def clob_market_info(self, _condition_id):
        return {
            "mts": "0.01",
            "mos": "1",
            "itode": False,
            "fd": {"r": "0.05", "e": 1, "to": True},
        }

    def fee_rate_bps(self, _asset_id):
        return 500

    def order_book(self, _asset_id):
        if len(self.books) > 1:
            return self.books.pop(0)
        return self.books[0]


class ScanClient:
    def leaderboard(self, _period, _limit):
        return [
            {
                "proxyWallet": "0x3333333333333333333333333333333333333333",
                "pnl": 100,
                "vol": 1000,
                "userName": "prospective",
            }
        ]

    def closed_positions(self, _address):
        return []


def add_wallet(db: Session) -> Wallet:
    wallet = Wallet(
        address="0x4444444444444444444444444444444444444444",
        username="r43",
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


def matrix_stub():
    metrics = SimpleNamespace(
        win_rate=0.7,
        profit_factor=2.0,
        realized_pnl=100.0,
        volume=1000.0,
        closed_count=100,
        concentration=0.2,
    )
    return SimpleNamespace(
        short_metrics=metrics,
        long_metrics=metrics,
        short_score=90.0,
        long_score=90.0,
        global_score=90.0,
        rejection_reason=None,
        execution_edge_score=50.0,
        execution_edge_sample_size=0,
        average_execution_edge=0.0,
    )


def test_prospective_scan_arms_only_future_activity(monkeypatch):
    local = factory()
    monkeypatch.setattr("app.scanner.score_matrix", lambda *a, **k: matrix_stub())
    before = int(time.time())
    with local() as db:
        initialize_state(db, settings())
        selected = scan_wallets(db, ScanClient(), settings(), prospective=True)
        assert len(selected) == 1
        effective = int(get_state(db, "paper_v5_selection_effective_at", "0"))
        assert effective > before
        assert selected[0].last_activity_at >= effective


def test_r43_ignores_activity_that_predates_active_selection():
    local = factory()
    now = int(time.time())
    client = ExecutionClient([book(asks=[(0.51, 100)], bids=[(0.49, 100)])])
    with local() as db:
        initialize_state(db, settings())
        wallet = add_wallet(db)
        set_state(db, CYCLE_SELECTION_EFFECTIVE_STATE, str(now))
        db.commit()
        handled = PaperEngineV5R43(settings(), client).process(
            db, wallet, activity(now - 1, "0xpreselection")
        )
        assert handled is False
        assert db.scalar(select(PaperV5Prediction)) is None
        event = db.scalar(
            select(AuditEvent).where(
                AuditEvent.event_type == "paper_v5_r43_preselection_activity_ignored"
            )
        )
        assert event is not None


def test_r43_valid_activity_persists_recomputable_selection_evidence(tmp_path: Path):
    local = factory()
    now = int(time.time())
    client = ExecutionClient(
        [
            book(asks=[(0.51, 100)], bids=[(0.49, 100)], suffix="1"),
            book(asks=[(0.52, 100)], bids=[(0.48, 100)], suffix="2"),
        ]
    )
    with local() as db:
        initialize_state(db, settings())
        wallet = add_wallet(db)
        set_state(db, CYCLE_SELECTION_EFFECTIVE_STATE, str(now - 1))
        db.commit()
        assert PaperEngineV5R43(settings(), client).process(
            db, wallet, activity(now, "0xprospective")
        ) is True
        prediction = db.scalar(select(PaperV5Prediction))
        assert prediction is not None
        evidence = db.get(PaperV5ExecutionEvidence, prediction.id)
        assert evidence is not None
        event = db.scalar(select(AuditEvent).where(AuditEvent.event_type == SELECTION_EVENT))
        assert event is not None
        payload = json.loads(event.payload_json)
        assert payload["selection_effective_at"] == now - 1
        assert payload["source_timestamp"] == now
        assert _selection_payload_hash_valid(payload) is True
        assert _selection_evidence_binding_valid(
            payload, evidence.execution_evidence_hash
        ) is True
        assert len(payload["r4_2_execution_evidence_hash"]) == 64
        assert len(payload["r4_3_execution_evidence_hash"]) == 64
        assert payload["r4_2_execution_evidence_hash"] != payload["r4_3_execution_evidence_hash"]

        path = tmp_path / "ledger.jsonl"
        _write_ledger_r43(legacy._write_ledger, db, path)
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        assert len(rows) == 1
        row = rows[0]
        assert row["selection_provenance"]["prospective_selection"] is True
        assert (
            row["selection_provenance"]["source_timestamp"]
            >= row["selection_provenance"]["selection_effective_at"]
        )
        assert row["selection_evidence_bound"] is True
        assert row["execution_evidence"]["execution_evidence_hash"] == payload[
            "r4_3_execution_evidence_hash"
        ]
        assert row["book_timing"]["decision_book_timestamp_ms"] is not None
        assert row["book_timing"]["arrival_book_timestamp_ms"] is not None
        assert "state timestamp" in row["book_timing"]["timestamp_semantics"]


def test_r43_selection_provenance_tampering_is_detected():
    payload = {
        "prediction_id": 1,
        "wallet": "0x4444444444444444444444444444444444444444",
        "wallet_score": 90.0,
        "selection_effective_at": 100,
        "source_timestamp": 101,
        "prospective_selection": True,
    }
    payload["selection_provenance_hash"] = r43.r4._canonical_hash(payload)
    parent = "a" * 64
    bridged = r43.r4._canonical_hash(
        {
            "r4_2_execution_evidence_hash": parent,
            "selection_provenance_hash": payload["selection_provenance_hash"],
        }
    )
    payload["r4_2_execution_evidence_hash"] = parent
    payload["r4_3_execution_evidence_hash"] = bridged
    assert _selection_payload_hash_valid(payload) is True
    assert _selection_evidence_binding_valid(payload, bridged) is True
    payload["wallet_score"] = 91.0
    assert _selection_payload_hash_valid(payload) is False
    assert _selection_evidence_binding_valid(payload, bridged) is True


def test_r43_mark_client_applies_run_local_shadow_debt():
    local = factory()
    raw = book(asks=[(0.51, 10)], bids=[(0.49, 10)], suffix="1")
    client = ExecutionClient([raw])
    engine = PaperEngineV5R43(settings(), client)
    engine._truth_client._shadow[("asset-r43", "SELL", "0.49")] = 6.0
    with local() as db:
        initialize_state(db, settings())
        db.add(
            PaperV5Position(
                asset_id="asset-r43",
                condition_id="condition-r43",
                market_title="R4.3 prospective market",
                outcome="Yes",
                shares=5.0,
                cost_basis_usd=2.0,
            )
        )
        db.commit()
        marked, errors = legacy.mark_positions_v5(db, engine.mark_client)
        assert errors == []
        assert marked == 1
        position = db.get(PaperV5Position, "asset-r43")
        assert position is not None
        assert position.mark_value_usd == pytest.approx(1.91002, abs=1e-5)
        assert position.mark_value_usd < 5 * 0.49


def test_first_clean_r43_cycle_arms_selection_without_backfill(monkeypatch, tmp_path: Path):
    local = factory()
    monkeypatch.setattr(r43.legacy, "SessionLocal", local)
    monkeypatch.setattr("app.scanner.score_matrix", lambda *a, **k: matrix_stub())

    class CycleClient(ScanClient):
        def activity(self, *_args, **_kwargs):
            raise AssertionError("clean seed cycle must not backfill activity")

    client = CycleClient()
    original_r42_run = r43.r42.run

    def fake_r42_run(_output_dir):
        with local() as db:
            initialize_state(db, settings())
            active = r43.legacy.scan_wallets(db, client, settings())
            assert active == []
            engine = SimpleNamespace(mark_client=client)
            processed, errors = r43.legacy.ingest_activity_v5(db, client, settings(), engine)
            assert processed == 0
            assert errors == []
            next_selected = list(
                db.scalars(select(Wallet).where(Wallet.selected.is_(True))).all()
            )
            assert len(next_selected) == 1
            assert int(get_state(db, "paper_v5_selection_effective_at", "0")) > 0
        return 0

    monkeypatch.setattr(r43.r42, "run", fake_r42_run)
    assert r43.run(tmp_path) == 0
    monkeypatch.setattr(r43.r42, "run", original_r42_run)
