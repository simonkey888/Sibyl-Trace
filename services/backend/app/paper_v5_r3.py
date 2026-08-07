from __future__ import annotations

import argparse
import time
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import paper_v5 as legacy
from app.domain import RiskRequest
from app.execution_v5 import (
    best_executable_price,
    market_rules_from_clob_info,
    simulate_fak_fill,
    worst_price_limit,
)
from app.models import Wallet
from app.models_v5 import PaperV5Position, PaperV5Prediction
from app.repository import audit, get_state

COHORT_ID = "PAPER_V5_R3_INTRACYCLE_MARK_2026_08_07"


def _status_code(exc: Exception) -> int | None:
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return int(status) if isinstance(status, int) else None


def _mark_position_from_book(position: PaperV5Position, book: dict[str, Any], rules: Any) -> None:
    if position.shares <= 0:
        position.mark_value_usd = 0
        position.mark_price = 0
        return
    liquidation = simulate_fak_fill(
        book,
        side="SELL",
        fee_rate=rules.fee_rate,
        minimum_order_size=rules.minimum_order_size,
        worst_price=max(rules.tick_size, 0.001),
        requested_shares=position.shares,
    )
    value = max(liquidation.net_cash_delta, 0.0) if liquidation.status != "NO_FILL" else 0.0
    position.mark_value_usd = value
    position.mark_price = value / position.shares if position.shares > 0 else 0.0


class PaperEngineV5R3(legacy.PaperEngineV5):
    def _no_fill(
        self,
        db: Session,
        prediction: PaperV5Prediction,
        reason: str,
        **kwargs: Any,
    ) -> None:
        prediction.decision = "NO_FILL"
        prediction.decision_reason = reason
        prediction.resolution_status = "NOT_APPLICABLE"
        prediction.result = "NO_FILL"
        db.add(
            legacy._execution_row(
                prediction,
                status="NO_FILL",
                reason=reason,
                **kwargs,
            )
        )
        audit(db, "paper_v5_no_fill", reason, prediction_id=prediction.id)
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

        source_key, payload_hash = legacy._source_identity(wallet.address, activity)
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
        state = legacy._portfolio_state(db, self.settings, position)
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
        except Exception as exc:
            self._reject(
                db,
                prediction,
                f"market_data_unavailable:{type(exc).__name__}:{str(exc)[:120]}",
            )
            return True

        try:
            decision_book = self.client.order_book(asset_id)
        except Exception as exc:
            if _status_code(exc) == 404:
                self._no_fill(
                    db,
                    prediction,
                    "decision_book_not_found",
                    rules=rules,
                )
            else:
                self._reject(
                    db,
                    prediction,
                    f"market_data_unavailable:{type(exc).__name__}:{str(exc)[:120]}",
                    rules=rules,
                )
            return True

        observed = best_executable_price(decision_book, side)
        if observed is None:
            self._no_fill(
                db,
                prediction,
                "empty_executable_book",
                decision_book=decision_book,
                rules=rules,
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
            kwargs = {
                "requested_usd": decision.amount_usd,
                "requested_shares": requested_shares,
                "decision_book": decision_book,
                "decision_best_price": observed,
                "worst_price": limit,
                "rules": rules,
            }
            if _status_code(exc) == 404:
                self._no_fill(db, prediction, "arrival_book_not_found", **kwargs)
            else:
                self._reject(
                    db,
                    prediction,
                    f"arrival_book_unavailable:{type(exc).__name__}:{str(exc)[:120]}",
                    **kwargs,
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
        execution = legacy._execution_row(
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

        _mark_position_from_book(position, arrival_book, rules)
        position.updated_at = legacy.utcnow()
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


def run(output_dir: Path) -> int:
    legacy.COHORT_ID = COHORT_ID
    legacy.PaperEngineV5 = PaperEngineV5R3
    return legacy.run(output_dir)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Sibyl Trace truthful PAPER V5 R3")
    parser.add_argument("--output-dir", type=Path, default=Path("paper-v5-output"))
    args = parser.parse_args()
    raise SystemExit(run(args.output_dir))


if __name__ == "__main__":
    main()
