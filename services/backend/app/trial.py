import argparse
import json
import logging
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.ai import OpenAIAnalyst
from app.config import Settings, get_settings
from app.db import SessionLocal, init_db
from app.models import (
    AIAnalysis,
    PaperOrder,
    PaperPosition,
    Signal,
    Wallet,
    WalletScoreProfile,
)
from app.paper import PaperEngine, ingest_wallet_activity, refresh_position_prices
from app.polymarket import PolymarketClient
from app.repository import audit, current_portfolio, get_state, initialize_state, set_state
from app.scanner import scan_wallets
from app.settlement import settle_closed_positions
from app.settlement_models import PaperSettlement

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("sibyl.github_trial")
T = TypeVar("T")


def utcnow() -> datetime:
    return datetime.now(UTC)


def short_wallet(value: str) -> str:
    return f"{value[:6]}…{value[-4:]}" if len(value) > 12 else value


def _count(db: Session, model: type[Any]) -> int:
    return int(db.scalar(select(func.count()).select_from(model)) or 0)


def _phase(
    db: Session,
    name: str,
    callback: Callable[[], T],
    errors: list[dict[str, str]],
) -> T | None:
    try:
        return callback()
    except Exception as exc:
        db.rollback()
        error = {"phase": name, "type": type(exc).__name__, "message": str(exc)[:500]}
        errors.append(error)
        audit(
            db,
            "github_trial_phase_failed",
            f"{name}: {error['type']}: {error['message']}",
            severity="ERROR",
            phase=name,
        )
        db.commit()
        log.exception("trial phase failed: %s", name)
        return None


