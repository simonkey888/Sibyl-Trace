from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.evidence_v1 import sha256_json

EVIDENCE_LINEAGE_SCHEMA = "EVIDENCE_LINEAGE_V2"
CURRENT = "CURRENT"
STALE_HISTORICAL = "STALE_HISTORICAL"
INCOMPATIBLE = "INCOMPATIBLE"


def parse_rfc3339(value: str) -> int:
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp())


def paper_v5_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    method = payload.get("methodology") or {}
    safety = payload.get("safety") or {}
    return {
        "schema_version": "PAPER_V5_R45",
        "methodology_compatibility_version": "PAPER_V5_R45_TRUTH_V1",
        "semantic_contract": {
            "cohort_id": payload.get("cohort_id"),
            "source_strategy_gate": method.get("source_strategy_gate"),
            "source_strategy_complete_history_required": method.get(
                "source_strategy_complete_history_required"
            ),
            "regime_execution_gate": method.get("regime_execution_gate"),
            "midpoint_fills": method.get("midpoint_fills"),
            "live_available": safety.get("live_available"),
            "order_placement": safety.get("order_placement"),
            "cost_authorized_usd": safety.get("cost_authorized_usd"),
        },
    }


def research_v4_metadata(payload: dict[str, Any]) -> dict[str, Any]:
    safety = payload.get("safety") or {}
    return {
        "schema_version": "SIBYL_RESEARCH_V4_OPERATIONAL",
        "methodology_compatibility_version": "RESEARCH_V4_OPERATIONAL_V1",
        "semantic_contract": {
            "evidence_generation": payload.get("evidence_generation"),
            "edge_status": payload.get("edge_status"),
            "mode": safety.get("mode"),
            "trading_mode": safety.get("trading_mode"),
            "live_available": safety.get("live_available"),
            "real_money": safety.get("real_money"),
            "cost_authorized_usd": safety.get("cost_authorized_usd"),
            "paid_apis": safety.get("paid_apis"),
            "order_placement": safety.get("order_placement"),
            "private_keys": safety.get("private_keys"),
        },
    }


def compatibility_contract(kind: str) -> dict[str, Any]:
    if kind == "paper_v5":
        semantic = {
            "cohort_id": "PAPER_V5_R4_5_REGIME_EVIDENCE_2026_08_09",
            "source_strategy_gate": True,
            "source_strategy_complete_history_required": True,
            "regime_execution_gate": False,
            "midpoint_fills": False,
            "live_available": False,
            "order_placement": False,
            "cost_authorized_usd": 0,
        }
        return {
            "contract_id": "PUBLIC_CONSUMER_PAPER_V5_R45_V1",
            "supported_schema_versions": ["PAPER_V5_R45"],
            "supported_methodology_versions": ["PAPER_V5_R45_TRUTH_V1"],
            "required_metadata_fields": [
                "schema_version",
                "methodology_compatibility_version",
                "semantic_contract",
            ],
            "semantic_contract": semantic,
        }
    if kind == "research_v4":
        semantic = {
            "evidence_generation": "SIBYL_RESEARCH_V4_OPERATIONAL",
            "edge_status": "UNPROVEN",
            "mode": "PAPER_SHADOW_ONLY",
            "trading_mode": "PAPER",
            "live_available": False,
            "real_money": False,
            "cost_authorized_usd": 0,
            "paid_apis": False,
            "order_placement": False,
            "private_keys": False,
        }
        return {
            "contract_id": "PUBLIC_CONSUMER_RESEARCH_V4_V1",
            "supported_schema_versions": ["SIBYL_RESEARCH_V4_OPERATIONAL"],
            "supported_methodology_versions": ["RESEARCH_V4_OPERATIONAL_V1"],
            "required_metadata_fields": [
                "schema_version",
                "methodology_compatibility_version",
                "semantic_contract",
            ],
            "semantic_contract": semantic,
        }
    raise ValueError(f"unknown_evidence_compatibility_kind:{kind}")


