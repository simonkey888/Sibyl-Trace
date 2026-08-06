import json
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import AuditEvent, PaperOrder, PaperPosition, PortfolioSnapshot, SystemState


def utcnow() -> datetime:
    return datetime.now(UTC)


def get_state(db: Session, key: str, default: str = "") -> str:
    row = db.get(SystemState, key)
    return row.value if row else default


def set_state(db: Session, key: str, value: str) -> None:
    row = db.get(SystemState, key)
    if row is None:
        row = SystemState(key=key, value=value)
        db.add(row)
    else:
        row.value = value
        row.updated_at = utcnow()


def audit(
    db: Session,
    event_type: str,
    message: str,
    severity: str = "INFO",
    **payload: object,
) -> None:
    db.add(
        AuditEvent(
            event_type=event_type,
            severity=severity,
            message=message,
            payload_json=json.dumps(payload, sort_keys=True, default=str),
        )
    )


def initialize_state(db: Session, settings: Settings) -> None:
    defaults = {
        "mode": settings.trading_mode,
        "paused": "false",
        "kill_switch": "false",
        "last_scan_at": "",
        "last_watch_at": "",
        "geoblock": "unknown",
    }
    for key, value in defaults.items():
        if db.get(SystemState, key) is None:
            db.add(SystemState(key=key, value=value))
    if db.scalar(select(func.count()).select_from(PortfolioSnapshot)) == 0:
        db.add(
            PortfolioSnapshot(
                cash=settings.initial_bankroll_usd,
                exposure=0,
                equity=settings.initial_bankroll_usd,
                realized_pnl=0,
                unrealized_pnl=0,
                drawdown=0,
            )
        )
    db.commit()


def current_portfolio(db: Session, initial_bankroll: float) -> dict[str, float]:
    positions = list(db.scalars(select(PaperPosition)).all())
    exposure = sum(
        max(position.shares, 0) * max(position.current_price, 0)
        for position in positions
    )
    cost_basis = sum(
        max(position.shares, 0) * max(position.average_price, 0)
        for position in positions
    )
    realized = sum(position.realized_pnl for position in positions)
    filled_buys = db.scalar(
        select(func.coalesce(func.sum(PaperOrder.filled_usd), 0)).where(
            PaperOrder.status == "FILLED", PaperOrder.side == "BUY"
        )
    )
    filled_sells = db.scalar(
        select(func.coalesce(func.sum(PaperOrder.filled_usd), 0)).where(
            PaperOrder.status == "FILLED", PaperOrder.side == "SELL"
        )
    )
    cash = initial_bankroll - float(filled_buys or 0) + float(filled_sells or 0)
    unrealized = exposure - cost_basis
    equity = cash + exposure
    peak = db.scalar(select(func.max(PortfolioSnapshot.equity))) or initial_bankroll
    drawdown = max((float(peak) - equity) / float(peak), 0.0) if peak else 0.0

    day_start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    prior_close = db.scalar(
        select(PortfolioSnapshot)
        .where(PortfolioSnapshot.captured_at < day_start)
        .order_by(PortfolioSnapshot.captured_at.desc())
        .limit(1)
    )
    opening_equity = float(prior_close.equity) if prior_close else initial_bankroll
    daily_pnl = equity - opening_equity
    return {
        "initial_bankroll": round(initial_bankroll, 4),
        "cash": round(cash, 4),
        "exposure": round(exposure, 4),
        "equity": round(equity, 4),
        "realized_pnl": round(realized, 4),
        "unrealized_pnl": round(unrealized, 4),
        "drawdown": round(drawdown, 6),
        "daily_pnl": round(daily_pnl, 4),
    }
