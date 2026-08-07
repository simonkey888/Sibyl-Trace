from __future__ import annotations

import argparse
import hashlib
import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
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
from app.models_v5 import (
    PaperV5Execution,
    PaperV5ExecutionEvidence,
    PaperV5Position,
    PaperV5Prediction,
)
from app.paper_v5_r3 import _mark_position_from_book, _status_code
from app.polymarket import PolymarketError
from app.repository import audit, get_state

COHORT_ID = "PAPER_V5_R4_AUDIT_RECONCILIATION_2026_08_07"
EXECUTION_MODEL = "L2_TAKER_FAK_ARRIVAL_BOOK_V2_AUDIT_RECONCILED"


def _canonical_hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def _market_by_condition(client: Any, condition_id: str) -> dict[str, Any]:
    data = client._get(
        f"{client.settings.gamma_api_base}/markets",
        {"condition_ids": [condition_id], "limit": 10},
    )
    rows = (
        data
        if isinstance(data, list)
        else (data.get("markets") or [] if isinstance(data, dict) else [])
    )
    for market in rows:
        if not isinstance(market, dict):
            continue
        current = str(market.get("conditionId") or market.get("condition_id") or "")
        if current == condition_id:
            return market
    raise PolymarketError("Gamma market details did not match requested condition")


def _market_state(market: dict[str, Any]) -> dict[str, Any]:
    return {
        "active": market.get("active"),
        "closed": market.get("closed"),
        "acceptingOrders": market.get("acceptingOrders"),
        "enableOrderBook": market.get("enableOrderBook"),
        "secondsDelay": market.get("secondsDelay"),
    }


def _is_trade_ready(market: dict[str, Any]) -> bool:
    state = _market_state(market)
    return (
        state["active"] is True
        and state["closed"] is not True
        and state["acceptingOrders"] is True
        and state["enableOrderBook"] is not False
    )


def _rules_from_official_metadata(clob_info: dict[str, Any], market: dict[str, Any]):
    raw_delay = market.get("secondsDelay")
    if raw_delay is None:
        raise ValueError("official_seconds_delay_unavailable")
    try:
        seconds_delay = float(raw_delay)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_official_seconds_delay") from exc
    if seconds_delay < 0 or seconds_delay > 300:
        raise ValueError("unsupported_official_seconds_delay")
    info = dict(clob_info)
    info["itode"] = False
    base = market_rules_from_clob_info(info)
    return replace(base, order_delay_ms=int(round(seconds_delay * 1000)))


def _evidence_payload(
    prediction: PaperV5Prediction,
    market: dict[str, Any],
    *,
    decision_book: dict[str, Any] | None,
    arrival_book: dict[str, Any] | None,
    decision_received_at_ms: int | None,
    arrival_received_at_ms: int | None,
    fee_rate_bps_crosscheck: int | None,
) -> tuple[str, str, dict[str, Any]]:
    state = _market_state(market)
    metadata = {
        "condition_id": prediction.condition_id,
        "asset_id": prediction.asset_id,
        "market_state": state,
    }
    metadata_hash = _canonical_hash(metadata)
    evidence = {
        "source_payload_hash": prediction.source_payload_hash,
        "market_metadata_hash": metadata_hash,
        "decision_book_hash": str((decision_book or {}).get("hash") or "") or None,
        "arrival_book_hash": str((arrival_book or {}).get("hash") or "") or None,
        "decision_received_at_ms": decision_received_at_ms,
        "arrival_received_at_ms": arrival_received_at_ms,
        "actual_gap_ms": (
            max(arrival_received_at_ms - decision_received_at_ms, 0)
            if decision_received_at_ms is not None and arrival_received_at_ms is not None
            else None
        ),
        "fee_rate_bps_crosscheck": fee_rate_bps_crosscheck,
    }
    return metadata_hash, _canonical_hash(evidence), state


