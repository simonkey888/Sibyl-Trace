from dataclasses import dataclass
from math import isfinite


QUALITY_SCORE_KIND = "HEURISTIC_QUALITY_RANKING"
QUALITY_SCORE_GLOBAL_FORMULA = "0.60*SHORT+0.40*LONG"
QUALITY_SCORE_HISTORY_BASIS = "DECIDED_OUTCOMES"
QUALITY_SCORE_CALIBRATED_PROBABILITY = False
QUALITY_SCORE_EXPECTED_RETURN_CLAIM = False
QUALITY_SCORE_ALPHA_CLAIM = False


@dataclass(frozen=True)
class WalletMetrics:
    closed_count: int
    wins: int
    losses: int
    realized_pnl: float
    gross_profit: float
    gross_loss: float
    concentration: float
    volume: float
    evidence_valid: bool = True
    invalid_rows: int = 0
    invalid_row_indexes: tuple[int, ...] = ()
    invalid_reasons: tuple[str, ...] = ()

    @property
    def decided_count(self) -> int:
        """Closed positions with a strictly positive or negative realized outcome."""
        return self.wins + self.losses

    @property
    def win_rate(self) -> float:
        # Break-even/zero-PnL closes are reported as closed history but provide no
        # directional correctness evidence and therefore do not enter this rate.
        return self.wins / self.decided_count if self.decided_count else 0.0

    @property
    def profit_factor(self) -> float:
        if self.gross_loss <= 0:
            return 5.0 if self.gross_profit > 0 else 0.0
        return min(self.gross_profit / self.gross_loss, 5.0)


def compute_wallet_metrics(closed_positions: list[dict], volume: float = 0.0) -> WalletMetrics:
    """Compute metrics only from a fully valid evidence sample.

    Invalid financial rows are never dropped or coerced to zero. They invalidate
    the sample so an incomplete/corrupt response cannot produce an authoritative
    score. The invalid row indexes/reasons remain available for audit evidence.
    """
    pnls: list[float] = []
    invalid_indexes: list[int] = []
    invalid_reasons: list[str] = []

    for index, item in enumerate(closed_positions):
        if not isinstance(item, dict):
            invalid_indexes.append(index)
            invalid_reasons.append("row_not_object")
            continue
        if "realizedPnl" not in item:
            invalid_indexes.append(index)
            invalid_reasons.append("missing_realizedPnl")
            continue
        raw_value = item["realizedPnl"]
        if isinstance(raw_value, bool):
            invalid_indexes.append(index)
            invalid_reasons.append("realizedPnl_boolean")
            continue
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            invalid_indexes.append(index)
            invalid_reasons.append("realizedPnl_not_numeric")
            continue
        if not isfinite(value):
            invalid_indexes.append(index)
            invalid_reasons.append("realizedPnl_non_finite")
            continue
        pnls.append(value)

    evidence_valid = not invalid_indexes
    wins = sum(value > 0 for value in pnls)
    losses = sum(value < 0 for value in pnls)
    gross_profit = sum(value for value in pnls if value > 0)
    gross_loss = abs(sum(value for value in pnls if value < 0))
    positive = sorted((value for value in pnls if value > 0), reverse=True)
    concentration = sum(positive[:3]) / gross_profit if gross_profit > 0 else 1.0
    return WalletMetrics(
        closed_count=len(closed_positions),
        wins=wins,
        losses=losses,
        realized_pnl=sum(pnls),
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        concentration=min(max(concentration, 0.0), 1.0),
        volume=max(float(volume or 0.0), 0.0),
        evidence_valid=evidence_valid,
        invalid_rows=len(invalid_indexes),
        invalid_row_indexes=tuple(invalid_indexes),
        invalid_reasons=tuple(invalid_reasons),
    )


