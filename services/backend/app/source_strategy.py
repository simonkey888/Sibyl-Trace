from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any

DIRECTIONAL_CANDIDATE = "DIRECTIONAL_CANDIDATE"
NON_DIRECTIONAL_MAKER = "NON_DIRECTIONAL_MAKER"
NON_DIRECTIONAL_FULL_SET = "NON_DIRECTIONAL_FULL_SET"
NON_DIRECTIONAL_TWO_SIDED = "NON_DIRECTIONAL_TWO_SIDED"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
UNAVAILABLE = "UNAVAILABLE"

_ACTIVITY_TYPES = (
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
class ActivityHistoryEvidence:
    status: str
    scope: str
    requested_limit: int
    returned_rows: int
    pages_fetched: int
    page_size: int
    exhausted: bool
    has_more: bool
    malformed_rows: int
    invalid_timestamp_rows: int
    source_hash: str
    reason: str | None = None

    @property
    def authoritative(self) -> bool:
        return self.status == "COMPLETE" and self.exhausted and not self.has_more

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SourceActivityHistory(list[dict[str, Any]]):
    def __init__(
        self,
        rows: list[dict[str, Any]],
        evidence: ActivityHistoryEvidence,
    ) -> None:
        super().__init__(rows)
        self.evidence = evidence


@dataclass(frozen=True)
class SourceStrategyProfile:
    wallet_hash: str
    classification: str
    rejection_reason: str | None
    cutoff_at: int
    event_count: int
    invalid_timestamp_event_count: int
    trade_count: int
    attributable_trade_count: int
    unattributable_trade_count: int
    maker_rebate_count: int
    taker_rebate_count: int
    split_count: int
    merge_count: int
    conversion_count: int
    paired_condition_count: int
    paired_trade_count: int
    paired_trade_fraction: float
    activity_sample_hash: str
    activity_history: dict[str, Any] | None
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
            "invalid_timestamp_event_count": self.invalid_timestamp_event_count,
            "trade_count": self.trade_count,
            "attributable_trade_count": self.attributable_trade_count,
            "unattributable_trade_count": self.unattributable_trade_count,
            "maker_rebate_count": self.maker_rebate_count,
            "taker_rebate_count": self.taker_rebate_count,
            "split_count": self.split_count,
            "merge_count": self.merge_count,
            "conversion_count": self.conversion_count,
            "paired_condition_count": self.paired_condition_count,
            "paired_trade_count": self.paired_trade_count,
            "paired_trade_fraction": self.paired_trade_fraction,
            "activity_sample_hash": self.activity_sample_hash,
            "activity_history": self.activity_history,
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


def _event_timestamp(event: dict[str, Any]) -> int:
    try:
        return int(event.get("timestamp") or 0)
    except (TypeError, ValueError):
        return 0


def _event_identity(event: dict[str, Any]) -> dict[str, Any]:
    outcome_index = event.get("outcomeIndex")
    return {
        "type": str(event.get("type") or "").upper(),
        "transaction_hash": str(event.get("transactionHash") or ""),
        "condition_id": str(event.get("conditionId") or ""),
        "asset_id": str(event.get("asset") or ""),
        "side": str(event.get("side") or "").upper(),
        "outcome_index": "" if outcome_index is None else str(outcome_index),
        "outcome": str(event.get("outcome") or "").strip().casefold(),
        "timestamp": _event_timestamp(event),
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
            row["outcome"],
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


def _activity_params(
    wallet: str,
    *,
    cutoff_at: int,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    return {
        "user": wallet,
        "type": ",".join(_ACTIVITY_TYPES),
        "start": 1,
        "end": max(int(cutoff_at), 1),
        "limit": limit,
        "offset": offset,
        "sortBy": "TIMESTAMP",
        "sortDirection": "DESC",
    }


def fetch_public_activity_events(
    client: Any,
    wallet: str,
    *,
    cutoff_at: int,
    limit: int,
) -> SourceActivityHistory:
    """Read public activity with explicit completeness evidence.

    Polymarket currently caps a single page at 500 rows and permits offsets up to
    10,000. Sibyl reads up to the configured bound and probes the next row when
    the bound is filled exactly. Any unknown continuation, malformed row or bad
    timestamp is non-authoritative and therefore cannot select a source wallet.
    """

    target = min(max(int(limit), 0), 10_000)
    if target == 0:
        evidence = ActivityHistoryEvidence(
            status="COMPLETE",
            scope="EMPTY_REQUEST",
            requested_limit=0,
            returned_rows=0,
            pages_fetched=0,
            page_size=0,
            exhausted=True,
            has_more=False,
            malformed_rows=0,
            invalid_timestamp_rows=0,
            source_hash=canonical_hash([]),
        )
        return SourceActivityHistory([], evidence)

    results: list[dict[str, Any]] = []
    raw_identity_material: list[Any] = []
    seen: set[str] = set()
    page_size = min(500, target)
    pages_fetched = 0
    malformed_rows = 0
    invalid_timestamp_rows = 0
    exhausted = False

    for offset in range(0, target, page_size):
        current_limit = min(page_size, target - offset)
        data = client._get(
            f"{client.settings.data_api_base}/activity",
            _activity_params(
                wallet,
                cutoff_at=cutoff_at,
                limit=current_limit,
                offset=offset,
            ),
        )
        if not isinstance(data, list):
            raise ValueError("public_activity_response_not_list")
        pages_fetched += 1
        raw_identity_material.append(data)
        for event in data:
            if not isinstance(event, dict):
                malformed_rows += 1
                continue
            timestamp = _event_timestamp(event)
            if timestamp <= 0 or timestamp > cutoff_at:
                invalid_timestamp_rows += 1
                continue
            identity_hash = canonical_hash(_event_identity(event))
            if identity_hash in seen:
                continue
            seen.add(identity_hash)
            results.append(event)
        if len(data) < current_limit:
            exhausted = True
            break

    has_more = False
    if not exhausted and malformed_rows == 0 and invalid_timestamp_rows == 0:
        probe = client._get(
            f"{client.settings.data_api_base}/activity",
            _activity_params(
                wallet,
                cutoff_at=cutoff_at,
                limit=1,
                offset=target,
            ),
        )
        if not isinstance(probe, list):
            raise ValueError("public_activity_probe_response_not_list")
        pages_fetched += 1
        raw_identity_material.append(probe)
        has_more = bool(probe)
        exhausted = not has_more

    reason: str | None = None
    status = "COMPLETE"
    scope = "FULL_AVAILABLE_FILTERED_HISTORY"
    if malformed_rows:
        status = "INCOMPLETE"
        scope = "INVALID_DATA"
        reason = "malformed_activity_rows"
    elif invalid_timestamp_rows:
        status = "INCOMPLETE"
        scope = "INVALID_DATA"
        reason = "invalid_activity_timestamps"
    elif has_more or not exhausted:
        status = "INCOMPLETE"
        scope = "BOUNDED_WINDOW"
        reason = "activity_history_has_more"

    evidence = ActivityHistoryEvidence(
        status=status,
        scope=scope,
        requested_limit=target,
        returned_rows=len(results),
        pages_fetched=pages_fetched,
        page_size=page_size,
        exhausted=exhausted,
        has_more=has_more,
        malformed_rows=malformed_rows,
        invalid_timestamp_rows=invalid_timestamp_rows,
        source_hash=canonical_hash(raw_identity_material),
        reason=reason,
    )
    return SourceActivityHistory(results, evidence)


def classify_source_strategy(
    wallet: str,
    events: list[dict[str, Any]],
    *,
    cutoff_at: int,
    policy: SourceStrategyPolicy,
) -> SourceStrategyProfile:
    raw = [event for event in events if isinstance(event, dict)]
    clean = [
        event
        for event in raw
        if 0 < _event_timestamp(event) <= int(cutoff_at)
    ]
    invalid_timestamp_event_count = len(raw) - len(clean)
    counts = {event_type: 0 for event_type in _ACTIVITY_TYPES}
    outcomes_by_condition: dict[str, set[str]] = {}
    trade_count_by_condition: dict[str, int] = {}
    attributable_trade_count = 0

    for event in clean:
        event_type = str(event.get("type") or "").upper()
        if event_type in counts:
            counts[event_type] += 1
        if event_type != "TRADE":
            continue
        condition_id = str(event.get("conditionId") or "").strip()
        outcome_index = event.get("outcomeIndex")
        outcome_key = (
            str(outcome_index)
            if outcome_index is not None and str(outcome_index) != ""
            else str(event.get("outcome") or "").strip().casefold()
        )
        if not condition_id or not outcome_key:
            continue
        attributable_trade_count += 1
        outcomes_by_condition.setdefault(condition_id, set()).add(outcome_key)
        trade_count_by_condition[condition_id] = (
            trade_count_by_condition.get(condition_id, 0) + 1
        )

    paired_conditions = {
        condition_id
        for condition_id, outcomes in outcomes_by_condition.items()
        if len(outcomes) >= 2
    }
    paired_trade_count = sum(
        trade_count_by_condition.get(condition_id, 0)
        for condition_id in paired_conditions
    )
    trade_count = counts["TRADE"]
    unattributable_trade_count = max(trade_count - attributable_trade_count, 0)
    paired_fraction = (
        paired_trade_count / attributable_trade_count if attributable_trade_count else 0.0
    )

    history_evidence = getattr(events, "evidence", None)
    history_payload = (
        history_evidence.to_dict()
        if isinstance(history_evidence, ActivityHistoryEvidence)
        else None
    )

    classification = DIRECTIONAL_CANDIDATE
    rejection_reason: str | None = None
    if history_evidence is not None and not history_evidence.authoritative:
        classification = UNAVAILABLE
        rejection_reason = "source_strategy_history_incomplete"
    elif counts["SPLIT"] > 0 or counts["MERGE"] > 0 or counts["CONVERSION"] > 0:
        classification = NON_DIRECTIONAL_FULL_SET
        rejection_reason = "source_strategy_full_set_or_conversion"
    elif (
        len(paired_conditions) >= policy.min_paired_conditions
        and paired_fraction >= policy.max_paired_trade_fraction
    ):
        classification = NON_DIRECTIONAL_TWO_SIDED
        rejection_reason = "source_strategy_two_sided"
    elif attributable_trade_count < policy.min_trade_count:
        classification = INSUFFICIENT_EVIDENCE
        rejection_reason = "source_strategy_insufficient_evidence"

    profile_without_hash = {
        "wallet_hash": wallet_hash(wallet),
        "classification": classification,
        "rejection_reason": rejection_reason,
        "cutoff_at": int(cutoff_at),
        "event_count": len(clean),
        "invalid_timestamp_event_count": invalid_timestamp_event_count,
        "trade_count": trade_count,
        "attributable_trade_count": attributable_trade_count,
        "unattributable_trade_count": unattributable_trade_count,
        "maker_rebate_count": counts["MAKER_REBATE"],
        "taker_rebate_count": 0,
        "split_count": counts["SPLIT"],
        "merge_count": counts["MERGE"],
        "conversion_count": counts["CONVERSION"],
        "paired_condition_count": len(paired_conditions),
        "paired_trade_count": paired_trade_count,
        "paired_trade_fraction": round(paired_fraction, 6),
        "activity_sample_hash": _sample_hash(clean),
        "activity_history": history_payload,
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
        invalid_timestamp_event_count=invalid_timestamp_event_count,
        trade_count=trade_count,
        attributable_trade_count=attributable_trade_count,
        unattributable_trade_count=unattributable_trade_count,
        maker_rebate_count=counts["MAKER_REBATE"],
        taker_rebate_count=0,
        split_count=counts["SPLIT"],
        merge_count=counts["MERGE"],
        conversion_count=counts["CONVERSION"],
        paired_condition_count=len(paired_conditions),
        paired_trade_count=paired_trade_count,
        paired_trade_fraction=round(paired_fraction, 6),
        activity_sample_hash=profile_without_hash["activity_sample_hash"],
        activity_history=history_payload,
        evidence_hash=evidence_hash,
        policy=policy,
    )
