from __future__ import annotations

import json
from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db import Base
from app.models import Wallet
from app.paper_v5_r44 import (
    ACTIVE_PROFILES_STATE,
    PaperEngineV5R44,
    _profile_predates_selection,
    _strategy_binding_valid,
)
from app.repository import initialize_state, set_state
from app.scanner import scan_wallets
from app.source_strategy import SourceStrategyPolicy, classify_source_strategy


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
        candidate_limit=3,
        tracked_wallet_limit=1,
        source_strategy_activity_limit=30,
        source_strategy_min_trade_count=5,
        source_strategy_min_paired_conditions=2,
        source_strategy_max_paired_trade_fraction=0.5,
    )


def matrix_stub():
    metrics = SimpleNamespace(
        win_rate=0.7,
        profit_factor=2.0,
        realized_pnl=100.0,
        volume=1000.0,
        closed_count=100,
        decided_count=100,
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


def trades(wallet: str):
    return [
        {
            "type": "TRADE",
            "transactionHash": f"0x{i:064x}",
            "conditionId": f"condition-{wallet[-1]}-{i}",
            "asset": f"asset-{i}",
            "side": "BUY",
            "outcomeIndex": 0,
            "timestamp": 1_700_000_000 + i,
            "price": 0.5,
            "size": 10,
            "usdcSize": 5,
        }
        for i in range(5)
    ]


class ScanClient:
    settings = SimpleNamespace(data_api_base="https://data.test")
    maker = "0x1111111111111111111111111111111111111111"
    directional = "0x2222222222222222222222222222222222222222"

    def leaderboard(self, _period, _limit):
        return [
            {"proxyWallet": self.maker, "pnl": 200, "vol": 2000, "userName": "maker"},
            {
                "proxyWallet": self.directional,
                "pnl": 100,
                "vol": 1000,
                "userName": "directional",
            },
        ]

    def closed_positions(self, _address, *, limit=1000):
        assert limit == 1000
        return []

    def _get(self, url, params=None):
        if url == "https://data.test/closed-positions":
            return []
        assert url == "https://data.test/activity"
        wallet = params["user"]
        rows = trades(wallet)
        if wallet == self.maker:
            rows.append({"type": "MAKER_REBATE", "timestamp": 1_700_000_100})
        return rows if int(params.get("offset") or 0) == 0 else []


def test_prospective_source_gate_skips_maker_and_selects_directional(monkeypatch):
    local = factory()
    monkeypatch.setattr("app.scanner.score_matrix", lambda *a, **k: matrix_stub())
    with local() as db:
        initialize_state(db, settings())
        selected = scan_wallets(
            db,
            ScanClient(),
            settings(),
            prospective=True,
            source_strategy_gate=True,
        )
        assert [wallet.address for wallet in selected] == [ScanClient.directional]
        maker = db.get(Wallet, ScanClient.maker)
        assert maker is not None
        assert maker.rejection_reason == "source_strategy_maker_rebate"


def test_r44_engine_fails_closed_without_directional_profile():
    local = factory()
    with local() as db:
        initialize_state(db, settings())
        wallet = Wallet(
            address=ScanClient.directional,
            score=90,
            selected=True,
        )
        db.add(wallet)
        set_state(db, ACTIVE_PROFILES_STATE, "[]")
        db.commit()
        engine = PaperEngineV5R44(settings(), SimpleNamespace())
        try:
            engine.process(db, wallet, {"timestamp": 1_700_000_000})
        except RuntimeError as exc:
            assert "source_strategy_directional_provenance" in str(exc)
        else:
            raise AssertionError("R4.4 must fail closed without strategy provenance")


def test_source_profile_must_predate_selection_effective_time():
    profile = {"cutoff_at": 1_700_000_000}
    assert _profile_predates_selection(profile, 1_700_000_001) is True
    assert _profile_predates_selection(profile, 1_700_000_000) is False
    assert _profile_predates_selection(profile, 1_699_999_999) is False
    assert _profile_predates_selection({"cutoff_at": 0}, 1_700_000_001) is False


def test_strategy_hash_bridge_is_recomputable_and_tamper_evident():
    profile = classify_source_strategy(
        ScanClient.directional,
        trades(ScanClient.directional),
        cutoff_at=1_900_000_000,
        policy=SourceStrategyPolicy(
            min_trade_count=5,
            min_paired_conditions=2,
            max_paired_trade_fraction=0.5,
        ),
    ).to_dict()
    parent = "a" * 64
    from app import paper_v5_r43 as r43

    child = r43.r4._canonical_hash(
        {
            "r4_3_execution_evidence_hash": parent,
            "source_strategy_evidence_hash": profile["evidence_hash"],
        }
    )
    payload = {
        "prediction_id": 1,
        "source_strategy_profile": profile,
        "r4_3_execution_evidence_hash": parent,
        "r4_4_execution_evidence_hash": child,
    }
    assert _strategy_binding_valid(payload, child) is True
    tampered = json.loads(json.dumps(payload))
    tampered["source_strategy_profile"]["classification"] = "NON_DIRECTIONAL_MAKER"
    assert _strategy_binding_valid(tampered, child) is False
