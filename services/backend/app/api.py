import json
import secrets
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.domain import (
    QUALITY_SCORE_ALPHA_CLAIM,
    QUALITY_SCORE_CALIBRATED_PROBABILITY,
    QUALITY_SCORE_EXPECTED_RETURN_CLAIM,
    QUALITY_SCORE_GLOBAL_FORMULA,
    QUALITY_SCORE_KIND,
)
from app.models import (
    AIAnalysis,
    AuditEvent,
    PaperOrder,
    PortfolioSnapshot,
    Signal,
    Wallet,
    WalletScoreProfile,
)
from app.repository import audit, current_portfolio, get_state, set_state

router = APIRouter(prefix="/api/v1")

SettingsDep = Annotated[Settings, Depends(get_settings)]
DatabaseDep = Annotated[Session, Depends(get_db)]
GatewaySecretHeader = Annotated[str | None, Header()]
AdminTokenHeader = Annotated[str | None, Header()]
SelectedQuery = Annotated[bool | None, Query()]


def quality_score_contract() -> dict:
    return {
        "kind": QUALITY_SCORE_KIND,
        "global_formula": QUALITY_SCORE_GLOBAL_FORMULA,
        "calibrated_probability": QUALITY_SCORE_CALIBRATED_PROBABILITY,
        "expected_return_claim": QUALITY_SCORE_EXPECTED_RETURN_CLAIM,
        "alpha_claim": QUALITY_SCORE_ALPHA_CLAIM,
        "win_rate_denominator": "wins_plus_losses",
        "break_even_counts_toward_history": True,
        "edge": "execution copyability evidence, not outcome alpha",
    }


