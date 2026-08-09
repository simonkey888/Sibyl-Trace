from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import paper_v5 as legacy
from app import paper_v5_r4 as r4
from app import paper_v5_r42 as r42
from app.models import AuditEvent, Wallet
from app.models_v5 import (
    PaperV5Execution,
    PaperV5ExecutionEvidence,
    PaperV5Prediction,
)
from app.repository import audit, get_state, set_state

COHORT_ID = "PAPER_V5_R4_3_PROSPECTIVE_TRUTH_2026_08_08"
EXECUTION_MODEL = (
    "L2_TAKER_FAK_ARRIVAL_BOOK_V5_PROSPECTIVE_SELECTION_SHADOW_MARK"
)
SELECTION_EVENT = "paper_v5_r43_selection_provenance"
CYCLE_SELECTION_EFFECTIVE_STATE = "paper_v5_cycle_selection_effective_at"
ACTIVE_SELECTION_STATE = "paper_v5_cycle_selected_wallets"
NEXT_SELECTION_STATE = "paper_v5_next_selected_wallets"
_SELECTION_MATERIAL_KEYS = (
    "prediction_id",
    "wallet",
    "wallet_score",
    "selection_effective_at",
    "source_timestamp",
    "prospective_selection",
)

_apply_r42_report_base = r42._apply_r42_report
_write_ledger_r42_base = r42._write_ledger_r42


def _state_int(db: Session, key: str) -> int:
    try:
        return int(get_state(db, key, "0") or 0)
    except (TypeError, ValueError):
        return 0


