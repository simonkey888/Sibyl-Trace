from app.domain import (
    QUALITY_SCORE_ALPHA_CLAIM,
    QUALITY_SCORE_CALIBRATED_PROBABILITY,
    QUALITY_SCORE_EXPECTED_RETURN_CLAIM,
    QUALITY_SCORE_GLOBAL_FORMULA,
    QUALITY_SCORE_HISTORY_BASIS,
    QUALITY_SCORE_KIND,
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


def test_break_even_closes_do_not_silently_count_as_losses() -> None:
    positions = [{"realizedPnl": value} for value in ([2.0] * 6 + [-1.0] * 4 + [0.0] * 10)]
    metrics = compute_wallet_metrics(positions)
    assert metrics.closed_count == 20
    assert metrics.decided_count == 10
    assert metrics.win_rate == 0.6


def test_break_even_padding_cannot_authorize_quality_score() -> None:
    positions = [{"realizedPnl": 0.0} for _ in range(100)] + [
        {"realizedPnl": value} for value in ([2.0] * 12 + [-1.0] * 7)
    ]
    metrics = compute_wallet_metrics(positions)
    score, rejection = wallet_score(metrics)
    assert metrics.closed_count == 119
    assert metrics.decided_count == 19
    assert score == 0
    assert rejection == "insufficient_decided_history"


def test_quality_history_component_uses_decided_outcomes_not_flat_closes() -> None:
    decided = [{"realizedPnl": 2.0} for _ in range(15)] + [
        {"realizedPnl": -1.0} for _ in range(5)
    ]
    base = compute_wallet_metrics(decided)
    padded = compute_wallet_metrics(decided + [{"realizedPnl": 0.0} for _ in range(80)])
    base_score, base_rejection = wallet_score(base)
    padded_score, padded_rejection = wallet_score(padded)
    assert base_rejection is None
    assert padded_rejection is None
    assert padded.closed_count > base.closed_count
    assert padded.decided_count == base.decided_count == 20
    assert padded_score == base_score


def test_evidence_integrity_rejects_missing_realized_pnl() -> None:
    metrics = compute_wallet_metrics([{"realizedPnl": 1.0}, {}] + [{"realizedPnl": 1.0}] * 20)
    score, rejection = wallet_score(metrics)
    assert not metrics.evidence_valid
    assert metrics.invalid_rows == 1
    assert metrics.invalid_row_indexes == (1,)
    assert metrics.invalid_reasons == ("missing_realizedPnl",)
    assert metrics.closed_count == 22
    assert metrics.decided_count == 21
    assert score == 0
    assert rejection == "invalid_data"


def test_evidence_integrity_rejects_non_numeric_and_non_finite_values() -> None:
    metrics = compute_wallet_metrics(
        [
            {"realizedPnl": 1.0},
            {"realizedPnl": "not-a-number"},
            {"realizedPnl": float("nan")},
            {"realizedPnl": float("inf")},
            {"realizedPnl": True},
        ]
    )
    score, rejection = wallet_score(metrics)
    assert not metrics.evidence_valid
    assert metrics.invalid_rows == 4
    assert metrics.invalid_row_indexes == (1, 2, 3, 4)
    assert metrics.invalid_reasons == (
        "realizedPnl_not_numeric",
        "realizedPnl_non_finite",
        "realizedPnl_non_finite",
        "realizedPnl_boolean",
    )
    assert score == 0
    assert rejection == "invalid_data"


def test_evidence_integrity_rejects_malformed_rows_without_dropping_them() -> None:
    metrics = compute_wallet_metrics([{"realizedPnl": 2.0}, None, "bad"] + [{"realizedPnl": -1.0}] * 20)
    score, rejection = wallet_score(metrics)
    assert not metrics.evidence_valid
    assert metrics.invalid_rows == 2
    assert metrics.invalid_row_indexes == (1, 2)
    assert metrics.invalid_reasons == ("row_not_object", "row_not_object")
    assert metrics.closed_count == 23
    assert metrics.decided_count == 21
    assert score == 0
    assert rejection == "invalid_data"


def test_valid_sample_preserves_previous_score_exactly() -> None:
    positions = [{"realizedPnl": value} for value in ([8.0] * 30 + [-3.0] * 10)]
    metrics = compute_wallet_metrics(positions)
    assert metrics.evidence_valid
    score, rejection = wallet_score(metrics)
    assert rejection is None
    assert score == 75.5


def test_quality_score_contract_is_explicitly_non_calibrated() -> None:
    assert QUALITY_SCORE_KIND == "HEURISTIC_QUALITY_RANKING"
    assert QUALITY_SCORE_GLOBAL_FORMULA == "0.60*SHORT+0.40*LONG"
    assert QUALITY_SCORE_HISTORY_BASIS == "DECIDED_OUTCOMES"
    assert QUALITY_SCORE_CALIBRATED_PROBABILITY is False
    assert QUALITY_SCORE_EXPECTED_RETURN_CLAIM is False
    assert QUALITY_SCORE_ALPHA_CLAIM is False


def test_wallet_score_rejects_concentrated_outlier() -> None:
    positions = [{"realizedPnl": 1000.0}] + [{"realizedPnl": 1.0} for _ in range(24)]
    metrics = compute_wallet_metrics(positions)
    score, rejection = wallet_score(metrics)
    assert score == 0
    assert rejection == "pnl_too_concentrated"


def state() -> PortfolioState:
    return PortfolioState(
        equity=300,
        cash=300,
        total_exposure=0,
        daily_pnl=0,
        drawdown=0,
        asset_exposure=0,
        asset_shares=0,
    )


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
        state(),
    )
    assert decision.approved
    assert decision.amount_usd == 6.0


def test_risk_policy_rejects_stale_signal_during_preflight() -> None:
    request = RiskRequest(
        side="BUY",
        wallet_score=90,
        signal_age_seconds=31,
        source_price=0.5,
        observed_price=None,
        source_usdc=100,
    )
    decision = RiskPolicy().preflight(request, state())
    assert decision is not None
    assert not decision.approved
    assert decision.reason == "stale_signal"


def test_risk_policy_includes_exact_slippage_boundary() -> None:
    decision = RiskPolicy().evaluate(
        RiskRequest(
            side="BUY",
            wallet_score=90,
            signal_age_seconds=1,
            source_price=0.53,
            observed_price=0.56,
            source_usdc=100,
        ),
        state(),
    )
    assert decision.approved
    assert decision.reason == "approved"


def test_risk_policy_never_naked_sells() -> None:
    decision = RiskPolicy().evaluate(
        RiskRequest("SELL", 90, 1, 0.5, 0.5, 100),
        state(),
    )
    assert not decision.approved
    assert decision.reason == "no_paper_position_to_sell"


def test_small_sell_rejection_has_truthful_reason() -> None:
    decision = RiskPolicy().evaluate(
        RiskRequest("SELL", 90, 1, 0.5, 0.5, 1),
        PortfolioState(300, 299.5, 0.5, 0, 0, 0.5, 1),
    )
    assert not decision.approved
    assert decision.reason == "insufficient_paper_position"
