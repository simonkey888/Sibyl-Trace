from __future__ import annotations

import argparse
import json
from collections import defaultdict, deque
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import paper_v5_r44 as r44
from app.models import AuditEvent, Wallet
from app.models_v5 import PaperV5Execution, PaperV5ExecutionEvidence, PaperV5Prediction
from app.repository import audit

COHORT_ID = "PAPER_V5_R4_5_REGIME_EVIDENCE_2026_08_09"
EXECUTION_MODEL = (
    "L2_TAKER_FAK_ARRIVAL_BOOK_V7_PROSPECTIVE_DIRECTIONAL_REGIME_EVIDENCE"
)
REGIME_EVENT = "paper_v5_r45_regime_provenance"
MIN_EXPLORATORY_SETTLED = 50
_WEEKDAYS = (
    "MONDAY",
    "TUESDAY",
    "WEDNESDAY",
    "THURSDAY",
    "FRIDAY",
    "SATURDAY",
    "SUNDAY",
)
_REGIME_MATERIAL_KEYS = (
    "source_timestamp",
    "utc_weekday_index",
    "utc_weekday",
    "utc_hour",
    "utc_4h_bucket",
    "weekpart",
)

_apply_r44_report_base = r44._apply_r44_report
_write_ledger_r44_base = r44._write_ledger_r44
_PaperEngineV5R44Base = r44.PaperEngineV5R44


