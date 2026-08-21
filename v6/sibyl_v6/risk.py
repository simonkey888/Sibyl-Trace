from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class RiskLimits:
    max_net_shares: Decimal
    max_hedge_failures: int
    flatten_on_stop: bool = True
    max_loss_usd: Decimal = Decimal("10")
    min_requote_ms: int = 2000
    position_read_max_age_ms: int = 15000


@dataclass
class RiskState:
    limits: RiskLimits
    quote_pulled: bool = False
    killed: bool = False
    flattened: bool = False
    hedge_failures: int = 0
    processed_event_ids: set[str] = field(default_factory=set)
    last_position_read_ms: int | None = None

    def observe_inventory(self, net_shares: Decimal) -> None:
        if abs(net_shares) >= self.limits.max_net_shares:
            self.quote_pulled = True

    def observe_hedge(self, success: bool) -> None:
        if success:
            self.hedge_failures = 0
            return
        self.hedge_failures += 1
        if self.hedge_failures >= self.limits.max_hedge_failures:
            self.quote_pulled = True

    def observe_position_read(self, observed_at_ms: int) -> None:
        self.last_position_read_ms = observed_at_ms

    def position_read_fresh(self, now_ms: int) -> bool:
        if self.last_position_read_ms is None:
            return False
        return 0 <= now_ms - self.last_position_read_ms <= self.limits.position_read_max_age_ms

    def accept_event_once(self, event_id: str) -> bool:
        if event_id in self.processed_event_ids:
            return False
        self.processed_event_ids.add(event_id)
        return True

    def kill(self) -> None:
        self.killed = True
        self.quote_pulled = True
        if self.limits.flatten_on_stop:
            self.flattened = True

    def target_80_adjusted_limits(self, target_net_24h_usd: Decimal) -> RiskLimits:
        """Measurement target cannot mutate size/risk. Returns the exact same limits."""
        _ = target_net_24h_usd
        return self.limits
