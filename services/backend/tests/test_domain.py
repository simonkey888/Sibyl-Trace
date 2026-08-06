from app.domain import (
    PortfolioState,
    RiskPolicy,
    RiskRequest,
    compute_wallet_metrics,
    wallet_score,
)


def test_wallet_metrics_and_score_reward_repeatable_profit() -> None:
    positions = [{"realizedPnl": value} for value in ([8.0] * 30 + [-3.0] * 10)]
    metrics = compute_wallet_metrics(positions, volume=20_000)
    score, rejection = wallet_score(metrics)
    assert rejection is None
    assert metrics.win_rate == 0.75
    assert metrics.profit_factor == 5.0
    assert score >= 60


def test_wallet_score_rejects_concentrated_outlier() -> None:
    positions = [{"realizedPnl": 1000.0}] + [{"realizedPnl": 1.0} for _ in range(24)]
    metrics = compute_wallet_metrics(positions)
    score, rejection = wallet_score(metrics)
    assert score == 0
    assert rejection == "pnl_too_concentrated"


def test_risk_policy_approves_small_fresh_copy() -> None:
    decision = RiskPolicy().evaluate(
        RiskRequest(
            side="BUY",
            wallet_score=82,
            signal_age_seconds=4,
            source_price=0.51,
            observed_price=0.52,
            source_usdc=100,
        ),
        PortfolioState(
            equity=300,
            cash=300,
            total_exposure=0,
            daily_pnl=0,
            drawdown=0,
            asset_exposure=0,
            asset_shares=0,
        ),
    )
    assert decision.approved
    assert decision.amount_usd == 6.0


def test_risk_policy_rejects_stale_signal() -> None:
    decision = RiskPolicy().evaluate(
        RiskRequest(
            side="BUY",
            wallet_score=90,
            signal_age_seconds=31,
            source_price=0.5,
            observed_price=0.5,
            source_usdc=100,
        ),
        PortfolioState(300, 300, 0, 0, 0, 0, 0),
    )
    assert not decision.approved
    assert decision.reason == "stale_signal"


def test_risk_policy_never_naked_sells() -> None:
    decision = RiskPolicy().evaluate(
        RiskRequest("SELL", 90, 1, 0.5, 0.5, 100),
        PortfolioState(300, 300, 0, 0, 0, 0, 0),
    )
    assert not decision.approved
    assert decision.reason == "no_paper_position_to_sell"