def _regime_context(source_timestamp: int) -> dict[str, Any]:
    timestamp = int(source_timestamp or 0)
    if timestamp <= 0:
        raise ValueError("source_timestamp_required_for_regime_context")
    dt = datetime.fromtimestamp(timestamp, UTC)
    weekday_index = dt.weekday()
    bucket_start = (dt.hour // 4) * 4
    bucket_end = bucket_start + 3
    material = {
        "source_timestamp": timestamp,
        "utc_weekday_index": weekday_index,
        "utc_weekday": _WEEKDAYS[weekday_index],
        "utc_hour": dt.hour,
        "utc_4h_bucket": f"{bucket_start:02d}-{bucket_end:02d}",
        "weekpart": "WEEKEND" if weekday_index >= 5 else "WEEKDAY",
    }
    return {
        **material,
        "regime_context_hash": r44.r43.r4._canonical_hash(material),
    }


def _regime_context_hash_valid(context: dict[str, Any]) -> bool:
    if not isinstance(context, dict):
        return False
    claimed = str(context.get("regime_context_hash") or "")
    material = {key: context.get(key) for key in _REGIME_MATERIAL_KEYS}
    return len(claimed) == 64 and claimed == r44.r43.r4._canonical_hash(material)


def _regime_binding_valid(payload: dict[str, Any], evidence_hash: str | None) -> bool:
    context = payload.get("regime_context") or {}
    if not _regime_context_hash_valid(context):
        return False
    parent = str(payload.get("r4_4_execution_evidence_hash") or "")
    claimed = str(payload.get("r4_5_execution_evidence_hash") or "")
    if not parent and not claimed and evidence_hash is None:
        return True
    if len(parent) != 64 or len(claimed) != 64 or len(str(evidence_hash or "")) != 64:
        return False
    expected = r44.r43.r4._canonical_hash(
        {
            "r4_4_execution_evidence_hash": parent,
            "regime_context_hash": context.get("regime_context_hash"),
        }
    )
    return claimed == expected == evidence_hash


def _bridge_regime_evidence(
    db: Session,
    prediction: PaperV5Prediction,
    context: dict[str, Any],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "prediction_id": prediction.id,
        "regime_context": context,
    }
    evidence = db.get(PaperV5ExecutionEvidence, prediction.id)
    if evidence is None:
        payload["r4_4_execution_evidence_hash"] = None
        payload["r4_5_execution_evidence_hash"] = None
        return payload
    parent = evidence.execution_evidence_hash
    child = r44.r43.r4._canonical_hash(
        {
            "r4_4_execution_evidence_hash": parent,
            "regime_context_hash": context["regime_context_hash"],
        }
    )
    evidence.execution_evidence_hash = child
    db.add(evidence)
    payload["r4_4_execution_evidence_hash"] = parent
    payload["r4_5_execution_evidence_hash"] = child
    return payload


class PaperEngineV5R45(_PaperEngineV5R44Base):
    """R4.4 truth gates plus immutable prospective regime labeling.

    R4.5 deliberately does not change execution eligibility from weekday/hour
    observations. Regime labels are evidence for later out-of-sample tests only.
    """

    def process(self, db: Session, wallet: Wallet, activity: dict[str, Any]) -> bool:
        source_key, _ = r44.r43.r4.legacy._source_identity(wallet.address, activity)
        handled = super().process(db, wallet, activity)
        prediction = db.scalar(
            select(PaperV5Prediction).where(PaperV5Prediction.source_key == source_key)
        )
        if prediction is None:
            if handled:
                raise RuntimeError("regime_prediction_missing_after_execution")
            return False

        evidence = db.get(PaperV5ExecutionEvidence, prediction.id)
        evidence_hash = evidence.execution_evidence_hash if evidence is not None else None
        existing = _regime_by_prediction(db).get(prediction.id)
        if existing is not None:
            if not _regime_binding_valid(existing, evidence_hash):
                raise RuntimeError("existing_regime_provenance_invalid")
            return handled

        # A prior attempt can commit the inherited R4.4 truth chain and then fail
        # before the R4.5 event is committed. Base dedupe returns False on retry;
        # repair only when the exact R4.4 terminal parent is still independently
        # valid. This prevents a transient R4.5 failure from wedging the cohort.
        strategy = r44._strategy_by_prediction(db).get(prediction.id)
        if strategy is None or not r44._strategy_binding_valid(strategy, evidence_hash):
            raise RuntimeError("regime_provenance_requires_valid_r4_4_parent")

        context = _regime_context(int(prediction.source_timestamp or 0))
        payload = _bridge_regime_evidence(db, prediction, context)
        audit(
            db,
            REGIME_EVENT,
            "Bind immutable UTC regime context to copied prediction",
            repaired_after_dedupe=not handled,
            **payload,
        )
        db.commit()
        return handled


def _regime_by_prediction(db: Session) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    events = db.scalars(
        select(AuditEvent)
        .where(AuditEvent.event_type == REGIME_EVENT)
        .order_by(AuditEvent.id)
    ).all()
    for event in events:
        try:
            payload = json.loads(event.payload_json)
            prediction_id = int(payload.get("prediction_id") or 0)
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if prediction_id > 0:
            out[prediction_id] = payload
    return out


def _temporary_r44_evidence_parents(
    db: Session, provenance: dict[int, dict[str, Any]]
) -> dict[int, str | None]:
    saved: dict[int, str | None] = {}
    for prediction_id, payload in provenance.items():
        evidence = db.get(PaperV5ExecutionEvidence, prediction_id)
        if evidence is None:
            continue
        saved[prediction_id] = evidence.execution_evidence_hash
        parent = payload.get("r4_4_execution_evidence_hash")
        if parent:
            evidence.execution_evidence_hash = str(parent)
    return saved


def _restore_r45_evidence(db: Session, saved: dict[int, str | None]) -> None:
    for prediction_id, evidence_hash in saved.items():
        evidence = db.get(PaperV5ExecutionEvidence, prediction_id)
        if evidence is not None:
            evidence.execution_evidence_hash = evidence_hash


def _resolved_directional_observations(db: Session) -> list[dict[str, Any]]:
    rows = db.execute(
        select(PaperV5Prediction, PaperV5Execution).join(
            PaperV5Execution, PaperV5Execution.prediction_id == PaperV5Prediction.id
        )
    ).all()
    observations: list[dict[str, Any]] = []
    for prediction, execution in rows:
        if prediction.side != "BUY":
            continue
        if execution.status not in {"FILLED", "PARTIAL_FILLED"}:
            continue
        if prediction.resolution_status != "RESOLVED" or prediction.resolution_price is None:
            continue
        context = _regime_context(int(prediction.source_timestamp or 0))
        observations.append(
            {
                "prediction_id": prediction.id,
                "asset_id": prediction.asset_id,
                "source_timestamp": int(prediction.source_timestamp or 0),
                "win": prediction.result == "WIN",
                "loss": prediction.result == "LOSS",
                "weekpart": context["weekpart"],
                "utc_hour": context["utc_hour"],
                "utc_4h_bucket": context["utc_4h_bucket"],
            }
        )
    return observations


def _attributable_economic_observations(db: Session) -> list[dict[str, Any]]:
    rows = db.execute(
        select(PaperV5Prediction, PaperV5Execution).join(
            PaperV5Execution, PaperV5Execution.prediction_id == PaperV5Prediction.id
        )
    ).all()
    filled_by_asset: dict[str, list[tuple[PaperV5Prediction, PaperV5Execution]]] = defaultdict(list)
    for prediction, execution in rows:
        if (
            execution.status in {"FILLED", "PARTIAL_FILLED"}
            and float(execution.filled_shares or 0) > 0
        ):
            filled_by_asset[prediction.asset_id].append((prediction, execution))

    observations: list[dict[str, Any]] = []
    for prediction, execution in rows:
        if prediction.side != "BUY":
            continue
        if execution.status not in {"FILLED", "PARTIAL_FILLED"}:
            continue
        if prediction.resolution_status != "RESOLVED" or prediction.resolution_price is None:
            continue
        asset_fills = filled_by_asset.get(prediction.asset_id) or []
        if len(asset_fills) != 1 or asset_fills[0][0].id != prediction.id:
            # Multiple BUYs or any filled SELL/exit create multiple filled
            # executions for the same asset. Without lot accounting their PnL is
            # not exactly attributable to this entry, so exclude it.
            continue
        shares = float(execution.filled_shares or 0)
        if shares <= 0:
            continue
        entry_cost = -float(execution.net_cash_delta or 0)
        if entry_cost <= 0:
            entry_cost = float(execution.gross_notional or 0) + float(execution.fee_usd or 0)
        proceeds = shares * float(prediction.resolution_price)
        pnl = proceeds - entry_cost
        context = _regime_context(int(prediction.source_timestamp or 0))
        observations.append(
            {
                "prediction_id": prediction.id,
                "asset_id": prediction.asset_id,
                "source_timestamp": int(prediction.source_timestamp or 0),
                "pnl": pnl,
                "win": pnl > 0,
                "loss": pnl < 0,
                "weekpart": context["weekpart"],
                "utc_hour": context["utc_hour"],
                "utc_4h_bucket": context["utc_4h_bucket"],
                "attribution_basis": "single_filled_execution_for_asset_no_exit",
            }
        )
    return observations


def _aggregate_directional(
    observations: list[dict[str, Any]], key: str
) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in observations:
        groups[str(row[key])].append(row)
    result: dict[str, Any] = {}
    for label, rows in sorted(groups.items()):
        wins = sum(1 for row in rows if row["win"])
        losses = sum(1 for row in rows if row["loss"])
        decided = wins + losses
        result[label] = {
            "resolved": len(rows),
            "wins": wins,
            "losses": losses,
            "win_rate": wins / decided if decided else None,
        }
    return result


def _aggregate_economic(
    observations: list[dict[str, Any]], key: str
) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in observations:
        groups[str(row[key])].append(row)
    result: dict[str, Any] = {}
    for label, rows in sorted(groups.items()):
        pnl = sum(float(row["pnl"]) for row in rows)
        wins = sum(1 for row in rows if row["win"])
        losses = sum(1 for row in rows if row["loss"])
        result[label] = {
            "attributable_settled": len(rows),
            "economic_wins": wins,
            "economic_losses": losses,
            "net_pnl_usd": round(pnl, 8),
            "mean_pnl_usd": round(pnl / len(rows), 8) if rows else None,
        }
    return result


def _loss_cluster_metrics(observations: list[dict[str, Any]]) -> dict[str, int]:
    ordered = sorted(
        observations,
        key=lambda row: (
            int(row["source_timestamp"]),
            int(row.get("prediction_id") or 0),
        ),
    )
    max_streak = 0
    streak = 0
    loss_times: deque[int] = deque()
    max_losses_60m = 0
    for row in ordered:
        if row["loss"]:
            streak += 1
            max_streak = max(max_streak, streak)
            ts = int(row["source_timestamp"])
            loss_times.append(ts)
            while loss_times and loss_times[0] < ts - 3600:
                loss_times.popleft()
            max_losses_60m = max(max_losses_60m, len(loss_times))
        else:
            streak = 0
    return {
        "max_consecutive_attributable_economic_losses": max_streak,
        "max_attributable_economic_losses_in_rolling_60m": max_losses_60m,
    }


def _regime_analysis_from_observations(
    directional_observations: list[dict[str, Any]],
    economic_observations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if economic_observations is None:
        economic_observations = [row for row in directional_observations if "pnl" in row]
    resolved = len(directional_observations)
    attributable = len(economic_observations)
    return {
        "state": (
            "INSUFFICIENT_EVIDENCE"
            if attributable < MIN_EXPLORATORY_SETTLED
            else "EXPLORATORY_ONLY"
        ),
        "settled_observations": attributable,
        "resolved_directional_observations": resolved,
        "attributable_economic_observations": attributable,
        "unattributable_economic_observations": max(resolved - attributable, 0),
        "evidence_level_basis": "attributable_economic_observations",
        "economic_attribution_rule": "single filled execution for asset and no filled exit",
        "minimum_settled_for_exploratory_breakdown": MIN_EXPLORATORY_SETTLED,
        "automatic_execution_gate": False,
        "out_of_sample_confirmation_required": True,
        "weekday_weekend_claim_verified": False,
        "time_of_day_claim_verified": False,
        "naive_strategy_inversion_allowed": False,
        "naive_strategy_inversion_reason": (
            "opposite-side profitability requires opposite executable price, depth, fees and settlement economics"
        ),
        "directional_by_weekpart": _aggregate_directional(
            directional_observations, "weekpart"
        ),
        "directional_by_utc_4h_bucket": _aggregate_directional(
            directional_observations, "utc_4h_bucket"
        ),
        "economic_by_weekpart": _aggregate_economic(economic_observations, "weekpart"),
        "economic_by_utc_4h_bucket": _aggregate_economic(
            economic_observations, "utc_4h_bucket"
        ),
        "loss_clustering": _loss_cluster_metrics(economic_observations),
    }


def _write_ledger_r45(original_writer: Any, db: Session, path: Path) -> None:
    provenance = _regime_by_prediction(db)
    saved = _temporary_r44_evidence_parents(db, provenance)
    try:
        with db.no_autoflush:
            _write_ledger_r44_base(original_writer, db, path)
    finally:
        _restore_r45_evidence(db, saved)

    rewritten: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(raw)
        prediction_id = int(row.get("prediction_id") or 0)
        payload = provenance.get(prediction_id)
        terminal_hash = saved.get(prediction_id)
        evidence = row.get("execution_evidence") or {}
        if terminal_hash is not None:
            evidence["execution_evidence_hash"] = terminal_hash
            row["execution_evidence"] = evidence
        context = (payload or {}).get("regime_context")
        row["regime_context"] = context
        row["regime_provenance"] = payload
        row["regime_evidence_bound"] = (
            None
            if terminal_hash is None
            else bool(payload and _regime_binding_valid(payload, terminal_hash))
        )
        rewritten.append(json.dumps(row, sort_keys=True, separators=(",", ":")))
    path.write_text("\n".join(rewritten) + ("\n" if rewritten else ""), encoding="utf-8")


def _apply_r45_report(
    report: dict[str, Any], db: Session, baseline: dict[str, int]
) -> dict[str, Any]:
    provenance = _regime_by_prediction(db)
    saved = _temporary_r44_evidence_parents(db, provenance)
    try:
        with db.no_autoflush:
            report = _apply_r44_report_base(report, db, baseline)
    finally:
        _restore_r45_evidence(db, saved)

    predictions = list(db.scalars(select(PaperV5Prediction)).all())
    missing: list[int] = []
    context_mismatch: list[int] = []
    bridge_mismatch: list[int] = []
    for prediction in predictions:
        payload = provenance.get(prediction.id)
        if payload is None:
            missing.append(prediction.id)
            continue
        context = payload.get("regime_context") or {}
        if (
            not _regime_context_hash_valid(context)
            or int(context.get("source_timestamp") or 0) != int(prediction.source_timestamp or 0)
        ):
            context_mismatch.append(prediction.id)
        evidence = db.get(PaperV5ExecutionEvidence, prediction.id)
        evidence_hash = evidence.execution_evidence_hash if evidence is not None else None
        if not _regime_binding_valid(payload, evidence_hash):
            bridge_mismatch.append(prediction.id)

    errors: list[str] = []
    if missing:
        errors.append(f"prediction_missing_regime_provenance:{len(missing)}")
    if context_mismatch:
        errors.append(f"regime_context_hash_or_timestamp_mismatch:{len(context_mismatch)}")
    if bridge_mismatch:
        errors.append(f"regime_execution_evidence_bridge_mismatch:{len(bridge_mismatch)}")

    report["cohort_id"] = COHORT_ID
    report["methodology"].update(
        {
            "execution_model": EXECUTION_MODEL,
            "regime_context_in_ledger": True,
            "regime_context_utc_only": True,
            "execution_evidence_hash_includes_regime_context": True,
            "regime_analysis_settled_only": True,
            "regime_filters_research_only": True,
            "regime_execution_gate": False,
            "weekday_weekend_rule_imported": False,
            "time_of_day_rule_imported": False,
            "naive_strategy_inversion": False,
            "loss_cluster_metrics_settled_only": True,
            "regime_pnl_requires_single_fill_asset_no_exit": True,
            "regime_unattributable_pnl_excluded": True,
            "regime_min_settled_exploratory": MIN_EXPLORATORY_SETTLED,
            "regime_filter_requires_out_of_sample_confirmation": True,
            "regime_provenance_retry_safe": True,
            "loss_cluster_timestamp_ties_deterministic": True,
            "regime_exploratory_threshold_uses_attributable_economics": True,
        }
    )
    report["regime_provenance"] = {
        "state": "PASS" if not errors else "FAIL",
        "errors": errors,
        "prediction_contexts": len(provenance),
        "missing_prediction_contexts": len(missing),
        "context_hash_or_timestamp_mismatches": len(context_mismatch),
        "execution_evidence_bridge_mismatches": len(bridge_mismatch),
    }
    report["regime_analysis"] = _regime_analysis_from_observations(
        _resolved_directional_observations(db),
        _attributable_economic_observations(db),
    )
    if errors:
        report["status"] = "DEGRADED"
        report["run"]["errors"] = list(report["run"].get("errors") or []) + errors
        reconciliation = report.get("evidence_reconciliation") or {}
        reconciliation["state"] = "FAIL"
        reconciliation["errors"] = list(reconciliation.get("errors") or []) + errors
        report["evidence_reconciliation"] = reconciliation
    return report


def run(output_dir: Path) -> int:
    original_cohort = r44.COHORT_ID
    original_model = r44.EXECUTION_MODEL
    original_engine = r44.PaperEngineV5R44
    original_apply = r44._apply_r44_report
    original_writer = r44._write_ledger_r44

    r44.COHORT_ID = COHORT_ID
    r44.EXECUTION_MODEL = EXECUTION_MODEL
    r44.PaperEngineV5R44 = PaperEngineV5R45
    r44._apply_r44_report = _apply_r45_report
    r44._write_ledger_r44 = _write_ledger_r45
    try:
        return r44.run(output_dir)
    finally:
        r44.COHORT_ID = original_cohort
        r44.EXECUTION_MODEL = original_model
        r44.PaperEngineV5R44 = original_engine
        r44._apply_r44_report = original_apply
        r44._write_ledger_r44 = original_writer


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run Sibyl Trace PAPER V5 R4.5 regime evidence"
    )
    parser.add_argument("--output-dir", type=Path, default=Path("paper-v5-output"))
    args = parser.parse_args()
    raise SystemExit(run(args.output_dir))


if __name__ == "__main__":
    main()
