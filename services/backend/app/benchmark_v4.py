from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from app.evidence import hash_payload
from app.event_tape_v4 import TapeEvent, reconstruct_l2, stable_tape_order


@dataclass(frozen=True)
class SealedEpisode:
    schema_version: int
    episode_id: str
    venue: str
    asset_id: str
    events: tuple[TapeEvent, ...]
    settlement: dict[str, Any]
    strategy_config: dict[str, Any]
    expected_invariants: tuple[str, ...]


def seal_episode(
    *,
    venue: str,
    asset_id: str,
    events: tuple[TapeEvent, ...],
    settlement: dict[str, Any],
    strategy_config: dict[str, Any],
    expected_invariants: tuple[str, ...] = (),
) -> SealedEpisode:
    ordered = stable_tape_order(events)
    if any(event.venue != venue or event.asset_id != asset_id for event in ordered):
        raise ValueError("episode identity does not match tape")
    canonical = {
        "schema_version": 1,
        "venue": venue,
        "asset_id": asset_id,
        "events": [asdict(event) for event in ordered],
        "settlement": settlement,
        "strategy_config": strategy_config,
        "expected_invariants": expected_invariants,
    }
    return SealedEpisode(
        schema_version=1,
        episode_id=hash_payload(canonical),
        venue=venue,
        asset_id=asset_id,
        events=ordered,
        settlement=dict(settlement),
        strategy_config=dict(strategy_config),
        expected_invariants=tuple(expected_invariants),
    )


def replay_episode(episode: SealedEpisode) -> dict[str, Any]:
    reconstruction = reconstruct_l2(episode.events)
    invariant_results = {
        "no_sequence_gaps": not reconstruction.gaps,
        "book_reconstructable": reconstruction.book is not None,
        "schema_supported": episode.schema_version == 1,
    }
    failed_expected = tuple(
        name for name in episode.expected_invariants if not invariant_results.get(name, False)
    )
    return {
        "episode_id": episode.episode_id,
        "status": "PASS" if not failed_expected else "FAIL",
        "event_count": len(episode.events),
        "reconstruction_status": reconstruction.status,
        "gaps": reconstruction.gaps,
        "invariants": invariant_results,
        "failed_expected_invariants": failed_expected,
    }
