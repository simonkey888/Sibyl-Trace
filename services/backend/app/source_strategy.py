from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

DIRECTIONAL_CANDIDATE = "DIRECTIONAL_CANDIDATE"
NON_DIRECTIONAL_MAKER = "NON_DIRECTIONAL_MAKER"
NON_DIRECTIONAL_FULL_SET = "NON_DIRECTIONAL_FULL_SET"
NON_DIRECTIONAL_TWO_SIDED = "NON_DIRECTIONAL_TWO_SIDED"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
UNAVAILABLE = "UNAVAILABLE"

_EVENT_TYPES = (
    "TRADE",
    "SPLIT",
    "MERGE",
    "REDEEM",
    "REWARD",
    "CONVERSION",
    "MAKER_REBATE",
    "REFERRAL_REWARD",
)


@dataclass(frozen=True)
class SourceStrategyPolicy:
    min_trade_count: int = 30
    min_paired_conditions: int = 2
    max_paired_trade_fraction: float = 0.25


@dataclass(frozen=True)
class SourceStrategyProfile:
    wallet_hash: str
    classification: str
    rejection_reason: str | None
    cutoff_at: int
    event_count: int
    trade_count: int
    maker_rebate_count: int
    split_count: int
    merge_count: int
    conversion_count: int
    paired_condition_count: int
    paired_trade_count: int
    paired_trade_fraction: float
    activity_sample_hash: str
    evidence_hash: str
    policy: SourceStrategyPolicy

    @property
    def directional(self) -> bool:
        return self.classification == DIRECTIONAL_CANDIDATE

    def to_dict(self) -> dict[str, Any]:
        return {
            "wallet_hash": self.wallet_hash,
            "classification": self.classification,
            "directional": self.directional,
            "rejection_reason": self.rejection_reason,
            "cutoff_at": self.cutoff_at,
            "event_count": self.event_count,
            "trade_count": self.trade_count,
            "maker_rebate_count": self.maker_rebate_count,
            "split_count": self.split_count,
            "merge_count": self.merge_count,
            "conversion_count": self.conversion_count,
            "paired_condition_count": self.paired_condition_count,
            "paired_trade_count": self.paired_trade_count,
            "paired_trade_fraction": self.paired_trade_fraction,
            "activity_sample_hash": self.activity_sample_hash,
            "evidence_hash": self.evidence_hash,
            "policy": {
                "min_trade_count": self.policy.min_trade_count,
                "min_paired_conditions": self.policy.min_paired_conditions,
                "max_paired_trade_fraction": self.policy.max_paired_trade_fraction,
            },
        }


def canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode()).hexdigest()


def wallet_hash(address: str) -> str:
    return hashlib.sha256(str(address).strip().lower().encode()).hexdigest()


def _event_identity(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": str(event.get("type") or "").upper(),
        "transaction_hash": str(event.get("transactionHash") or ""),
        "condition_id": str(event.get("conditionId") or ""),
        "asset_id": str(event.get("asset") or ""),
        "side": str(event.get("side") or "").upper(),
        "outcome_index": str(event.get("outcomeIndex") or ""),
        "timestamp": int(event.get("timestamp") or 0),
        "price": str(event.get("price") or ""),
        "size": str(event.get("size") or ""),
        "usdc_size": str(event.get("usdcSize") or ""),
    }


def _sample_hash(events: list[dict[str, Any]]) -> str:
    normalized = [_event_identity(event) for event in events if isinstance(event, dict)]
    normalized.sort(
        key=lambda row: (
            row["timestamp"],
            row["type"],
            row["transaction_hash"],
            row["condition_id"],
            row["asset_id"],
            row["side"],
            row["outcome_index"],
        )
    )
    return canonical_hash(normalized)


def _profile_material(profile: dict[str, Any]) -> dict[str, Any]:
    material = dict(profile)
    material.pop("evidence_hash", None)
    material.pop("directional", None)
    return material


def profile_hash_valid(profile: dict[str, Any]) -> bool:
    claimed = str(profile.get("evidence_hash") or "")
    return len(claimed) == 64 and claimed == canonical_hash(_profile_material(profile))


def fetch_public_activity_events(
    client: Any,
    wallet: str,
    *,
    cutoff_at: int,
    limit: int,
) -> list[dict[str, Any]]:
    """Read a bounded, point-in-time public activity sample without trading auth."""
    target = min(max(int(limit), 0), 5000)
    if target == 0:
        return []
    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    page_size = min(500, target)
    for offset in range(0, target, page_size):
        current_limit = min(page_size, target - offset)
        data = client._get(
            f"{client.settings.data_api_base}/activity",
            {
                "user": wallet,
                "start": 0,
                "end": max(int(cutoff_at), 0),
                "limit": current_limit,
                "offset": offset,
                "sortBy": "TIMESTAMP",
                "sortDirection": "DESC",
            },
        )
        page = data if isinstance(data, list) else []
        for event in page:
            if not isinstance(event, dict):
                continue
            if int(event.get("timestamp") or 0) > cutoff_at:
                continue
            identity_hash = canonical_hash(_event_identity(event))
            if identity_hash in seen:
                continue
            seen.add(identity_hash)
            results.append(event)
        if len(page) < current_limit:
            break
    return results


