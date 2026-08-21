from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any


class PairState(StrEnum):
    CANDIDATE = "CANDIDATE"
    UNVERIFIED_TITLE_ONLY = "UNVERIFIED_TITLE_ONLY"
    POLARITY_MISMATCH = "POLARITY_MISMATCH"
    RULE_MISMATCH = "RULE_MISMATCH"
    EXACT_EQUIVALENT = "EXACT_EQUIVALENT"


RULE_FIELDS = (
    "underlying",
    "polarity",
    "threshold",
    "comparison_operator",
    "reference_source",
    "window_start_utc",
    "window_end_utc",
    "resolution_instant_utc",
    "price_to_beat_construction",
    "equality_tie_handling",
    "invalid_market_rules",
    "cancellation_rules",
    "fallback_oracle_failure_rules",
    "settlement_semantics",
)


@dataclass(frozen=True)
class ResolutionRule:
    underlying: str | None
    polarity: str | None
    threshold: str | None
    comparison_operator: str | None
    reference_source: str | None
    window_start_utc: str | None
    window_end_utc: str | None
    resolution_instant_utc: str | None
    price_to_beat_construction: str | None
    equality_tie_handling: str | None
    invalid_market_rules: str | None
    cancellation_rules: str | None
    fallback_oracle_failure_rules: str | None
    settlement_semantics: str | None

    def canonical(self) -> dict[str, str | None]:
        return {field: _canon(getattr(self, field)) for field in RULE_FIELDS}

    @property
    def complete(self) -> bool:
        return all(self.canonical()[field] not in (None, "") for field in RULE_FIELDS)

    @property
    def fingerprint(self) -> str:
        payload = json.dumps(self.canonical(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class MarketDescriptor:
    venue: str
    market_id: str
    title: str
    rule: ResolutionRule | None = None
    source_payload_hash: str | None = None


@dataclass(frozen=True)
class PairComparison:
    state: PairState
    left_market_id: str
    right_market_id: str
    left_rule_fingerprint: str | None
    right_rule_fingerprint: str | None
    differing_fields: tuple[str, ...]
    unknown_fields: tuple[str, ...]
    comparison_fingerprint: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["state"] = self.state.value
        return payload


def _canon(value: Any) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).strip().split())
    return text.casefold() if text else None


def compare_markets(left: MarketDescriptor, right: MarketDescriptor) -> PairComparison:
    if left.rule is None or right.rule is None:
        return _comparison(PairState.UNVERIFIED_TITLE_ONLY, left, right, (), RULE_FIELDS)

    lrule = left.rule.canonical()
    rrule = right.rule.canonical()
    unknown = tuple(field for field in RULE_FIELDS if not lrule[field] or not rrule[field])
    if unknown:
        return _comparison(PairState.UNVERIFIED_TITLE_ONLY, left, right, (), unknown)

    if lrule["polarity"] != rrule["polarity"]:
        return _comparison(PairState.POLARITY_MISMATCH, left, right, ("polarity",), ())

    diff = tuple(field for field in RULE_FIELDS if lrule[field] != rrule[field])
    if diff:
        return _comparison(PairState.RULE_MISMATCH, left, right, diff, ())

    return _comparison(PairState.EXACT_EQUIVALENT, left, right, (), ())


def _comparison(
    state: PairState,
    left: MarketDescriptor,
    right: MarketDescriptor,
    differing: tuple[str, ...],
    unknown: tuple[str, ...],
) -> PairComparison:
    material = {
        "state": state.value,
        "left": left.market_id,
        "right": right.market_id,
        "left_rule": left.rule.fingerprint if left.rule else None,
        "right_rule": right.rule.fingerprint if right.rule else None,
        "differing": differing,
        "unknown": unknown,
    }
    digest = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return PairComparison(
        state=state,
        left_market_id=left.market_id,
        right_market_id=right.market_id,
        left_rule_fingerprint=left.rule.fingerprint if left.rule else None,
        right_rule_fingerprint=right.rule.fingerprint if right.rule else None,
        differing_fields=differing,
        unknown_fields=unknown,
        comparison_fingerprint=digest,
    )
