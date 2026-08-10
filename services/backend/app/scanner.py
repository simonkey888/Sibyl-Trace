import json
import time
from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.config import Settings
from app.domain import (
    QUALITY_SCORE_ALPHA_CLAIM,
    QUALITY_SCORE_CALIBRATED_PROBABILITY,
    QUALITY_SCORE_EXPECTED_RETURN_CLAIM,
    QUALITY_SCORE_GLOBAL_FORMULA,
    QUALITY_SCORE_HISTORY_BASIS,
    QUALITY_SCORE_KIND,
)
from app.models import Wallet, WalletScoreProfile, WalletSnapshot
from app.polymarket import PolymarketClient
from app.repository import audit, set_state
from app.scoring import score_matrix
from app.source_strategy import (
    UNAVAILABLE,
    SourceStrategyPolicy,
    canonical_hash,
    classify_source_strategy,
    fetch_public_activity_events,
    wallet_hash,
)

SOURCE_STRATEGY_PROFILES_STATE = "paper_v5_source_strategy_profiles"
SOURCE_STRATEGY_CUTOFF_STATE = "paper_v5_source_strategy_cutoff_at"


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _unavailable_profile(
    wallet: str,
    *,
    cutoff_at: int,
    policy: SourceStrategyPolicy,
    error_type: str,
) -> dict:
    material = {
        "wallet_hash": wallet_hash(wallet),
        "classification": UNAVAILABLE,
        "rejection_reason": "source_strategy_unavailable",
        "cutoff_at": cutoff_at,
        "event_count": 0,
        "invalid_timestamp_event_count": 0,
        "trade_count": 0,
        "attributable_trade_count": 0,
        "unattributable_trade_count": 0,
        "maker_rebate_count": 0,
        "taker_rebate_count": 0,
        "split_count": 0,
        "merge_count": 0,
        "conversion_count": 0,
        "paired_condition_count": 0,
        "paired_trade_count": 0,
        "paired_trade_fraction": 0.0,
        "activity_sample_hash": canonical_hash([]),
        "policy": {
            "min_trade_count": policy.min_trade_count,
            "min_paired_conditions": policy.min_paired_conditions,
            "max_paired_trade_fraction": policy.max_paired_trade_fraction,
        },
        "error_type": error_type,
    }
    return {
        **material,
        "directional": False,
        "evidence_hash": canonical_hash(material),
    }


