from __future__ import annotations

import copy

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import paper_v5_r44 as r44
from app.config import Settings
from app.db import Base
from app.models import Wallet
from app.models_v5 import PaperV5Execution, PaperV5ExecutionEvidence, PaperV5Prediction
from app.paper_v5_r45 import (
    MIN_EXPLORATORY_SETTLED,
    PaperEngineV5R45,
    _attributable_economic_observations,
    _loss_cluster_metrics,
    _regime_analysis_from_observations,
    _regime_binding_valid,
    _regime_by_prediction,
    _regime_context,
    _regime_context_hash_valid,
)
from app.repository import audit
from app.source_strategy import (
    ActivityHistoryEvidence,
    SourceActivityHistory,
    SourceStrategyPolicy,
    canonical_hash,
    classify_source_strategy,
)


def factory():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False, class_=Session)


def observation(
    ts: int,
    pnl: float,
    *,
    prediction_id: int | None = None,
    weekpart: str = "WEEKDAY",
    bucket: str = "12-15",
):
    return {
        "prediction_id": ts if prediction_id is None else prediction_id,
        "source_timestamp": ts,
        "pnl": pnl,
        "win": pnl > 0,
        "loss": pnl < 0,
        "weekpart": weekpart,
        "utc_hour": 12,
        "utc_4h_bucket": bucket,
    }


def test_regime_context_is_deterministic_utc_and_hash_bound():
    # 2026-08-08T12:34:56Z is Saturday.
    context = _regime_context(1_786_192_496)
    assert context["utc_weekday_index"] == 5
    assert context["utc_weekday"] == "SATURDAY"
    assert context["weekpart"] == "WEEKEND"
    assert context["utc_hour"] == 12
    assert context["utc_4h_bucket"] == "12-15"
    assert _regime_context_hash_valid(context) is True

    tampered = copy.deepcopy(context)
    tampered["utc_hour"] = 13
    assert _regime_context_hash_valid(tampered) is False


def test_regime_hash_bridge_is_recomputable_and_tamper_evident():
    context = _regime_context(1_786_192_496)
    parent = "a" * 64
    child = r44.r43.r4._canonical_hash(
        {
            "r4_4_execution_evidence_hash": parent,
            "regime_context_hash": context["regime_context_hash"],
        }
    )
    payload = {
        "prediction_id": 1,
        "regime_context": context,
        "r4_4_execution_evidence_hash": parent,
        "r4_5_execution_evidence_hash": child,
    }
    assert _regime_binding_valid(payload, child) is True

    tampered = copy.deepcopy(payload)
    tampered["regime_context"]["weekpart"] = "WEEKDAY"
    assert _regime_binding_valid(tampered, child) is False


def test_loss_clustering_uses_only_attributable_economic_losses():
    rows = [
        observation(100, -1),
        observation(600, -2),
        observation(1200, -3),
        observation(1800, 1),
        observation(2400, -1),
        observation(3000, -1),
        observation(5000, -1),
    ]
    metrics = _loss_cluster_metrics(rows)
    assert metrics["max_consecutive_attributable_economic_losses"] == 3
    assert metrics["max_attributable_economic_losses_in_rolling_60m"] == 5


def test_loss_cluster_timestamp_ties_are_deterministic_by_prediction_id():
    a = observation(1_700_000_000, -1, prediction_id=1)
    b = observation(1_700_000_000, 1, prediction_id=2)
    c = observation(1_700_000_000, -1, prediction_id=3)
    forward = _loss_cluster_metrics([a, b, c])
    reversed_input = _loss_cluster_metrics([c, b, a])
    assert forward == reversed_input
    assert forward["max_consecutive_attributable_economic_losses"] == 1


def test_regime_analysis_never_turns_anecdote_into_execution_gate():
    rows = [observation(1_700_000_000 + i * 60, 1 if i % 2 else -1) for i in range(10)]
    analysis = _regime_analysis_from_observations(rows)
    assert analysis["state"] == "INSUFFICIENT_EVIDENCE"
    assert analysis["settled_observations"] == 10
    assert analysis["attributable_economic_observations"] == 10
    assert analysis["minimum_settled_for_exploratory_breakdown"] == MIN_EXPLORATORY_SETTLED
    assert analysis["automatic_execution_gate"] is False
    assert analysis["out_of_sample_confirmation_required"] is True
    assert analysis["weekday_weekend_claim_verified"] is False
    assert analysis["time_of_day_claim_verified"] is False
    assert analysis["naive_strategy_inversion_allowed"] is False


def test_unattributable_settlements_are_excluded_from_pnl_and_loss_clusters():
    directional = [
        observation(1_700_000_000, -1),
        observation(1_700_000_060, -1),
        observation(1_700_000_120, -1),
    ]
    economic = [directional[0]]
    analysis = _regime_analysis_from_observations(directional, economic)
    assert analysis["resolved_directional_observations"] == 3
    assert analysis["attributable_economic_observations"] == 1
    assert analysis["unattributable_economic_observations"] == 2
    assert analysis["loss_clustering"]["max_consecutive_attributable_economic_losses"] == 1
    assert analysis["economic_by_weekpart"]["WEEKDAY"]["attributable_settled"] == 1


