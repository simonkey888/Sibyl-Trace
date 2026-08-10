from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Any

from app.source_strategy import (
    DIRECTIONAL_CANDIDATE,
    INSUFFICIENT_EVIDENCE,
    NON_DIRECTIONAL_FULL_SET,
    NON_DIRECTIONAL_MAKER,
    NON_DIRECTIONAL_TWO_SIDED,
    UNAVAILABLE,
    canonical_hash,
    profile_hash_valid,
    wallet_hash,
)

FORENSICS_SCHEMA_VERSION = 1
DIRECTIONAL_COPY_RESEARCH = "DIRECTIONAL_COPY_RESEARCH"
STRUCTURAL_MAKER_RESEARCH = "STRUCTURAL_MAKER_RESEARCH"
STRUCTURAL_FULL_SET_RESEARCH = "STRUCTURAL_FULL_SET_RESEARCH"
STRUCTURAL_TWO_SIDED_RESEARCH = "STRUCTURAL_TWO_SIDED_RESEARCH"
INSUFFICIENT_EVIDENCE_RESEARCH = "INSUFFICIENT_EVIDENCE_RESEARCH"
UNAVAILABLE_RESEARCH = "UNAVAILABLE_RESEARCH"
MAKER_STYLE_VETO_MIN_SAMPLE = 20
MAKER_STYLE_VETO_RATIO = 0.80


@dataclass(frozen=True)
class WalletForensics:
    payload: dict[str, Any]

    @property
    def evidence_hash(self) -> str:
        return str(self.payload["evidence_hash"])

    @property
    def lane(self) -> str:
        return str(self.payload["lane"])

    @property
    def copyable_directional(self) -> bool:
        return bool(self.payload["copyable_directional"])

    @property
    def research_only(self) -> bool:
        return bool(self.payload["research_only"])

    def to_dict(self) -> dict[str, Any]:
        return dict(self.payload)


def _as_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _event_timestamp(event: dict[str, Any]) -> int:
    try:
        return int(event.get("timestamp") or 0)
    except (TypeError, ValueError):
        return 0


def _lane_for_classification(classification: str) -> str:
    if classification == DIRECTIONAL_CANDIDATE:
        return DIRECTIONAL_COPY_RESEARCH
    if classification == NON_DIRECTIONAL_MAKER:
        return STRUCTURAL_MAKER_RESEARCH
    if classification == NON_DIRECTIONAL_FULL_SET:
        return STRUCTURAL_FULL_SET_RESEARCH
    if classification == NON_DIRECTIONAL_TWO_SIDED:
        return STRUCTURAL_TWO_SIDED_RESEARCH
    if classification == UNAVAILABLE:
        return UNAVAILABLE_RESEARCH
    return INSUFFICIENT_EVIDENCE_RESEARCH


def _sum_usdc(events: list[dict[str, Any]], event_type: str) -> float:
    return round(
        sum(
            _as_float(event.get("usdcSize"))
            for event in events
            if str(event.get("type") or "").upper() == event_type
        ),
        6,
    )


def _trade_identity(row: dict[str, Any]) -> tuple[str, str, str, str, str, int]:
    return (
        str(row.get("transactionHash") or ""),
        str(row.get("asset") or ""),
        str(row.get("side") or "").upper(),
        str(row.get("price") or ""),
        str(row.get("size") or ""),
        _event_timestamp(row),
    )


