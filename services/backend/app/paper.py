import hashlib
import json
import time
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.domain import PortfolioState, RiskPolicy, RiskRequest
from app.models import PaperOrder, PaperPosition, PortfolioSnapshot, Signal, Wallet
from app.polymarket import PolymarketClient, PolymarketError
from app.repository import audit, current_portfolio, get_state, set_state


def activity_source_keys(wallet_address: str, activity: dict) -> tuple[str, str]:
    tx_hash = str(activity.get("transactionHash") or "")
    asset_id = str(activity.get("asset") or "")
    side = str(activity.get("side") or "").upper()
    legacy = f"{wallet_address}:{tx_hash}:{asset_id}:{side}"
    identity = {
        "wallet": wallet_address,
        "transaction_hash": tx_hash,
        "asset_id": asset_id,
        "side": side,
        "timestamp": int(activity.get("timestamp") or 0),
        "price": str(activity.get("price") or ""),
        "size": str(activity.get("size") or ""),
        "usdc_size": str(activity.get("usdcSize") or ""),
        "outcome_index": str(activity.get("outcomeIndex") or ""),
    }
    encoded = json.dumps(identity, sort_keys=True, separators=(",", ":"))
    current = f"v2:{hashlib.sha256(encoded.encode()).hexdigest()}"
    return current, legacy


class PaperEngine:
    def __init__(self, settings: Settings, client: PolymarketClient):
        self.settings = settings
        self.client = client
        self.policy = RiskPolicy(
            maximum_signal_age_seconds=settings.risk_max_signal_age_seconds
        )
        self._price_cache: dict[str, float | None] = {}

    def clear_price_cache(self) -> None:
        self._price_cache.clear()

    def _midpoint(self, asset_id: str) -> float:
        if asset_id in self._price_cache:
            cached = self._price_cache[asset_id]
            if cached is None:
                raise PolymarketError("cached midpoint unavailable")
            return cached
        try:
            midpoint = self.client.midpoint(asset_id)
        except Exception:
            self._price_cache[asset_id] = None
            raise
        self._price_cache[asset_id] = midpoint
        return midpoint

    def process_signal(self, db: Session, signal: Signal) -> PaperOrder:
        mode = get_state(db, "mode", self.settings.trading_mode)
        paused = get_state(db, "paused", "false") == "true"
        killed = get_state(db, "kill_switch", "false") == "true"
        if mode != "PAPER" or paused or killed:
            return self._reject(db, signal, "system_not_accepting_orders")

        position = db.get(PaperPosition, signal.asset_id)
        portfolio = current_portfolio(db, self.settings.initial_bankroll_usd)
        asset_shares = position.shares if position else 0.0
        reference_price = (
            position.current_price
            if position is not None and position.current_price > 0
            else signal.source_price
        )
        source_usdc = max(signal.source_usdc, signal.source_size * signal.source_price)
        request = RiskRequest(
            side=signal.side,
            wallet_score=signal.wallet_score,
            signal_age_seconds=max(int(time.time()) - signal.source_timestamp, 0),
            source_price=signal.source_price,
            observed_price=None,
            source_usdc=source_usdc,
        )
        state = PortfolioState(
            equity=portfolio["equity"],
            cash=portfolio["cash"],
            total_exposure=portfolio["exposure"],
            daily_pnl=portfolio["daily_pnl"],
            drawdown=portfolio["drawdown"],
            asset_exposure=asset_shares * max(reference_price, 0),
            asset_shares=asset_shares,
        )
        preflight = self.policy.preflight(request, state)
        if preflight is not None:
            return self._reject(db, signal, preflight.reason)

        try:
            observed_price = self._midpoint(signal.asset_id)
        except Exception as exc:
            audit(db, "price_fetch_failed", str(exc), severity="WARN", signal_id=signal.id)
            return self._reject(db, signal, "price_unavailable")

        state = PortfolioState(
            equity=portfolio["equity"],
            cash=portfolio["cash"],
            total_exposure=portfolio["exposure"],
            daily_pnl=portfolio["daily_pnl"],
            drawdown=portfolio["drawdown"],
            asset_exposure=asset_shares * observed_price,
            asset_shares=asset_shares,
        )
        request = RiskRequest(
            side=signal.side,
            wallet_score=signal.wallet_score,
            signal_age_seconds=request.signal_age_seconds,
            source_price=signal.source_price,
            observed_price=observed_price,
            source_usdc=source_usdc,
        )
        decision = self.policy.evaluate(request, state)
        if not decision.approved:
            return self._reject(db, signal, decision.reason, observed_price)

        fill_price = min(max(observed_price, 0.001), 0.999)
        order = PaperOrder(
            signal_id=signal.id,
            asset_id=signal.asset_id,
            condition_id=signal.condition_id,
            market_title=signal.market_title,
            outcome=signal.outcome,
            side=signal.side,
            requested_usd=decision.amount_usd,
            filled_usd=decision.amount_usd,
            source_price=signal.source_price,
            observed_price=observed_price,
            fill_price=fill_price,
            slippage=fill_price - signal.source_price,
            status="FILLED",
        )
        db.add(order)
        self._apply_fill(db, signal, decision.amount_usd, fill_price)
        signal.decision = "APPROVED"
        signal.decision_reason = "paper_fill"
        db.flush()
        self._snapshot(db)
        audit(
            db,
            "paper_order_filled",
            f"{signal.side} {signal.outcome} for ${decision.amount_usd:.2f}",
            signal_id=signal.id,
            order_id=order.id,
            fill_price=fill_price,
        )
        db.commit()
        return order

    def _apply_fill(self, db: Session, signal: Signal, amount: float, price: float) -> None:
        position = db.get(PaperPosition, signal.asset_id)
        if position is None:
            position = PaperPosition(
                asset_id=signal.asset_id,
                condition_id=signal.condition_id,
                market_title=signal.market_title,
                outcome=signal.outcome,
            )
            db.add(position)
            db.flush()
        position.current_price = price
        if signal.side == "BUY":
            shares = amount / price
            total_cost = position.shares * position.average_price + amount
            position.shares += shares
            position.average_price = total_cost / position.shares if position.shares else 0.0
        else:
            shares = min(amount / price, position.shares)
            proceeds = shares * price
            cost = shares * position.average_price
            position.shares -= shares
            position.realized_pnl += proceeds - cost
            if position.shares <= 1e-9:
                position.shares = 0
                position.average_price = 0
        position.updated_at = datetime.now(UTC)

    def _reject(
        self, db: Session, signal: Signal, reason: str, observed_price: float | None = None
    ) -> PaperOrder:
        order = PaperOrder(
            signal_id=signal.id,
            asset_id=signal.asset_id,
            condition_id=signal.condition_id,
            market_title=signal.market_title,
            outcome=signal.outcome,
            side=signal.side,
            requested_usd=0,
            filled_usd=0,
            source_price=signal.source_price,
            observed_price=observed_price,
            fill_price=None,
            slippage=(
                observed_price - signal.source_price
                if observed_price is not None
                else None
            ),
            status="REJECTED",
            rejection_reason=reason,
        )
        db.add(order)
        signal.decision = "REJECTED"
        signal.decision_reason = reason
        audit(db, "paper_order_rejected", reason, severity="WARN", signal_id=signal.id)
        db.commit()
        return order

    def _snapshot(self, db: Session) -> None:
        portfolio = current_portfolio(db, self.settings.initial_bankroll_usd)
        snapshot_keys = (
            "cash",
            "exposure",
            "equity",
            "realized_pnl",
            "unrealized_pnl",
            "drawdown",
        )
        db.add(PortfolioSnapshot(**{key: portfolio[key] for key in snapshot_keys}))