def _prediction(
    *,
    source_key: str,
    wallet: str,
    asset: str,
    side: str,
    timestamp: int,
    tx: str,
    payload_hash: str,
    resolved: bool,
) -> PaperV5Prediction:
    return PaperV5Prediction(
        source_key=source_key,
        wallet_address=wallet,
        wallet_score=90,
        condition_id="condition-1",
        asset_id=asset,
        market_title="Market",
        outcome="YES",
        side=side,
        source_price=0.5,
        source_size=10,
        source_usdc=5,
        source_timestamp=timestamp,
        transaction_hash=tx,
        source_payload_hash=payload_hash,
        decision="FILLED",
        decision_reason="arrival_book_fak",
        resolution_status="RESOLVED" if resolved else "NOT_APPLICABLE",
        resolution_price=1.0 if resolved else None,
        result="WIN" if resolved else "EXIT",
    )


def test_filled_exit_excludes_original_buy_from_regime_pnl():
    local = factory()
    with local() as db:
        wallet = "0x2222222222222222222222222222222222222222"
        buy = _prediction(
            source_key="buy",
            wallet=wallet,
            asset="asset-1",
            side="BUY",
            timestamp=1_700_000_000,
            tx="0xbuy",
            payload_hash="a" * 64,
            resolved=True,
        )
        sell = _prediction(
            source_key="sell",
            wallet=wallet,
            asset="asset-1",
            side="SELL",
            timestamp=1_700_000_100,
            tx="0xsell",
            payload_hash="b" * 64,
            resolved=False,
        )
        db.add_all([buy, sell])
        db.flush()
        db.add_all(
            [
                PaperV5Execution(
                    prediction_id=buy.id,
                    status="FILLED",
                    filled_shares=10,
                    gross_notional=5,
                    fee_usd=0.1,
                    net_cash_delta=-5.1,
                ),
                PaperV5Execution(
                    prediction_id=sell.id,
                    status="FILLED",
                    filled_shares=4,
                    gross_notional=2.4,
                    fee_usd=0.05,
                    net_cash_delta=2.35,
                ),
            ]
        )
        db.commit()
        assert _attributable_economic_observations(db) == []


def _strategy_profile(wallet: str) -> dict:
    rows = [
        {
            "type": "TRADE",
            "transactionHash": f"0x{i:064x}",
            "conditionId": f"condition-{i}",
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
    evidence = ActivityHistoryEvidence(
        status="COMPLETE",
        scope="FULL_AVAILABLE_FILTERED_HISTORY",
        requested_limit=len(rows) + 1,
        returned_rows=len(rows),
        pages_fetched=1,
        page_size=len(rows) + 1,
        exhausted=True,
        has_more=False,
        malformed_rows=0,
        invalid_timestamp_rows=0,
        source_hash=canonical_hash("r45-test-fixture"),
    )
    return classify_source_strategy(
        wallet,
        SourceActivityHistory(rows, evidence),
        cutoff_at=1_700_000_100,
        policy=SourceStrategyPolicy(
            min_trade_count=5,
            min_paired_conditions=2,
            max_paired_trade_fraction=0.5,
        ),
    ).to_dict()


def test_retry_repairs_missing_r45_provenance_after_inherited_commit(monkeypatch):
    local = factory()
    wallet_address = "0x2222222222222222222222222222222222222222"
    activity = {
        "type": "TRADE",
        "transactionHash": "0x" + "1" * 64,
        "conditionId": "condition-1",
        "asset": "asset-1",
        "side": "BUY",
        "outcome": "YES",
        "outcomeIndex": 0,
        "timestamp": 1_700_000_200,
        "price": 0.5,
        "size": 10,
        "usdcSize": 5,
    }
    source_key, payload_hash = r44.r43.r4.legacy._source_identity(wallet_address, activity)
    with local() as db:
        wallet = Wallet(address=wallet_address, score=90, selected=True)
        prediction = _prediction(
            source_key=source_key,
            wallet=wallet_address,
            asset="asset-1",
            side="BUY",
            timestamp=activity["timestamp"],
            tx=activity["transactionHash"],
            payload_hash=payload_hash,
            resolved=False,
        )
        db.add_all([wallet, prediction])
        db.flush()

        profile = _strategy_profile(wallet_address)
        r43_parent = "c" * 64
        r44_child = r44.r43.r4._canonical_hash(
            {
                "r4_3_execution_evidence_hash": r43_parent,
                "source_strategy_evidence_hash": profile["evidence_hash"],
            }
        )
        db.add(
            PaperV5ExecutionEvidence(
                prediction_id=prediction.id,
                market_metadata_hash="d" * 64,
                execution_evidence_hash=r44_child,
            )
        )
        audit(
            db,
            r44.STRATEGY_EVENT,
            "existing valid R4.4 strategy parent",
            prediction_id=prediction.id,
            source_strategy_profile=profile,
            r4_3_execution_evidence_hash=r43_parent,
            r4_4_execution_evidence_hash=r44_child,
        )
        db.commit()

        monkeypatch.setattr(r44.PaperEngineV5R44, "process", lambda *args, **kwargs: False)
        engine = PaperEngineV5R45(
            Settings(trading_mode="PAPER", paper_trading_enabled=True),
            object(),
        )
        handled = engine.process(db, wallet, activity)
        assert handled is False
        regime = _regime_by_prediction(db)[prediction.id]
        evidence = db.get(PaperV5ExecutionEvidence, prediction.id)
        assert regime["repaired_after_dedupe"] is True
        assert _regime_binding_valid(regime, evidence.execution_evidence_hash) is True


def test_even_fifty_settlements_remain_exploratory_not_auto_gated():
    rows = [observation(1_700_000_000 + i * 60, 1.0) for i in range(MIN_EXPLORATORY_SETTLED)]
    analysis = _regime_analysis_from_observations(rows)
    assert analysis["state"] == "EXPLORATORY_ONLY"
    assert analysis["automatic_execution_gate"] is False
    assert analysis["out_of_sample_confirmation_required"] is True
