from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import fmean, pstdev
from typing import Any, Iterable

from app.evidence import hash_payload
from app.research import payout_asymmetry


PROTECTED_CONFIG_KEYS = frozenset(
    {
        "live_trading_enabled",
        "real_money_enabled",
        "initial_bankroll_usd",
        "risk_max_daily_loss_pct",
        "risk_max_drawdown_pct",
        "risk_max_total_exposure_pct",
        "cost_authorized_usd",
    }
)


@dataclass(frozen=True)
class SeriesMetrics:
    sample_size: int
    total_pnl: float
    mean_pnl: float
    sharpe_like: float
    max_drawdown: float
    win_rate: float
    payoff_ratio: float
    break_even_win_rate: float
    expectancy_r: float


@dataclass(frozen=True)
class WalkForwardSplit:
    train_start: int
    train_end: int
    test_start: int
    test_end: int


@dataclass(frozen=True)
class HypothesisSpec:
    hypothesis_id: str
    kind: str
    thesis: str
    config: dict[str, Any]
    expected_metric: str
    minimum_sample: int
    minimum_expectancy_r: float
    maximum_drawdown: float
    parent_id: str | None = None


@dataclass(frozen=True)
class HypothesisEvaluation:
    decision: str
    metrics: SeriesMetrics
    breaker_metrics: SeriesMetrics
    reasons: tuple[str, ...]


def series_metrics(pnls: Iterable[float]) -> SeriesMetrics:
    values = [float(value) for value in pnls]
    asymmetry = payout_asymmetry(values)
    if not values:
        return SeriesMetrics(0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0)
    mean = fmean(values)
    volatility = pstdev(values) if len(values) > 1 else 0.0
    sharpe_like = mean / volatility * math.sqrt(len(values)) if volatility > 0 else 0.0
    equity = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for value in values:
        equity += value
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
    return SeriesMetrics(
        sample_size=len(values),
        total_pnl=sum(values),
        mean_pnl=mean,
        sharpe_like=sharpe_like,
        max_drawdown=max_drawdown,
        win_rate=asymmetry.win_rate,
        payoff_ratio=asymmetry.payoff_ratio,
        break_even_win_rate=asymmetry.break_even_win_rate,
        expectancy_r=asymmetry.expectancy_r,
    )


def walk_forward_splits(
    sample_size: int,
    *,
    train_size: int,
    test_size: int,
    step_size: int | None = None,
) -> list[WalkForwardSplit]:
    if min(sample_size, train_size, test_size) < 0 or train_size == 0 or test_size == 0:
        raise ValueError("sample, train, and test sizes must be valid")
    step = step_size or test_size
    if step <= 0:
        raise ValueError("step_size must be positive")
    splits: list[WalkForwardSplit] = []
    train_start = 0
    while train_start + train_size + test_size <= sample_size:
        train_end = train_start + train_size
        test_end = train_end + test_size
        splits.append(WalkForwardSplit(train_start, train_end, train_end, test_end))
        train_start += step
    return splits


def breaker_pnls(
    gross_pnls: Iterable[float],
    *,
    transaction_cost_per_trade: float,
    cost_multiplier: float = 2.0,
) -> list[float]:
    if transaction_cost_per_trade < 0 or cost_multiplier < 1:
        raise ValueError("breaker costs must be non-negative and multiplier >= 1")
    penalty = transaction_cost_per_trade * cost_multiplier
    return [float(value) - penalty for value in gross_pnls]


def make_hypothesis(
    *,
    kind: str,
    thesis: str,
    config: dict[str, Any],
    expected_metric: str = "expectancy_r",
    minimum_sample: int = 30,
    minimum_expectancy_r: float = 0.0,
    maximum_drawdown: float = 10.0,
    parent_id: str | None = None,
) -> HypothesisSpec:
    preregistration = {
        "kind": kind,
        "thesis": thesis,
        "config": config,
        "expected_metric": expected_metric,
        "minimum_sample": minimum_sample,
        "minimum_expectancy_r": minimum_expectancy_r,
        "maximum_drawdown": maximum_drawdown,
        "parent_id": parent_id,
    }
    return HypothesisSpec(
        hypothesis_id=hash_payload(preregistration)[:24],
        kind=kind,
        thesis=thesis,
        config=dict(config),
        expected_metric=expected_metric,
        minimum_sample=minimum_sample,
        minimum_expectancy_r=minimum_expectancy_r,
        maximum_drawdown=maximum_drawdown,
        parent_id=parent_id,
    )


def evaluate_hypothesis(
    spec: HypothesisSpec,
    pnls: Iterable[float],
    *,
    transaction_cost_per_trade: float = 0.0,
) -> HypothesisEvaluation:
    values = [float(value) for value in pnls]
    metrics = series_metrics(values)
    breaker = series_metrics(
        breaker_pnls(
            values,
            transaction_cost_per_trade=transaction_cost_per_trade,
            cost_multiplier=2.0,
        )
    )
    reasons: list[str] = []
    if metrics.sample_size < spec.minimum_sample:
        reasons.append("insufficient_sample")
    if metrics.expectancy_r <= spec.minimum_expectancy_r:
        reasons.append("expectancy_not_positive")
    if metrics.max_drawdown > spec.maximum_drawdown:
        reasons.append("drawdown_exceeded")
    if breaker.sample_size >= spec.minimum_sample and breaker.expectancy_r <= 0:
        reasons.append("fails_double_cost_breaker")

    if "insufficient_sample" in reasons:
        decision = "CONTINUE_RESEARCH"
    elif reasons:
        decision = "REVISE_STRATEGY"
    else:
        decision = "CONTINUE_RESEARCH"
    return HypothesisEvaluation(decision, metrics, breaker, tuple(reasons))


def propose_variant(
    parent: HypothesisSpec,
    changes: dict[str, Any],
    *,
    rationale: str,
) -> HypothesisSpec:
    protected = PROTECTED_CONFIG_KEYS.intersection(changes)
    if protected:
        raise ValueError(f"candidate cannot mutate protected keys: {sorted(protected)}")
    config = {**parent.config, **changes, "proposal_rationale": rationale}
    return make_hypothesis(
        kind=parent.kind,
        thesis=parent.thesis,
        config=config,
        expected_metric=parent.expected_metric,
        minimum_sample=parent.minimum_sample,
        minimum_expectancy_r=parent.minimum_expectancy_r,
        maximum_drawdown=parent.maximum_drawdown,
        parent_id=parent.hypothesis_id,
    )