def scan_wallets(
    db: Session,
    client: PolymarketClient,
    settings: Settings,
    *,
    prospective: bool = False,
    source_strategy_gate: bool = False,
) -> list[Wallet]:
    if source_strategy_gate and not prospective:
        raise ValueError("source strategy truth gate requires prospective selection")

    candidates: dict[str, dict] = {}
    for period in ("WEEK", "MONTH", "ALL"):
        for item in client.leaderboard(period, settings.candidate_limit):
            address = str(item.get("proxyWallet") or "").lower()
            if len(address) != 42:
                continue
            candidate = candidates.setdefault(
                address,
                {"pnl": 0.0, "vol": 0.0, "username": None},
            )
            candidate["pnl"] = max(
                float(candidate["pnl"]),
                float(item.get("pnl") or 0.0),
            )
            candidate["vol"] = max(
                float(candidate["vol"]),
                float(item.get("vol") or 0.0),
            )
            candidate["username"] = candidate["username"] or item.get("userName")

    db.execute(update(Wallet).values(selected=False))
    wallets: list[Wallet] = []
    for address, candidate in candidates.items():
        try:
            closed = client.closed_positions(address)
        except Exception as exc:
            audit(
                db,
                "wallet_score_failed",
                f"Could not score {address}: {exc}",
                severity="WARN",
                wallet=address,
            )
            continue

        matrix = score_matrix(
            db,
            address,
            closed,
            volume=float(candidate["vol"]),
        )
        metrics = matrix.long_metrics
        wallet = db.get(Wallet, address) or Wallet(address=address)
        wallet.username = candidate["username"]
        wallet.score = matrix.global_score
        wallet.win_rate = metrics.win_rate
        wallet.profit_factor = metrics.profit_factor
        wallet.realized_pnl = metrics.realized_pnl
        wallet.volume = metrics.volume
        wallet.closed_count = metrics.closed_count
        wallet.concentration = metrics.concentration
        wallet.rejection_reason = matrix.rejection_reason
        wallet.selected = False
        wallet.updated_at = datetime.now(UTC)
        db.add(wallet)

        profile = db.get(WalletScoreProfile, address) or WalletScoreProfile(
            wallet_address=address
        )
        profile.short_score = matrix.short_score
        profile.long_score = matrix.long_score
        profile.global_score = matrix.global_score
        profile.execution_edge_score = matrix.execution_edge_score
        profile.execution_edge_sample_size = matrix.execution_edge_sample_size
        profile.average_execution_edge = matrix.average_execution_edge
        # These fields are the score-bearing samples, not raw closed-row counts.
        profile.short_sample_size = matrix.short_metrics.decided_count
        profile.long_sample_size = matrix.long_metrics.decided_count
        profile.updated_at = datetime.now(UTC)
        db.add(profile)

        audit(
            db,
            "wallet_quality_scored",
            "Persist reconstructable heuristic source-quality evidence",
            wallet=address,
            score_kind=QUALITY_SCORE_KIND,
            score_global_formula=QUALITY_SCORE_GLOBAL_FORMULA,
            score_history_basis=QUALITY_SCORE_HISTORY_BASIS,
            calibrated_probability=QUALITY_SCORE_CALIBRATED_PROBABILITY,
            expected_return_claim=QUALITY_SCORE_EXPECTED_RETURN_CLAIM,
            alpha_claim=QUALITY_SCORE_ALPHA_CLAIM,
            short_closed_count=matrix.short_metrics.closed_count,
            short_decided_count=matrix.short_metrics.decided_count,
            short_score=matrix.short_score,
            long_closed_count=matrix.long_metrics.closed_count,
            long_decided_count=matrix.long_metrics.decided_count,
            long_score=matrix.long_score,
            global_score=matrix.global_score,
            win_rate_denominator="wins_plus_losses",
            rejection_reason=matrix.rejection_reason,
        )

        db.add(
            WalletSnapshot(
                wallet_address=address,
                score=matrix.global_score,
                win_rate=metrics.win_rate,
                profit_factor=metrics.profit_factor,
                realized_pnl=metrics.realized_pnl,
                concentration=metrics.concentration,
                closed_count=metrics.closed_count,
            )
        )
        wallets.append(wallet)

    ranked = sorted(
        (wallet for wallet in wallets if wallet.rejection_reason is None),
        key=lambda wallet: wallet.score,
        reverse=True,
    )

    strategy_profiles: list[dict] = []
    if source_strategy_gate:
        cutoff_at = int(time.time())
        policy = SourceStrategyPolicy(
            min_trade_count=settings.source_strategy_min_trade_count,
            min_paired_conditions=settings.source_strategy_min_paired_conditions,
            max_paired_trade_fraction=settings.source_strategy_max_paired_trade_fraction,
        )
        eligible: list[Wallet] = []
        for wallet in ranked:
            if len(eligible) >= settings.tracked_wallet_limit:
                break
            try:
                events = fetch_public_activity_events(
                    client,
                    wallet.address,
                    cutoff_at=cutoff_at,
                    limit=settings.source_strategy_activity_limit,
                )
                strategy = classify_source_strategy(
                    wallet.address,
                    events,
                    cutoff_at=cutoff_at,
                    policy=policy,
                )
                strategy_payload = strategy.to_dict()
            except Exception as exc:
                strategy_payload = _unavailable_profile(
                    wallet.address,
                    cutoff_at=cutoff_at,
                    policy=policy,
                    error_type=type(exc).__name__,
                )
            strategy_profiles.append(strategy_payload)
            audit(
                db,
                "wallet_source_strategy_classified",
                "Classified public source behavior before prospective selection",
                severity="INFO" if strategy_payload["directional"] else "WARN",
                **strategy_payload,
            )
            if strategy_payload["directional"]:
                eligible.append(wallet)
            else:
                wallet.rejection_reason = strategy_payload["rejection_reason"]
        set_state(
            db,
            SOURCE_STRATEGY_PROFILES_STATE,
            json.dumps(strategy_profiles, sort_keys=True),
        )
        set_state(db, SOURCE_STRATEGY_CUTOFF_STATE, str(cutoff_at))
    else:
        eligible = ranked[: settings.tracked_wallet_limit]

    selection_effective_at = int(time.time()) + 1 if prospective else None
    for wallet in eligible:
        wallet.selected = True
        if selection_effective_at is not None:
            wallet.last_activity_at = max(
                int(wallet.last_activity_at or 0), selection_effective_at
            )

    set_state(db, "last_scan_at", now_iso())
    if selection_effective_at is not None:
        set_state(db, "paper_v5_selection_effective_at", str(selection_effective_at))
    audit(
        db,
        "wallet_scan_completed",
        f"Scored {len(wallets)} wallets; selected {len(eligible)}",
        candidates=len(wallets),
        selected=[wallet.address for wallet in eligible],
        score_contract="HEURISTIC GLOBAL=60% SHORT + 40% LONG; HISTORY=DECIDED_OUTCOMES; EDGE=execution copyability only",
        score_calibrated_probability=False,
        score_expected_return_claim=False,
        score_alpha_claim=False,
        selection_mode="PROSPECTIVE_ONLY" if prospective else "LEGACY_SCAN_THEN_INGEST",
        selection_effective_at=selection_effective_at,
        source_strategy_gate=source_strategy_gate,
        source_strategy_profiles=len(strategy_profiles),
    )
    db.commit()
    return eligible