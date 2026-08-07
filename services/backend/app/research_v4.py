from __future__ import annotations

from dataclasses import asdict
from typing import Any

from app.event_tape_v4 import TapeEvent, reconstruct_l2
from app.forecast_skill_v4 import market_relative_forecast_alpha
from app.market_data_v3 import V3Event
from app.market_identity_v4 import IdentityDecision
from app.parity_v4 import compare_binary_parity
from app.temporal_features_v4 import build_temporal_features
from app.venue_v3 import NormalizedBook


def build_research_v4_summary(
    *,
    events: tuple[V3Event, ...],
    tape: tuple[TapeEvent, ...] = (),
    left_book: NormalizedBook | None = None,
    right_book: NormalizedBook | None = None,
    identity: IdentityDecision | None = None,
    model_probabilities: list[float] | None = None,
    market_probabilities: list[float] | None = None,
    outcomes: list[int] | None = None,
) -> dict[str, Any]:
    reconstruction = reconstruct_l2(tape)
    temporal = build_temporal_features(events)

    parity: dict[str, Any] = {"status": "NO_DATA"}
    if left_book is not None and right_book is not None and identity is not None:
        parity = asdict(compare_binary_parity(left_book, right_book, identity))

    forecast: dict[str, Any] = {"status": "NO_DATA"}
    if (
        model_probabilities is not None
        and market_probabilities is not None
        and outcomes is not None
    ):
        forecast = asdict(
            market_relative_forecast_alpha(
                model_probabilities,
                market_probabilities,
                outcomes,
            )
        )

    v4_status = "ACTIVE" if events or tape else "NO_DATA"
    return {
        "schema_version": 4,
        "status": v4_status,
        "safety": {
            "mode": "PAPER_SHADOW_ONLY",
            "order_placement": False,
            "private_keys": False,
            "historical_fill_rewrite": False,
        },
        "l2_reconstruction_v4": {
            "status": reconstruction.status,
            "applied_events": reconstruction.applied_events,
            "gaps": reconstruction.gaps,
            "levels": {
                "bids": len(reconstruction.book.bids) if reconstruction.book else 0,
                "asks": len(reconstruction.book.asks) if reconstruction.book else 0,
            },
        },
        "temporal_features_v4": temporal,
        "cross_venue_parity_v4": parity,
        "forecast_skill_v4": forecast,
        "v3_v4_shadow": {
            "v3_preserved": True,
            "v4_replaces_v3_fills": False,
            "comparison_mode": "ADDITIVE_ONLY",
        },
    }
