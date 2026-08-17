from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.models import PaperOrder, Signal
from app.scoring import execution_edge, score_matrix


def session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def closed_history() -> list[dict]:
    recent_pnl = [8.0] * 40 + [-3.0] * 10
    older_pnl = [3.0] * 25 + [-2.0] * 25
    recent = [
        {
            "realizedPnl": pnl,
            "timestamp": 2_000 - index,
            "transactionHash": f"recent-{index:03d}",
        }
        for index, pnl in enumerate(recent_pnl)
    ]
    older = [
        {
            "realizedPnl": pnl,
            "timestamp": 1_000 - index,
            "transactionHash": f"older-{index:03d}",
        }
        for index, pnl in enumerate(older_pnl)
    ]
    return recent + older


def signal(wallet: str, signal_id: int) -> Signal:
    return Signal(
        id=signal_id,
        source_key=f"signal-{signal_id}",
        wallet_address=wallet,
        wallet_score=80,
        condition_id="condition",
        asset_id=f"asset-{signal_id}",
        market_title="Market",
        outcome="YES",
        side="BUY",
        source_price=0.55,
        source_size=10,
        source_usdc=5.5,
        source_timestamp=1,
        transaction_hash=f"tx-{signal_id}",
    )


def order(signal_id: int, observed: float) -> PaperOrder:
    return PaperOrder(
        signal_id=signal_id,
        asset_id=f"asset-{signal_id}",
        condition_id="condition",
        market_title="Market",
        outcome="YES",
        side="BUY",
        requested_usd=1,
        filled_usd=1,
        source_price=0.55,
        observed_price=observed,
        fill_price=observed,
        slippage=observed - 0.55,
        status="FILLED",
    )


def test_score_matrix_separates_short_long_and_global() -> None:
    wallet = "0x" + "1" * 40
    with session() as db:
        matrix = score_matrix(db, wallet, closed_history(), volume=100_000)

    assert matrix.rejection_reason is None
    assert matrix.short_metrics.closed_count == 50
    assert matrix.long_metrics.closed_count == 100
    assert matrix.short_score != matrix.long_score
    assert matrix.global_score == round(
        0.60 * matrix.short_score + 0.40 * matrix.long_score,
        2,
    )
    assert matrix.execution_edge_score == 50
    assert matrix.execution_edge_sample_size == 0


def test_execution_edge_is_directional_and_confidence_weighted() -> None:
    wallet = "0x" + "2" * 40
    with session() as db:
        for index in range(1, 31):
            db.add(signal(wallet, index))
            db.add(order(index, observed=0.52))
        db.commit()
        score, sample_size, average = execution_edge(db, wallet)

    assert sample_size == 30
    assert average == 0.03
    assert score == 100


def test_execution_edge_remains_neutral_without_observed_prices() -> None:
    wallet = "0x" + "3" * 40
    with session() as db:
        score, sample_size, average = execution_edge(db, wallet)

    assert score == 50
    assert sample_size == 0
    assert average == 0