def ingest_wallet_activity(
    db: Session, client: PolymarketClient, settings: Settings, engine: PaperEngine
) -> int:
    processed = 0
    engine.clear_price_cache()
    wallets = list(db.scalars(select(Wallet).where(Wallet.selected.is_(True))).all())
    for wallet in wallets:
        start = (
            wallet.last_activity_at
            if wallet.last_activity_at
            else int(time.time()) - settings.activity_lookback_seconds
        )
        activities = client.activity(
            wallet.address,
            start=start,
            limit=settings.activity_fetch_limit,
        )
        for activity in activities:
            if activity.get("type") != "TRADE":
                continue
            timestamp = int(activity.get("timestamp") or 0)
            tx_hash = str(activity.get("transactionHash") or "")
            asset_id = str(activity.get("asset") or "")
            side = str(activity.get("side") or "").upper()
            if not timestamp or not tx_hash or not asset_id or side not in {"BUY", "SELL"}:
                continue
            source_key, legacy_key = activity_source_keys(wallet.address, activity)
            existing = db.scalar(
                select(Signal.id).where(Signal.source_key.in_([source_key, legacy_key]))
            )
            if existing:
                wallet.last_activity_at = max(wallet.last_activity_at, timestamp)
                continue
            signal = Signal(
                source_key=source_key,
                wallet_address=wallet.address,
                wallet_score=wallet.score,
                condition_id=str(activity.get("conditionId") or ""),
                asset_id=asset_id,
                market_title=str(activity.get("title") or "Unknown market"),
                outcome=str(activity.get("outcome") or "UNKNOWN"),
                side=side,
                source_price=float(activity.get("price") or 0),
                source_size=float(activity.get("size") or 0),
                source_usdc=float(activity.get("usdcSize") or 0),
                source_timestamp=timestamp,
                transaction_hash=tx_hash,
            )
            db.add(signal)
            wallet.last_activity_at = max(wallet.last_activity_at, timestamp)
            db.flush()
            engine.process_signal(db, signal)
            processed += 1
    set_state(db, "last_watch_at", datetime.now(UTC).isoformat())
    db.commit()
    return processed


def refresh_position_prices(
    db: Session,
    client: PolymarketClient,
    settings: Settings,
) -> int:
    positions = list(
        db.scalars(select(PaperPosition).where(PaperPosition.shares > 0)).all()
    )
    updated = 0
    for position in positions:
        try:
            position.current_price = client.midpoint(position.asset_id)
            position.updated_at = datetime.now(UTC)
            updated += 1
        except Exception as exc:
            audit(
                db,
                "position_mark_failed",
                str(exc),
                severity="WARN",
                asset_id=position.asset_id,
            )
    if updated:
        portfolio = current_portfolio(db, settings.initial_bankroll_usd)
        snapshot_keys = (
            "cash",
            "exposure",
            "equity",
            "realized_pnl",
            "unrealized_pnl",
            "drawdown",
        )
        db.add(PortfolioSnapshot(**{key: portfolio[key] for key in snapshot_keys}))
    db.commit()
    return updated
