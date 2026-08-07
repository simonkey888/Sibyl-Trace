from __future__ import annotations

import math
import random
from dataclasses import dataclass
from statistics import fmean

from app.calibration import brier_score


@dataclass(frozen=True)
class BrierDecomposition:
    sample_size: int
    brier: float | None
    reliability: float | None
    resolution: float | None
    uncertainty: float | None


@dataclass(frozen=True)
class ForecastAlpha:
    sample_size: int
    model_brier: float | None
    market_brier: float | None
    alpha: float | None
    bootstrap_ci_low: float | None
    bootstrap_ci_high: float | None
    minimum_power_sample: int | None
    status: str


def brier_decomposition(
    probabilities: list[float],
    outcomes: list[int],
    *,
    bins: int = 10,
) -> BrierDecomposition:
    if len(probabilities) != len(outcomes):
        raise ValueError("probabilities and outcomes must have equal length")
    if bins <= 0:
        raise ValueError("bins must be positive")
    metrics = brier_score(probabilities, outcomes)
    if not probabilities:
        return BrierDecomposition(0, None, None, None, None)
    base_rate = fmean(outcomes)
    uncertainty = base_rate * (1.0 - base_rate)
    reliability = 0.0
    resolution = 0.0
    n = len(probabilities)
    for index in range(bins):
        lower = index / bins
        upper = (index + 1) / bins
        members = [
            position
            for position, probability in enumerate(probabilities)
            if lower <= probability < upper or (index == bins - 1 and probability == 1.0)
        ]
        if not members:
            continue
        weight = len(members) / n
        forecast_mean = fmean(probabilities[position] for position in members)
        outcome_mean = fmean(outcomes[position] for position in members)
        reliability += weight * (forecast_mean - outcome_mean) ** 2
        resolution += weight * (outcome_mean - base_rate) ** 2
    return BrierDecomposition(n, metrics.brier_score, reliability, resolution, uncertainty)


def _paired_brier_differences(
    model: list[float], market: list[float], outcomes: list[int]
) -> list[float]:
    if len(model) != len(market) or len(model) != len(outcomes):
        raise ValueError("model, market and outcomes must have equal length")
    return [
        (market_probability - outcome) ** 2 - (model_probability - outcome) ** 2
        for model_probability, market_probability, outcome in zip(
            model, market, outcomes, strict=True
        )
    ]


def bootstrap_mean_ci(
    values: list[float],
    *,
    samples: int = 2_000,
    seed: int = 0,
) -> tuple[float | None, float | None]:
    if not values:
        return None, None
    if samples < 100:
        raise ValueError("bootstrap samples must be at least 100")
    rng = random.Random(seed)
    n = len(values)
    means = sorted(fmean(values[rng.randrange(n)] for _ in range(n)) for _ in range(samples))
    low_index = max(0, math.floor(0.025 * samples) - 1)
    high_index = min(samples - 1, math.ceil(0.975 * samples) - 1)
    return means[low_index], means[high_index]


def minimum_sample_for_alpha(
    alpha: float,
    *,
    paired_diff_sd: float = 0.133,
    z_alpha: float = 1.96,
    z_power: float = 0.84,
) -> int | None:
    if alpha <= 0:
        return None
    if paired_diff_sd <= 0:
        raise ValueError("paired_diff_sd must be positive")
    return math.ceil(((z_alpha + z_power) * paired_diff_sd / alpha) ** 2)


def market_relative_forecast_alpha(
    model_probabilities: list[float],
    market_probabilities: list[float],
    outcomes: list[int],
    *,
    target_alpha: float = 0.02,
    bootstrap_samples: int = 2_000,
) -> ForecastAlpha:
    differences = _paired_brier_differences(model_probabilities, market_probabilities, outcomes)
    if not differences:
        return ForecastAlpha(0, None, None, None, None, None, None, "NO_DATA")
    model = brier_score(model_probabilities, outcomes)
    market = brier_score(market_probabilities, outcomes)
    alpha = fmean(differences)
    low, high = bootstrap_mean_ci(differences, samples=bootstrap_samples)
    minimum = minimum_sample_for_alpha(target_alpha)
    if len(differences) < (minimum or 0):
        status = "UNDERPOWERED"
    elif low is not None and low > 0:
        status = "POSITIVE_SKILL"
    elif high is not None and high < 0:
        status = "NEGATIVE_SKILL"
    else:
        status = "INCONCLUSIVE"
    return ForecastAlpha(
        sample_size=len(differences),
        model_brier=model.brier_score,
        market_brier=market.brier_score,
        alpha=alpha,
        bootstrap_ci_low=low,
        bootstrap_ci_high=high,
        minimum_power_sample=minimum,
        status=status,
    )
