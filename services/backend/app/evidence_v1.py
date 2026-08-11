from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from statistics import mean
from typing import Any


HISTORY_COMPLETENESS_SCHEMA = "HISTORY_COMPLETENESS_V1"
SCORE_PROVENANCE_SCHEMA = "SCORE_PROVENANCE_V1"
SCORE_DETERMINISM_SCHEMA = "SCORE_DETERMINISM_V1"
EXTERNAL_FORENSICS_SCHEMA = "EXTERNAL_MARKET_FORENSICS_V1"
OOS_CONTROL_SCHEMA = "OOS_CONTROL_GROUP_V1"


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


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
        status, scope, reason = "INCOMPLETE", "BOUNDED_WINDOW", "history_limit_reached"
    else:
        status, scope, reason = "COMPLETE", "FULL_AVAILABLE_HISTORY", None
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
        source_hash=sha256_json(source_payload if source_payload is not None else rows),
        reason=reason,
    )


def canonicalize_closed_positions(rows: list[dict]) -> list[dict]:
    """Normalize only ordering; never drop or coerce evidence rows."""
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


def score_input_hash(
    *,
    short_rows: list[dict],
    long_rows: list[dict],
    volume: float,
    algorithm_version: str,
) -> str:
    return sha256_json(
        {
            "algorithm_version": algorithm_version,
            "short_rows": short_rows,
            "long_rows": long_rows,
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
    return ScoreProvenance(
        schema_version=SCORE_PROVENANCE_SCHEMA,
        algorithm_version=algorithm_version,
        code_sha=code_sha,
        calculated_at=datetime.now(UTC).isoformat(),
        source_endpoint=source_endpoint,
        history_status=history.status,
        history_scope=history.scope,
        source_row_count=len(long_rows),
        decided_row_count=decided_row_count,
        source_hash=history.source_hash,
        input_hash=score_input_hash(
            short_rows=short_rows,
            long_rows=long_rows,
            volume=volume,
            algorithm_version=algorithm_version,
        ),
        short_source_hash=sha256_json(short_rows),
        long_source_hash=sha256_json(long_rows),
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


def infer_market_bias(market_title: str, outcome: str = "") -> int | None:
    text = f"{market_title} {outcome}".casefold()
    bullish = ("up", "above", "higher", "bull", "yes")
    bearish = ("down", "below", "lower", "bear", "no")
    if any(token in text for token in bullish):
        return 1
    if any(token in text for token in bearish):
        return -1
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
    markout: dict[str, float]
    pre_move: float | None
    post_move: float | None
    lead_lag: str
    bias: int | None
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def classify_lead_lag(pre_move: float | None, post_move: float | None, bias: int | None) -> str:
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
    """Read-only public market feed. No credentials and no trading capability."""

    base_url = "https://api.binance.com/api/v3/klines"

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
        return ExternalMarkout(
            EXTERNAL_FORENSICS_SCHEMA, "UNKNOWN", "BINANCE_PUBLIC", symbol,
            source_timestamp_ms, entry_price, {}, {}, None, None, "UNKNOWN", bias,
            "unmapped_market_or_bias",
        )
    if entry_price is None or not math.isfinite(entry_price) or entry_price <= 0:
        return ExternalMarkout(
            EXTERNAL_FORENSICS_SCHEMA, "INVALID_DATA", "BINANCE_PUBLIC", symbol,
            source_timestamp_ms, entry_price, {}, {}, None, None, "UNKNOWN", bias,
            "invalid_entry_price",
        )
    start = max(source_timestamp_ms - 60_000, 0)
    end = source_timestamp_ms + 300_000
    rows = feed.klines(symbol, start, end)
    if not rows:
        return ExternalMarkout(
            EXTERNAL_FORENSICS_SCHEMA, "INCOMPLETE", "BINANCE_PUBLIC", symbol,
            source_timestamp_ms, entry_price, {}, {}, None, None, "UNKNOWN", bias,
            "empty_external_window",
        )

    points: dict[str, float] = {}
    for row in rows:
        if not isinstance(row, list) or len(row) < 5:
            continue
        try:
            ts = int(row[0])
            close = float(row[4])
        except (TypeError, ValueError):
            continue
        if not math.isfinite(close) or close <= 0:
            continue
        delta = ts - source_timestamp_ms
        for label, target in (("pre_60s", -60_000), ("t0", 0), ("10s", 10_000), ("30s", 30_000), ("60s", 60_000), ("300s", 300_000)):
            if label not in points and abs(delta - target) <= 1_000:
                points[label] = close
    if "t0" not in points:
        return ExternalMarkout(
            EXTERNAL_FORENSICS_SCHEMA, "INCOMPLETE", "BINANCE_PUBLIC", symbol,
            source_timestamp_ms, entry_price, points, {}, None, None, "UNKNOWN", bias,
            "missing_external_t0",
        )

    t0 = points["t0"]
    markout = {
        label: bias * (value - t0) / t0
        for label, value in points.items()
        if label in {"10s", "30s", "60s", "300s"}
    }
    pre_move = None
    if "pre_60s" in points:
        pre_move = (t0 - points["pre_60s"]) / points["pre_60s"]
    post_move = markout.get("60s")
    status = "VERIFIED" if len(markout) >= 3 else "INCOMPLETE"
    return ExternalMarkout(
        EXTERNAL_FORENSICS_SCHEMA, status, "BINANCE_PUBLIC", symbol,
        source_timestamp_ms, entry_price, points, markout, pre_move, post_move,
        classify_lead_lag(pre_move, post_move, bias), bias,
        None if status == "VERIFIED" else "insufficient_markout_horizons",
    )


@dataclass(frozen=True)
class OOSControlResult:
    schema_version: str
    cutoff_timestamp: int
    in_sample_count: int
    out_of_sample_count: int
    control_count: int
    treatment_oos_mean: float | None
    control_oos_mean: float | None
    treatment_vs_control_delta: float | None
    treatment_oos_percentile: float | None
    status: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def evaluate_oos_control(
    observations: list[dict[str, Any]],
    *,
    cutoff_timestamp: int,
    treatment_wallets: set[str],
    value_key: str = "realized_pnl",
) -> OOSControlResult:
    in_sample = [row for row in observations if int(row.get("timestamp") or 0) < cutoff_timestamp]
    oos = [row for row in observations if int(row.get("timestamp") or 0) >= cutoff_timestamp]
    treatment = [row for row in oos if str(row.get("wallet") or "") in treatment_wallets]
    control = [row for row in oos if str(row.get("wallet") or "") not in treatment_wallets]
    treatment_values = [float(row[value_key]) for row in treatment if _finite_number(row.get(value_key))]
    control_values = [float(row[value_key]) for row in control if _finite_number(row.get(value_key))]
    treatment_mean = mean(treatment_values) if treatment_values else None
    control_mean = mean(control_values) if control_values else None
    delta = treatment_mean - control_mean if treatment_mean is not None and control_mean is not None else None
    percentile = None
    if treatment_mean is not None and control_values:
        percentile = round(sum(value <= treatment_mean for value in control_values) / len(control_values), 6)
    return OOSControlResult(
        OOS_CONTROL_SCHEMA,
        cutoff_timestamp,
        len(in_sample),
        len(oos),
        len(control_values),
        treatment_mean,
        control_mean,
        delta,
        percentile,
        "VERIFIED" if treatment_values and control_values else "INSUFFICIENT_DATA",
    )


def _finite_number(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False
