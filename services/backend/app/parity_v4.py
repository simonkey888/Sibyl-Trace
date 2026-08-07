from __future__ import annotations

from dataclasses import dataclass

from app.market_identity_v4 import IdentityDecision, equivalence_allows_parity
from app.venue_v3 import NormalizedBook


@dataclass(frozen=True)
class ExecutableQuote:
    venue: str
    side: str
    price: float
    size: float
    fee_rate: float
    effective_price: float


@dataclass(frozen=True)
class ParityResult:
    status: str
    direction: str | None
    gross_edge: float | None
    net_edge: float | None
    max_size: float
    reason: str


def _take(book: NormalizedBook, *, side: str, fee_rate: float) -> ExecutableQuote | None:
    level = book.best_ask if side == "BUY" else book.best_bid
    if level is None:
        return None
    fee = level.price * fee_rate
    effective = level.price + fee if side == "BUY" else level.price - fee
    return ExecutableQuote(book.venue, side, level.price, level.size, fee_rate, effective)


def compare_binary_parity(
    left: NormalizedBook,
    right: NormalizedBook,
    identity: IdentityDecision,
    *,
    left_fee_rate: float = 0.0,
    right_fee_rate: float = 0.0,
    settlement_penalty: float = 0.0,
) -> ParityResult:
    if min(left_fee_rate, right_fee_rate, settlement_penalty) < 0:
        raise ValueError("cost inputs cannot be negative")
    if not equivalence_allows_parity(identity):
        return ParityResult("BLOCKED", None, None, None, 0.0, "identity_gate_not_exact")

    left_buy = _take(left, side="BUY", fee_rate=left_fee_rate)
    left_sell = _take(left, side="SELL", fee_rate=left_fee_rate)
    right_buy = _take(right, side="BUY", fee_rate=right_fee_rate)
    right_sell = _take(right, side="SELL", fee_rate=right_fee_rate)
    candidates: list[tuple[str, float, float, float]] = []

    if left_buy and right_sell:
        gross = right_sell.price - left_buy.price
        net = right_sell.effective_price - left_buy.effective_price - settlement_penalty
        candidates.append(("BUY_LEFT_SELL_RIGHT", gross, net, min(left_buy.size, right_sell.size)))
    if right_buy and left_sell:
        gross = left_sell.price - right_buy.price
        net = left_sell.effective_price - right_buy.effective_price - settlement_penalty
        candidates.append(("BUY_RIGHT_SELL_LEFT", gross, net, min(right_buy.size, left_sell.size)))
    if not candidates:
        return ParityResult("NO_DATA", None, None, None, 0.0, "missing_two_sided_liquidity")

    direction, gross, net, size = max(candidates, key=lambda candidate: candidate[2])
    status = "POSITIVE" if net > 0 else "NO_EDGE"
    return ParityResult(status, direction, gross, net, size, "executable_top_of_book_after_costs")