def compute_execution_mix(
    all_trades: list[dict[str, Any]],
    taker_trades: list[dict[str, Any]],
    *,
    row_limit: int,
) -> dict[str, Any]:
    all_rows = [
        row for row in all_trades if isinstance(row, dict) and _event_timestamp(row) > 0
    ]
    taker_rows = [
        row for row in taker_trades if isinstance(row, dict) and _event_timestamp(row) > 0
    ]
    if not all_rows:
        return {
            "proven": False,
            "reason": "NO_PUBLIC_TRADE_ROWS",
            "scope": "RECENT_OVERLAP_SAMPLE",
            "sample_total": 0,
            "maker": 0,
            "taker": 0,
            "maker_ratio": None,
            "taker_ratio": None,
            "orphan_taker_fills": 0,
            "truncated": False,
            "window_from": None,
            "window_to": None,
        }

    all_min = min(_event_timestamp(row) for row in all_rows)
    all_max = max(_event_timestamp(row) for row in all_rows)
    taker_truncated = len(taker_rows) >= row_limit
    if taker_rows and taker_truncated:
        taker_min = min(_event_timestamp(row) for row in taker_rows)
        window_from = max(all_min, taker_min)
    else:
        # A non-truncated taker page is exhaustive for this wallet, so the absence
        # of taker rows elsewhere inside the all-trades page is itself evidence.
        window_from = all_min
    window_to = all_max

    all_window = [
        row
        for row in all_rows
        if window_from <= _event_timestamp(row) <= window_to
    ]
    taker_window = [
        row
        for row in taker_rows
        if window_from <= _event_timestamp(row) <= window_to
    ]
    all_counter = Counter(_trade_identity(row) for row in all_window)
    taker_counter = Counter(_trade_identity(row) for row in taker_window)
    orphan_taker_fills = sum(
        max(count - all_counter.get(identity, 0), 0)
        for identity, count in taker_counter.items()
    )
    total = len(all_window)
    taker = len(taker_window)
    if orphan_taker_fills or taker > total or total == 0:
        return {
            "proven": False,
            "reason": "TAKER_ROWS_NOT_SUBSET_OF_ALL_TRADES",
            "scope": "RECENT_OVERLAP_SAMPLE",
            "sample_total": total,
            "maker": None,
            "taker": taker,
            "maker_ratio": None,
            "taker_ratio": None,
            "orphan_taker_fills": orphan_taker_fills,
            "truncated": len(all_rows) >= row_limit or len(taker_rows) >= row_limit,
            "window_from": window_from,
            "window_to": window_to,
        }

    maker = total - taker
    return {
        "proven": True,
        "reason": None,
        "scope": "RECENT_OVERLAP_SAMPLE",
        "sample_total": total,
        "maker": maker,
        "taker": taker,
        "maker_ratio": round(maker / total, 6),
        "taker_ratio": round(taker / total, 6),
        "orphan_taker_fills": 0,
        "truncated": len(all_rows) >= row_limit or len(taker_rows) >= row_limit,
        "window_from": window_from,
        "window_to": window_to,
    }


def fetch_public_execution_mix(
    client: Any, wallet: str, *, limit: int = 500
) -> dict[str, Any]:
    row_limit = min(max(int(limit), 1), 500)
    base = {"user": wallet, "limit": row_limit, "offset": 0}
    all_data = client._get(
        f"{client.settings.data_api_base}/trades",
        {**base, "takerOnly": False},
    )
    taker_data = client._get(
        f"{client.settings.data_api_base}/trades",
        {**base, "takerOnly": True},
    )
    all_rows = all_data if isinstance(all_data, list) else []
    taker_rows = taker_data if isinstance(taker_data, list) else []
    return compute_execution_mix(all_rows, taker_rows, row_limit=row_limit)