def build_report(
    db: Session,
    settings: Settings,
    *,
    started_at: datetime,
    completed_at: datetime,
    selected_count: int,
    settled_count: int,
    marked_count: int,
    processed_count: int,
    ai_created: bool,
    errors: list[dict[str, str]],
) -> dict[str, Any]:
    portfolio = current_portfolio(db, settings.initial_bankroll_usd)
    wallets = list(
        db.scalars(
            select(Wallet)
            .where(Wallet.selected.is_(True))
            .order_by(desc(Wallet.score))
            .limit(settings.tracked_wallet_limit)
        )
    )
    profiles = {
        profile.wallet_address: profile
        for profile in db.scalars(
            select(WalletScoreProfile).where(
                WalletScoreProfile.wallet_address.in_(
                    [wallet.address for wallet in wallets]
                )
            )
        ).all()
    }
    positions = list(
        db.scalars(
            select(PaperPosition)
            .where(PaperPosition.shares > 0)
            .order_by(desc(PaperPosition.updated_at))
            .limit(20)
        )
    )
    orders = list(
        db.scalars(select(PaperOrder).order_by(desc(PaperOrder.id)).limit(12))
    )
    latest_ai = db.scalar(select(AIAnalysis).order_by(desc(AIAnalysis.id)).limit(1))
    filled = int(
        db.scalar(
            select(func.count()).select_from(PaperOrder).where(PaperOrder.status == "FILLED")
        )
        or 0
    )
    rejected = int(
        db.scalar(
            select(func.count())
            .select_from(PaperOrder)
            .where(PaperOrder.status == "REJECTED")
        )
        or 0
    )

    return {
        "schema_version": 2,
        "run": {
            "status": "PASS" if not errors else "DEGRADED",
            "profile": "GITHUB_DELAYED_PAPER",
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "duration_seconds": round((completed_at - started_at).total_seconds(), 3),
            "github_run_id": os.getenv("GITHUB_RUN_ID", ""),
            "github_sha": os.getenv("GITHUB_SHA", ""),
            "errors": errors,
        },
        "safety": {
            "trading_mode": settings.trading_mode,
            "live_available": False,
            "private_key_required": False,
            "signal_age_limit_seconds": settings.risk_max_signal_age_seconds,
            "activity_lookback_seconds": settings.activity_lookback_seconds,
        },
        "score_contract": {
            "short": "most recent 50 closed positions",
            "long": "up to 200 closed positions",
            "global": "60% SHORT + 40% LONG",
            "edge": "confidence-weighted PAPER execution copyability; not outcome alpha",
        },
        "system": {
            "geoblock": get_state(db, "geoblock", "unknown"),
            "paused": get_state(db, "paused", "false") == "true",
            "kill_switch": get_state(db, "kill_switch", "false") == "true",
            "last_scan_at": get_state(db, "last_scan_at", ""),
            "last_watch_at": get_state(db, "last_watch_at", ""),
        },
        "cycle": {
            "selected_wallets": selected_count,
            "positions_settled": settled_count,
            "positions_marked": marked_count,
            "signals_processed": processed_count,
            "ai_report_created": ai_created,
        },
        "totals": {
            "wallets": _count(db, Wallet),
            "signals": _count(db, Signal),
            "orders": _count(db, PaperOrder),
            "filled_orders": filled,
            "rejected_orders": rejected,
            "open_positions": len(positions),
            "settled_positions": _count(db, PaperSettlement),
        },
        "portfolio": portfolio,
        "selected_wallets": [
            {
                "wallet": short_wallet(wallet.address),
                "username": wallet.username,
                "score": round(wallet.score, 2),
                "short_score": (
                    round(profiles[wallet.address].short_score, 2)
                    if wallet.address in profiles
                    else None
                ),
                "long_score": (
                    round(profiles[wallet.address].long_score, 2)
                    if wallet.address in profiles
                    else None
                ),
                "global_score": (
                    round(profiles[wallet.address].global_score, 2)
                    if wallet.address in profiles
                    else round(wallet.score, 2)
                ),
                "execution_edge_score": (
                    round(profiles[wallet.address].execution_edge_score, 2)
                    if wallet.address in profiles
                    else None
                ),
                "execution_edge_sample_size": (
                    profiles[wallet.address].execution_edge_sample_size
                    if wallet.address in profiles
                    else 0
                ),
                "average_execution_edge": (
                    round(profiles[wallet.address].average_execution_edge, 6)
                    if wallet.address in profiles
                    else None
                ),
                "win_rate": round(wallet.win_rate, 6),
                "profit_factor": round(wallet.profit_factor, 4),
                "realized_pnl": round(wallet.realized_pnl, 4),
                "closed_count": wallet.closed_count,
                "concentration": round(wallet.concentration, 6),
            }
            for wallet in wallets
        ],
        "open_positions": [
            {
                "market": position.market_title,
                "outcome": position.outcome,
                "shares": round(position.shares, 6),
                "average_price": round(position.average_price, 6),
                "current_price": round(position.current_price, 6),
                "realized_pnl": round(position.realized_pnl, 4),
            }
            for position in positions
        ],
        "recent_orders": [
            {
                "created_at": order.created_at.isoformat(),
                "market": order.market_title,
                "outcome": order.outcome,
                "side": order.side,
                "status": order.status,
                "filled_usd": round(order.filled_usd, 4),
                "source_price": round(order.source_price, 6),
                "observed_price": (
                    round(order.observed_price, 6)
                    if order.observed_price is not None
                    else None
                ),
                "slippage": round(order.slippage, 6) if order.slippage is not None else None,
                "reason": order.rejection_reason,
            }
            for order in orders
        ],
        "latest_ai": (
            {
                "model": latest_ai.model,
                "created_at": latest_ai.created_at.isoformat(),
                "report": json.loads(latest_ai.report_json),
            }
            if latest_ai
            else None
        ),
    }


