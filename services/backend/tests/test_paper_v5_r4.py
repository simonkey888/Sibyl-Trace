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
from app.config import Settings
from app.db import Base
from app.models import Wallet
from app.models_v5 import PaperV5Execution, PaperV5ExecutionEvidence, PaperV5Prediction
from app.paper_v5_r4 import (
    PaperEngineV5R4,
    _apply_r4_report,
    _canonical_hash,
    _market_by_condition,
    _rules_from_official_metadata,
    _status_counts,
    _write_ledger_r4,
)
from app.paper_v5_r4 import (
    run as run_r4,
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
        self.settings = SimpleNamespace(gamma_api_base="https://gamma.test")

    def _get(self, url, params=None):
        assert url == "https://gamma.test/markets"
        assert params == {"condition_ids": ["condition-r4"], "limit": 10}
        return [dict(self.market_data)]

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


def test_market_lookup_and_hash_are_deterministic():
    client = FakeClient([])
    resolved = _market_by_condition(client, "condition-r4")
    assert resolved["conditionId"] == "condition-r4"
    first = _canonical_hash({"b": 2, "a": 1})
    second = _canonical_hash({"a": 1, "b": 2})
    assert first == second
    assert len(first) == 64

    client.market_data = {"conditionId": "other"}
    with pytest.raises(Exception, match="did not match requested condition"):
        _market_by_condition(client, "condition-r4")


def test_r4_report_reconciles_cycle_counts_and_ledger(tmp_path, monkeypatch):
    local = factory()
    client = FakeClient(
        [
            book(asks=[(0.51, 100)], bids=[(0.49, 100)], suffix="1"),
            book(asks=[(0.51, 100)], bids=[(0.48, 100)], suffix="2"),
        ]
    )
    monkeypatch.setattr("app.paper_v5_r4.time.sleep", lambda _seconds: None)
    with local() as db:
        initialize_state(db, settings())
        wallet = add_wallet(db)
        baseline = _status_counts(db)
        assert baseline["predictions"] == 0
        PaperEngineV5R4(settings(), client).process(db, wallet, activity("0xreconcile"))
        current = _status_counts(db)
        assert current["predictions"] == current["executions"] == 1
        assert current["FILLED"] + current["PARTIAL_FILLED"] == 1
        assert current["decision_books"] == current["arrival_books"] == 1

        report = {
            "status": "PASS",
            "run": {"errors": []},
            "methodology": {"midpoint_fills": False},
            "cycle": {"signals_processed": 1},
        }
        reconciled = _apply_r4_report(report, db, baseline)
        assert reconciled["status"] == "PASS"
        assert reconciled["evidence_reconciliation"]["state"] == "PASS"
        assert reconciled["cycle"]["new_predictions_created"] == 1
        assert reconciled["cycle"]["new_executions_created"] == 1
        assert reconciled["methodology"]["synthetic_canonical_latency"] is False
        assert reconciled["methodology"]["summary_ledger_reconciliation"] is True

        ledger = tmp_path / "ledger.jsonl"
        _write_ledger_r4(legacy._write_ledger, db, ledger)
        rows = [json.loads(line) for line in ledger.read_text().splitlines()]
        assert len(rows) == 1
        evidence = rows[0]["execution_evidence"]
        assert len(evidence["execution_evidence_hash"]) == 64
        assert evidence["actual_gap_ms"] >= 0
        assert evidence["market_state"]["acceptingOrders"] is True


def test_reconciliation_fails_closed_on_cycle_count_contradiction():
    local = factory()
    with local() as db:
        initialize_state(db, settings())
        report = {
            "status": "PASS",
            "run": {"errors": []},
            "methodology": {},
            "cycle": {"signals_processed": 1},
        }
        reconciled = _apply_r4_report(report, db, _status_counts(db))
        assert reconciled["status"] == "DEGRADED"
        assert reconciled["evidence_reconciliation"]["state"] == "FAIL"
        assert any(
            "cycle_processed_prediction_mismatch" in error for error in reconciled["run"]["errors"]
        )


def test_empty_decision_book_is_evidence_backed_no_fill():
    local = factory()
    client = FakeClient([book(asks=[], bids=[], suffix="1")])
    with local() as db:
        initialize_state(db, settings())
        wallet = add_wallet(db)
        assert PaperEngineV5R4(settings(), client).process(db, wallet, activity("0xempty")) is True
        prediction = db.scalar(select(PaperV5Prediction))
        execution = db.scalar(select(PaperV5Execution))
        evidence = db.scalar(select(PaperV5ExecutionEvidence))
        assert prediction.result == "NO_FILL"
        assert execution.status == "NO_FILL"
        assert execution.reason == "empty_executable_book"
        assert evidence is not None
        assert evidence.decision_received_at_ms is not None
        assert evidence.arrival_received_at_ms is None


def test_large_decision_slippage_is_rejected_before_arrival():
    local = factory()
    client = FakeClient([book(asks=[(0.80, 100)], bids=[(0.79, 100)], suffix="1")])
    with local() as db:
        initialize_state(db, settings())
        wallet = add_wallet(db)
        PaperEngineV5R4(settings(), client).process(db, wallet, activity("0xslip"))
        prediction = db.scalar(select(PaperV5Prediction))
        execution = db.scalar(select(PaperV5Execution))
        evidence = db.scalar(select(PaperV5ExecutionEvidence))
        assert prediction.result == "REJECTED"
        assert execution.status == "REJECTED"
        assert execution.decision_book_hash == "r4-book-1"
        assert execution.arrival_book_hash is None
        assert evidence is not None


def test_active_market_arrival_404_is_data_failure(monkeypatch):
    class Response:
        status_code = 404

    class MissingBook(Exception):
        response = Response()

    class Client(FakeClient):
        def __init__(self):
            super().__init__([book(asks=[(0.51, 100)], bids=[(0.49, 100)], suffix="1")])
            self.calls = 0

        def order_book(self, _asset_id):
            self.calls += 1
            if self.calls == 1:
                return self.books[0]
            raise MissingBook("arrival missing")

    local = factory()
    monkeypatch.setattr("app.paper_v5_r4.time.sleep", lambda _seconds: None)
    with local() as db:
        initialize_state(db, settings())
        wallet = add_wallet(db)
        PaperEngineV5R4(settings(), Client()).process(db, wallet, activity("0xarrival404"))
        prediction = db.scalar(select(PaperV5Prediction))
        execution = db.scalar(select(PaperV5Execution))
        evidence = db.scalar(select(PaperV5ExecutionEvidence))
        assert prediction.result == "REJECTED"
        assert execution.status == "REJECTED"
        assert "active_market_book_404" in execution.reason
        assert execution.decision_book_hash == "r4-book-1"
        assert evidence is not None
        assert evidence.decision_received_at_ms is not None
        assert evidence.arrival_received_at_ms is None


def test_non_paper_mode_fails_before_market_access(monkeypatch):
    local = factory()
    client = FakeClient([])

    def fake_state(_db, key, default=None):
        return "LIVE" if key == "mode" else default

    monkeypatch.setattr("app.paper_v5_r4.get_state", fake_state)
    with local() as db:
        wallet = add_wallet(db)
        PaperEngineV5R4(settings(), client).process(db, wallet, activity("0xmode"))
        execution = db.scalar(select(PaperV5Execution))
        assert execution.status == "REJECTED"
        assert execution.reason == "system_not_in_paper_mode"


def test_official_seconds_delay_invalid_values_fail_closed():
    info = {
        "mts": "0.01",
        "mos": "1",
        "fd": {"r": "0.05", "e": 1, "to": True},
    }
    with pytest.raises(ValueError, match="invalid_official_seconds_delay"):
        _rules_from_official_metadata(info, market(secondsDelay="not-a-number"))
    with pytest.raises(ValueError, match="unsupported_official_seconds_delay"):
        _rules_from_official_metadata(info, market(secondsDelay=-1))
    with pytest.raises(ValueError, match="unsupported_official_seconds_delay"):
        _rules_from_official_metadata(info, market(secondsDelay=301))


def test_sell_without_position_rejects_before_market_io():
    class NoMarketClient(FakeClient):
        def _get(self, _url, params=None):
            raise AssertionError("market I/O must not run for impossible SELL")

        def order_book(self, _asset_id):
            raise AssertionError("book I/O must not run for impossible SELL")

    local = factory()
    sell = activity("0xsell-none")
    sell["side"] = "SELL"
    with local() as db:
        initialize_state(db, settings())
        wallet = add_wallet(db)
        PaperEngineV5R4(settings(), NoMarketClient([])).process(db, wallet, sell)
        prediction = db.scalar(select(PaperV5Prediction))
        execution = db.scalar(select(PaperV5Execution))
        evidence = db.scalar(select(PaperV5ExecutionEvidence))
        assert prediction.result == "REJECTED"
        assert execution.status == "REJECTED"
        assert execution.reason == "no_paper_position_to_sell"
        assert execution.decision_book_hash is None
        assert evidence is None


def test_run_wrapper_installs_and_restores_r4_contract(monkeypatch, tmp_path):
    local = factory()
    original_cohort = legacy.COHORT_ID
    original_engine = legacy.PaperEngineV5
    original_build = legacy.build_report
    original_writer = legacy._write_ledger
    original_model = legacy.EXECUTION_MODEL
    observed = {}

    monkeypatch.setattr(legacy, "init_db", lambda: None)
    monkeypatch.setattr(legacy, "SessionLocal", local)

    def fake_run(output_dir: Path):
        observed["output_dir"] = output_dir
        observed["cohort"] = legacy.COHORT_ID
        observed["engine"] = legacy.PaperEngineV5
        observed["model"] = legacy.EXECUTION_MODEL
        assert legacy.build_report is not original_build
        assert legacy._write_ledger is not original_writer
        return 0

    monkeypatch.setattr(legacy, "run", fake_run)
    assert run_r4(tmp_path) == 0
    assert observed["output_dir"] == tmp_path
    assert observed["cohort"] == "PAPER_V5_R4_AUDIT_RECONCILIATION_2026_08_07"
    assert observed["engine"] is PaperEngineV5R4
    assert observed["model"] == "L2_TAKER_FAK_ARRIVAL_BOOK_V2_AUDIT_RECONCILED"
    assert original_cohort == legacy.COHORT_ID
    assert legacy.PaperEngineV5 is original_engine
    assert legacy.build_report is original_build
    assert legacy._write_ledger is original_writer
    assert original_model == legacy.EXECUTION_MODEL
