import pytest

from app.research import (
    dixon_coles_match_probabilities,
    dixon_coles_rho_bounds,
    dixon_coles_tau,
    fair_edge,
    monotonic_price_size_multiplier,
    payout_asymmetry,
    simulate_book_fill,
    weather_price_bucket,
)


def test_payout_asymmetry_can_be_positive_below_fifty_percent_wins() -> None:
    pnls = [1.32] * 487 + [-1.0] * 513
    metrics = payout_asymmetry(pnls)
    assert metrics.win_rate == pytest.approx(0.487)
    assert metrics.payoff_ratio == pytest.approx(1.32)
    assert metrics.break_even_win_rate == pytest.approx(1 / 2.32)
    assert metrics.expectancy_r > 0


def test_payout_asymmetry_empty_sample_is_unproven() -> None:
    metrics = payout_asymmetry([])
    assert metrics.sample_size == 0
    assert metrics.expectancy_r == 0
    assert metrics.break_even_win_rate == 1


def test_dixon_coles_low_score_adjustment_matches_paper_definition() -> None:
    home_lambda = 1.4
    away_mu = 1.1
    rho = -0.08
    assert dixon_coles_tau(0, 0, home_lambda, away_mu, rho) == pytest.approx(
        1 - home_lambda * away_mu * rho
    )
    assert dixon_coles_tau(0, 1, home_lambda, away_mu, rho) == pytest.approx(
        1 + home_lambda * rho
    )
    assert dixon_coles_tau(1, 0, home_lambda, away_mu, rho) == pytest.approx(
        1 + away_mu * rho
    )
    assert dixon_coles_tau(1, 1, home_lambda, away_mu, rho) == pytest.approx(1 - rho)
    assert dixon_coles_tau(2, 1, home_lambda, away_mu, rho) == 1


def test_dixon_coles_rho_must_be_admissible() -> None:
    lower, upper = dixon_coles_rho_bounds(1.4, 1.1)
    with pytest.raises(ValueError):
        dixon_coles_tau(0, 0, 1.4, 1.1, lower - 0.01)
    with pytest.raises(ValueError):
        dixon_coles_tau(0, 0, 1.4, 1.1, upper + 0.01)


def test_dixon_coles_match_probabilities_are_normalized() -> None:
    probabilities = dixon_coles_match_probabilities(1.5, 1.0, -0.06)
    assert sum(probabilities.values()) == pytest.approx(1.0)
    assert probabilities["HOME"] > probabilities["AWAY"]


def test_fair_edge_requires_edge_after_costs() -> None:
    edge = fair_edge(0.447, 0.420, costs=0.010)
    assert edge.gross_edge == pytest.approx(0.027)
    assert edge.net_edge == pytest.approx(0.017)
    assert edge.positive_after_costs
    assert not fair_edge(0.447, 0.440, costs=0.010).positive_after_costs


def test_buy_fill_consumes_asks_from_cheapest_price() -> None:
    result = simulate_book_fill(
        [(0.54, 10), (0.52, 25), (0.53, 60)],
        40,
        side="BUY",
    )
    assert result.complete
    assert result.filled_shares == 40
    assert result.average_price == pytest.approx((25 * 0.52 + 15 * 0.53) / 40)


def test_sell_fill_consumes_bids_from_highest_price() -> None:
    result = simulate_book_fill(
        [(0.48, 30), (0.50, 15), (0.49, 20)],
        25,
        side="SELL",
    )
    assert result.complete
    assert result.average_price == pytest.approx((15 * 0.50 + 10 * 0.49) / 25)


def test_fill_reports_partial_liquidity_instead_of_inventing_a_fill() -> None:
    result = simulate_book_fill([(0.52, 5)], 10, side="BUY")
    assert not result.complete
    assert result.filled_shares == 5
    assert result.unfilled_shares == 5


def test_weather_price_hypothesis_buckets_are_explicit() -> None:
    assert weather_price_bucket(0.05) == "LOW_01_10"
    assert weather_price_bucket(0.60) == "MID_50_70"
    assert weather_price_bucket(0.30) == "OTHER"


def test_weather_sizing_hypothesis_is_monotonic_not_operational_risk() -> None:
    assert monotonic_price_size_multiplier(0.05) < monotonic_price_size_multiplier(0.60)
    assert monotonic_price_size_multiplier(0.60) < monotonic_price_size_multiplier(0.95)