def classify_source_strategy(
    wallet: str,
    events: list[dict[str, Any]],
    *,
    cutoff_at: int,
    policy: SourceStrategyPolicy,
) -> SourceStrategyProfile:
    clean = [event for event in events if isinstance(event, dict)]
    counts = {event_type: 0 for event_type in _EVENT_TYPES}
    outcomes_by_condition: dict[str, set[str]] = {}
    trade_count_by_condition: dict[str, int] = {}

    for event in clean:
        event_type = str(event.get("type") or "").upper()
        if event_type in counts:
            counts[event_type] += 1
        if event_type != "TRADE":
            continue
        condition_id = str(event.get("conditionId") or "").strip()
        if not condition_id:
            continue
        outcome_index = event.get("outcomeIndex")
        outcome_key = (
            str(outcome_index)
            if outcome_index is not None and str(outcome_index) != ""
            else str(event.get("outcome") or "").strip().casefold()
        )
        if not outcome_key:
            continue
        outcomes_by_condition.setdefault(condition_id, set()).add(outcome_key)
        trade_count_by_condition[condition_id] = trade_count_by_condition.get(condition_id, 0) + 1

    paired_conditions = {
        condition_id
        for condition_id, outcomes in outcomes_by_condition.items()
        if len(outcomes) >= 2
    }
    paired_trade_count = sum(
        trade_count_by_condition.get(condition_id, 0) for condition_id in paired_conditions
    )
    trade_count = counts["TRADE"]
    paired_fraction = paired_trade_count / trade_count if trade_count else 0.0

    classification = DIRECTIONAL_CANDIDATE
    rejection_reason: str | None = None
    if counts["MAKER_REBATE"] > 0:
        classification = NON_DIRECTIONAL_MAKER
        rejection_reason = "source_strategy_maker_rebate"
    elif counts["SPLIT"] > 0 or counts["MERGE"] > 0 or counts["CONVERSION"] > 0:
        classification = NON_DIRECTIONAL_FULL_SET
        rejection_reason = "source_strategy_full_set_or_conversion"
    elif (
        len(paired_conditions) >= policy.min_paired_conditions
        and paired_fraction >= policy.max_paired_trade_fraction
    ):
        classification = NON_DIRECTIONAL_TWO_SIDED
        rejection_reason = "source_strategy_two_sided"
    elif trade_count < policy.min_trade_count:
        classification = INSUFFICIENT_EVIDENCE
        rejection_reason = "source_strategy_insufficient_evidence"

    profile_without_hash = {
        "wallet_hash": wallet_hash(wallet),
        "classification": classification,
        "rejection_reason": rejection_reason,
        "cutoff_at": int(cutoff_at),
        "event_count": len(clean),
        "trade_count": trade_count,
        "maker_rebate_count": counts["MAKER_REBATE"],
        "split_count": counts["SPLIT"],
        "merge_count": counts["MERGE"],
        "conversion_count": counts["CONVERSION"],
        "paired_condition_count": len(paired_conditions),
        "paired_trade_count": paired_trade_count,
        "paired_trade_fraction": round(paired_fraction, 6),
        "activity_sample_hash": _sample_hash(clean),
        "policy": {
            "min_trade_count": policy.min_trade_count,
            "min_paired_conditions": policy.min_paired_conditions,
            "max_paired_trade_fraction": policy.max_paired_trade_fraction,
        },
    }
    evidence_hash = canonical_hash(profile_without_hash)
    return SourceStrategyProfile(
        wallet_hash=profile_without_hash["wallet_hash"],
        classification=classification,
        rejection_reason=rejection_reason,
        cutoff_at=int(cutoff_at),
        event_count=len(clean),
        trade_count=trade_count,
        maker_rebate_count=counts["MAKER_REBATE"],
        split_count=counts["SPLIT"],
        merge_count=counts["MERGE"],
        conversion_count=counts["CONVERSION"],
        paired_condition_count=len(paired_conditions),
        paired_trade_count=paired_trade_count,
        paired_trade_fraction=round(paired_fraction, 6),
        activity_sample_hash=profile_without_hash["activity_sample_hash"],
        evidence_hash=evidence_hash,
        policy=policy,
    )