def compute_wallet_forensics(
    wallet: str,
    events: list[dict[str, Any]],
    closed_positions: list[dict[str, Any]],
    *,
    cutoff_at: int,
    source_strategy_profile: dict[str, Any],
    scan_candidate_count: int | None = None,
    scan_realized_pnl_percentile: float | None = None,
    execution_mix: dict[str, Any] | None = None,
    execution_mix_error: str | None = None,
) -> WalletForensics:
    if not profile_hash_valid(source_strategy_profile):
        raise ValueError("source_strategy_profile_hash_invalid")

    clean_events = [
        event
        for event in events
        if isinstance(event, dict) and 0 < _event_timestamp(event) <= int(cutoff_at)
    ]
    trades = [
        event
        for event in clean_events
        if str(event.get("type") or "").upper() == "TRADE"
    ]
    buy_trades = [event for event in trades if str(event.get("side") or "").upper() == "BUY"]
    sell_trades = [
        event for event in trades if str(event.get("side") or "").upper() == "SELL"
    ]

    condition_ids = {
        str(event.get("conditionId") or "").strip()
        for event in trades
        if str(event.get("conditionId") or "").strip()
    }
    sides_by_asset: dict[str, set[str]] = {}
    for event in trades:
        asset = str(event.get("asset") or "").strip()
        side = str(event.get("side") or "").upper()
        if not asset or side not in {"BUY", "SELL"}:
            continue
        sides_by_asset.setdefault(asset, set()).add(side)

    round_trip_assets = sum(1 for sides in sides_by_asset.values() if sides == {"BUY", "SELL"})
    buy_only_assets = sum(1 for sides in sides_by_asset.values() if sides == {"BUY"})
    sell_only_assets = sum(1 for sides in sides_by_asset.values() if sides == {"SELL"})

    event_counts: dict[str, int] = {}
    for event in clean_events:
        event_type = str(event.get("type") or "").upper()
        event_counts[event_type] = event_counts.get(event_type, 0) + 1

    realized_values = [
        _as_float(row.get("realizedPnl"))
        for row in closed_positions
        if isinstance(row, dict)
    ]
    reported_realized_pnl = round(sum(realized_values), 6)
    decided_closed_positions = sum(1 for value in realized_values if abs(value) > 1e-12)

    source_classification = str(source_strategy_profile.get("classification") or UNAVAILABLE)
    lane = _lane_for_classification(source_classification)
    mix = execution_mix if isinstance(execution_mix, dict) else {}
    mix_proven = bool(mix.get("proven"))
    maker_ratio = mix.get("maker_ratio") if mix_proven else None
    maker_style_veto = bool(
        source_classification == DIRECTIONAL_CANDIDATE
        and mix_proven
        and int(mix.get("sample_total") or 0) >= MAKER_STYLE_VETO_MIN_SAMPLE
        and maker_ratio is not None
        and float(maker_ratio) >= MAKER_STYLE_VETO_RATIO
    )
    if maker_style_veto:
        lane = STRUCTURAL_MAKER_RESEARCH
    copyable_directional = (
        source_classification == DIRECTIONAL_CANDIDATE and not maker_style_veto
    )

    material: dict[str, Any] = {
        "schema_version": FORENSICS_SCHEMA_VERSION,
        "wallet_hash": wallet_hash(wallet),
        "cutoff_at": int(cutoff_at),
        "lane": lane,
        "copyable_directional": copyable_directional,
        "research_only": not copyable_directional,
        "execution_gate": False,
        "selection_veto": maker_style_veto,
        "selection_veto_reason": (
            "execution_mix_structural_maker" if maker_style_veto else None
        ),
        "maker_style_veto_min_sample": MAKER_STYLE_VETO_MIN_SAMPLE,
        "maker_style_veto_ratio": MAKER_STYLE_VETO_RATIO,
        "source_strategy_classification": source_classification,
        "source_strategy_evidence_hash": source_strategy_profile.get("evidence_hash"),
        "activity_event_count": len(clean_events),
        "trade_count": len(trades),
        "buy_trade_count": len(buy_trades),
        "sell_trade_count": len(sell_trades),
        "distinct_condition_count": len(condition_ids),
        "distinct_asset_count": len(sides_by_asset),
        "round_trip_asset_count": round_trip_assets,
        "buy_only_asset_count": buy_only_assets,
        "sell_only_asset_count": sell_only_assets,
        "paired_condition_count": int(source_strategy_profile.get("paired_condition_count") or 0),
        "paired_trade_count": int(source_strategy_profile.get("paired_trade_count") or 0),
        "paired_trade_fraction": float(source_strategy_profile.get("paired_trade_fraction") or 0.0),
        "split_count": event_counts.get("SPLIT", 0),
        "merge_count": event_counts.get("MERGE", 0),
        "redeem_count": event_counts.get("REDEEM", 0),
        "conversion_count": event_counts.get("CONVERSION", 0),
        "maker_rebate_count": event_counts.get("MAKER_REBATE", 0),
        "reward_count": event_counts.get("REWARD", 0),
        "referral_reward_count": event_counts.get("REFERRAL_REWARD", 0),
        "split_usdc_observed": _sum_usdc(clean_events, "SPLIT"),
        "merge_usdc_observed": _sum_usdc(clean_events, "MERGE"),
        "redeem_usdc_observed": _sum_usdc(clean_events, "REDEEM"),
        "maker_rebate_usdc_observed": _sum_usdc(clean_events, "MAKER_REBATE"),
        "reward_usdc_observed": _sum_usdc(clean_events, "REWARD"),
        "referral_reward_usdc_observed": _sum_usdc(clean_events, "REFERRAL_REWARD"),
        "reported_closed_position_count": len(realized_values),
        "reported_decided_closed_position_count": decided_closed_positions,
        "reported_realized_pnl": reported_realized_pnl,
        "reported_pnl_source": "DATA_API_CLOSED_POSITIONS_REPORTED",
        "cashflow_pnl_reconstructed": False,
        "maker_ratio": maker_ratio,
        "taker_ratio": mix.get("taker_ratio") if mix_proven else None,
        "maker_taker_ratio_proven": mix_proven,
        "maker_taker_scope": mix.get("scope") if mix else None,
        "maker_taker_sample_total": int(mix.get("sample_total") or 0),
        "maker_taker_maker_count": mix.get("maker"),
        "maker_taker_taker_count": mix.get("taker"),
        "maker_taker_window_from": mix.get("window_from"),
        "maker_taker_window_to": mix.get("window_to"),
        "maker_taker_truncated": bool(mix.get("truncated", False)),
        "maker_taker_orphan_taker_fills": int(mix.get("orphan_taker_fills") or 0),
        "maker_taker_reason": (
            mix.get("reason")
            if mix
            else execution_mix_error or "PUBLIC_EXECUTION_MIX_NOT_FETCHED"
        ),
        "markout_10s": None,
        "markout_30s": None,
        "markout_60s": None,
        "markout_proven": False,
        "scan_candidate_count": scan_candidate_count,
        "scan_realized_pnl_percentile": scan_realized_pnl_percentile,
        "scan_percentile_basis": (
            "CURRENT_SCANNER_CANDIDATES_REPORTED_REALIZED_PNL"
            if scan_realized_pnl_percentile is not None
            else None
        ),
        "control_group_percentile": None,
        "control_group_percentile_proven": False,
        "hold_to_resolution_ratio": None,
        "scratch_exit_ratio": None,
        "lifecycle_ratio_proven": False,
        "claims": {
            "profitability_proven_by_forensics_v1": False,
            "alpha_proven": False,
            "expected_return_proven": False,
            "maker_taker_ratio_proven": mix_proven,
            "markout_proven": False,
            "control_group_percentile_proven": False,
        },
    }
    evidence_hash = canonical_hash(material)
    return WalletForensics({**material, "evidence_hash": evidence_hash})


def unavailable_wallet_forensics(
    wallet: str,
    *,
    cutoff_at: int,
    source_strategy_profile: dict[str, Any],
    error_type: str,
) -> WalletForensics:
    material = {
        "schema_version": FORENSICS_SCHEMA_VERSION,
        "wallet_hash": wallet_hash(wallet),
        "cutoff_at": int(cutoff_at),
        "lane": UNAVAILABLE_RESEARCH,
        "copyable_directional": False,
        "research_only": True,
        "execution_gate": False,
        "source_strategy_classification": str(
            source_strategy_profile.get("classification") or UNAVAILABLE
        ),
        "source_strategy_evidence_hash": source_strategy_profile.get("evidence_hash"),
        "error_type": error_type,
        "claims": {
            "profitability_proven_by_forensics_v1": False,
            "alpha_proven": False,
            "expected_return_proven": False,
            "maker_taker_ratio_proven": False,
            "markout_proven": False,
            "control_group_percentile_proven": False,
        },
    }
    evidence_hash = canonical_hash(material)
    return WalletForensics({**material, "evidence_hash": evidence_hash})
