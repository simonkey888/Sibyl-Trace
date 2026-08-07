from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, ROUND_DOWN, ROUND_HALF_UP, Decimal
from typing import Any

D = Decimal
FEE_QUANTUM = D("0.00001")


@dataclass(frozen=True)
class MarketRulesV5:
    tick_size: float
    minimum_order_size: float
    fee_rate: float
    fee_exponent: float
    order_delay_ms: int


@dataclass(frozen=True)
class FillV5:
    status: str
    reason: str | None
    filled_shares: float
    gross_notional: float
    fee_usd: float
    net_cash_delta: float
    average_fill_price: float | None
    effective_price: float | None
    fill_fraction: float
    levels_consumed: int


def _decimal(value: Any, *, default: str = "0") -> Decimal:
    try:
        return D(str(value))
    except Exception:
        return D(default)


def market_rules_from_clob_info(info: dict[str, Any]) -> MarketRulesV5:
    tick = _decimal(info.get("mts"))
    minimum = _decimal(info.get("mos"))
    fee = info.get("fd")
    if tick <= 0 or minimum <= 0:
        raise ValueError("missing_clob_trading_constraints")
    if not isinstance(fee, dict):
        raise ValueError("fee_schedule_unavailable")
    rate = _decimal(fee.get("r"), default="-1")
    exponent = _decimal(fee.get("e"), default="-1")
    taker_only = fee.get("to")
    if rate < 0 or rate > 1 or exponent != 1 or taker_only is not True:
        raise ValueError("unsupported_fee_schedule")
    return MarketRulesV5(
        tick_size=float(tick),
        minimum_order_size=float(minimum),
        fee_rate=float(rate),
        fee_exponent=float(exponent),
        order_delay_ms=250 if info.get("itode") is True else 0,
    )


def taker_fee_usd(shares: float, price: float, fee_rate: float) -> float:
    c = max(_decimal(shares), D("0"))
    p = _decimal(price)
    rate = max(_decimal(fee_rate), D("0"))
    if c <= 0 or p <= 0 or p >= 1 or rate <= 0:
        return 0.0
    fee = c * rate * p * (D("1") - p)
    return float(fee.quantize(FEE_QUANTUM, rounding=ROUND_HALF_UP))


def _levels(book: dict[str, Any], side: str) -> list[tuple[Decimal, Decimal]]:
    source = book.get("asks" if side == "BUY" else "bids")
    rows: list[tuple[Decimal, Decimal]] = []
    if not isinstance(source, list):
        return rows
    for item in source:
        if not isinstance(item, dict):
            continue
        price = _decimal(item.get("price"))
        size = _decimal(item.get("size"))
        if D("0") < price < D("1") and size > 0:
            rows.append((price, size))
    rows.sort(key=lambda item: item[0], reverse=side == "SELL")
    return rows


def best_executable_price(book: dict[str, Any], side: str) -> float | None:
    rows = _levels(book, side.upper())
    return float(rows[0][0]) if rows else None


def worst_price_limit(
    *,
    source_price: float,
    side: str,
    tick_size: float,
    maximum_absolute_slippage: float,
) -> float:
    source = _decimal(source_price)
    tick = _decimal(tick_size)
    slip = _decimal(maximum_absolute_slippage)
    if tick <= 0:
        raise ValueError("invalid_tick_size")
    if side.upper() == "BUY":
        raw = min(source + slip, D("0.999"))
        steps = (raw / tick).to_integral_value(rounding=ROUND_DOWN)
    else:
        raw = max(source - slip, D("0.001"))
        steps = (raw / tick).to_integral_value(rounding=ROUND_CEILING)
    value = steps * tick
    return float(min(max(value, tick), D("1") - tick))


def _fee_decimal(shares: Decimal, price: Decimal, fee_rate: Decimal) -> Decimal:
    if shares <= 0 or fee_rate <= 0:
        return D("0")
    return (shares * fee_rate * price * (D("1") - price)).quantize(
        FEE_QUANTUM,
        rounding=ROUND_HALF_UP,
    )


def _no_fill(reason: str, levels_consumed: int = 0) -> FillV5:
    return FillV5(
        status="NO_FILL",
        reason=reason,
        filled_shares=0,
        gross_notional=0,
        fee_usd=0,
        net_cash_delta=0,
        average_fill_price=None,
        effective_price=None,
        fill_fraction=0,
        levels_consumed=levels_consumed,
    )


def simulate_fak_fill(
    book: dict[str, Any],
    *,
    side: str,
    fee_rate: float,
    minimum_order_size: float,
    worst_price: float,
    requested_usd: float = 0.0,
    requested_shares: float = 0.0,
) -> FillV5:
    side = side.upper()
    if side not in {"BUY", "SELL"}:
        return _no_fill("unsupported_side")
    levels = _levels(book, side)
    if not levels:
        return _no_fill("empty_executable_book")

    fee_rate_d = _decimal(fee_rate)
    min_size = _decimal(minimum_order_size)
    limit = _decimal(worst_price)
    filled = D("0")
    gross = D("0")
    fees = D("0")
    levels_used = 0

    if side == "BUY":
        budget = _decimal(requested_usd)
        if budget <= 0:
            return _no_fill("invalid_buy_budget")
        best = levels[0][0]
        best_unit_cost = best + fee_rate_d * best * (D("1") - best)
        if best > limit or best_unit_cost <= 0 or budget / best_unit_cost < min_size:
            return _no_fill("below_min_order_or_price_limit")
        remaining = budget
        for price, available in levels:
            if price > limit or remaining <= 0:
                break
            unit_cost = price + fee_rate_d * price * (D("1") - price)
            if unit_cost <= 0:
                continue
            take = min(available, remaining / unit_cost)
            if take <= 0:
                continue
            fee = _fee_decimal(take, price, fee_rate_d)
            total = take * price + fee
            if total > remaining:
                take = max((remaining - FEE_QUANTUM) / unit_cost, D("0"))
                fee = _fee_decimal(take, price, fee_rate_d)
                total = take * price + fee
            if take <= 0 or total > remaining:
                continue
            filled += take
            gross += take * price
            fees += fee
            remaining -= total
            levels_used += 1
        if filled < min_size:
            return _no_fill("filled_below_min_order_size", levels_used)
        spent = gross + fees
        fraction = min(spent / budget, D("1")) if budget > 0 else D("0")
        net_cash = -spent
    else:
        requested = _decimal(requested_shares)
        if requested < min_size:
            return _no_fill("below_min_order_size")
        remaining = requested
        for price, available in levels:
            if price < limit or remaining <= 0:
                break
            take = min(available, remaining)
            if take <= 0:
                continue
            filled += take
            gross += take * price
            fees += _fee_decimal(take, price, fee_rate_d)
            remaining -= take
            levels_used += 1
        if filled < min_size:
            return _no_fill("filled_below_min_order_size", levels_used)
        fraction = min(filled / requested, D("1")) if requested > 0 else D("0")
        net_cash = gross - fees

    average = gross / filled if filled > 0 else None
    effective = (
        ((gross + fees) / filled if side == "BUY" else (gross - fees) / filled)
        if filled > 0
        else None
    )
    status = "FILLED" if fraction >= D("0.999999") else "PARTIAL_FILLED"
    return FillV5(
        status=status,
        reason=None,
        filled_shares=float(filled),
        gross_notional=float(gross),
        fee_usd=float(fees),
        net_cash_delta=float(net_cash),
        average_fill_price=float(average) if average is not None else None,
        effective_price=float(effective) if effective is not None else None,
        fill_fraction=float(fraction),
        levels_consumed=levels_used,
    )
