from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any

HISTORY_COMPLETENESS_SCHEMA = "HISTORY_COMPLETENESS_V1"
SCORE_PROVENANCE_SCHEMA = "SCORE_PROVENANCE_V1"
SCORE_DETERMINISM_SCHEMA = "SCORE_DETERMINISM_V1"
EXTERNAL_FORENSICS_SCHEMA = "EXTERNAL_MARKET_FORENSICS_V1"
OOS_CONTROL_SCHEMA = "OOS_CONTROL_GROUP_V2"
PROSPECTIVE_OOS_COHORT_SCHEMA = "PROSPECTIVE_OOS_COHORT_V1"


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        default=str,
    )


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class HistoryEvidence:
    status: str
    scope: str
    requested_limit: int
    returned_rows: int
    pages_fetched: int
    page_size: int
    exhausted: bool
    has_more: bool
    source_order: str
    source_hash: str
    reason: str | None = None

    @property
    def authoritative(self) -> bool:
        return self.status == "COMPLETE"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def history_evidence(
    pages: list[list[dict]],
    *,
    requested_limit: int,
    page_size: int,
    source_order: str = "TIMESTAMP_DESC",
    source_payload: Any | None = None,
    transport_complete: bool = True,
) -> HistoryEvidence:
    rows = [item for page in pages for item in page]
    exhausted = bool(pages) and len(pages[-1]) < page_size
    has_more = not exhausted and len(rows) >= requested_limit
    if not pages and requested_limit > 0:
        status, scope, reason = "INCOMPLETE", "UNKNOWN", "empty_response"
    elif not transport_complete:
        status, scope, reason = "INCOMPLETE", "UNKNOWN", "transport_incomplete"
    elif has_more:
        status, scope, reason = (
            "INCOMPLETE",
            "BOUNDED_WINDOW",
            "history_limit_reached",
        )
    else:
        status, scope, reason = "COMPLETE", "FULL_AVAILABLE_HISTORY", None
    source = source_payload if source_payload is not None else rows
    return HistoryEvidence(
        status=status,
        scope=scope,
        requested_limit=requested_limit,
        returned_rows=len(rows),
        pages_fetched=len(pages),
        page_size=page_size,
        exhausted=exhausted,
        has_more=has_more,
        source_order=source_order,
        source_hash=sha256_json(source),
        reason=reason,
    )


def canonicalize_closed_positions(rows: list[dict]) -> list[dict]:
    def key(item: dict) -> tuple[int, str, str, str, str, str]:
        return (
            -int(item.get("timestamp") or item.get("closedTimestamp") or 0),
            str(item.get("transactionHash") or ""),
            str(item.get("conditionId") or ""),
            str(item.get("asset") or item.get("assetId") or ""),
            str(item.get("outcome") or ""),
            str(item.get("realizedPnl") or ""),
        )

    return sorted(rows, key=key)


