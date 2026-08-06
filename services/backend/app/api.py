import json
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.models import AuditEvent, PaperOrder, PortfolioSnapshot, Signal, Wallet
from app.repository import audit, current_portfolio, get_state, set_state

router = APIRouter(prefix="/api/v1")


def verify_gateway(
    x_sibyl_gateway_secret: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    if settings.app_env == "development":
        return
    if not x_sibyl_gateway_secret or not secrets.compare_digest(
        x_sibyl_gateway_secret, settings.gateway_shared_secret
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid gateway")


def verify_admin(
    x_sibyl_admin_token: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    if not x_sibyl_admin_token or not secrets.compare_digest(
        x_sibyl_admin_token, settings.admin_token
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="admin token required")


@router.get("/dashboard", dependencies=[Depends(verify_gateway)])
def dashboard(db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> dict:
    portfolio = current_portfolio(db, settings.initial_bankroll_usd)
    wallets = list(db.scalars(select(Wallet).order_by(desc(Wallet.selected), desc(Wallet.score)).limit(20)))
    signals = list(db.scalars(select(Signal).order_by(desc(Signal.id)).limit(30)))
    orders = list(db.scalars(select(PaperOrder).order_by(desc(PaperOrder.id)).limit(30)))
    history = list(
        db.scalars(select(PortfolioSnapshot).order_by(desc(PortfolioSnapshot.id)).limit(100))
    )[::-1]
    events = list(db.scalars(select(AuditEvent).order_by(desc(AuditEvent.id)).limit(40)))
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
        },
        "portfolio": portfolio,
        "wallets": [serialize_wallet(row) for row in wallets],
        "signals": [serialize_signal(row) for row in signals],
        "orders": [serialize_order(row) for row in orders],
        "equity": [
            {"time": row.captured_at.isoformat(), "equity": row.equity, "drawdown": row.drawdown}
            for row in history
        ],
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
def wallets(
    selected: bool | None = Query(default=None), db: Session = Depends(get_db)
) -> list[dict]:
    query = select(Wallet).order_by(desc(Wallet.score))
    if selected is not None:
        query = query.where(Wallet.selected.is_(selected))
    return [serialize_wallet(row) for row in db.scalars(query).all()]


@router.post("/control/pause", dependencies=[Depends(verify_gateway), Depends(verify_admin)])
def pause(db: Session = Depends(get_db)) -> dict:
    set_state(db, "paused", "true")
    audit(db, "system_paused", "New paper orders paused by owner", severity="WARN")
    db.commit()
    return {"ok": True, "paused": True}


@router.post("/control/resume", dependencies=[Depends(verify_gateway), Depends(verify_admin)])
def resume(db: Session = Depends(get_db)) -> dict:
    if get_state(db, "kill_switch", "false") == "true":
        raise HTTPException(status_code=409, detail="kill switch is active")
    set_state(db, "paused", "false")
    audit(db, "system_resumed", "Paper order intake resumed by owner")
    db.commit()
    return {"ok": True, "paused": False}


@router.post("/control/kill", dependencies=[Depends(verify_gateway), Depends(verify_admin)])
def kill(db: Session = Depends(get_db)) -> dict:
    set_state(db, "kill_switch", "true")
    set_state(db, "paused", "true")
    audit(db, "kill_switch_activated", "Emergency stop activated by owner", severity="CRITICAL")
    db.commit()
    return {"ok": True, "kill_switch": True}


@router.post("/control/clear-kill", dependencies=[Depends(verify_gateway), Depends(verify_admin)])
def clear_kill(db: Session = Depends(get_db)) -> dict:
    set_state(db, "kill_switch", "false")
    audit(db, "kill_switch_cleared", "Emergency stop cleared; system remains paused", severity="WARN")
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


def serialize_wallet(row: Wallet) -> dict:
    return {
        "address": row.address,
        "username": row.username,
        "score": row.score,
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