def _state_json_list(db: Session, key: str) -> list[dict[str, Any]]:
    try:
        value = json.loads(get_state(db, key, "[]") or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _selection_material(payload: dict[str, Any]) -> dict[str, Any]:
    return {key: payload.get(key) for key in _SELECTION_MATERIAL_KEYS}


def _selection_payload(
    prediction: PaperV5Prediction,
    wallet: Wallet,
    selection_effective_at: int,
) -> dict[str, Any]:
    material = {
        "prediction_id": prediction.id,
        "wallet": wallet.address,
        "wallet_score": prediction.wallet_score,
        "selection_effective_at": selection_effective_at,
        "source_timestamp": prediction.source_timestamp,
        "prospective_selection": True,
    }
    return {
        **material,
        "selection_provenance_hash": r4._canonical_hash(material),
    }


def _selection_payload_hash_valid(payload: dict[str, Any]) -> bool:
    claimed = str(payload.get("selection_provenance_hash") or "")
    return len(claimed) == 64 and claimed == r4._canonical_hash(
        _selection_material(payload)
    )


def _selection_evidence_binding_valid(
    payload: dict[str, Any], evidence_hash: str | None
) -> bool:
    parent = str(payload.get("r4_2_execution_evidence_hash") or "")
    claimed = str(payload.get("r4_3_execution_evidence_hash") or "")
    if not parent and not claimed and evidence_hash is None:
        return True
    if len(parent) != 64 or len(claimed) != 64 or len(str(evidence_hash or "")) != 64:
        return False
    expected = r4._canonical_hash(
        {
            "r4_2_execution_evidence_hash": parent,
            "selection_provenance_hash": payload.get("selection_provenance_hash"),
        }
    )
    return claimed == expected == evidence_hash


def _bridge_selection_evidence_hash(
    db: Session,
    prediction: PaperV5Prediction,
    selection_provenance: dict[str, Any],
) -> dict[str, Any]:
    payload = dict(selection_provenance)
    evidence = db.get(PaperV5ExecutionEvidence, prediction.id)
    if evidence is None:
        payload["r4_2_execution_evidence_hash"] = None
        payload["r4_3_execution_evidence_hash"] = None
        return payload
    parent = evidence.execution_evidence_hash
    bridged = r4._canonical_hash(
        {
            "r4_2_execution_evidence_hash": parent,
            "selection_provenance_hash": payload["selection_provenance_hash"],
        }
    )
    evidence.execution_evidence_hash = bridged
    db.add(evidence)
    payload["r4_2_execution_evidence_hash"] = parent
    payload["r4_3_execution_evidence_hash"] = bridged
    return payload


class PaperEngineV5R43(r42.PaperEngineV5R42):
    """R4.2 execution realism plus point-in-time prospective source selection."""

    @property
    def mark_client(self) -> Any:
        # The proxy retains run-local shadow debt after each signal finishes.
        return self._truth_client

    def process(self, db: Session, wallet: Wallet, activity: dict[str, Any]) -> bool:
        source_timestamp = int(activity.get("timestamp") or 0)
        selection_effective_at = _state_int(db, CYCLE_SELECTION_EFFECTIVE_STATE)
        if selection_effective_at <= 0 or source_timestamp < selection_effective_at:
            audit(
                db,
                "paper_v5_r43_preselection_activity_ignored",
                "Ignored activity that predates the active prospective wallet selection",
                severity="WARN",
                wallet=wallet.address,
                source_timestamp=source_timestamp,
                selection_effective_at=selection_effective_at,
            )
            db.commit()
            return False

        source_key, _ = r4.legacy._source_identity(wallet.address, activity)
        handled = super().process(db, wallet, activity)
        if not handled:
            return handled
        prediction = db.scalar(
            select(PaperV5Prediction).where(PaperV5Prediction.source_key == source_key)
        )
        if prediction is None:
            return handled
        selection_provenance = _bridge_selection_evidence_hash(
            db,
            prediction,
            _selection_payload(prediction, wallet, selection_effective_at),
        )
        audit(
            db,
            SELECTION_EVENT,
            "Persist point-in-time wallet-selection basis for copied source activity",
            **selection_provenance,
        )
        db.commit()
        return handled


def _selection_provenance_by_prediction(db: Session) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    events = db.scalars(
        select(AuditEvent)
        .where(AuditEvent.event_type == SELECTION_EVENT)
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


def _write_ledger_r43(original_writer: Any, db: Session, path: Path) -> None:
    _write_ledger_r42_base(original_writer, db, path)
    selection_by_prediction = _selection_provenance_by_prediction(db)
    executions = {
        execution.prediction_id: execution
        for execution in db.scalars(select(PaperV5Execution)).all()
    }
    rewritten: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(raw)
        prediction_id = int(row.get("prediction_id") or 0)
        execution = executions.get(prediction_id)
        evidence = row.get("execution_evidence") or {}
        evidence_hash = evidence.get("execution_evidence_hash")
        decision_received = evidence.get("decision_received_at_ms")
        arrival_received = evidence.get("arrival_received_at_ms")
        decision_book_ts = (
            execution.decision_book_timestamp_ms if execution is not None else None
        )
        arrival_book_ts = (
            execution.arrival_book_timestamp_ms if execution is not None else None
        )
        selection = selection_by_prediction.get(prediction_id)
        row["selection_provenance"] = selection
        row["selection_evidence_bound"] = (
            None
            if evidence_hash is None
            else bool(
                selection
                and _selection_payload_hash_valid(selection)
                and _selection_evidence_binding_valid(selection, evidence_hash)
            )
        )
        row["book_timing"] = {
            "timestamp_semantics": (
                "CLOB order-book state timestamp; retained for audit, not treated as HTTP freshness"
            ),
            "decision_book_timestamp_ms": decision_book_ts,
            "decision_received_at_ms": decision_received,
            "decision_state_offset_at_receipt_ms": (
                int(decision_received) - int(decision_book_ts)
                if decision_received is not None and decision_book_ts is not None
                else None
            ),
            "decision_state_age_at_receipt_ms": (
                max(int(decision_received) - int(decision_book_ts), 0)
                if decision_received is not None and decision_book_ts is not None
                else None
            ),
            "arrival_book_timestamp_ms": arrival_book_ts,
            "arrival_received_at_ms": arrival_received,
            "arrival_state_offset_at_receipt_ms": (
                int(arrival_received) - int(arrival_book_ts)
                if arrival_received is not None and arrival_book_ts is not None
                else None
            ),
            "arrival_state_age_at_receipt_ms": (
                max(int(arrival_received) - int(arrival_book_ts), 0)
                if arrival_received is not None and arrival_book_ts is not None
                else None
            ),
        }
        rewritten.append(json.dumps(row, sort_keys=True, separators=(",", ":")))
    path.write_text("\n".join(rewritten) + ("\n" if rewritten else ""), encoding="utf-8")


def _apply_r43_report(
    report: dict[str, Any], db: Session, baseline: dict[str, int]
) -> dict[str, Any]:
    report = _apply_r42_report_base(report, db, baseline)
    selection_by_prediction = _selection_provenance_by_prediction(db)
    prediction_rows = list(db.scalars(select(PaperV5Prediction)).all())
    missing = [row.id for row in prediction_rows if row.id not in selection_by_prediction]
    temporal_violations: list[int] = []
    hash_violations: list[int] = []
    evidence_bridge_violations: list[int] = []
    for row in prediction_rows:
        provenance = selection_by_prediction.get(row.id) or {}
        try:
            effective = int(provenance.get("selection_effective_at") or 0)
        except (TypeError, ValueError):
            effective = 0
        if effective <= 0 or int(row.source_timestamp or 0) < effective:
            temporal_violations.append(row.id)
        if provenance and not _selection_payload_hash_valid(provenance):
            hash_violations.append(row.id)
        evidence = db.get(PaperV5ExecutionEvidence, row.id)
        evidence_hash = evidence.execution_evidence_hash if evidence is not None else None
        if provenance and not _selection_evidence_binding_valid(provenance, evidence_hash):
            evidence_bridge_violations.append(row.id)

    errors: list[str] = []
    if missing:
        errors.append(f"prediction_missing_selection_provenance:{len(missing)}")
    if temporal_violations:
        errors.append(
            f"prediction_predates_selection_effective_at:{len(temporal_violations)}"
        )
    if hash_violations:
        errors.append(f"selection_provenance_hash_mismatch:{len(hash_violations)}")
    if evidence_bridge_violations:
        errors.append(
            f"selection_execution_evidence_bridge_mismatch:{len(evidence_bridge_violations)}"
        )

    report["cohort_id"] = COHORT_ID
    report["methodology"].update(
        {
            "execution_model": EXECUTION_MODEL,
            "prospective_wallet_selection": True,
            "selection_order": (
                "execute previously armed selection -> rescore current public history -> arm next cycle"
            ),
            "preselection_activity_backfill": False,
            "selection_provenance_in_ledger": True,
            "execution_evidence_hash_includes_selection_provenance": True,
            "end_cycle_mark_uses_shadow_client": True,
            "book_state_timestamps_in_ledger": True,
            "book_timestamp_freshness_gate": False,
            "book_timestamp_freshness_semantics": (
                "book timestamp records last upstream state time and is not by itself proof of stale HTTP data"
            ),
        }
    )
    report["selection_provenance"] = {
        "state": "PASS" if not errors else "FAIL",
        "errors": errors,
        "active_selection_effective_at": _state_int(
            db, CYCLE_SELECTION_EFFECTIVE_STATE
        ),
        "active_selection": _state_json_list(db, ACTIVE_SELECTION_STATE),
        "next_selection_effective_at": _state_int(
            db, "paper_v5_selection_effective_at"
        ),
        "next_selection": _state_json_list(db, NEXT_SELECTION_STATE),
        "predictions_with_selection_provenance": len(selection_by_prediction),
        "selection_provenance_hash_mismatches": len(hash_violations),
        "selection_execution_evidence_bridge_mismatches": len(
            evidence_bridge_violations
        ),
    }
    report["selected_wallets"] = report["selection_provenance"]["active_selection"]
    if errors:
        report["status"] = "DEGRADED"
        report["run"]["errors"] = list(report["run"].get("errors") or []) + errors
        reconciliation = report.get("evidence_reconciliation") or {}
        reconciliation["state"] = "FAIL"
        reconciliation["errors"] = list(reconciliation.get("errors") or []) + errors
        report["evidence_reconciliation"] = reconciliation
    return report


def run(output_dir: Path) -> int:
    original_r42_cohort = r42.COHORT_ID
    original_r42_model = r42.EXECUTION_MODEL
    original_r42_engine = r42.PaperEngineV5R42
    original_r42_apply = r42._apply_r42_report
    original_r42_writer = r42._write_ledger_r42
    original_scan = legacy.scan_wallets
    original_ingest = legacy.ingest_activity_v5
    original_mark = legacy.mark_positions_v5
    active_engine: PaperEngineV5R43 | None = None

    def active_selection_only(db: Session, _client: Any, settings: Any):
        active = list(
            db.scalars(
                select(Wallet)
                .where(Wallet.selected.is_(True))
                .order_by(Wallet.score.desc())
                .limit(settings.tracked_wallet_limit)
            ).all()
        )
        effective = _state_int(db, "paper_v5_selection_effective_at")
        if active and effective <= 0:
            raise RuntimeError("prospective_selection_effective_at_missing")
        payload = [legacy._wallet_payload(wallet) for wallet in active]
        set_state(db, CYCLE_SELECTION_EFFECTIVE_STATE, str(effective))
        set_state(db, ACTIVE_SELECTION_STATE, json.dumps(payload, sort_keys=True))
        db.commit()
        return active

    def ingest_then_arm_next(
        db: Session, client: Any, settings: Any, engine: Any
    ) -> tuple[int, list[str]]:
        nonlocal active_engine
        active_engine = engine
        processed, errors = original_ingest(db, client, settings, engine)
        try:
            next_selected = original_scan(db, client, settings, prospective=True)
            next_payload = [legacy._wallet_payload(wallet) for wallet in next_selected]
            set_state(db, NEXT_SELECTION_STATE, json.dumps(next_payload, sort_keys=True))
            db.commit()
        except Exception as exc:
            db.rollback()
            errors.append(
                f"prospective_wallet_scan:{type(exc).__name__}:{str(exc)[:160]}"
            )
        return processed, errors

    def shadow_consistent_mark(db: Session, client: Any):
        if active_engine is not None:
            return original_mark(db, active_engine.mark_client)
        return original_mark(db, client)

    r42.COHORT_ID = COHORT_ID
    r42.EXECUTION_MODEL = EXECUTION_MODEL
    r42.PaperEngineV5R42 = PaperEngineV5R43
    r42._apply_r42_report = _apply_r43_report
    r42._write_ledger_r42 = _write_ledger_r43
    legacy.scan_wallets = active_selection_only
    legacy.ingest_activity_v5 = ingest_then_arm_next
    legacy.mark_positions_v5 = shadow_consistent_mark
    try:
        return r42.run(output_dir)
    finally:
        r42.COHORT_ID = original_r42_cohort
        r42.EXECUTION_MODEL = original_r42_model
        r42.PaperEngineV5R42 = original_r42_engine
        r42._apply_r42_report = original_r42_apply
        r42._write_ledger_r42 = original_r42_writer
        legacy.scan_wallets = original_scan
        legacy.ingest_activity_v5 = original_ingest
        legacy.mark_positions_v5 = original_mark


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run Sibyl Trace PAPER V5 R4.3 prospective truth"
    )
    parser.add_argument("--output-dir", type=Path, default=Path("paper-v5-output"))
    args = parser.parse_args()
    raise SystemExit(run(args.output_dir))


if __name__ == "__main__":
    main()
