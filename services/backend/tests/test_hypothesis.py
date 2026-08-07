import pytest

from app.hypothesis import (
    breaker_pnls,
    evaluate_hypothesis,
    make_hypothesis,
    propose_variant,
    series_metrics,
    walk_forward_splits,
)


def test_walk_forward_seals_test_after_training_window() -> None:
    splits = walk_forward_splits(100, train_size=40, test_size=20, step_size=20)
    assert splits[0].train_start == 0
    assert splits[0].train_end == splits[0].test_start == 40
    assert splits[0].test_end == 60
    assert all(split.train_end <= split.test_start for split in splits)


def test_series_metrics_preserve_payout_asymmetry() -> None:
    metrics = series_metrics([1.32] * 48 + [-1.0] * 52)
    assert metrics.win_rate == pytest.approx(0.48)
    assert metrics.payoff_ratio == pytest.approx(1.32)
    assert metrics.expectancy_r > 0


def test_double_cost_breaker_is_stricter_than_base_test() -> None:
    values = [0.25, 0.20, -0.10] * 20
    base = series_metrics(values)
    breaker = series_metrics(breaker_pnls(values, transaction_cost_per_trade=0.08))
    assert breaker.total_pnl < base.total_pnl


def test_small_positive_sample_never_promotes_beyond_research() -> None:
    spec = make_hypothesis(kind="LATENCY", thesis="test", config={}, minimum_sample=30)
    result = evaluate_hypothesis(spec, [1.0, 1.0, 1.0])
    assert result.decision == "CONTINUE_RESEARCH"
    assert "insufficient_sample" in result.reasons


def test_failed_strategy_is_revised_not_deleted() -> None:
    spec = make_hypothesis(kind="SPORTS", thesis="edge", config={"threshold": 0.02})
    result = evaluate_hypothesis(spec, [-1.0] * 40)
    assert result.decision == "REVISE_STRATEGY"
    candidate = propose_variant(spec, {"threshold": 0.03}, rationale="raise edge floor")
    assert candidate.parent_id == spec.hypothesis_id
    assert candidate.hypothesis_id != spec.hypothesis_id


def test_candidate_cannot_rewrite_protected_risk_or_live_boundary() -> None:
    spec = make_hypothesis(kind="COPY", thesis="test", config={})
    with pytest.raises(ValueError):
        propose_variant(spec, {"live_trading_enabled": True}, rationale="unsafe")
    with pytest.raises(ValueError):
        propose_variant(spec, {"risk_max_daily_loss_pct": 0.99}, rationale="unsafe")