def wallet_score(metrics: WalletMetrics) -> tuple[float, str | None]:
    """Return a bounded heuristic quality ranking, never a probability or ER claim."""
    if not metrics.evidence_valid:
        return 0.0, "invalid_data"
    # A flat close can be useful operational history but does not establish a
    # directional outcome. Requiring decided history prevents zero-PnL padding
    # from authorizing or increasing a wallet's quality rank.
    if metrics.decided_count < 20:
        return 0.0, "insufficient_decided_history"
    if metrics.realized_pnl <= 0:
        return 0.0, "non_positive_realized_pnl"
    if metrics.concentration > 0.65:
        return 0.0, "pnl_too_concentrated"

    history = min(metrics.decided_count / 100, 1.0)
    consistency = min(metrics.win_rate / 0.75, 1.0)
    profitability = min(metrics.profit_factor / 2.5, 1.0)
    diversification = 1.0 - metrics.concentration
    score = 100 * (
        0.25 * history
        + 0.30 * consistency
        + 0.30 * profitability
        + 0.15 * diversification
    )
    return round(min(max(score, 0.0), 100.0), 2), None


@dataclass(frozen=True)
class PortfolioState:
    equity: float
    cash: float
    total_exposure: float
    daily_pnl: float
    drawdown: float
    asset_exposure: float
    asset_shares: float


@dataclass(frozen=True)
class RiskRequest:
    side: str
    wallet_score: float
    signal_age_seconds: int
    source_price: float
    observed_price: float | None
    source_usdc: float


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    amount_usd: float
    reason: str


@dataclass(frozen=True)
class RiskPolicy:
    minimum_wallet_score: float = 65.0
    maximum_signal_age_seconds: int = 30
    maximum_absolute_slippage: float = 0.03
    maximum_position_fraction: float = 0.02
    maximum_total_exposure_fraction: float = 0.15
    maximum_daily_loss_fraction: float = 0.03
    maximum_drawdown_fraction: float = 0.10
    copy_fraction: float = 0.10
    minimum_order_usd: float = 1.0

    def preflight(
        self,
        request: RiskRequest,
        portfolio: PortfolioState,
    ) -> RiskDecision | None:
        side = request.side.upper()
        if side not in {"BUY", "SELL"}:
            return RiskDecision(False, 0.0, "unsupported_side")
        if request.wallet_score < self.minimum_wallet_score:
            return RiskDecision(False, 0.0, "wallet_score_below_threshold")
        if (
            request.signal_age_seconds < 0
            or request.signal_age_seconds > self.maximum_signal_age_seconds
        ):
            return RiskDecision(False, 0.0, "stale_signal")
        if not 0 < request.source_price < 1:
            return RiskDecision(False, 0.0, "invalid_price")
        if portfolio.daily_pnl <= -(portfolio.equity * self.maximum_daily_loss_fraction):
            return RiskDecision(False, 0.0, "daily_loss_limit")
        if portfolio.drawdown >= self.maximum_drawdown_fraction:
            return RiskDecision(False, 0.0, "drawdown_limit")
        if side == "SELL" and portfolio.asset_shares <= 0:
            return RiskDecision(False, 0.0, "no_paper_position_to_sell")
        return None

    def evaluate(self, request: RiskRequest, portfolio: PortfolioState) -> RiskDecision:
        preflight = self.preflight(request, portfolio)
        if preflight is not None:
            return preflight

        observed_price = request.observed_price
        if observed_price is None or not 0 < observed_price < 1:
            return RiskDecision(False, 0.0, "invalid_price")
        if (
            abs(observed_price - request.source_price)
            > self.maximum_absolute_slippage + 1e-9
        ):
            return RiskDecision(False, 0.0, "slippage_limit")

        if request.side.upper() == "SELL":
            amount = min(request.source_usdc * self.copy_fraction, portfolio.asset_exposure)
            if amount < self.minimum_order_usd:
                return RiskDecision(False, 0.0, "insufficient_paper_position")
            return RiskDecision(True, round(amount, 2), "approved")

        position_room = max(
            portfolio.equity * self.maximum_position_fraction - portfolio.asset_exposure,
            0.0,
        )
        total_room = max(
            portfolio.equity * self.maximum_total_exposure_fraction - portfolio.total_exposure,
            0.0,
        )
        amount = min(
            request.source_usdc * self.copy_fraction,
            position_room,
            total_room,
            portfolio.cash,
        )
        if amount < self.minimum_order_usd:
            return RiskDecision(False, 0.0, "insufficient_risk_capacity")
        return RiskDecision(True, round(amount, 2), "approved")