def _cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_markdown(report: dict[str, Any]) -> str:
    run = report["run"]
    portfolio = report["portfolio"]
    cycle = report["cycle"]
    totals = report["totals"]
    safety = report["safety"]
    lines = [
        "# Sibyl Trace — GitHub PAPER Trial",
        "",
        f"**Status:** `{run['status']}`  ",
        f"**Profile:** `{run['profile']}`  ",
        f"**Completed:** `{run['completed_at']}`  ",
        f"**Commit:** `{run['github_sha'] or 'local'}`",
        "",
        "## Safety",
        "",
        f"- Mode: `{safety['trading_mode']}`",
        "- LIVE execution: `UNAVAILABLE`",
        f"- Delayed signal window: `{safety['signal_age_limit_seconds']}s`",
        f"- Activity lookback: `{safety['activity_lookback_seconds']}s`",
        "",
        "## Portfolio",
        "",
        "| Equity | Cash | Exposure | Realized PnL | Unrealized PnL | Drawdown |",
        "|---:|---:|---:|---:|---:|---:|",
        (
            f"| ${portfolio['equity']:.2f} | ${portfolio['cash']:.2f} | "
            f"${portfolio['exposure']:.2f} | ${portfolio['realized_pnl']:.2f} | "
            f"${portfolio['unrealized_pnl']:.2f} | {portfolio['drawdown'] * 100:.2f}% |"
        ),
        "",
        "## Current cycle",
        "",
        "| Selected | Settled | Marked | Signals | AI report |",
        "|---:|---:|---:|---:|:---:|",
        (
            f"| {cycle['selected_wallets']} | {cycle['positions_settled']} | "
            f"{cycle['positions_marked']} | {cycle['signals_processed']} | "
            f"{str(cycle['ai_report_created']).lower()} |"
        ),
        "",
        "## Accumulated state",
        "",
        "| Wallets | Signals | Orders | Filled | Rejected | Open | Settled |",
        "|---:|---:|---:|---:|---:|---:|---:|",
        (
            f"| {totals['wallets']} | {totals['signals']} | {totals['orders']} | "
            f"{totals['filled_orders']} | {totals['rejected_orders']} | "
            f"{totals['open_positions']} | {totals['settled_positions']} |"
        ),
        "",
        "## Selected wallets",
        "",
    ]
    wallets = report["selected_wallets"]
    if wallets:
        lines.extend(
            [
                "| Wallet | SHORT | LONG | GLOBAL | EDGE | Edge n | Win rate |",
                "|---|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for wallet in wallets:
            short_score = wallet["short_score"]
            long_score = wallet["long_score"]
            edge_score = wallet["execution_edge_score"]
            lines.append(
                f"| {_cell(wallet['username'] or wallet['wallet'])} | "
                f"{short_score if short_score is not None else '—'} | "
                f"{long_score if long_score is not None else '—'} | "
                f"{wallet['global_score']:.2f} | "
                f"{edge_score if edge_score is not None else '—'} | "
                f"{wallet['execution_edge_sample_size']} | "
                f"{wallet['win_rate'] * 100:.2f}% |"
            )
    else:
        lines.append("_No wallet qualified in this cycle._")

    lines.extend(["", "## Recent PAPER decisions", ""])
    orders = report["recent_orders"]
    if orders:
        lines.extend(
            [
                "| Market | Side | Status | Filled | Slippage | Reason |",
                "|---|:---:|:---:|---:|---:|---|",
            ]
        )
        for order in orders[:8]:
            slippage = (
                f"{order['slippage']:+.4f}" if order["slippage"] is not None else "—"
            )
            lines.append(
                f"| {_cell(order['market'])} | {order['side']} | {order['status']} | "
                f"${order['filled_usd']:.2f} | {slippage} | "
                f"{_cell(order['reason'] or '—')} |"
            )
    else:
        lines.append("_No PAPER decisions have been recorded yet._")

    if run["errors"]:
        lines.extend(["", "## Degraded phases", ""])
        for error in run["errors"]:
            lines.append(
                f"- `{_cell(error['phase'])}` — {_cell(error['type'])}: "
                f"{_cell(error['message'])}"
            )

    lines.extend(
        [
            "",
            "---",
            "This is a delayed PAPER experiment on ephemeral GitHub-hosted runners. "
            "It is not a 24/7 executor and cannot place real orders.",
            "",
        ]
    )
    return "\n".join(lines)


def write_report(report: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "trial-summary.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (output_dir / "trial-summary.md").write_text(
        render_markdown(report),
        encoding="utf-8",
    )


def run_cycle(output_dir: Path) -> int:
    settings = get_settings()
    started_at = utcnow()
    errors: list[dict[str, str]] = []
    selected_count = 0
    settled_count = 0
    marked_count = 0
    processed_count = 0
    ai_created = False
    client = PolymarketClient(settings)
    analyst = OpenAIAnalyst(settings)

    try:
        init_db()
        with SessionLocal() as db:
            initialize_state(db, settings)

            geoblock = _phase(db, "geoblock", client.geoblock, errors)
            if isinstance(geoblock, dict):
                state = "blocked" if geoblock.get("blocked") else "clear"
                set_state(db, "geoblock", state)
                db.commit()

            selected = _phase(
                db,
                "wallet_scan",
                lambda: scan_wallets(db, client, settings),
                errors,
            )
            if selected is not None:
                selected_count = len(selected)

            settled = _phase(
                db,
                "position_settlement",
                lambda: settle_closed_positions(db, client, settings),
                errors,
            )
            if settled is not None:
                settled_count = int(settled)

            marked = _phase(
                db,
                "position_mark",
                lambda: refresh_position_prices(db, client, settings),
                errors,
            )
            if marked is not None:
                marked_count = int(marked)

            engine = PaperEngine(settings, client)
            processed = _phase(
                db,
                "wallet_activity",
                lambda: ingest_wallet_activity(db, client, settings, engine),
                errors,
            )
            if processed is not None:
                processed_count = int(processed)

            if analyst.enabled:
                analysis = _phase(
                    db,
                    "ai_advisory",
                    lambda: analyst.run(db),
                    errors,
                )
                ai_created = analysis is not None

            completed_at = utcnow()
            audit(
                db,
                "github_trial_cycle_completed",
                f"GitHub delayed PAPER cycle completed with {len(errors)} degraded phases",
                severity="WARN" if errors else "INFO",
                status="DEGRADED" if errors else "PASS",
                selected=selected_count,
                settled=settled_count,
                marked=marked_count,
                processed=processed_count,
            )
            db.commit()
            report = build_report(
                db,
                settings,
                started_at=started_at,
                completed_at=completed_at,
                selected_count=selected_count,
                settled_count=settled_count,
                marked_count=marked_count,
                processed_count=processed_count,
                ai_created=ai_created,
                errors=errors,
            )
            write_report(report, output_dir)
            log.info("trial status=%s", report["run"]["status"])
            return 1 if errors else 0
    finally:
        analyst.close()
        client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one persisted GitHub PAPER cycle")
    parser.add_argument("--output-dir", type=Path, default=Path("trial-output"))
    args = parser.parse_args()
    try:
        return run_cycle(args.output_dir)
    except Exception as exc:
        completed_at = utcnow()
        report = {
            "schema_version": 2,
            "run": {
                "status": "FAILED",
                "profile": "GITHUB_DELAYED_PAPER",
                "started_at": completed_at.isoformat(),
                "completed_at": completed_at.isoformat(),
                "duration_seconds": 0,
                "github_run_id": os.getenv("GITHUB_RUN_ID", ""),
                "github_sha": os.getenv("GITHUB_SHA", ""),
                "errors": [
                    {
                        "phase": "bootstrap",
                        "type": type(exc).__name__,
                        "message": str(exc)[:500],
                    }
                ],
            },
            "safety": {
                "trading_mode": "PAPER",
                "live_available": False,
                "private_key_required": False,
                "signal_age_limit_seconds": 0,
                "activity_lookback_seconds": 0,
            },
            "score_contract": {},
            "system": {},
            "cycle": {
                "selected_wallets": 0,
                "positions_settled": 0,
                "positions_marked": 0,
                "signals_processed": 0,
                "ai_report_created": False,
            },
            "totals": {
                "wallets": 0,
                "signals": 0,
                "orders": 0,
                "filled_orders": 0,
                "rejected_orders": 0,
                "open_positions": 0,
                "settled_positions": 0,
            },
            "portfolio": {
                "equity": 0,
                "cash": 0,
                "exposure": 0,
                "realized_pnl": 0,
                "unrealized_pnl": 0,
                "drawdown": 0,
            },
            "selected_wallets": [],
            "open_positions": [],
            "recent_orders": [],
            "latest_ai": None,
        }
        write_report(report, args.output_dir)
        log.exception("trial bootstrap failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