def compute_compatibility(
    source_metadata: dict[str, Any],
    consumer_contract: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(source_metadata, dict) or not isinstance(consumer_contract, dict):
        return {"status": INCOMPATIBLE, "reason": "compatibility_metadata_missing"}
    required = consumer_contract.get("required_metadata_fields") or []
    missing = [name for name in required if name not in source_metadata]
    if missing:
        return {
            "status": INCOMPATIBLE,
            "reason": "compatibility_required_metadata_missing",
            "missing_fields": sorted(missing),
        }
    schema = source_metadata.get("schema_version")
    if schema not in set(consumer_contract.get("supported_schema_versions") or []):
        return {"status": INCOMPATIBLE, "reason": "compatibility_schema_unsupported"}
    methodology = source_metadata.get("methodology_compatibility_version")
    if methodology not in set(consumer_contract.get("supported_methodology_versions") or []):
        return {
            "status": INCOMPATIBLE,
            "reason": "compatibility_methodology_unsupported",
        }
    observed_semantic = source_metadata.get("semantic_contract")
    expected_semantic = consumer_contract.get("semantic_contract")
    if not isinstance(observed_semantic, dict) or not isinstance(expected_semantic, dict):
        return {
            "status": INCOMPATIBLE,
            "reason": "compatibility_semantic_contract_missing",
        }
    observed_hash = sha256_json(observed_semantic)
    expected_hash = sha256_json(expected_semantic)
    if observed_hash != expected_hash:
        return {
            "status": INCOMPATIBLE,
            "reason": "compatibility_semantic_contract_mismatch",
            "observed_semantic_contract_hash": observed_hash,
            "expected_semantic_contract_hash": expected_hash,
        }
    return {
        "status": "COMPATIBLE",
        "reason": None,
        "observed_semantic_contract_hash": observed_hash,
        "expected_semantic_contract_hash": expected_hash,
    }


def build_evidence_block(
    *,
    source_workflow: str,
    source_run_id: int,
    source_sha: str,
    generated_at: int,
    observed_at: int,
    now: int,
    max_age_seconds: int,
    source_metadata: dict[str, Any],
    consumer_contract: dict[str, Any],
    content_hash: str,
) -> dict[str, Any]:
    if source_run_id <= 0:
        raise ValueError("lineage_source_run_id_invalid")
    if len(source_sha) != 40:
        raise ValueError("lineage_source_sha_invalid")
    if generated_at <= 0 or observed_at <= 0 or now <= 0:
        raise ValueError("lineage_timestamp_invalid")
    if max_age_seconds <= 0:
        raise ValueError("lineage_max_age_invalid")
    if len(content_hash) != 64:
        raise ValueError("lineage_content_hash_invalid")
    compatibility = compute_compatibility(source_metadata, consumer_contract)
    age_seconds = max(now - observed_at, 0)
    freshness_status = CURRENT if age_seconds <= max_age_seconds else STALE_HISTORICAL
    compatibility_status = compatibility["status"]
    effective_status = freshness_status if compatibility_status == "COMPATIBLE" else INCOMPATIBLE
    material = {
        "schema_version": EVIDENCE_LINEAGE_SCHEMA,
        "source_workflow": source_workflow,
        "source_run_id": source_run_id,
        "source_sha": source_sha,
        "generated_at": generated_at,
        "observed_at": observed_at,
        "age_seconds": age_seconds,
        "max_age_seconds": max_age_seconds,
        "freshness_status": freshness_status,
        "compatibility_status": compatibility_status,
        "compatibility_reason": compatibility.get("reason"),
        "compatibility_contract_id": consumer_contract.get("contract_id"),
        "source_schema_version": source_metadata.get("schema_version"),
        "methodology_compatibility_version": source_metadata.get(
            "methodology_compatibility_version"
        ),
        "semantic_contract_hash": sha256_json(source_metadata.get("semantic_contract")),
        "effective_status": effective_status,
        "content_hash": content_hash,
    }
    return {**material, "lineage_hash": sha256_json(material)}


def evidence_block_hash_valid(block: dict[str, Any]) -> bool:
    if not isinstance(block, dict):
        return False
    material = dict(block)
    claimed = str(material.pop("lineage_hash", ""))
    return len(claimed) == 64 and claimed == sha256_json(material)


def validate_evidence_lineage(payload: dict[str, Any]) -> None:
    if payload.get("schema_version") != EVIDENCE_LINEAGE_SCHEMA:
        raise ValueError("evidence_lineage_schema_invalid")
    blocks = payload.get("blocks")
    if not isinstance(blocks, dict) or not blocks:
        raise ValueError("evidence_lineage_blocks_missing")
    for name, block in blocks.items():
        if not evidence_block_hash_valid(block):
            raise ValueError(f"evidence_lineage_hash_invalid:{name}")
        if block.get("compatibility_status") != "COMPATIBLE":
            if block.get("effective_status") != INCOMPATIBLE:
                raise ValueError(f"evidence_lineage_compatibility_mismatch:{name}")
        elif block.get("freshness_status") not in {CURRENT, STALE_HISTORICAL}:
            raise ValueError(f"evidence_lineage_freshness_invalid:{name}")
        elif block.get("effective_status") != block.get("freshness_status"):
            raise ValueError(f"evidence_lineage_effective_status_invalid:{name}")


def build_evidence_lineage(blocks: dict[str, dict[str, Any]]) -> dict[str, Any]:
    payload = {"schema_version": EVIDENCE_LINEAGE_SCHEMA, "blocks": blocks}
    validate_evidence_lineage(payload)
    payload["lineage_manifest_hash"] = sha256_json(payload)
    return payload
