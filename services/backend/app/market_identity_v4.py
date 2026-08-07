from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Equivalence = Literal[
    "EXACT_EQUIVALENT",
    "CONDITIONAL_EQUIVALENT",
    "NON_EQUIVALENT",
    "UNKNOWN",
]


@dataclass(frozen=True)
class MarketContract:
    venue: str
    market_id: str
    title: str
    underlying: str | None
    event: str | None
    outcome: str | None
    strike: str | None
    cutoff_iso: str | None
    timezone: str | None
    resolution_source: str | None
    resolution_rule: str | None
    exceptions: tuple[str, ...] = ()


@dataclass(frozen=True)
class IdentityDecision:
    decision: Equivalence
    candidate_similarity: float
    mismatches: tuple[str, ...]
    unknown_fields: tuple[str, ...]


def _norm(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
    return re.sub(r"\s+", " ", cleaned) or None


def title_similarity(left: str, right: str) -> float:
    left_tokens = set(_norm(left).split()) if _norm(left) else set()
    right_tokens = set(_norm(right).split()) if _norm(right) else set()
    union = left_tokens | right_tokens
    return len(left_tokens & right_tokens) / len(union) if union else 0.0


def compare_contracts(left: MarketContract, right: MarketContract) -> IdentityDecision:
    similarity = title_similarity(left.title, right.title)
    fields = (
        "underlying",
        "event",
        "outcome",
        "strike",
        "cutoff_iso",
        "timezone",
        "resolution_source",
        "resolution_rule",
    )
    mismatches: list[str] = []
    unknown: list[str] = []
    for field in fields:
        left_value = _norm(getattr(left, field))
        right_value = _norm(getattr(right, field))
        if left_value is None or right_value is None:
            unknown.append(field)
        elif left_value != right_value:
            mismatches.append(field)

    left_exceptions = {_norm(value) for value in left.exceptions if _norm(value)}
    right_exceptions = {_norm(value) for value in right.exceptions if _norm(value)}
    if left_exceptions != right_exceptions:
        mismatches.append("exceptions")

    resolution_mismatch = any(
        field in mismatches
        for field in ("cutoff_iso", "timezone", "resolution_source", "resolution_rule", "exceptions")
    )
    semantic_mismatch = any(
        field in mismatches for field in ("underlying", "event", "outcome", "strike")
    )

    if resolution_mismatch or semantic_mismatch:
        decision: Equivalence = "NON_EQUIVALENT"
    elif unknown:
        decision = "UNKNOWN" if similarity < 0.5 else "CONDITIONAL_EQUIVALENT"
    else:
        decision = "EXACT_EQUIVALENT"
    return IdentityDecision(decision, similarity, tuple(mismatches), tuple(unknown))


def equivalence_allows_parity(decision: IdentityDecision) -> bool:
    return decision.decision == "EXACT_EQUIVALENT"
