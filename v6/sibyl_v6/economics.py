from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any


class Target80Status(StrEnum):
    PROVEN = "PROVEN"
    PLAUSIBLE = "PLAUSIBLE"
    CAPITAL_CONSTRAINED = "CAPITAL_CONSTRAINED"
    FILL_RATE_CONSTRAINED = "FILL_RATE_CONSTRAINED"
    EDGE_INSUFFICIENT = "EDGE_INSUFFICIENT"
    UNPROVEN = "UNPROVEN"


@dataclass
class EconomicsLedger:
    cross_venue_spread_realized: Decimal = Decimal("0")
    limitless_maker_rebate_realized: Decimal = Decimal("0")
    limitless_lp_reward_realized: Decimal = Decimal("0")
    polymarket_hedge_fee: Decimal = Decimal("0")
    gas_cost: Decimal = Decimal("0")
    slippage: Decimal = Decimal("0")
    adverse_selection_markout: Decimal = Decimal("0")
    infra_cost: Decimal = Decimal("0")
    capital_locked: Decimal = Decimal("0")
    capital_hours: Decimal = Decimal("0")
    turnover: Decimal = Decimal("0")
    prospective_attributable_hours: Decimal = Decimal("0")
    realized_event_count: int = 0

    def record_realized(self, kind: str, amount: Decimal, *, attributable: bool) -> None:
        if not attributable:
            raise ValueError("REALIZED economics require attributable evidence")
        allowed = {
            "cross_venue_spread_realized",
            "limitless_maker_rebate_realized",
            "limitless_lp_reward_realized",
            "polymarket_hedge_fee",
            "gas_cost",
            "slippage",
            "adverse_selection_markout",
            "infra_cost",
            "turnover",
        }
        if kind not in allowed:
            raise ValueError(f"unsupported economic field: {kind}")
        setattr(self, kind, getattr(self, kind) + amount)
        self.realized_event_count += 1

    @property
    def net_pnl(self) -> Decimal:
        return (
            self.cross_venue_spread_realized
            + self.limitless_maker_rebate_realized
            + self.limitless_lp_reward_realized
            - self.polymarket_hedge_fee
            - self.gas_cost
            - self.slippage
            + self.adverse_selection_markout
            - self.infra_cost
        )

    def target_80_status(self) -> Target80Status:
        if self.prospective_attributable_hours < Decimal("24") or self.realized_event_count == 0:
            return Target80Status.UNPROVEN
        if self.net_pnl >= Decimal("80"):
            return Target80Status.PROVEN
        if self.net_pnl <= 0:
            return Target80Status.EDGE_INSUFFICIENT
        return Target80Status.UNPROVEN

    def to_dict(self) -> dict[str, Any]:
        payload = {k: str(v) if isinstance(v, Decimal) else v for k, v in asdict(self).items()}
        payload["net_pnl"] = str(self.net_pnl)
        payload["target_80_status"] = self.target_80_status().value
        return payload
