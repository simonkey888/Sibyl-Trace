from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from math import exp, factorial
from statistics import fmean
from typing import Literal


@dataclass(frozen=True)
class PayoutAsymmetry:
    sample_size: int
    wins: int
    losses: int
    win_rate: float
    average_win: float
    average_loss: float
    payoff_ratio: float
    break_even_win_rate: float
    expectancy_r: float
    expectancy_cash: float


@dataclass(frozen=True)
class BookFill:
    side: Literal["BUY", "SELL"]
    requested_shares: float
    filled_shares: float
    average_price: float | None
    notional: float
    unfilled_shares: float
    complete: bool


@dataclass(frozen=True)
class FairEdge:
    model_probability: float
    market_price: float
    gross_edge: float
    costs: float
    net_edge: float
    positive_after_costs: bool


def payout_asymmetry(pnls: Iterable[float]) -> PayoutAsymmetry:
    values = [float(value) for value in pnls if abs(float(value)) > 1e-12]
    wins = [value for value in values if value > 0]
    losses = [-value for value in values if value < 0]
    sample_size = len(values)
    if sample_size == 0:
        return PayoutAsymmetry(0, 0, 0, 0.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0)

    win_rate = len(wins) / sample_size
    average_win = fmean(wins) if wins else 0.0
    average_loss = fmean(losses) if losses else 0.0
    payoff_ratio = average_win / average_loss if average_loss > 0 else 0.0
    break_even = 1.0 / (1.0 + payoff_ratio) if payoff_ratio > 0 else 1.0
    expectancy_r = win_rate * payoff_ratio - (1.0 - win_rate)
    expectancy_cash = win_rate * average_win - (1.0 - win_rate) * average_loss
    return PayoutAsymmetry(
        sample_size=sample_size,
        wins=len(wins),
        losses=len(losses),
        win_rate=win_rate,
        average_win=average_win,
        average_loss=average_loss,
        payoff_ratio=payoff_ratio,
        break_even_win_rate=break_even,
        expectancy_r=expectancy_r,
        expectancy_cash=expectancy_cash,
    )


def dixon_coles_rho_bounds(home_lambda: float, away_mu: float) -> tuple[float, float]:
    if home_lambda <= 0 or away_mu <= 0:
        raise ValueError("Poisson intensities must be positive")
    lower = max(-1.0 / home_lambda, -1.0 / away_mu)
    upper = min(1.0 / (home_lambda * away_mu), 1.0)
    return lower, upper


def dixon_coles_tau(
    home_goals: int,
    away_goals: int,
    home_lambda: float,
    away_mu: float,
    rho: float,
) -> float:
    lower, upper = dixon_coles_rho_bounds(home_lambda, away_mu)
    if not lower <= rho <= upper:
        raise ValueError("rho is outside the Dixon-Coles admissible interval")
    if home_goals == 0 and away_goals == 0:
        return 1.0 - home_lambda * away_mu * rho
    if home_goals == 0 and away_goals == 1:
        return 1.0 + home_lambda * rho
    if home_goals == 1 and away_goals == 0:
        return 1.0 + away_mu * rho
    if home_goals == 1 and away_goals == 1:
        return 1.0 - rho
    return 1.0


def _poisson_probability(goals: int, intensity: float) -> float:
    if goals < 0:
        return 0.0
    return exp(-intensity) * intensity**goals / factorial(goals)


def dixon_coles_match_probabilities(
    home_lambda: float,
    away_mu: float,
    rho: float,
    *,
    max_goals: int = 12,
) -> dict[str, float]:
    if max_goals < 3:
        raise ValueError("max_goals must be at least 3")
    outcomes = {"HOME": 0.0, "DRAW": 0.0, "AWAY": 0.0}
    total = 0.0
    for home_goals in range(max_goals + 1):
        for away_goals in range(max_goals + 1):
            probability = (
                dixon_coles_tau(home_goals, away_goals, home_lambda, away_mu, rho)
                * _poisson_probability(home_goals, home_lambda)
                * _poisson_probability(away_goals, away_mu)
            )
            total += probability
            if home_goals > away_goals:
                outcomes["HOME"] += probability
            elif home_goals == away_goals:
                outcomes["DRAW"] += probability
            else:
                outcomes["AWAY"] += probability
    if total <= 0:
        raise ValueError("Dixon-Coles grid has no probability mass")
    return {key: value / total for key, value in outcomes.items()}


def fair_edge(model_probability: float, market_price: float, costs: float = 0.0) -> FairEdge:
    for name, value in {
        "model_probability": model_probability,
        "market_price": market_price,
        "costs": costs,
    }.items():
        if value < 0:
            raise ValueError(f"{name} cannot be negative")
    if model_probability > 1 or market_price > 1:
        raise ValueError("probabilities and binary-market prices cannot exceed 1")
    gross = model_probability - market_price
    net = gross - costs
    return FairEdge(
        model_probability=model_probability,
        market_price=market_price,
        gross_edge=gross,
        costs=costs,
        net_edge=net,
        positive_after_costs=net > 0,
    )


def simulate_book_fill(
    levels: Iterable[tuple[float, float]],
    requested_shares: float,
    *,
    side: Literal["BUY", "SELL"],
) -> BookFill:
    if requested_shares <= 0:
        raise ValueError("requested_shares must be positive")
    normalized: list[tuple[float, float]] = []
    for price, size in levels:
        price_value = float(price)
        size_value = float(size)
        if not 0 < price_value < 1 or size_value <= 0:
            continue
        normalized.append((price_value, size_value))
    normalized.sort(key=lambda level: level[0], reverse=side == "SELL")

    remaining = requested_shares
    filled = 0.0
    notional = 0.0
    for price, available in normalized:
        take = min(remaining, available)
        filled += take
        notional += take * price
        remaining -= take
        if remaining <= 1e-12:
            remaining = 0.0
            break
    average_price = notional / filled if filled > 0 else None
    return BookFill(
        side=side,
        requested_shares=requested_shares,
        filled_shares=filled,
        average_price=average_price,
        notional=notional,
        unfilled_shares=remaining,
        complete=remaining == 0.0,
    )


def weather_price_bucket(price: float) -> str:
    if not 0 <= price <= 1:
        raise ValueError("price must be between 0 and 1")
    if 0.01 <= price <= 0.10:
        return "LOW_01_10"
    if 0.50 <= price <= 0.70:
        return "MID_50_70"
    return "OTHER"


def monotonic_price_size_multiplier(
    price: float,
    *,
    minimum: float = 0.25,
    maximum: float = 1.0,
) -> float:
    if not 0 <= price <= 1:
        raise ValueError("price must be between 0 and 1")
    if minimum <= 0 or maximum < minimum:
        raise ValueError("invalid sizing multiplier bounds")
    return minimum + (maximum - minimum) * price
