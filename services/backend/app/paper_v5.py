from __future__ import annotations

import argparse
import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import SessionLocal, init_db
from app.domain import PortfolioState, RiskPolicy, RiskRequest
from app.execution_v5 import (
    FillV5,
    best_executable_price,
    market_rules_from_clob_info,
    simulate_fak_fill,
    worst_price_limit,
)
from app.models import Wallet
from app.models_v5 import (
    PaperV5Execution,
    PaperV5PortfolioSnapshot,
    PaperV5Position,
    PaperV5Prediction,
    PaperV5Settlement,
)
from app.polymarket import PolymarketClient
from app.repository import audit, get_state, initialize_state, set_state
from app.scanner import scan_wallets
from app.watchdogs import accounting_watchdog

EVIDENCE_GENERATION = "SIBYL_PAPER_V5_EXECUTION_REALISTIC"
EXECUTION_MODEL = "L2_TAKER_FAK_ARRIVAL_BOOK_V1"
LEGACY_LABEL = "LEGACY_SIMULATION_MIDPOINT_V2"


def utcnow() -> datetime:
    return datetime.now(UTC)


def _count(db: Session, model: type[Any]) -> int:
    return int(db.scalar(select(func.count()).select_from(model)) or 0)


def _source_identity(wallet: str, activity: dict[str, Any]) -> tuple[str, str]:
    identity = {
        "wallet": wallet,
        "transaction_hash": str(activity.get("transactionHash") or ""),
        "asset_id": str(activity.get("asset") or ""),
        "side": str(activity.get("side") or "").upper(),
        "timestamp": int(activity.get("timestamp") or 0),
        "price": str(activity.get("price") or ""),
        "size": str(activity.get("size") or ""),
        "usdc_size": str(activity.get("usdcSize") or ""),
        "outcome_index": str(activity.get("outcomeIndex") or ""),
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(encoded.encode()).hexdigest()
    return f"v5:{digest}", digest


def _list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _resolved_price(market: dict[str, Any], asset_id: str) -> float | None:
    token_ids = [str(value) for value in _list(market.get("clobTokenIds"))]
    prices = _list(market.get("outcomePrices"))
    if asset_id not in token_ids:
        return None
    index = token_ids.index(asset_id)
    if index >= len(prices):
        return None
    try:
        price = float(prices[index])
    except (TypeError, ValueError):
        return None
    if price >= 0.999:
        return 1.0
    if price <= 0.001:
        return 0.0
    return None


def current_portfolio_v5(db: Session, initial_bankroll: float) -> dict[str, float]:
    positions = list(db.scalars(select(PaperV5Position)).all())
    exposure = sum(max(position.mark_value_usd, 0.0) for position in positions)
    cost_basis = sum(max(position.cost_basis_usd, 0.0) for position in positions)
    realized = sum(position.realized_pnl for position in positions)
    execution_cash = float(
        db.scalar(select(func.coalesce(func.sum(PaperV5Execution.net_cash_delta), 0))) or 0
    )
    settlement_proceeds = float(
        db.scalar(select(func.coalesce(func.sum(PaperV5Settlement.proceeds), 0))) or 0
    )
    cash = initial_bankroll + execution_cash + settlement_proceeds
    unrealized = exposure - cost_basis
    equity = cash + exposure
    peak = db.scalar(select(func.max(PaperV5PortfolioSnapshot.equity))) or initial_bankroll
    drawdown = max((float(peak) - equity) / float(peak), 0.0) if peak else 0.0
    day_start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    prior_close = db.scalar(
        select(PaperV5PortfolioSnapshot)
        .where(PaperV5PortfolioSnapshot.captured_at < day_start)
        .order_by(PaperV5PortfolioSnapshot.captured_at.desc())
        .limit(1)
    )
    opening_equity = float(prior_close.equity) if prior_close else initial_bankroll
    return {
        "initial_bankroll": round(initial_bankroll, 4),
        "cash": round(cash, 4),
        "exposure": round(exposure, 4),
        "equity": round(equity, 4),
        "realized_pnl": round(realized, 4),
        "unrealized_pnl": round(unrealized, 4),
        "drawdown": round(drawdown, 6),
        "daily_pnl": round(equity - opening_equity, 4),
        "settlement_proceeds": round(settlement_proceeds, 4),
    }


def _portfolio_state(
    db: Session,
    settings: Settings,
    position: PaperV5Position | None,
) -> PortfolioState:
    portfolio = current_portfolio_v5(db, settings.initial_bankroll_usd)
    return PortfolioState(
        equity=portfolio["equity"],
        cash=portfolio["cash"],
        total_exposure=portfolio["exposure"],
        daily_pnl=portfolio["daily_pnl"],
        drawdown=portfolio["drawdown"],
        asset_exposure=max(position.mark_value_usd, 0.0) if position else 0.0,
        asset_shares=max(position.shares, 0.0) if position else 0.0,
    )


def _execution_row(
    prediction: PaperV5Prediction,
    *,
    status: str,
    reason: str | None,
    requested_usd: float = 0,
    requested_shares: float = 0,
    decision_book: dict[str, Any] | None = None,
    arrival_book: dict[str, Any] | None = None,
    decision_best_price: float | None = None,
    worst_price: float | None = None,
    rules: Any = None,
    fill: FillV5 | None = None,
    source_price: float | None = None,
) -> PaperV5Execution:
    effective = fill.effective_price if fill else None
    slippage = None
    if effective is not None and source_price is not None:
        slippage = (
            effective - source_price if prediction.side == "BUY" else source_price - effective
        )
    return PaperV5Execution(
        prediction_id=prediction.id,
        order_type="FAK",
        requested_usd=requested_usd,
        requested_shares=requested_shares,
        decision_book_hash=str((decision_book or {}).get("hash") or "") or None,
        decision_book_timestamp_ms=int((decision_book or {}).get("timestamp") or 0) or None,
        arrival_book_hash=str((arrival_book or {}).get("hash") or "") or None,
        arrival_book_timestamp_ms=int((arrival_book or {}).get("timestamp") or 0) or None,
        decision_best_price=decision_best_price,
        worst_price_limit=worst_price,
        tick_size=getattr(rules, "tick_size", None),
        minimum_order_size=getattr(rules, "minimum_order_size", None),
        fee_rate=getattr(rules, "fee_rate", None),
        fee_exponent=getattr(rules, "fee_exponent", None),
        simulated_latency_ms=getattr(rules, "order_delay_ms", 0),
        filled_shares=fill.filled_shares if fill else 0,
        gross_notional=fill.gross_notional if fill else 0,
        fee_usd=fill.fee_usd if fill else 0,
        net_cash_delta=fill.net_cash_delta if fill else 0,
        average_fill_price=fill.average_fill_price if fill else None,
        effective_price=effective,
        slippage=slippage,
        fill_fraction=fill.fill_fraction if fill else 0,
        levels_consumed=fill.levels_consumed if fill else 0,
        status=status,
        reason=reason,
    )


class PaperEngineV5:
    def __init__(self, settings: Settings, client: PolymarketClient):
        self.settings = settings
        self.client = client
        self.policy = RiskPolicy(maximum_signal_age_seconds=settings.risk_max_signal_age_seconds)

    def _reject(
        self,
        db: Session,
        prediction: PaperV5Prediction,
        reason: str,
        **kwargs: Any,
    ) -> None:
        prediction.decision = "REJECTED"
        prediction.decision_reason = reason
        prediction.resolution_status = "NOT_APPLICABLE"
        prediction.result = "REJECTED"
        db.add(_execution_row(prediction, status="REJECTED", reason=reason, **kwargs))
        audit(db, "paper_v5_rejected", reason, severity="WARN", prediction_id=prediction.id)
        db.commit()

    def process(self, db: Session, wallet: Wallet, activity: dict[str, Any]) -> bool:
        timestamp = int(activity.get("timestamp") or 0)
        tx_hash = str(activity.get("transactionHash") or "")
        asset_id = str(activity.get("asset") or "")
        side = str(activity.get("side") or "").upper()
        condition_id = str(activity.get("conditionId") or "")
        if (
            not timestamp
            or not tx_hash
            or not asset_id
            or not condition_id
            or side not in {"BUY", "SELL"}
        ):
            return False
        source_key, payload_hash = _source_identity(wallet.address, activity)
        if db.scalar(
            select(PaperV5Prediction.id).where(PaperV5Prediction.source_key == source_key)
        ):
            return False

        source_price = float(activity.get("price") or 0)
        source_size = float(activity.get("size") or 0)
        source_usdc = max(float(activity.get("usdcSize") or 0), source_size * source_price)
        prediction = PaperV5Prediction(
            source_key=source_key,
            wallet_address=wallet.address,
            wallet_score=wallet.score,
            condition_id=condition_id,
            asset_id=asset_id,
            market_title=str(activity.get("title") or "Unknown market"),
            outcome=str(activity.get("outcome") or "UNKNOWN"),
            side=side,
            source_price=source_price,
            source_size=source_size,
            source_usdc=source_usdc,
            source_timestamp=timestamp,
            transaction_hash=tx_hash,
            source_payload_hash=payload_hash,
        )
        db.add(prediction)
        db.flush()

        if get_state(db, "mode", self.settings.trading_mode) != "PAPER":
            self._reject(db, prediction, "system_not_in_paper_mode")
            return True
        if (
            get_state(db, "paused", "false") == "true"
            or get_state(db, "kill_switch", "false") == "true"
        ):
            self._reject(db, prediction, "system_not_accepting_orders")
            return True

        position = db.get(PaperV5Position, asset_id)
        state = _portfolio_state(db, self.settings, position)
        request = RiskRequest(
            side=side,
            wallet_score=wallet.score,
            signal_age_seconds=max(int(time.time()) - timestamp, 0),
            source_price=source_price,
            observed_price=None,
            source_usdc=source_usdc,
        )
        preflight = self.policy.preflight(request, state)
        if preflight is not None:
            self._reject(db, prediction, preflight.reason)
            return True

        try:
            info = self.client.clob_market_info(condition_id)
            rules = market_rules_from_clob_info(info)
            decision_book = self.client.order_book(asset_id)
            observed = best_executable_price(decision_book, side)
        except Exception as exc:
            self._reject(db, prediction, f"market_data_unavailable:{type(exc).__name__}")
            return True
        if observed is None:
            self._reject(
                db, prediction, "empty_executable_book", decision_book=decision_book, rules=rules
            )
            return True

        request = RiskRequest(
            side=side,
            wallet_score=wallet.score,
            signal_age_seconds=request.signal_age_seconds,
            source_price=source_price,
            observed_price=observed,
            source_usdc=source_usdc,
        )
        decision = self.policy.evaluate(request, state)
        if not decision.approved:
            self._reject(
                db,
                prediction,
                decision.reason,
                decision_book=decision_book,
                decision_best_price=observed,
                rules=rules,
            )
            return True

        limit = worst_price_limit(
            source_price=source_price,
            side=side,
            tick_size=rules.tick_size,
            maximum_absolute_slippage=self.policy.maximum_absolute_slippage,
        )
        requested_shares = 0.0
        if side == "SELL":
            if position is None or position.shares <= 0:
                self._reject(db, prediction, "no_paper_position_to_sell")
                return True
            requested_shares = min(position.shares, decision.amount_usd / observed)

        time.sleep(rules.order_delay_ms / 1000)
        try:
            arrival_book = self.client.order_book(asset_id)
        except Exception as exc:
            self._reject(
                db,
                prediction,
                f"arrival_book_unavailable:{type(exc).__name__}",
                requested_usd=decision.amount_usd,
                requested_shares=requested_shares,
                decision_book=decision_book,
                decision_best_price=observed,
                worst_price=limit,
                rules=rules,
            )
            return True

        fill = simulate_fak_fill(
            arrival_book,
            side=side,
            fee_rate=rules.fee_rate,
            minimum_order_size=rules.minimum_order_size,
            worst_price=limit,
            requested_usd=decision.amount_usd if side == "BUY" else 0,
            requested_shares=requested_shares,
        )
        execution = _execution_row(
            prediction,
            status=fill.status,
            reason=fill.reason,
            requested_usd=decision.amount_usd,
            requested_shares=requested_shares,
            decision_book=decision_book,
            arrival_book=arrival_book,
            decision_best_price=observed,
            worst_price=limit,
            rules=rules,
            fill=fill,
            source_price=source_price,
        )
        db.add(execution)
        if fill.status == "NO_FILL":
            prediction.decision = "NO_FILL"
            prediction.decision_reason = fill.reason
            prediction.resolution_status = "NOT_APPLICABLE"
            prediction.result = "NO_FILL"
            db.commit()
            return True

        if position is None:
            position = PaperV5Position(
                asset_id=asset_id,
                condition_id=condition_id,
                market_title=prediction.market_title,
                outcome=prediction.outcome,
            )
            db.add(position)
            db.flush()

        if side == "BUY":
            total_cost = -fill.net_cash_delta
            if total_cost > state.cash + 1e-6:
                db.rollback()
                raise RuntimeError("v5 buy fill exceeded available cash")
            position.shares += fill.filled_shares
            position.cost_basis_usd += total_cost
        else:
            before = max(position.shares, 0.0)
            if before <= 0 or fill.filled_shares > before + 1e-9:
                db.rollback()
                raise RuntimeError("v5 sell fill exceeded position shares")
            allocated_cost = position.cost_basis_usd * (fill.filled_shares / before)
            position.shares = max(before - fill.filled_shares, 0.0)
            position.cost_basis_usd = max(position.cost_basis_usd - allocated_cost, 0.0)
            position.realized_pnl += fill.net_cash_delta - allocated_cost
            prediction.resolution_status = "NOT_APPLICABLE"
            prediction.result = "EXIT"
        position.updated_at = utcnow()
        prediction.decision = fill.status
        prediction.decision_reason = "arrival_book_fak"
        audit(
            db,
            "paper_v5_fill",
            f"{side} {prediction.outcome}: {fill.status}",
            prediction_id=prediction.id,
            filled_shares=round(fill.filled_shares, 8),
            gross_notional=round(fill.gross_notional, 6),
            fee_usd=round(fill.fee_usd, 6),
            fill_fraction=round(fill.fill_fraction, 6),
        )
        db.commit()
        return True


def ingest_activity_v5(
    db: Session,
    client: PolymarketClient,
    settings: Settings,
    engine: PaperEngineV5,
) -> tuple[int, list[str]]:
    processed = 0
    errors: list[str] = []
    wallets = list(db.scalars(select(Wallet).where(Wallet.selected.is_(True))).all())
    for wallet in wallets:
        start = wallet.last_activity_at or int(time.time()) - settings.activity_lookback_seconds
        try:
            activities = client.activity(
                wallet.address, start=start, limit=settings.activity_fetch_limit
            )
        except Exception as exc:
            errors.append(f"activity:{wallet.address}:{type(exc).__name__}:{str(exc)[:160]}")
            continue
        for activity in activities:
            if activity.get("type") != "TRADE":
                continue
            timestamp = int(activity.get("timestamp") or 0)
            try:
                created = engine.process(db, wallet, activity)
            except Exception as exc:
                db.rollback()
                errors.append(f"execution:{wallet.address}:{type(exc).__name__}:{str(exc)[:160]}")
                continue
            if created:
                processed += 1
            if timestamp:
                wallet.last_activity_at = max(wallet.last_activity_at, timestamp)
        db.commit()
    set_state(db, "last_watch_at", utcnow().isoformat())
    db.commit()
    return processed, errors


def mark_positions_v5(db: Session, client: PolymarketClient) -> tuple[int, list[str]]:
    updated = 0
    errors: list[str] = []
    positions = list(db.scalars(select(PaperV5Position).where(PaperV5Position.shares > 0)).all())
    for position in positions:
        try:
            rules = market_rules_from_clob_info(client.clob_market_info(position.condition_id))
            book = client.order_book(position.asset_id)
            fill = simulate_fak_fill(
                book,
                side="SELL",
                fee_rate=rules.fee_rate,
                minimum_order_size=rules.minimum_order_size,
                worst_price=max(rules.tick_size, 0.001),
                requested_shares=position.shares,
            )
            value = max(fill.net_cash_delta, 0.0) if fill.status != "NO_FILL" else 0.0
            position.mark_value_usd = value
            position.mark_price = value / position.shares if position.shares > 0 else 0.0
            position.updated_at = utcnow()
            updated += 1
        except Exception as exc:
            position.mark_value_usd = 0
            position.mark_price = 0
            position.updated_at = utcnow()
            errors.append(f"mark:{position.asset_id}:{type(exc).__name__}:{str(exc)[:160]}")
    db.commit()
    return updated, errors


def settle_v5(db: Session, client: PolymarketClient) -> tuple[int, int, list[str]]:
    unresolved = list(
        db.scalars(
            select(PaperV5Prediction).where(
                PaperV5Prediction.side == "BUY",
                PaperV5Prediction.resolution_status == "OPEN",
            )
        ).all()
    )
    positions = list(db.scalars(select(PaperV5Position).where(PaperV5Position.shares > 0)).all())
    condition_ids = list(
        dict.fromkeys(
            [prediction.condition_id for prediction in unresolved]
            + [position.condition_id for position in positions]
        )
    )
    if not condition_ids:
        return 0, 0, []
    try:
        markets = client.closed_markets(condition_ids)
    except Exception as exc:
        return 0, 0, [f"settlement_fetch:{type(exc).__name__}:{str(exc)[:160]}"]
    by_condition = {
        str(market.get("conditionId") or market.get("condition_id") or ""): market
        for market in markets
        if isinstance(market, dict) and market.get("closed") is True
    }
    now = utcnow()
    resolved_predictions = 0
    settled_positions = 0

    for prediction in unresolved:
        execution = db.scalar(
            select(PaperV5Execution).where(PaperV5Execution.prediction_id == prediction.id)
        )
        if execution is None or execution.filled_shares <= 0:
            continue
        market = by_condition.get(prediction.condition_id)
        if market is None:
            continue
        price = _resolved_price(market, prediction.asset_id)
        if price is None:
            continue
        prediction.resolution_status = "RESOLVED"
        prediction.resolution_price = price
        prediction.resolved_at = now
        prediction.result = "WIN" if price == 1.0 else "LOSS"
        resolved_predictions += 1

    for position in positions:
        market = by_condition.get(position.condition_id)
        if market is None:
            continue
        price = _resolved_price(market, position.asset_id)
        if price is None or db.get(PaperV5Settlement, position.asset_id) is not None:
            continue
        shares = max(position.shares, 0.0)
        cost_basis = max(position.cost_basis_usd, 0.0)
        proceeds = shares * price
        realized = proceeds - cost_basis
        db.add(
            PaperV5Settlement(
                asset_id=position.asset_id,
                condition_id=position.condition_id,
                market_title=position.market_title,
                outcome=position.outcome,
                settlement_price=price,
                shares=shares,
                proceeds=proceeds,
                cost_basis=cost_basis,
                realized_pnl=realized,
            )
        )
        position.realized_pnl += realized
        position.shares = 0
        position.cost_basis_usd = 0
        position.mark_value_usd = 0
        position.mark_price = price
        position.updated_at = now
        settled_positions += 1
    db.commit()
    return resolved_predictions, settled_positions, []


def _wallet_payload(wallet: Wallet) -> dict[str, Any]:
    return {
        "wallet": f"{wallet.address[:6]}…{wallet.address[-4:]}",
        "username": wallet.username,
        "score": round(wallet.score, 2),
        "win_rate": round(wallet.win_rate, 6),
        "profit_factor": round(wallet.profit_factor, 4),
        "realized_pnl": round(wallet.realized_pnl, 4),
        "closed_count": wallet.closed_count,
    }


def _recent_rows(db: Session, limit: int = 30) -> list[dict[str, Any]]:
    rows = db.execute(
        select(PaperV5Prediction, PaperV5Execution)
        .join(PaperV5Execution, PaperV5Execution.prediction_id == PaperV5Prediction.id)
        .order_by(desc(PaperV5Prediction.id))
        .limit(limit)
    ).all()
    output: list[dict[str, Any]] = []
    for prediction, execution in rows:
        output.append(
            {
                "id": prediction.id,
                "created_at": prediction.created_at.isoformat(),
                "market": prediction.market_title,
                "outcome": prediction.outcome,
                "side": prediction.side,
                "source_price": round(prediction.source_price, 6),
                "observed_price": (
                    round(execution.decision_best_price, 6)
                    if execution.decision_best_price is not None
                    else None
                ),
                "average_fill_price": (
                    round(execution.average_fill_price, 6)
                    if execution.average_fill_price is not None
                    else None
                ),
                "effective_price": (
                    round(execution.effective_price, 6)
                    if execution.effective_price is not None
                    else None
                ),
                "slippage": round(execution.slippage, 6)
                if execution.slippage is not None
                else None,
                "filled_usd": round(execution.gross_notional, 4),
                "filled_shares": round(execution.filled_shares, 6),
                "fee_usd": round(execution.fee_usd, 5),
                "fill_fraction": round(execution.fill_fraction, 6),
                "levels_consumed": execution.levels_consumed,
                "status": execution.status,
                "reason": execution.reason or prediction.decision_reason,
                "result": prediction.result,
                "resolution_status": prediction.resolution_status,
                "resolution_price": prediction.resolution_price,
                "transaction_hash": prediction.transaction_hash,
                "source_payload_hash": prediction.source_payload_hash,
                "decision_book_hash": execution.decision_book_hash,
                "arrival_book_hash": execution.arrival_book_hash,
                "simulated_latency_ms": execution.simulated_latency_ms,
            }
        )
    return output


def build_report(
    db: Session,
    settings: Settings,
    *,
    started_at: datetime,
    selected_count: int,
    processed_count: int,
    resolved_count: int,
    settled_count: int,
    marked_count: int,
    errors: list[str],
) -> dict[str, Any]:
    portfolio = current_portfolio_v5(db, settings.initial_bankroll_usd)
    accounting = accounting_watchdog(
        cash=portfolio["cash"],
        open_market_value=portfolio["exposure"],
        equity=portfolio["equity"],
        initial_bankroll=portfolio["initial_bankroll"],
        realized_pnl=portfolio["realized_pnl"],
        unrealized_pnl=portfolio["unrealized_pnl"],
        tolerance=0.02,
    )
    wins = int(
        db.scalar(
            select(func.count())
            .select_from(PaperV5Prediction)
            .where(PaperV5Prediction.result == "WIN")
        )
        or 0
    )
    losses = int(
        db.scalar(
            select(func.count())
            .select_from(PaperV5Prediction)
            .where(PaperV5Prediction.result == "LOSS")
        )
        or 0
    )
    filled = int(
        db.scalar(
            select(func.count())
            .select_from(PaperV5Execution)
            .where(PaperV5Execution.status.in_(["FILLED", "PARTIAL_FILLED"]))
        )
        or 0
    )
    partial = int(
        db.scalar(
            select(func.count())
            .select_from(PaperV5Execution)
            .where(PaperV5Execution.status == "PARTIAL_FILLED")
        )
        or 0
    )
    no_fill = int(
        db.scalar(
            select(func.count())
            .select_from(PaperV5Execution)
            .where(PaperV5Execution.status == "NO_FILL")
        )
        or 0
    )
    rejected = int(
        db.scalar(
            select(func.count())
            .select_from(PaperV5Execution)
            .where(PaperV5Execution.status == "REJECTED")
        )
        or 0
    )
    positions = list(
        db.scalars(
            select(PaperV5Position)
            .where(PaperV5Position.shares > 0)
            .order_by(desc(PaperV5Position.updated_at))
        ).all()
    )
    wallets = list(
        db.scalars(
            select(Wallet)
            .where(Wallet.selected.is_(True))
            .order_by(desc(Wallet.score))
            .limit(settings.tracked_wallet_limit)
        ).all()
    )
    completed_at = utcnow()
    accuracy = wins / (wins + losses) if wins + losses else None
    is_pass = not errors and accounting.state != "RED"
    db.add(
        PaperV5PortfolioSnapshot(
            cash=portfolio["cash"],
            exposure=portfolio["exposure"],
            equity=portfolio["equity"],
            realized_pnl=portfolio["realized_pnl"],
            unrealized_pnl=portfolio["unrealized_pnl"],
            drawdown=portfolio["drawdown"],
            accounting_ok=accounting.state != "RED",
        )
    )
    db.commit()
    return {
        "schema_version": 5,
        "evidence_generation": EVIDENCE_GENERATION,
        "status": "PASS" if is_pass else "DEGRADED",
        "run": {
            "github_run_id": os.getenv("GITHUB_RUN_ID", ""),
            "github_sha": os.getenv("GITHUB_SHA", ""),
            "started_at": started_at.isoformat(),
            "completed_at": completed_at.isoformat(),
            "duration_seconds": round((completed_at - started_at).total_seconds(), 3),
            "errors": errors,
        },
        "safety": {
            "trading_mode": "PAPER",
            "live_available": False,
            "real_money": False,
            "order_placement": False,
            "private_keys": False,
            "paid_apis": False,
            "cost_authorized_usd": 0,
        },
        "methodology": {
            "execution_model": EXECUTION_MODEL,
            "order_type": "FAK",
            "midpoint_fills": False,
            "arrival_book_refetch": True,
            "l2_depth_consumed": True,
            "partial_fills": True,
            "no_fill_is_valid_evidence": True,
            "fee_schedule_source": "CLOB getClobMarketInfo fd",
            "fee_formula": "shares * rate * p * (1-p); rounded to 5 decimals",
            "regular_arrival_delay_ms": 0,
            "regular_arrival_delay_basis": "immediate public-book refetch; no synthetic delay",
            "delayed_market_arrival_delay_ms": 1000,
            "delayed_market_arrival_delay_basis": "CLOB itode flag",
            "marking": "net executable liquidation value; unfilled residual = zero",
            "accuracy_denominator": "resolved filled BUY predictions only",
            "sell_signals_scored_as_directional_predictions": False,
            "legacy_history_rewritten": False,
            "legacy_v2_label": LEGACY_LABEL,
        },
        "cycle": {
            "selected_wallets": selected_count,
            "signals_processed": processed_count,
            "predictions_resolved": resolved_count,
            "positions_settled": settled_count,
            "positions_marked": marked_count,
        },
        "totals": {
            "predictions": _count(db, PaperV5Prediction),
            "executions": _count(db, PaperV5Execution),
            "filled_orders": filled,
            "partial_fills": partial,
            "no_fills": no_fill,
            "rejected": rejected,
            "open_positions": len(positions),
            "settled_positions": _count(db, PaperV5Settlement),
            "resolved_directional_entries": wins + losses,
            "wins": wins,
            "losses": losses,
            "accuracy": round(accuracy, 6) if accuracy is not None else None,
        },
        "portfolio": portfolio,
        "accounting_watchdog": {
            "state": accounting.state,
            "watchdog": accounting.watchdog,
            "message": accounting.message,
            "payload": accounting.payload,
        },
        "selected_wallets": [_wallet_payload(wallet) for wallet in wallets],
        "open_positions": [
            {
                "market": position.market_title,
                "outcome": position.outcome,
                "shares": round(position.shares, 6),
                "average_price": round(position.cost_basis_usd / position.shares, 6)
                if position.shares > 0
                else 0,
                "current_price": round(position.mark_price, 6),
                "mark_value_usd": round(position.mark_value_usd, 4),
                "realized_pnl": round(position.realized_pnl, 4),
            }
            for position in positions
        ],
        "recent_orders": _recent_rows(db),
    }


def _write_json(path: Path, value: Any) -> None:
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _write_ledger(db: Session, path: Path) -> None:
    rows = db.execute(
        select(PaperV5Prediction, PaperV5Execution)
        .join(PaperV5Execution, PaperV5Execution.prediction_id == PaperV5Prediction.id)
        .order_by(PaperV5Prediction.id)
    ).all()
    with path.open("w", encoding="utf-8") as handle:
        for prediction, execution in rows:
            record = {
                "prediction_id": prediction.id,
                "source_key": prediction.source_key,
                "wallet_address": prediction.wallet_address,
                "condition_id": prediction.condition_id,
                "asset_id": prediction.asset_id,
                "market": prediction.market_title,
                "outcome": prediction.outcome,
                "side": prediction.side,
                "source_price": prediction.source_price,
                "source_timestamp": prediction.source_timestamp,
                "transaction_hash": prediction.transaction_hash,
                "source_payload_hash": prediction.source_payload_hash,
                "decision": prediction.decision,
                "decision_reason": prediction.decision_reason,
                "execution": {
                    "order_type": execution.order_type,
                    "requested_usd": execution.requested_usd,
                    "requested_shares": execution.requested_shares,
                    "decision_book_hash": execution.decision_book_hash,
                    "arrival_book_hash": execution.arrival_book_hash,
                    "decision_best_price": execution.decision_best_price,
                    "worst_price_limit": execution.worst_price_limit,
                    "tick_size": execution.tick_size,
                    "minimum_order_size": execution.minimum_order_size,
                    "fee_rate": execution.fee_rate,
                    "fee_exponent": execution.fee_exponent,
                    "simulated_latency_ms": execution.simulated_latency_ms,
                    "filled_shares": execution.filled_shares,
                    "gross_notional": execution.gross_notional,
                    "fee_usd": execution.fee_usd,
                    "net_cash_delta": execution.net_cash_delta,
                    "average_fill_price": execution.average_fill_price,
                    "effective_price": execution.effective_price,
                    "slippage": execution.slippage,
                    "fill_fraction": execution.fill_fraction,
                    "levels_consumed": execution.levels_consumed,
                    "status": execution.status,
                    "reason": execution.reason,
                },
                "resolution": {
                    "status": prediction.resolution_status,
                    "price": prediction.resolution_price,
                    "result": prediction.result,
                    "resolved_at": prediction.resolved_at.isoformat()
                    if prediction.resolved_at
                    else None,
                },
            }
            handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")


def _render_markdown(report: dict[str, Any]) -> str:
    totals = report["totals"]
    portfolio = report["portfolio"]
    accuracy = totals.get("accuracy")
    accuracy_text = "UNPROVEN" if accuracy is None else f"{accuracy * 100:.2f}%"
    return "\n".join(
        [
            "# Sibyl Trace — PAPER V5 Truthful Execution",
            "",
            f"**Status:** `{report['status']}`  ",
            "**Mode:** `PAPER`  ",
            "**LIVE:** `ABSENT`  ",
            f"**Execution model:** `{EXECUTION_MODEL}`  ",
            "**Midpoint fills:** `FALSE`",
            "",
            "## Canonical V5 portfolio",
            "",
            f"- Initial: ${portfolio['initial_bankroll']:.2f}",
            f"- Equity: ${portfolio['equity']:.2f}",
            f"- Realized PnL: ${portfolio['realized_pnl']:.2f}",
            f"- Unrealized PnL: ${portfolio['unrealized_pnl']:.2f}",
            f"- Drawdown: {portfolio['drawdown'] * 100:.2f}%",
            "",
            "## Truth metrics",
            "",
            f"- Predictions observed: {totals['predictions']}",
            f"- Filled or partially filled: {totals['filled_orders']}",
            f"- No-fill: {totals['no_fills']}",
            f"- Rejected: {totals['rejected']}",
            f"- Resolved directional entries: {totals['resolved_directional_entries']}",
            f"- Wins / losses: {totals['wins']} / {totals['losses']}",
            f"- Accuracy: {accuracy_text}",
            "",
            "V2 history is preserved as LEGACY_SIMULATION and is never rewritten into V5.",
            "V5 accuracy excludes rejected, no-fill, unresolved and SELL/exit decisions.",
            "",
        ]
    )


def run(output_dir: Path) -> int:
    started = utcnow()
    settings = get_settings()
    if settings.live_trading_enabled or settings.cost_authorized_usd != 0:
        raise ValueError("unsafe V5 runtime configuration")
    output_dir.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    selected_count = processed_count = resolved_count = settled_count = marked_count = 0
    client = PolymarketClient(settings)
    try:
        init_db()
        with SessionLocal() as db:
            initialize_state(db, settings)
            try:
                geoblock = client.geoblock()
                set_state(db, "geoblock", "blocked" if geoblock.get("blocked") else "clear")
                db.commit()
            except Exception as exc:
                errors.append(f"geoblock:{type(exc).__name__}:{str(exc)[:160]}")

            try:
                selected = scan_wallets(db, client, settings)
                selected_count = len(selected)
            except Exception as exc:
                db.rollback()
                errors.append(f"wallet_scan:{type(exc).__name__}:{str(exc)[:160]}")

            resolved_count, settled_count, settlement_errors = settle_v5(db, client)
            errors.extend(settlement_errors)
            marked_count, mark_errors = mark_positions_v5(db, client)
            errors.extend(mark_errors)

            engine = PaperEngineV5(settings, client)
            processed_count, ingest_errors = ingest_activity_v5(db, client, settings, engine)
            errors.extend(ingest_errors)

            more_resolved, more_settled, settlement_errors = settle_v5(db, client)
            resolved_count += more_resolved
            settled_count += more_settled
            errors.extend(settlement_errors)
            marked_count, mark_errors = mark_positions_v5(db, client)
            errors.extend(mark_errors)

            report = build_report(
                db,
                settings,
                started_at=started,
                selected_count=selected_count,
                processed_count=processed_count,
                resolved_count=resolved_count,
                settled_count=settled_count,
                marked_count=marked_count,
                errors=errors,
            )
            _write_json(output_dir / "paper-v5-summary.json", report)
            (output_dir / "paper-v5-summary.md").write_text(
                _render_markdown(report), encoding="utf-8"
            )
            _write_ledger(db, output_dir / "prediction-ledger-v5.jsonl")
    finally:
        client.close()

    files = [output_dir / "paper-v5-summary.json", output_dir / "prediction-ledger-v5.jsonl"]
    manifest = {
        "schema_version": 5,
        "evidence_generation": EVIDENCE_GENERATION,
        "code_sha": os.getenv("GITHUB_SHA", settings.app_version),
        "execution_model": EXECUTION_MODEL,
        "legacy_history_rewritten": False,
        "cost_policy": {"authorized_usd": 0, "paid_apis": False},
        "live_policy": {"available": False, "real_money": False},
        "files": {
            path.name: hashlib.sha256(path.read_bytes()).hexdigest()
            for path in files
            if path.is_file()
        },
    }
    _write_json(output_dir / "evidence-manifest-v5.json", manifest)
    report = json.loads((output_dir / "paper-v5-summary.json").read_text(encoding="utf-8"))
    return 0 if report.get("status") == "PASS" else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Sibyl Trace truthful PAPER V5")
    parser.add_argument("--output-dir", type=Path, default=Path("paper-v5-output"))
    args = parser.parse_args()
    raise SystemExit(run(args.output_dir))


if __name__ == "__main__":
    main()
