from __future__ import annotations

from dataclasses import dataclass
from typing import Any


STATE_ORDER = {"GREEN": 0, "YELLOW": 1, "RED": 2}


@dataclass(frozen=True)
class WatchdogAssessment:
    watchdog: str
    state: str
    message: str
    payload: dict[str, Any]


def _assessment(name: str, state: str, message: str, **payload: Any) -> WatchdogAssessment:
    if state not in STATE_ORDER:
        raise ValueError(f"invalid watchdog state: {state}")
    return WatchdogAssessment(name, state, message, payload)


def accounting_watchdog(
    *,
    cash: float,
    open_market_value: float,
    equity: float,
    initial_bankroll: float,
    realized_pnl: float,
    unrealized_pnl: float,
    tolerance: float = 1e-6,
) -> WatchdogAssessment:
    identity_one = cash + open_market_value
    identity_two = initial_bankroll + realized_pnl + unrealized_pnl
    error_one = abs(identity_one - equity)
    error_two = abs(identity_two - equity)
    if max(error_one, error_two) > tolerance:
        return _assessment(
            "ACCOUNTING_RECONCILIATION_FAILURE",
            "RED",
            "Portfolio identities do not reconcile",
            identity_one_error=error_one,
            identity_two_error=error_two,
        )
    return _assessment(
        "ACCOUNTING_RECONCILIATION_FAILURE",
        "GREEN",
        "Portfolio identities reconcile",
        identity_one_error=error_one,
        identity_two_error=error_two,
    )


def feed_watchdog(source_counts: dict[str, int], errors: tuple[str, ...]) -> WatchdogAssessment:
    required = ("BINANCE", "COINBASE", "POLYMARKET")
    missing = [source for source in required if source_counts.get(source, 0) == 0]
    if len(missing) >= 2:
        return _assessment(
            "LATENCY_FEED_DESYNC",
            "RED",
            "Two or more required public feeds produced no observations",
            missing=missing,
            errors=list(errors),
        )
    if missing or errors:
        return _assessment(
            "LATENCY_FEED_DESYNC",
            "YELLOW",
            "Latency evidence is degraded by a missing feed or feed error",
            missing=missing,
            errors=list(errors),
        )
    return _assessment(
        "LATENCY_FEED_DESYNC",
        "GREEN",
        "All public latency feeds produced observations",
        missing=[],
        errors=[],
    )


def sample_watchdog(name: str, sample_size: int, *, minimum: int) -> WatchdogAssessment:
    if sample_size < 0 or minimum <= 0:
        raise ValueError("invalid sample size contract")
    if sample_size == 0:
        return _assessment(name, "YELLOW", "No evidence yet", sample_size=0, minimum=minimum)
    if sample_size < minimum:
        return _assessment(
            name,
            "YELLOW",
            "Evidence remains below the preregistered sample floor",
            sample_size=sample_size,
            minimum=minimum,
        )
    return _assessment(
        name,
        "GREEN",
        "Evidence reached the preregistered sample floor",
        sample_size=sample_size,
        minimum=minimum,
    )


def edge_decay_watchdog(
    recent_edge: float | None,
    baseline_edge: float | None,
    *,
    warning_drop: float = 0.01,
    red_drop: float = 0.03,
) -> WatchdogAssessment:
    if recent_edge is None or baseline_edge is None:
        return _assessment("LATENCY_EDGE_DECAY", "YELLOW", "Edge baseline is not mature")
    drop = baseline_edge - recent_edge
    if drop >= red_drop:
        state = "RED"
    elif drop >= warning_drop:
        state = "YELLOW"
    else:
        state = "GREEN"
    return _assessment(
        "LATENCY_EDGE_DECAY",
        state,
        "Recent executable edge compared with frozen baseline",
        recent_edge=recent_edge,
        baseline_edge=baseline_edge,
        drop=drop,
    )


def global_watchdog_state(assessments: list[WatchdogAssessment]) -> str:
    if not assessments:
        return "YELLOW"
    return max(assessments, key=lambda item: STATE_ORDER[item.state]).state
