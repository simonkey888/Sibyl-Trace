from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from decimal import Decimal
from typing import Any, Iterable


D = Decimal


@dataclass(frozen=True)
class L2Level:
    price: Decimal
    size: Decimal


@dataclass(frozen=True)
class BookSnapshot:
    venue: str
    market_id: str
    side: str
    timestamp_ms: int
    levels: tuple[L2Level, ...]
    source: str

    @property
    def hash(self) -> str:
        return _hash(
            {
                "venue": self.venue,
                "market_id": self.market_id,
                "side": self.side,
                "timestamp_ms": self.timestamp_ms,
                "levels": [[str(x.price), str(x.size)] for x in self.levels],
                "source": self.source,
            }
        )


@dataclass(frozen=True)
class FeeQuote:
    source: str
    bps: Decimal


@dataclass(frozen=True)
class ExecutionEvidence:
    schema_version: str
    decision_book_hash: str
    decision_book_timestamp: int
    arrival_book_hash: str
    arrival_book_timestamp: int
    quote_age_ms: int
    market_age_ms: int
    L2_levels_consumed: int
    requested_size: str
    filled_size: str
    VWAP: str | None
    worst_price: str | None
    fee_source: str
    fee_bps: str
    fee_usd: str
    slippage_usd: str
    hedge_latency_ms: int | None
    hedge_fill_status: str
    partial_fill: bool
    orphan_exposure: str
    net_edge_before_cost: str
    net_edge_after_all_costs: str
    status: str
    rejection_reason: str | None
    evidence_hash: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_fill_to_hedge(
    *,
    decision_book: BookSnapshot,
    arrival_book: BookSnapshot,
    requested_size: Decimal,
    maker_fill_price: Decimal,
    fee: FeeQuote,
    decision_timestamp_ms: int,
    market_timestamp_ms: int,
    hedge_started_ms: int,
    hedge_finished_ms: int | None,
    hedge_success: bool,
    max_quote_age_ms: int,
    max_market_age_ms: int,
    infra_cost_usd: Decimal = D("0"),
) -> ExecutionEvidence:
    quote_age = max(0, decision_timestamp_ms - decision_book.timestamp_ms)
    market_age = max(0, decision_timestamp_ms - market_timestamp_ms)
    rejection: str | None = None
    if quote_age > max_quote_age_ms:
        rejection = "STALE_BOOK"
    elif market_age > max_market_age_ms:
        rejection = "STALE_MARKET"
    elif requested_size <= 0:
        rejection = "ZERO_REQUEST"

    fill_size, vwap, worst, levels = _consume(arrival_book.levels, requested_size)
    partial = fill_size != requested_size
    if rejection is None and fill_size <= 0:
        rejection = "ZERO_FILL"
    elif rejection is None and partial:
        rejection = "INSUFFICIENT_L2_DEPTH"

    notional = (fill_size * vwap) if vwap is not None else D("0")
    fee_usd = notional * fee.bps / D("10000")
    best = arrival_book.levels[0].price if arrival_book.levels else None
    slippage = D("0")
    if best is not None and vwap is not None:
        slippage = max(D("0"), (vwap - best) * fill_size)

    edge_before = D("0")
    if vwap is not None:
        edge_before = (D("1") - maker_fill_price - vwap) * fill_size
    edge_after = edge_before - fee_usd - slippage - infra_cost_usd
    hedge_latency = None if hedge_finished_ms is None else max(0, hedge_finished_ms - hedge_started_ms)
    orphan = requested_size - fill_size
    if fill_size > 0 and not hedge_success:
        orphan += fill_size
        if rejection is None:
            rejection = "HEDGE_FAILED"
    status = "GREEN" if rejection is None and hedge_success and orphan == 0 else "REJECTED"
    hedge_status = "SUCCESS" if hedge_success else "FAILED"

    material = {
        "decision_book_hash": decision_book.hash,
        "arrival_book_hash": arrival_book.hash,
        "quote_age_ms": quote_age,
        "market_age_ms": market_age,
        "levels": levels,
        "requested": str(requested_size),
        "filled": str(fill_size),
        "vwap": str(vwap) if vwap is not None else None,
        "worst": str(worst) if worst is not None else None,
        "fee_source": fee.source,
        "fee_bps": str(fee.bps),
        "fee_usd": str(fee_usd),
        "slippage_usd": str(slippage),
        "hedge_latency_ms": hedge_latency,
        "hedge_status": hedge_status,
        "partial_fill": partial,
        "orphan": str(orphan),
        "edge_before": str(edge_before),
        "edge_after": str(edge_after),
        "status": status,
        "rejection": rejection,
    }
    return ExecutionEvidence(
        schema_version="SIBYL_V6_EXECUTION_EVIDENCE_V1",
        decision_book_hash=decision_book.hash,
        decision_book_timestamp=decision_book.timestamp_ms,
        arrival_book_hash=arrival_book.hash,
        arrival_book_timestamp=arrival_book.timestamp_ms,
        quote_age_ms=quote_age,
        market_age_ms=market_age,
        L2_levels_consumed=levels,
        requested_size=str(requested_size),
        filled_size=str(fill_size),
        VWAP=str(vwap) if vwap is not None else None,
        worst_price=str(worst) if worst is not None else None,
        fee_source=fee.source,
        fee_bps=str(fee.bps),
        fee_usd=str(fee_usd),
        slippage_usd=str(slippage),
        hedge_latency_ms=hedge_latency,
        hedge_fill_status=hedge_status,
        partial_fill=partial,
        orphan_exposure=str(orphan),
        net_edge_before_cost=str(edge_before),
        net_edge_after_all_costs=str(edge_after),
        status=status,
        rejection_reason=rejection,
        evidence_hash=_hash(material),
    )


def _consume(levels: Iterable[L2Level], requested: Decimal) -> tuple[Decimal, Decimal | None, Decimal | None, int]:
    remaining = requested
    cost = D("0")
    filled = D("0")
    worst: Decimal | None = None
    used = 0
    for level in levels:
        if remaining <= 0:
            break
        if level.price <= 0 or level.price >= 1 or level.size <= 0:
            continue
        take = min(remaining, level.size)
        if take <= 0:
            continue
        used += 1
        cost += take * level.price
        filled += take
        remaining -= take
        worst = level.price
    return filled, (cost / filled if filled > 0 else None), worst, used


def _hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
