import pytest

from app.calibration import (
    brier_score,
    closing_line_value,
    deflated_signal_score,
    edge_velocity,
)


def test_brier_score_measures_probability_accuracy_not_win_rate() -> None:
    calibrated = brier_score([0.8, 0.2, 0.7, 0.3], [1, 0, 1, 0])
    overconfident = brier_score([0.99, 0.99, 0.99, 0.99], [1, 0, 1, 0])
    assert calibrated.brier_score is not None
    assert overconfident.brier_score is not None
    assert calibrated.brier_score < overconfident.brier_score


def test_clv_is_positive_when_entry_beats_close_in_held_direction() -> None:
    metrics = closing_line_value([0.45, 0.55], [0.52, 0.60])
    assert metrics.average_clv == pytest.approx(0.06)
    assert metrics.positive_clv_rate == 1


def test_edge_velocity_decays_stale_edge() -> None:
    fresh = edge_velocity(0.04, 0, half_life_seconds=30)
    stale = edge_velocity(0.04, 60, half_life_seconds=30)
    assert fresh == pytest.approx(0.04)
    assert stale == pytest.approx(0.01)


def test_multiple_testing_penalty_increases_with_trials() -> None:
    few = deflated_signal_score(1.5, trials=2, sample_size=200)
    many = deflated_signal_score(1.5, trials=4000, sample_size=200)
    assert many < few < 1.5
