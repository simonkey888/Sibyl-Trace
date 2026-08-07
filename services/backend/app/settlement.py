import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import PaperPosition, PortfolioSnapshot
from app.polymarket import PolymarketClient
from app.repository import audit, current_portfolio
from app.settlement_models import PaperSettlement


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


def _condition_id(market: dict) -> str:
    return str(market.get("conditionId") or market.get("condition_id") or "")


def _resolved_price(market: dict, asset_id: str) -> float | None:
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


def _settle_shares(
    position: PaperPosition,
    settlement_price: float,
) -> tuple[float, float, float, float]:
    shares = max(position.shares, 0.0)
    cost_basis = shares * max(position.average_price, 0.0)
    proceeds = shares * settlement_price
    realized_pnl = proceeds - cost_basis
    position.current_price = settlement_price
    position.realized_pnl += realized_pnl
    position.shares = 0
    position.average_price = 0
    position.updated_at = datetime.now(UTC)
    return shares, cost_basis, proceeds, realized_pnl


def settle_closed_positions(
    db: Session,
    client: PolymarketClient,
    settings: Settings,
) -> int:
    positions = list(
        db.scalars(select(PaperPosition).where(PaperPosition.shares > 0)).all()
    )
    if not positions:
        return 0

    condition_ids = list(dict.fromkeys(position.condition_id for position in positions))
    markets = client.closed_markets(condition_ids)
    by_condition = {
        _condition_id(market): market
        for market in markets
        if market.get("closed") is True and _condition_id(market)
    }

    settled = 0
    for position in positions:
        existing = db.get(PaperSettlement, position.asset_id)
        if existing is not None:
            shares, cost_basis, proceeds, realized_pnl = _settle_shares(
                position,
                existing.settlement_price,
            )
            existing.shares += shares
            existing.cost_basis += cost_basis
            existing.proceeds += proceeds
            existing.realized_pnl += realized_pnl
            audit(
                db,
                "paper_position_reopened_after_settlement",
                "Residual PAPER shares were reconciled at the recorded terminal price",
                severity="WARN",
                asset_id=position.asset_id,
                condition_id=position.condition_id,
                shares=round(shares, 8),
                proceeds=round(proceeds, 6),
                realized_pnl=round(realized_pnl, 6),
            )
            settled += 1
            continue

        market = by_condition.get(position.condition_id)
        if market is None:
            continue
        settlement_price = _resolved_price(market, position.asset_id)
        if settlement_price is None:
            audit(
                db,
                "paper_settlement_deferred",
                "Closed market did not expose a terminal token price",
                severity="WARN",
                asset_id=position.asset_id,
                condition_id=position.condition_id,
            )
            continue

        shares, cost_basis, proceeds, realized_pnl = _settle_shares(
            position,
            settlement_price,
        )
        db.add(
            PaperSettlement(
                asset_id=position.asset_id,
                condition_id=position.condition_id,
                market_title=position.market_title,
                outcome=position.outcome,
                settlement_price=settlement_price,
                shares=shares,
                proceeds=proceeds,
                cost_basis=cost_basis,
                realized_pnl=realized_pnl,
            )
        )
        audit(
            db,
            "paper_position_settled",
            f"{position.outcome} settled at {settlement_price:.0f}",
            asset_id=position.asset_id,
            condition_id=position.condition_id,
            proceeds=round(proceeds, 6),
            realized_pnl=round(realized_pnl, 6),
        )
        settled += 1

    if settled:
        db.flush()
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
    return settled
