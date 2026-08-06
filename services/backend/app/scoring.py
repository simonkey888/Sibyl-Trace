from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import WalletMetrics, compute_wallet_metrics, wallet_score
from app.models import PaperOrder, Signal


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


def score_matrix(
    db: Session,
    wallet_address: str,
    closed_positions: list[dict],
    *,
    volume: float,
) -> ScoreMatrix:
    short_positions = closed_positions[:50]
    short_metrics = compute_wallet_metrics(short_positions, volume=volume)
    long_metrics = compute_wallet_metrics(closed_positions, volume=volume)
    short_score, short_rejection = wallet_score(short_metrics)
    long_score, long_rejection = wallet_score(long_metrics)

    rejection = long_rejection or short_rejection
    if rejection is None:
        global_score = round(0.60 * short_score + 0.40 * long_score, 2)
    else:
        global_score = 0.0

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