def canonical_score_windows(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    canonical = canonicalize_closed_positions(rows)
    return canonical[:50], canonical[:200]


def score_input_hash(
    *,
    short_rows: list[dict],
    long_rows: list[dict],
    volume: float,
    algorithm_version: str,
) -> str:
    canonical_short = canonicalize_closed_positions(short_rows)[:50]
    canonical_long = canonicalize_closed_positions(long_rows)[:200]
    return sha256_json(
        {
            "algorithm_version": algorithm_version,
            "short_rows": canonical_short,
            "long_rows": canonical_long,
            "volume": volume,
        }
    )


@dataclass(frozen=True)
class ScoreProvenance:
    schema_version: str
    algorithm_version: str
    code_sha: str
    calculated_at: str
    source_endpoint: str
    history_status: str
    history_scope: str
    source_row_count: int
    decided_row_count: int
    source_hash: str
    input_hash: str
    short_source_hash: str
    long_source_hash: str
    score: float
    rejection_reason: str | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def make_score_provenance(
    *,
    code_sha: str,
    source_endpoint: str,
    history: HistoryEvidence,
    short_rows: list[dict],
    long_rows: list[dict],
    decided_row_count: int,
    volume: float,
    score: float,
    rejection_reason: str | None,
    algorithm_version: str = "SCORE_60_40_V1",
) -> ScoreProvenance:
    canonical_short = canonicalize_closed_positions(short_rows)[:50]
    canonical_long = canonicalize_closed_positions(long_rows)[:200]
    return ScoreProvenance(
        schema_version=SCORE_PROVENANCE_SCHEMA,
        algorithm_version=algorithm_version,
        code_sha=code_sha,
        calculated_at=datetime.now(UTC).isoformat(),
        source_endpoint=source_endpoint,
        history_status=history.status,
        history_scope=history.scope,
        source_row_count=len(canonical_long),
        decided_row_count=decided_row_count,
        source_hash=history.source_hash,
        input_hash=score_input_hash(
            short_rows=canonical_short,
            long_rows=canonical_long,
            volume=volume,
            algorithm_version=algorithm_version,
        ),
        short_source_hash=sha256_json(canonical_short),
        long_source_hash=sha256_json(canonical_long),
        score=score,
        rejection_reason=rejection_reason,
    )


def deterministic_score_payload(
    *,
    short_rows: list[dict],
    long_rows: list[dict],
    volume: float,
    score: float,
) -> dict[str, Any]:
    return {
        "schema_version": SCORE_DETERMINISM_SCHEMA,
        "input_hash": score_input_hash(
            short_rows=short_rows,
            long_rows=long_rows,
            volume=volume,
            algorithm_version="SCORE_60_40_V1",
        ),
        "score": score,
    }


def infer_external_symbol(market_title: str, outcome: str = "") -> str | None:
    text = f"{market_title} {outcome}".casefold()
    if "bitcoin" in text or "btc" in text:
        return "BTCUSDT"
    if "ethereum" in text or "eth" in text:
        return "ETHUSDT"
    if "solana" in text or "sol" in text:
        return "SOLUSDT"
    return None


def _proposition_bias(market_title: str) -> int | None:
    tokens = set(re.findall(r"[a-z0-9]+", market_title.casefold()))
    bullish = {"above", "higher", "up", "bull", "bullish"}
    bearish = {"below", "lower", "down", "bear", "bearish"}
    has_bullish = bool(tokens & bullish)
    has_bearish = bool(tokens & bearish)
    if has_bullish == has_bearish:
        return None
    return 1 if has_bullish else -1


def infer_market_bias(market_title: str, outcome: str = "") -> int | None:
    normalized_outcome = outcome.strip().casefold()
    if normalized_outcome in {"up", "above", "higher", "bull", "bullish"}:
        return 1
    if normalized_outcome in {"down", "below", "lower", "bear", "bearish"}:
        return -1

    proposition = _proposition_bias(market_title)
    if normalized_outcome == "yes":
        return proposition
    if normalized_outcome == "no":
        return -proposition if proposition is not None else None
    if not normalized_outcome:
        return proposition
    return None


@dataclass(frozen=True)
class ExternalMarkout:
    schema_version: str
    status: str
    provider: str
    symbol: str | None
    source_timestamp_ms: int
    entry_price: float | None
    prices: dict[str, float]
    point_timestamps_ms: dict[str, int]
    markout: dict[str, float]
    pre_move: float | None
    post_move: float | None
    lead_lag: str
    bias: int | None
    raw_response_hash: str
    malformed_rows: int
    missing_horizons: int
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def classify_lead_lag(
    pre_move: float | None,
    post_move: float | None,
    bias: int | None,
) -> str:
    if pre_move is None or post_move is None or bias is None:
        return "UNKNOWN"
    pre = bias * pre_move
    post = bias * post_move
    if pre > 0.001 and post > pre:
        return "LEADING"
    if abs(pre) <= 0.001 and post > 0.001:
        return "LAGGING"
    if pre > 0.001 and abs(post - pre) <= 0.001:
        return "COINCIDENT"
    return "UNKNOWN"


class BinancePublicFeed:
    """Read-only public market-data feed; no credentials and no trading capability."""

    base_url = "https://data-api.binance.vision/api/v3/klines"

    def __init__(self, http_client: Any):
        self.http_client = http_client

    def klines(self, symbol: str, start_ms: int, end_ms: int) -> list[list[Any]]:
        response = self.http_client.get(
            self.base_url,
            params={
                "symbol": symbol,
                "interval": "1s",
                "startTime": start_ms,
                "endTime": end_ms,
                "limit": 1000,
            },
        )
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("external feed returned non-list klines")
        return payload


def _empty_markout(
    *,
    status: str,
    symbol: str | None,
    source_timestamp_ms: int,
    entry_price: float | None,
    bias: int | None,
    raw_response_hash: str,
    malformed_rows: int,
    reason: str,
) -> ExternalMarkout:
    return ExternalMarkout(
        schema_version=EXTERNAL_FORENSICS_SCHEMA,
        status=status,
        provider="BINANCE_PUBLIC",
        symbol=symbol,
        source_timestamp_ms=source_timestamp_ms,
        entry_price=entry_price,
        prices={},
        point_timestamps_ms={},
        markout={},
        pre_move=None,
        post_move=None,
        lead_lag="UNKNOWN",
        bias=bias,
        raw_response_hash=raw_response_hash,
        malformed_rows=malformed_rows,
        missing_horizons=6,
        reason=reason,
    )


def _select_closed_point(
    points: list[tuple[int, float]],
    target_ms: int,
    *,
    max_lag_ms: int = 1500,
) -> tuple[int, float] | None:
    eligible = [point for point in points if point[0] <= target_ms]
    if not eligible:
        return None
    selected = max(eligible, key=lambda point: point[0])
    if target_ms - selected[0] > max_lag_ms:
        return None
    return selected


def external_markout(
    feed: BinancePublicFeed,
    *,
    market_title: str,
    outcome: str,
    source_timestamp_ms: int,
    entry_price: float | None,
) -> ExternalMarkout:
    symbol = infer_external_symbol(market_title, outcome)
    bias = infer_market_bias(market_title, outcome)
    if symbol is None or bias is None:
        return _empty_markout(
            status="UNKNOWN",
            symbol=symbol,
            source_timestamp_ms=source_timestamp_ms,
            entry_price=entry_price,
            bias=bias,
            raw_response_hash=sha256_json([]),
            malformed_rows=0,
            reason="unmapped_market_or_bias",
        )
    if entry_price is None or not math.isfinite(entry_price) or entry_price <= 0:
        return _empty_markout(
            status="INVALID_DATA",
            symbol=symbol,
            source_timestamp_ms=source_timestamp_ms,
            entry_price=entry_price,
            bias=bias,
            raw_response_hash=sha256_json([]),
            malformed_rows=0,
            reason="invalid_entry_price",
        )

    start = max(source_timestamp_ms - 62_000, 0)
    end = source_timestamp_ms + 302_000
    rows = feed.klines(symbol, start, end)
    raw_hash = sha256_json(rows)
    if not rows:
        return _empty_markout(
            status="INCOMPLETE",
            symbol=symbol,
            source_timestamp_ms=source_timestamp_ms,
            entry_price=entry_price,
            bias=bias,
            raw_response_hash=raw_hash,
            malformed_rows=0,
            reason="empty_external_window",
        )

    closed_points: list[tuple[int, float]] = []
    malformed_rows = 0
    for row in rows:
        if not isinstance(row, list) or len(row) < 7:
            malformed_rows += 1
            continue
        try:
            close = float(row[4])
            close_timestamp = int(row[6])
        except (TypeError, ValueError):
            malformed_rows += 1
            continue
        if not math.isfinite(close) or close <= 0 or close_timestamp <= 0:
            malformed_rows += 1
            continue
        closed_points.append((close_timestamp, close))

    targets = {
        "pre_60s": source_timestamp_ms - 60_000,
        "t0": source_timestamp_ms,
        "10s": source_timestamp_ms + 10_000,
        "30s": source_timestamp_ms + 30_000,
        "60s": source_timestamp_ms + 60_000,
        "300s": source_timestamp_ms + 300_000,
    }
    prices: dict[str, float] = {}
    timestamps: dict[str, int] = {}
    for label, target in targets.items():
        selected = _select_closed_point(closed_points, target)
        if selected is None:
            continue
        timestamps[label], prices[label] = selected

    missing = len(targets) - len(prices)
    if "t0" not in prices:
        return ExternalMarkout(
            schema_version=EXTERNAL_FORENSICS_SCHEMA,
            status="INCOMPLETE",
            provider="BINANCE_PUBLIC",
            symbol=symbol,
            source_timestamp_ms=source_timestamp_ms,
            entry_price=entry_price,
            prices=prices,
            point_timestamps_ms=timestamps,
            markout={},
            pre_move=None,
            post_move=None,
            lead_lag="UNKNOWN",
            bias=bias,
            raw_response_hash=raw_hash,
            malformed_rows=malformed_rows,
            missing_horizons=missing,
            reason="missing_external_t0",
        )
    if timestamps["t0"] > source_timestamp_ms:
        raise AssertionError("external_t0_must_not_use_future_data")

    t0 = prices["t0"]
    markout = {
        label: bias * (value - t0) / t0
        for label, value in prices.items()
        if label in {"10s", "30s", "60s", "300s"}
    }
    pre_move = None
    if "pre_60s" in prices:
        pre_move = (t0 - prices["pre_60s"]) / prices["pre_60s"]
    post_move = markout.get("60s")
    required = {"10s", "30s", "60s"}
    status = "VERIFIED" if required.issubset(markout) else "INCOMPLETE"
    return ExternalMarkout(
        schema_version=EXTERNAL_FORENSICS_SCHEMA,
        status=status,
        provider="BINANCE_PUBLIC",
        symbol=symbol,
        source_timestamp_ms=source_timestamp_ms,
        entry_price=entry_price,
        prices=prices,
        point_timestamps_ms=timestamps,
        markout=markout,
        pre_move=pre_move,
        post_move=post_move,
        lead_lag=classify_lead_lag(pre_move, post_move, bias),
        bias=bias,
        raw_response_hash=raw_hash,
        malformed_rows=malformed_rows,
        missing_horizons=missing,
        reason=None if status == "VERIFIED" else "insufficient_markout_horizons",
    )


@dataclass(frozen=True)
class ProspectiveOOSCohort:
    schema_version: str
    cohort_id: str
    created_at: int
    selection_cutoff: int
    algorithm_source_sha: str
    algorithm_input_hash: str
    treatment_wallets: tuple[str, ...]
    control_definition: str
    feature_contract_hash: str
    membership_hash: str
    cohort_hash: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["treatment_wallets"] = list(self.treatment_wallets)
        return payload


def build_prospective_oos_cohort(
    *,
    cohort_id: str,
    created_at: int,
    selection_cutoff: int,
    algorithm_source_sha: str,
    algorithm_input_hash: str,
    treatment_wallets: set[str],
    control_definition: str,
    feature_contract: Any,
) -> ProspectiveOOSCohort:
    if not cohort_id.strip():
        raise ValueError("oos_cohort_id_required")
    if created_at <= 0 or selection_cutoff <= 0 or created_at >= selection_cutoff:
        raise ValueError("oos_cohort_must_be_created_before_cutoff")
    if len(algorithm_source_sha) != 40:
        raise ValueError("oos_algorithm_source_sha_invalid")
    if len(algorithm_input_hash) != 64:
        raise ValueError("oos_algorithm_input_hash_invalid")
    wallets = tuple(
        sorted(
            {
                wallet.strip().lower()
                for wallet in treatment_wallets
                if wallet.strip()
            }
        )
    )
    if not wallets:
        raise ValueError("oos_treatment_membership_required")
    membership_hash = sha256_json(wallets)
    feature_contract_hash = sha256_json(feature_contract)
    material = {
        "schema_version": PROSPECTIVE_OOS_COHORT_SCHEMA,
        "cohort_id": cohort_id,
        "created_at": created_at,
        "selection_cutoff": selection_cutoff,
        "algorithm_source_sha": algorithm_source_sha,
        "algorithm_input_hash": algorithm_input_hash,
        "treatment_wallets": wallets,
        "control_definition": control_definition,
        "feature_contract_hash": feature_contract_hash,
        "membership_hash": membership_hash,
    }
    return ProspectiveOOSCohort(
        **material,
        cohort_hash=sha256_json(material),
    )


def prospective_oos_cohort_hash_valid(cohort: ProspectiveOOSCohort) -> bool:
    payload = cohort.to_dict()
    claimed = str(payload.pop("cohort_hash", ""))
    payload["treatment_wallets"] = tuple(payload["treatment_wallets"])
    return len(claimed) == 64 and claimed == sha256_json(payload)


def persist_prospective_oos_cohort(path: Path, cohort: ProspectiveOOSCohort) -> None:
    if not prospective_oos_cohort_hash_valid(cohort):
        raise ValueError("oos_cohort_hash_invalid")
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    descriptor = os.open(path, flags, 0o600)
    try:
        payload = json.dumps(cohort.to_dict(), indent=2, sort_keys=True) + "\n"
        os.write(descriptor, payload.encode("utf-8"))
    finally:
        os.close(descriptor)


@dataclass(frozen=True)
class OOSControlResult:
    schema_version: str
    cohort_id: str
    membership_hash: str
    cutoff_timestamp: int
    in_sample_count: int
    out_of_sample_count: int
    control_count: int
    treatment_oos_mean: float | None
    control_oos_mean: float | None
    treatment_vs_control_delta: float | None
    treatment_oos_percentile: float | None
    status: str
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_oos_control(
    observations: list[dict[str, Any]],
    *,
    cohort: ProspectiveOOSCohort,
    value_key: str = "realized_pnl",
) -> OOSControlResult:
    if not prospective_oos_cohort_hash_valid(cohort):
        raise ValueError("oos_cohort_hash_invalid")
    cutoff = cohort.selection_cutoff
    if cohort.created_at >= cutoff:
        raise ValueError("oos_cohort_not_prospective")

    in_sample = [
        row for row in observations if int(row.get("timestamp") or 0) < cutoff
    ]
    oos = [
        row for row in observations if int(row.get("timestamp") or 0) >= cutoff
    ]
    if oos and cohort.created_at >= min(
        int(row.get("timestamp") or 0) for row in oos
    ):
        raise ValueError("oos_membership_not_fixed_before_evaluation")

    treatment_wallets = set(cohort.treatment_wallets)
    treatment = [
        row
        for row in oos
        if str(row.get("wallet") or "").lower() in treatment_wallets
    ]
    control = [
        row
        for row in oos
        if str(row.get("wallet") or "").lower() not in treatment_wallets
    ]
    treatment_values = [
        float(row[value_key])
        for row in treatment
        if _finite_number(row.get(value_key))
    ]
    control_values = [
        float(row[value_key])
        for row in control
        if _finite_number(row.get(value_key))
    ]
    treatment_mean = mean(treatment_values) if treatment_values else None
    control_mean = mean(control_values) if control_values else None
    delta = None
    if treatment_mean is not None and control_mean is not None:
        delta = treatment_mean - control_mean
    percentile = None
    if treatment_mean is not None and control_values:
        percentile = round(
            sum(value <= treatment_mean for value in control_values) / len(control_values),
            6,
        )
    verified = bool(treatment_values and control_values)
    return OOSControlResult(
        schema_version=OOS_CONTROL_SCHEMA,
        cohort_id=cohort.cohort_id,
        membership_hash=cohort.membership_hash,
        cutoff_timestamp=cutoff,
        in_sample_count=len(in_sample),
        out_of_sample_count=len(oos),
        control_count=len(control_values),
        treatment_oos_mean=treatment_mean,
        control_oos_mean=control_mean,
        treatment_vs_control_delta=delta,
        treatment_oos_percentile=percentile,
        status="VERIFIED" if verified else "INSUFFICIENT_DATA",
        reason=None if verified else "treatment_and_control_oos_required",
    )


def _finite_number(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False
