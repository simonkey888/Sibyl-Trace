from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import WalletMetrics, compute_wallet_metrics, wallet_score
from app.evidence_v1 import canonical_score_windows
from app.models import PaperOrder, Signal, Wallet, WalletScoreProfile


@dataclass(frozen=True)
class ScoreMatrix:
    short_metrics: WalletMetrics
    long_metrics: WalletMetrics
    short_score: float
    long_score: float
    global_score: float
    rejection_reason: str | None
    execution_edge_score: float
    execution_edge_sample_size: int
    average_execution_edge: float


def execution_edge(db: Session, wallet_address: str) -> tuple[float, int, float]:
    rows = db.execute(
        select(PaperOrder.side, PaperOrder.source_price, PaperOrder.observed_price)
        .join(Signal, PaperOrder.signal_id == Signal.id)
        .where(
            Signal.wallet_address == wallet_address,
            PaperOrder.observed_price.is_not(None),
        )
    ).all()
    values: list[float] = []
    for side, source_price, observed_price in rows:
        if observed_price is None:
            continue
        if str(side).upper() == "BUY":
            values.append(float(source_price) - float(observed_price))
        elif str(side).upper() == "SELL":
            values.append(float(observed_price) - float(source_price))
    if not values:
        return 50.0, 0, 0.0

    average = sum(values) / len(values)
    raw = min(max(50.0 + (average / 0.03) * 50.0, 0.0), 100.0)
    confidence = min(len(values) / 30.0, 1.0)
    score = 50.0 + (raw - 50.0) * confidence
    return round(score, 2), len(values), round(average, 6)


def refresh_execution_edge_profiles(db: Session) -> int:
    wallets = list(db.scalars(select(Wallet).where(Wallet.selected.is_(True))).all())
    refreshed = 0
    for wallet in wallets:
        profile = db.get(WalletScoreProfile, wallet.address)
        if profile is None:
            continue
        score, sample_size, average = execution_edge(db, wallet.address)
        profile.execution_edge_score = score
        profile.execution_edge_sample_size = sample_size
        profile.average_execution_edge = average
        profile.updated_at = datetime.now(UTC)
        refreshed += 1
    if refreshed:
        db.commit()
    return refreshed


def score_matrix(
    db: Session,
    wallet_address: str,
    closed_positions: list[dict],
    *,
    volume: float,
) -> ScoreMatrix:
    short_positions, long_positions = canonical_score_windows(closed_positions)
    short_metrics = compute_wallet_metrics(short_positions, volume=volume)
    long_metrics = compute_wallet_metrics(long_positions, volume=volume)
    short_score, short_rejection = wallet_score(short_metrics)
    long_score, long_rejection = wallet_score(long_metrics)

    rejection = long_rejection or short_rejection
    global_score = (
        round(0.60 * short_score + 0.40 * long_score, 2)
        if rejection is None
        else 0.0
    )

    edge_score, edge_sample_size, average_edge = execution_edge(db, wallet_address)
    return ScoreMatrix(
        short_metrics=short_metrics,
        long_metrics=long_metrics,
        short_score=short_score,
        long_score=long_score,
        global_score=global_score,
        rejection_reason=rejection,
        execution_edge_score=edge_score,
        execution_edge_sample_size=edge_sample_size,
        average_execution_edge=average_edge,
    )
