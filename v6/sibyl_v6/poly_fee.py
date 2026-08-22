from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP
from typing import Any, Iterable

FEE_QUANTUM = Decimal("0.00001")
# Defensive implementation bounds for remote CLOB metadata. These are not
# claims about protocol maxima: values outside them are treated as unsupported
# so an anomalous/malicious payload cannot drive pathological arithmetic.
MAX_SUPPORTED_FEE_RATE = 1.0
MAX_SUPPORTED_FEE_EXPONENT = 64


@dataclass(frozen=True)
class PolyFeeDetails:
    rate: float
    exponent: float
    taker_only: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _finite_nonnegative(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return number


def parse_clob_fee_details(info: dict[str, Any] | None) -> PolyFeeDetails | None:
    """Parse CLOB V2 fee details; unknown or malformed parameters fail closed."""
    if not isinstance(info, dict):
        return None
    raw = info.get("fd")
    if not isinstance(raw, dict):
        return None
    rate = _finite_nonnegative(raw.get("r"))
    exponent = _finite_nonnegative(raw.get("e"))
    taker_only = raw.get("to")
    if rate is None or exponent is None or not isinstance(taker_only, bool):
        return None
    # Defensive local bounds: fee rate is a fractional rate and the exponent is
    # only supported when integral and computationally bounded. Any future
    # protocol contract outside these bounds fails closed until explicitly
    # reviewed rather than being approximated or consuming unbounded work.
    if rate > MAX_SUPPORTED_FEE_RATE:
        return None
    if exponent != float(int(exponent)) or exponent > MAX_SUPPORTED_FEE_EXPONENT:
        return None
    # Fee-bearing V2 markets use a positive exponent. A zero-rate market is
    # fee-free regardless of exponent, so it remains a known fee contract.
    if rate > 0 and exponent <= 0:
        return None
    return PolyFeeDetails(rate=rate, exponent=exponent, taker_only=taker_only)


def protocol_fee_raw(shares: float, price: float, details: PolyFeeDetails) -> Decimal:
    """Polymarket V2 platform fee in USDC before 5-decimal protocol rounding.

    Official V2 clients use: shares * rate * (p * (1-p)) ** exponent.
    This is evaluated per execution level because a multi-level FAK can fill at
    multiple prices.
    """
    if not (math.isfinite(shares) and shares >= 0 and math.isfinite(price) and 0 < price < 1):
        raise ValueError("INVALID_FEE_FILL")
    if not (
        math.isfinite(details.rate)
        and 0 <= details.rate <= MAX_SUPPORTED_FEE_RATE
        and math.isfinite(details.exponent)
        and 0 <= details.exponent <= MAX_SUPPORTED_FEE_EXPONENT
    ):
        raise ValueError("UNSUPPORTED_FEE_DETAILS")
    s = Decimal(str(shares))
    p = Decimal(str(price))
    rate = Decimal(str(details.rate))
    exponent = Decimal(str(details.exponent))
    base = p * (Decimal(1) - p)
    # Decimal non-integral powers are implementation-dependent. Current CLOB
    # exponents are integral; reject anything else rather than approximate.
    if exponent != exponent.to_integral_value():
        raise ValueError("NON_INTEGER_FEE_EXPONENT_UNSUPPORTED")
    return s * rate * (base ** int(exponent))


def protocol_fee_for_fills(
    fills: Iterable[dict[str, float]], details: PolyFeeDetails
) -> dict[str, float]:
    raw = Decimal(0)
    shares = Decimal(0)
    for fill in fills:
        size = float(fill["size"])
        price = float(fill["price"])
        raw += protocol_fee_raw(size, price, details)
        shares += Decimal(str(size))
    expected = raw.quantize(FEE_QUANTUM, rounding=ROUND_HALF_UP)
    conservative = raw.quantize(FEE_QUANTUM, rounding=ROUND_CEILING)
    return {
        "shares": float(shares),
        "raw_usdc": float(raw),
        "expected_usdc": float(expected),
        "conservative_usdc": float(conservative),
    }


def safety_buffer_usdc(hedge_notional: float, buffer_bps: float) -> float:
    if not (
        math.isfinite(hedge_notional)
        and hedge_notional >= 0
        and math.isfinite(buffer_bps)
        and buffer_bps >= 0
    ):
        raise ValueError("INVALID_FEE_SAFETY_BUFFER")
    return hedge_notional * buffer_bps / 10_000.0