def _record_evidence(
    db: Session,
    prediction: PaperV5Prediction,
    market: dict[str, Any],
    *,
    decision_book: dict[str, Any] | None = None,
    arrival_book: dict[str, Any] | None = None,
    decision_received_at_ms: int | None = None,
    arrival_received_at_ms: int | None = None,
    fee_rate_bps_crosscheck: int | None = None,
) -> None:
    metadata_hash, evidence_hash, state = _evidence_payload(
        prediction,
        market,
        decision_book=decision_book,
        arrival_book=arrival_book,
        decision_received_at_ms=decision_received_at_ms,
        arrival_received_at_ms=arrival_received_at_ms,
        fee_rate_bps_crosscheck=fee_rate_bps_crosscheck,
    )
    db.merge(
        PaperV5ExecutionEvidence(
            prediction_id=prediction.id,
            market_metadata_hash=metadata_hash,
            execution_evidence_hash=evidence_hash,
            decision_received_at_ms=decision_received_at_ms,
            arrival_received_at_ms=arrival_received_at_ms,
            actual_gap_ms=(
                max(arrival_received_at_ms - decision_received_at_ms, 0)
                if decision_received_at_ms is not None and arrival_received_at_ms is not None
                else None
            ),
            official_seconds_delay=float(state.get("secondsDelay") or 0),
            fee_rate_bps_crosscheck=fee_rate_bps_crosscheck,
            market_active=state.get("active"),
            market_closed=state.get("closed"),
            accepting_orders=state.get("acceptingOrders"),
            enable_order_book=state.get("enableOrderBook"),
        )
    )
    db.commit()


