from __future__ import annotations

from dataclasses import dataclass
from math import log, sqrt
from statistics import fmean
from typing import Iterable


@dataclass(frozen=True)
class CalibrationMetrics:
    sample_size: int
    brier_score: float | None
    calibration_error: float | None


@dataclass(frozen=True)
class ClosingLineMetrics:
    sample_size: int
    average_clv: float | None
    positive_clv_rate: float | None


def brier_score(probabilities: Iterable[float], outcomes: Iterable[int]) -> CalibrationMetrics:
    probabilities_list = [float(value) for value in probabilities]
    outcomes_list = [int(value) for value in outcomes]
    if len(probabilities_list) != len(outcomes_list):
        raise ValueError("probabilities and outcomes must have equal length")
    if not probabilities_list:
        return CalibrationMetrics(0, None, None)
    if any(not 0 <= value <= 1 for value in probabilities_list):
        raise ValueError("probabilities must be between 0 and 1")
    if any(value not in {0, 1} for value in outcomes_list):
        raise ValueError("outcomes must be binary")
    score = fmean(
        (probability - outcome) ** 2
        for probability, outcome in zip(probabilities_list, outcomes_list, strict=True)
    )
    calibration_error = abs(fmean(probabilities_list) - fmean(outcomes_list))
    return CalibrationMetrics(len(probabilities_list), score, calibration_error)


def closing_line_value(entries: Iterable[float], closes: Iterable[float]) -> ClosingLineMetrics:
    entry_list = [float(value) for value in entries]
    close_list = [float(value) for value in closes]
    if len(entry_list) != len(close_list):
        raise ValueError("entries and closes must have equal length")
    if not entry_list:
        return ClosingLineMetrics(0, None, None)
    if any(not 0 < value < 1 for value in entry_list + close_list):
        raise ValueError("binary-market prices must be strictly between 0 and 1")
    values = [close - entry for entry, close in zip(entry_list, close_list, strict=True)]
    return ClosingLineMetrics(
        sample_size=len(values),
        average_clv=fmean(values),
        positive_clv_rate=sum(value > 0 for value in values) / len(values),
    )


def edge_velocity(edge: float, age_seconds: float, *, half_life_seconds: float = 30.0) -> float:
    if age_seconds < 0 or half_life_seconds <= 0:
        raise ValueError("invalid edge velocity inputs")
    decay = 0.5 ** (age_seconds / half_life_seconds)
    return float(edge) * decay


def deflated_signal_score(
    observed_sharpe: float,
    *,
    trials: int,
    sample_size: int,
) -> float:
    """Conservative multiple-testing penalty; research score, not a p-value."""
    if trials <= 0 or sample_size <= 1:
        raise ValueError("trials must be positive and sample_size > 1")
    penalty = sqrt(2.0 * max(0.0, log(trials))) / sqrt(sample_size)
    return observed_sharpe - penalty
