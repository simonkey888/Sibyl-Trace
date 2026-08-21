from __future__ import annotations

import json
from decimal import Decimal as D
from pathlib import Path

from .execution_evidence import BookSnapshot, FeeQuote, L2Level, evaluate_fill_to_hedge


def build_simulated_hedge() -> dict:
    decision = BookSnapshot(
        venue="POLYMARKET",
        market_id="synthetic-exact-pair",
        side="BUY_NO",
        timestamp_ms=1_000_000,
        levels=(L2Level(D("0.49"), D("3")), L2Level(D("0.50"), D("4"))),
        source="DETERMINISTIC_SYNTHETIC_L2",
    )
    arrival = BookSnapshot(
        venue="POLYMARKET",
        market_id="synthetic-exact-pair",
        side="BUY_NO",
        timestamp_ms=1_000_040,
        levels=(L2Level(D("0.50"), D("2")), L2Level(D("0.51"), D("3"))),
        source="DETERMINISTIC_SYNTHETIC_L2",
    )
    evidence = evaluate_fill_to_hedge(
        decision_book=decision,
        arrival_book=arrival,
        requested_size=D("5"),
        maker_fill_price=D("0.47"),
        fee=FeeQuote("SYNTHETIC_TEST_FEE_SCHEDULE", D("50")),
        decision_timestamp_ms=1_000_050,
        market_timestamp_ms=1_000_030,
        hedge_started_ms=1_000_060,
        hedge_finished_ms=1_000_125,
        hedge_success=True,
        max_quote_age_ms=100,
        max_market_age_ms=100,
        infra_cost_usd=D("0"),
    )
    return {
        "schema_version": "SIBYL_V6_SIMULATED_HEDGE_V1",
        "evidence_class": "SYNTHETIC_PAPER_NOT_REAL_FILL",
        "result": evidence.to_dict(),
        "LIVE": "NO",
        "REAL_ORDERS": 0,
        "CAPITAL_MOVED_USD": "0",
    }


def write_simulated_hedge(path: Path) -> dict:
    payload = build_simulated_hedge()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload
