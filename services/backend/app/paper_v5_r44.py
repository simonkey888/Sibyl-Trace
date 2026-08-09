from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import paper_v5_r43 as r43
from app import scanner
from app.models import AuditEvent, Wallet
from app.models_v5 import PaperV5ExecutionEvidence, PaperV5Prediction
from app.repository import audit, get_state, set_state
from app.source_strategy import profile_hash_valid, wallet_hash

COHORT_ID = "PAPER_V5_R4_4_SOURCE_STRATEGY_TRUTH_2026_08_08"
EXECUTION_MODEL = (
    "L2_TAKER_FAK_ARRIVAL_BOOK_V6_PROSPECTIVE_DIRECTIONAL_SOURCE_GATING"
)
STRATEGY_EVENT = "paper_v5_r44_source_strategy_provenance"
ACTIVE_PROFILES_STATE = "paper_v5_r44_active_source_strategy_profiles"
NEXT_PROFILES_STATE = scanner.SOURCE_STRATEGY_PROFILES_STATE

_apply_r43_report_base = r43._apply_r43_report
_write_ledger_r43_base = r43._write_ledger_r43
_PaperEngineV5R43Base = r43.PaperEngineV5R43


def _state_profiles(db: Session, key: str) -> list[dict[str, Any]]:
    try:
        value = json.loads(get_state(db, key, "[]") or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _profile_for_wallet(db: Session, address: str) -> dict[str, Any] | None:
    wanted = wallet_hash(address)
    for profile in _state_profiles(db, ACTIVE_PROFILES_STATE):
        if str(profile.get("wallet_hash") or "") == wanted:
            return profile
    return None


def _strategy_binding_valid(payload: dict[str, Any], evidence_hash: str | None) -> bool:
    profile = payload.get("source_strategy_profile") or {}
    if not isinstance(profile, dict) or not profile_hash_valid(profile):
        return False
    parent = str(payload.get("r4_3_execution_evidence_hash") or "")
    claimed = str(payload.get("r4_4_execution_evidence_hash") or "")
    if not parent and not claimed and evidence_hash is None:
        return True
    if len(parent) != 64 or len(claimed) != 64 or len(str(evidence_hash or "")) != 64:
        return False
    expected = r43.r4._canonical_hash(
        {
            "r4_3_execution_evidence_hash": parent,
            "source_strategy_evidence_hash": profile.get("evidence_hash"),
        }
    )
    return claimed == expected == evidence_hash


def _bridge_strategy_evidence(
    db: Session,
    prediction: PaperV5Prediction,
    profile: dict[str, Any],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "prediction_id": prediction.id,
        "source_strategy_profile": profile,
    }
    evidence = db.get(PaperV5ExecutionEvidence, prediction.id)
    if evidence is None:
        payload["r4_3_execution_evidence_hash"] = None
        payload["r4_4_execution_evidence_hash"] = None
        return payload
    parent = evidence.execution_evidence_hash
    bridged = r43.r4._canonical_hash(
        {
            "r4_3_execution_evidence_hash": parent,
            "source_strategy_evidence_hash": profile["evidence_hash"],
        }
    )
    evidence.execution_evidence_hash = bridged
    db.add(evidence)
    payload["r4_3_execution_evidence_hash"] = parent
    payload["r4_4_execution_evidence_hash"] = bridged
    return payload


class PaperEngineV5R44(_PaperEngineV5R43Base):
    """R4.3 execution plus fail-closed point-in-time source-strategy provenance."""

    def process(self, db: Session, wallet: Wallet, activity: dict[str, Any]) -> bool:
        profile = _profile_for_wallet(db, wallet.address)
        if (
            profile is None
            or profile.get("classification") != "DIRECTIONAL_CANDIDATE"
            or profile.get("directional") is not True
            or not profile_hash_valid(profile)
        ):
            raise RuntimeError("source_strategy_directional_provenance_missing_or_invalid")

        source_key, _ = r43.r4.legacy._source_identity(wallet.address, activity)
        handled = super().process(db, wallet, activity)
        if not handled:
            return handled
        prediction = db.scalar(
            select(PaperV5Prediction).where(PaperV5Prediction.source_key == source_key)
        )
        if prediction is None:
            raise RuntimeError("source_strategy_prediction_missing_after_execution")
        provenance = _bridge_strategy_evidence(db, prediction, profile)
        audit(
            db,
            STRATEGY_EVENT,
            "Bind directional source-strategy evidence to copied prediction",
            **provenance,
        )
        db.commit()
        return handled


def _strategy_by_prediction(db: Session) -> dict[int, dict[str, Any]]:
    out: dict[int, dict[str, Any]] = {}
    events = db.scalars(
        select(AuditEvent)
        .where(AuditEvent.event_type == STRATEGY_EVENT)
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


def _write_ledger_r44(original_writer: Any, db: Session, path: Path) -> None:
    _write_ledger_r43_base(original_writer, db, path)
    provenance = _strategy_by_prediction(db)
    rewritten: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        row = json.loads(raw)
        prediction_id = int(row.get("prediction_id") or 0)
        payload = provenance.get(prediction_id)
        evidence = row.get("execution_evidence") or {}
        evidence_hash = evidence.get("execution_evidence_hash")
        row["source_strategy_provenance"] = payload
        row["source_strategy_evidence_bound"] = (
            None
            if evidence_hash is None
            else bool(payload and _strategy_binding_valid(payload, evidence_hash))
        )
        rewritten.append(json.dumps(row, sort_keys=True, separators=(",", ":")))
    path.write_text("\n".join(rewritten) + ("\n" if rewritten else ""), encoding="utf-8")


def _apply_r44_report(
    report: dict[str, Any], db: Session, baseline: dict[str, int]
) -> dict[str, Any]:
    report = _apply_r43_report_base(report, db, baseline)
    provenance = _strategy_by_prediction(db)
    predictions = list(db.scalars(select(PaperV5Prediction)).all())
    missing: list[int] = []
    invalid_profile: list[int] = []
    non_directional: list[int] = []
    bridge_mismatch: list[int] = []

    for prediction in predictions:
        payload = provenance.get(prediction.id)
        if payload is None:
            missing.append(prediction.id)
            continue
        profile = payload.get("source_strategy_profile") or {}
        if not isinstance(profile, dict) or not profile_hash_valid(profile):
            invalid_profile.append(prediction.id)
            continue
        if (
            profile.get("classification") != "DIRECTIONAL_CANDIDATE"
            or profile.get("directional") is not True
        ):
            non_directional.append(prediction.id)
        evidence = db.get(PaperV5ExecutionEvidence, prediction.id)
        evidence_hash = evidence.execution_evidence_hash if evidence is not None else None
        if not _strategy_binding_valid(payload, evidence_hash):
            bridge_mismatch.append(prediction.id)

    errors: list[str] = []
    if missing:
        errors.append(f"prediction_missing_source_strategy_provenance:{len(missing)}")
    if invalid_profile:
        errors.append(f"source_strategy_profile_hash_mismatch:{len(invalid_profile)}")
    if non_directional:
        errors.append(f"non_directional_source_prediction_present:{len(non_directional)}")
    if bridge_mismatch:
        errors.append(f"source_strategy_execution_evidence_bridge_mismatch:{len(bridge_mismatch)}")

    active_profiles = _state_profiles(db, ACTIVE_PROFILES_STATE)
    next_profiles = _state_profiles(db, NEXT_PROFILES_STATE)
    report["cohort_id"] = COHORT_ID
    report["methodology"].update(
        {
            "execution_model": EXECUTION_MODEL,
            "source_strategy_gate": True,
            "source_strategy_public_activity_only": True,
            "source_strategy_point_in_time_cutoff": True,
            "source_strategy_fail_closed": True,
            "maker_rebate_source_rejected": True,
            "split_merge_conversion_source_rejected": True,
            "repeated_two_sided_source_rejected": True,
            "source_strategy_provenance_in_ledger": True,
            "execution_evidence_hash_includes_source_strategy": True,
            "maker_or_lp_execution_imported": False,
            "live_order_path_added": False,
        }
    )
    report["source_strategy_provenance"] = {
        "state": "PASS" if not errors else "FAIL",
        "errors": errors,
        "active_profiles": active_profiles,
        "next_profiles": next_profiles,
        "prediction_profiles": len(provenance),
        "missing_prediction_profiles": len(missing),
        "profile_hash_mismatches": len(invalid_profile),
        "non_directional_predictions": len(non_directional),
        "execution_evidence_bridge_mismatches": len(bridge_mismatch),
    }
    if errors:
        report["status"] = "DEGRADED"
        report["run"]["errors"] = list(report["run"].get("errors") or []) + errors
        reconciliation = report.get("evidence_reconciliation") or {}
        reconciliation["state"] = "FAIL"
        reconciliation["errors"] = list(reconciliation.get("errors") or []) + errors
        report["evidence_reconciliation"] = reconciliation
    return report


def run(output_dir: Path) -> int:
    original_cohort = r43.COHORT_ID
    original_model = r43.EXECUTION_MODEL
    original_engine = r43.PaperEngineV5R43
    original_apply = r43._apply_r43_report
    original_writer = r43._write_ledger_r43
    original_scan = r43.legacy.scan_wallets

    r43.legacy.init_db()
    with r43.legacy.SessionLocal() as db:
        current_profiles = get_state(db, NEXT_PROFILES_STATE, "[]") or "[]"
        set_state(db, ACTIVE_PROFILES_STATE, current_profiles)
        db.commit()

    def scan_r44(db: Session, client: Any, settings: Any, *, prospective: bool = False):
        return scanner.scan_wallets(
            db,
            client,
            settings,
            prospective=prospective,
            source_strategy_gate=prospective,
        )

    r43.COHORT_ID = COHORT_ID
    r43.EXECUTION_MODEL = EXECUTION_MODEL
    r43.PaperEngineV5R43 = PaperEngineV5R44
    r43._apply_r43_report = _apply_r44_report
    r43._write_ledger_r43 = _write_ledger_r44
    r43.legacy.scan_wallets = scan_r44
    try:
        return r43.run(output_dir)
    finally:
        r43.COHORT_ID = original_cohort
        r43.EXECUTION_MODEL = original_model
        r43.PaperEngineV5R43 = original_engine
        r43._apply_r43_report = original_apply
        r43._write_ledger_r43 = original_writer
        r43.legacy.scan_wallets = original_scan


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run Sibyl Trace PAPER V5 R4.4 source-strategy truth"
    )
    parser.add_argument("--output-dir", type=Path, default=Path("paper-v5-output"))
    args = parser.parse_args()
    raise SystemExit(run(args.output_dir))


if __name__ == "__main__":
    main()