class PaperEngineV5R4(legacy.PaperEngineV5):
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
        db.add(legacy._execution_row(prediction, status="NO_FILL", reason=reason, **kwargs))
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
            market = _market_by_condition(self.client, condition_id)
            if not _is_trade_ready(market):
                self._no_fill(db, prediction, "market_not_trade_ready")
                _record_evidence(db, prediction, market)
                return True
            info = self.client.clob_market_info(condition_id)
            rules = _rules_from_official_metadata(info, market)
        except Exception as exc:
            self._reject(
                db,
                prediction,
                f"market_data_unavailable:{type(exc).__name__}:{str(exc)[:120]}",
            )
            return True

        try:
            fee_bps = self.client.fee_rate_bps(asset_id)
        except Exception:
            fee_bps = None

        try:
            decision_book = self.client.order_book(asset_id)
            decision_received_ms = time.time_ns() // 1_000_000
        except Exception as exc:
            if _status_code(exc) == 404:
                try:
                    latest_market = _market_by_condition(self.client, condition_id)
                except Exception:
                    latest_market = market
                if _is_trade_ready(latest_market):
                    self._reject(
                        db,
                        prediction,
                        "market_data_unavailable:active_market_book_404",
                        rules=rules,
                    )
                else:
                    self._no_fill(
                        db,
                        prediction,
                        "decision_book_unavailable_nontradable",
                        rules=rules,
                    )
                _record_evidence(
                    db,
                    prediction,
                    latest_market,
                    fee_rate_bps_crosscheck=fee_bps,
                )
            else:
                self._reject(
                    db,
                    prediction,
                    f"market_data_unavailable:{type(exc).__name__}:{str(exc)[:120]}",
                    rules=rules,
                )
                _record_evidence(
                    db,
                    prediction,
                    market,
                    fee_rate_bps_crosscheck=fee_bps,
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
            _record_evidence(
                db,
                prediction,
                market,
                decision_book=decision_book,
                decision_received_at_ms=decision_received_ms,
                fee_rate_bps_crosscheck=fee_bps,
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
            _record_evidence(
                db,
                prediction,
                market,
                decision_book=decision_book,
                decision_received_at_ms=decision_received_ms,
                fee_rate_bps_crosscheck=fee_bps,
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
                _record_evidence(
                    db,
                    prediction,
                    market,
                    decision_book=decision_book,
                    decision_received_at_ms=decision_received_ms,
                    fee_rate_bps_crosscheck=fee_bps,
                )
                return True
            requested_shares = min(position.shares, decision.amount_usd / observed)

        if rules.order_delay_ms:
            time.sleep(rules.order_delay_ms / 1000)
        try:
            arrival_book = self.client.order_book(asset_id)
            arrival_received_ms = time.time_ns() // 1_000_000
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
                try:
                    latest_market = _market_by_condition(self.client, condition_id)
                except Exception:
                    latest_market = market
                if _is_trade_ready(latest_market):
                    self._reject(
                        db,
                        prediction,
                        "arrival_book_unavailable:active_market_book_404",
                        **kwargs,
                    )
                else:
                    self._no_fill(
                        db,
                        prediction,
                        "arrival_book_unavailable_nontradable",
                        **kwargs,
                    )
                _record_evidence(
                    db,
                    prediction,
                    latest_market,
                    decision_book=decision_book,
                    decision_received_at_ms=decision_received_ms,
                    fee_rate_bps_crosscheck=fee_bps,
                )
            else:
                self._reject(
                    db,
                    prediction,
                    f"arrival_book_unavailable:{type(exc).__name__}:{str(exc)[:120]}",
                    **kwargs,
                )
                _record_evidence(
                    db,
                    prediction,
                    market,
                    decision_book=decision_book,
                    decision_received_at_ms=decision_received_ms,
                    fee_rate_bps_crosscheck=fee_bps,
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
        execution.simulated_latency_ms = 0
        db.add(execution)
        if fill.status == "NO_FILL":
            prediction.decision = "NO_FILL"
            prediction.decision_reason = fill.reason
            prediction.resolution_status = "NOT_APPLICABLE"
            prediction.result = "NO_FILL"
            db.commit()
            _record_evidence(
                db,
                prediction,
                market,
                decision_book=decision_book,
                arrival_book=arrival_book,
                decision_received_at_ms=decision_received_ms,
                arrival_received_at_ms=arrival_received_ms,
                fee_rate_bps_crosscheck=fee_bps,
            )
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
        metadata_hash, evidence_hash, state_fields = _evidence_payload(
            prediction,
            market,
            decision_book=decision_book,
            arrival_book=arrival_book,
            decision_received_at_ms=decision_received_ms,
            arrival_received_at_ms=arrival_received_ms,
            fee_rate_bps_crosscheck=fee_bps,
        )
        db.add(
            PaperV5ExecutionEvidence(
                prediction_id=prediction.id,
                market_metadata_hash=metadata_hash,
                execution_evidence_hash=evidence_hash,
                decision_received_at_ms=decision_received_ms,
                arrival_received_at_ms=arrival_received_ms,
                actual_gap_ms=max(arrival_received_ms - decision_received_ms, 0),
                official_seconds_delay=float(state_fields.get("secondsDelay") or 0),
                fee_rate_bps_crosscheck=fee_bps,
                market_active=state_fields.get("active"),
                market_closed=state_fields.get("closed"),
                accepting_orders=state_fields.get("acceptingOrders"),
                enable_order_book=state_fields.get("enableOrderBook"),
            )
        )
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


def _status_counts(db: Session) -> dict[str, int]:
    out = {
        "predictions": int(db.scalar(select(func.count()).select_from(PaperV5Prediction)) or 0),
        "executions": int(db.scalar(select(func.count()).select_from(PaperV5Execution)) or 0),
    }
    for status in ("FILLED", "PARTIAL_FILLED", "NO_FILL", "REJECTED"):
        out[status] = int(
            db.scalar(
                select(func.count())
                .select_from(PaperV5Execution)
                .where(PaperV5Execution.status == status)
            )
            or 0
        )
    out["decision_books"] = int(
        db.scalar(
            select(func.count())
            .select_from(PaperV5Execution)
            .where(PaperV5Execution.decision_book_hash.is_not(None))
        )
        or 0
    )
    out["arrival_books"] = int(
        db.scalar(
            select(func.count())
            .select_from(PaperV5Execution)
            .where(PaperV5Execution.arrival_book_hash.is_not(None))
        )
        or 0
    )
    return out


def _write_ledger_r4(original_writer, db: Session, path: Path) -> None:
    original_writer(db, path)
    rewritten: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(raw)
        evidence = db.get(PaperV5ExecutionEvidence, int(row["prediction_id"]))
        row["execution_evidence"] = (
            None
            if evidence is None
            else {
                "market_metadata_hash": evidence.market_metadata_hash,
                "execution_evidence_hash": evidence.execution_evidence_hash,
                "decision_received_at_ms": evidence.decision_received_at_ms,
                "arrival_received_at_ms": evidence.arrival_received_at_ms,
                "actual_gap_ms": evidence.actual_gap_ms,
                "official_seconds_delay": evidence.official_seconds_delay,
                "fee_rate_bps_crosscheck": evidence.fee_rate_bps_crosscheck,
                "market_state": {
                    "active": evidence.market_active,
                    "closed": evidence.market_closed,
                    "acceptingOrders": evidence.accepting_orders,
                    "enableOrderBook": evidence.enable_order_book,
                },
            }
        )
        rewritten.append(json.dumps(row, sort_keys=True, separators=(",", ":")))
    path.write_text("\n".join(rewritten) + ("\n" if rewritten else ""), encoding="utf-8")


def _apply_r4_report(
    report: dict[str, Any], db: Session, baseline: dict[str, int]
) -> dict[str, Any]:
    current = _status_counts(db)
    delta = {key: current[key] - baseline.get(key, 0) for key in current}
    missing_filled_evidence = int(
        db.scalar(
            select(func.count())
            .select_from(PaperV5Execution)
            .outerjoin(
                PaperV5ExecutionEvidence,
                PaperV5ExecutionEvidence.prediction_id == PaperV5Execution.prediction_id,
            )
            .where(
                PaperV5Execution.status.in_(["FILLED", "PARTIAL_FILLED"]),
                PaperV5ExecutionEvidence.prediction_id.is_(None),
            )
        )
        or 0
    )
    processed = int((report.get("cycle") or {}).get("signals_processed") or 0)
    errors: list[str] = []
    if delta["predictions"] != delta["executions"]:
        errors.append(
            f"cycle_prediction_execution_mismatch:{delta['predictions']}:{delta['executions']}"
        )
    if processed != delta["predictions"]:
        errors.append(f"cycle_processed_prediction_mismatch:{processed}:{delta['predictions']}")
    if missing_filled_evidence:
        errors.append(f"filled_execution_missing_evidence:{missing_filled_evidence}")
    report["cohort_id"] = COHORT_ID
    report["methodology"].update(
        {
            "execution_model": EXECUTION_MODEL,
            "official_seconds_delay_source": "Gamma market.secondsDelay",
            "synthetic_canonical_latency": False,
            "actual_request_gap_recorded": True,
            "market_state_404_classification": True,
            "active_tradable_404_is_data_failure": True,
            "fee_schedule_dynamic": True,
            "fee_schedule_source": "CLOB getClobMarketInfo fd",
            "fee_rate_bps_crosscheck": True,
            "execution_evidence_hash": True,
            "summary_ledger_reconciliation": True,
            "legacy_history_rewritten": False,
        }
    )
    report["cycle"].update(
        {
            "new_predictions_created": delta["predictions"],
            "new_executions_created": delta["executions"],
            "new_filled": delta["FILLED"] + delta["PARTIAL_FILLED"],
            "new_no_fill": delta["NO_FILL"],
            "new_rejected": delta["REJECTED"],
            "new_decision_books_reached": delta["decision_books"],
            "new_arrival_books_reached": delta["arrival_books"],
        }
    )
    report["evidence_reconciliation"] = {
        "state": "PASS" if not errors else "FAIL",
        "errors": errors,
        "cycle_delta": delta,
        "filled_execution_missing_evidence": missing_filled_evidence,
    }
    if errors:
        report["status"] = "DEGRADED"
        report["run"]["errors"] = list(report["run"].get("errors") or []) + errors
    return report


def run(output_dir: Path) -> int:
    legacy.init_db()
    with legacy.SessionLocal() as db:
        baseline = _status_counts(db)
    original_cohort = legacy.COHORT_ID
    original_engine = legacy.PaperEngineV5
    original_build = legacy.build_report
    original_writer = legacy._write_ledger
    original_model = legacy.EXECUTION_MODEL

    def build_report_r4(db: Session, *args: Any, **kwargs: Any):
        return _apply_r4_report(original_build(db, *args, **kwargs), db, baseline)

    def write_ledger_r4(db: Session, path: Path):
        return _write_ledger_r4(original_writer, db, path)

    legacy.COHORT_ID = COHORT_ID
    legacy.PaperEngineV5 = PaperEngineV5R4
    legacy.build_report = build_report_r4
    legacy._write_ledger = write_ledger_r4
    legacy.EXECUTION_MODEL = EXECUTION_MODEL
    try:
        return legacy.run(output_dir)
    finally:
        legacy.COHORT_ID = original_cohort
        legacy.PaperEngineV5 = original_engine
        legacy.build_report = original_build
        legacy._write_ledger = original_writer
        legacy.EXECUTION_MODEL = original_model


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Sibyl Trace PAPER V5 R4 audit-reconciled")
    parser.add_argument("--output-dir", type=Path, default=Path("paper-v5-output"))
    args = parser.parse_args()
    raise SystemExit(run(args.output_dir))


if __name__ == "__main__":
    main()