def verify_gateway(
    settings: SettingsDep,
    x_sibyl_gateway_secret: GatewaySecretHeader = None,
) -> None:
    if settings.app_env == "development":
        return
    if not x_sibyl_gateway_secret or not secrets.compare_digest(
        x_sibyl_gateway_secret, settings.gateway_shared_secret
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid gateway")


def verify_admin(
    settings: SettingsDep,
    x_sibyl_admin_token: AdminTokenHeader = None,
) -> None:
    if not x_sibyl_admin_token or not secrets.compare_digest(
        x_sibyl_admin_token, settings.admin_token
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin token required")


def score_profiles(db: Session, wallets: list[Wallet]) -> dict[str, WalletScoreProfile]:
    addresses = [wallet.address for wallet in wallets]
    if not addresses:
        return {}
    rows = db.scalars(
        select(WalletScoreProfile).where(WalletScoreProfile.wallet_address.in_(addresses))
    ).all()
    return {row.wallet_address: row for row in rows}


@router.get("/dashboard", dependencies=[Depends(verify_gateway)])
def dashboard(db: DatabaseDep, settings: SettingsDep) -> dict:
    portfolio = current_portfolio(db, settings.initial_bankroll_usd)
    wallet_query = (
        select(Wallet)
        .order_by(desc(Wallet.selected), desc(Wallet.score))
        .limit(20)
    )
    wallet_rows = list(db.scalars(wallet_query))
    profiles = score_profiles(db, wallet_rows)
    signals = list(db.scalars(select(Signal).order_by(desc(Signal.id)).limit(30)))
    orders = list(db.scalars(select(PaperOrder).order_by(desc(PaperOrder.id)).limit(30)))
    history = list(
        db.scalars(select(PortfolioSnapshot).order_by(desc(PortfolioSnapshot.id)).limit(100))
    )[::-1]
    events = list(db.scalars(select(AuditEvent).order_by(desc(AuditEvent.id)).limit(40)))
    analysis = db.scalar(select(AIAnalysis).order_by(desc(AIAnalysis.id)).limit(1))
    return {
        "system": {
            "mode": get_state(db, "mode", settings.trading_mode),
            "paused": get_state(db, "paused", "false") == "true",
            "kill_switch": get_state(db, "kill_switch", "false") == "true",
            "geoblock": get_state(db, "geoblock", "unknown"),
            "last_scan_at": get_state(db, "last_scan_at"),
            "last_watch_at": get_state(db, "last_watch_at"),
            "version": settings.app_version,
            "live_available": False,
            "score_contract": "GLOBAL=60% SHORT + 40% LONG; EDGE=execution copyability",
            "score_semantics": quality_score_contract(),
        },
        "portfolio": portfolio,
        "wallets": [
            serialize_wallet(row, profiles.get(row.address)) for row in wallet_rows
        ],
        "signals": [serialize_signal(row) for row in signals],
        "orders": [serialize_order(row) for row in orders],
        "equity": [
            {
                "time": row.captured_at.isoformat(),
                "equity": row.equity,
                "drawdown": row.drawdown,
            }
            for row in history
        ],
        "ai_analysis": serialize_ai_analysis(analysis) if analysis else None,
        "events": [
            {
                "id": row.id,
                "type": row.event_type,
                "severity": row.severity,
                "message": row.message,
                "payload": json.loads(row.payload_json or "{}"),
                "time": row.created_at.isoformat(),
            }
            for row in events
        ],
    }


@router.get("/wallets", dependencies=[Depends(verify_gateway)])
def wallets(db: DatabaseDep, selected: SelectedQuery = None) -> list[dict]:
    query = select(Wallet).order_by(desc(Wallet.score))
    if selected is not None:
        query = query.where(Wallet.selected.is_(selected))
    wallet_rows = list(db.scalars(query).all())
    profiles = score_profiles(db, wallet_rows)
    return [serialize_wallet(row, profiles.get(row.address)) for row in wallet_rows]


@router.post("/control/pause", dependencies=[Depends(verify_gateway), Depends(verify_admin)])
def pause(db: DatabaseDep) -> dict:
    set_state(db, "paused", "true")
    audit(db, "system_paused", "New paper orders paused by owner", severity="WARN")
    db.commit()
    return {"ok": True, "paused": True}


@router.post("/control/resume", dependencies=[Depends(verify_gateway), Depends(verify_admin)])
def resume(db: DatabaseDep) -> dict:
    if get_state(db, "kill_switch", "false") == "true":
        raise HTTPException(status_code=409, detail="kill switch is active")
    set_state(db, "paused", "false")
    audit(db, "system_resumed", "Paper order intake resumed by owner")
    db.commit()
    return {"ok": True, "paused": False}


@router.post("/control/kill", dependencies=[Depends(verify_gateway), Depends(verify_admin)])
def kill(db: DatabaseDep) -> dict:
    set_state(db, "kill_switch", "true")
    set_state(db, "paused", "true")
    audit(
        db,
        "kill_switch_activated",
        "Emergency stop activated by owner",
        severity="CRITICAL",
    )
    db.commit()
    return {"ok": True, "kill_switch": True}


@router.post("/control/clear-kill", dependencies=[Depends(verify_gateway), Depends(verify_admin)])
def clear_kill(db: DatabaseDep) -> dict:
    set_state(db, "kill_switch", "false")
    audit(
        db,
        "kill_switch_cleared",
        "Emergency stop cleared; system remains paused",
        severity="WARN",
    )
    db.commit()
    return {"ok": True, "kill_switch": False, "paused": True}


@router.get("/live/readiness", dependencies=[Depends(verify_gateway)])
def live_readiness() -> dict:
    return {
        "ready": False,
        "blockers": [
            "LIVE adapter intentionally absent from V1",
            "No trading private key configured",
            "Paper-validation acceptance gate not completed",
            "Owner promotion token not issued",
        ],
    }


def serialize_wallet(row: Wallet, profile: WalletScoreProfile | None = None) -> dict:
    return {
        "address": row.address,
        "username": row.username,
        "score": row.score,
        "score_kind": QUALITY_SCORE_KIND,
        "score_calibrated_probability": QUALITY_SCORE_CALIBRATED_PROBABILITY,
        "score_expected_return_claim": QUALITY_SCORE_EXPECTED_RETURN_CLAIM,
        "score_alpha_claim": QUALITY_SCORE_ALPHA_CLAIM,
        "short_score": profile.short_score if profile else None,
        "long_score": profile.long_score if profile else None,
        "global_score": profile.global_score if profile else row.score,
        "execution_edge_score": profile.execution_edge_score if profile else None,
        "execution_edge_sample_size": (
            profile.execution_edge_sample_size if profile else 0
        ),
        "average_execution_edge": profile.average_execution_edge if profile else None,
        "short_sample_size": profile.short_sample_size if profile else 0,
        "long_sample_size": profile.long_sample_size if profile else row.closed_count,
        "win_rate": row.win_rate,
        "profit_factor": row.profit_factor,
        "realized_pnl": row.realized_pnl,
        "volume": row.volume,
        "closed_count": row.closed_count,
        "concentration": row.concentration,
        "selected": row.selected,
        "rejection_reason": row.rejection_reason,
        "last_activity_at": row.last_activity_at,
        "updated_at": row.updated_at.isoformat(),
    }


def serialize_signal(row: Signal) -> dict:
    return {
        "id": row.id,
        "wallet": row.wallet_address,
        "wallet_score": row.wallet_score,
        "market": row.market_title,
        "outcome": row.outcome,
        "side": row.side,
        "price": row.source_price,
        "usdc": row.source_usdc,
        "timestamp": row.source_timestamp,
        "decision": row.decision,
        "reason": row.decision_reason,
        "transaction_hash": row.transaction_hash,
    }


def serialize_order(row: PaperOrder) -> dict:
    return {
        "id": row.id,
        "signal_id": row.signal_id,
        "market": row.market_title,
        "outcome": row.outcome,
        "side": row.side,
        "requested_usd": row.requested_usd,
        "filled_usd": row.filled_usd,
        "source_price": row.source_price,
        "observed_price": row.observed_price,
        "fill_price": row.fill_price,
        "slippage": row.slippage,
        "status": row.status,
        "reason": row.rejection_reason,
        "created_at": row.created_at.isoformat(),
    }


def serialize_ai_analysis(row: AIAnalysis) -> dict:
    return {
        "id": row.id,
        "model": row.model,
        "report": json.loads(row.report_json),
        "input_tokens": row.input_tokens,
        "output_tokens": row.output_tokens,
        "created_at": row.created_at.isoformat(),
    }
