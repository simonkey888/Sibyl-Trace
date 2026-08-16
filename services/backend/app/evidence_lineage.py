from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from app.evidence_v1 import sha256_json

EVIDENCE_LINEAGE_SCHEMA = "EVIDENCE_LINEAGE_V1"
CURRENT = "CURRENT"
STALE_HISTORICAL = "STALE_HISTORICAL"
INCOMPATIBLE = "INCOMPATIBLE"


def parse_rfc3339(value: str) -> int:
    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return int(parsed.timestamp())


def build_evidence_block(
    *,
    source_workflow: str,
    source_run_id: int,
    source_sha: str,
    generated_at: int,
    observed_at: int,
    now: int,
    max_age_seconds: int,
    compatibility_status: str,
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
    age_seconds = max(now - observed_at, 0)
    freshness_status = CURRENT if age_seconds <= max_age_seconds else STALE_HISTORICAL
    effective_status = (
        freshness_status if compatibility_status == "COMPATIBLE" else INCOMPATIBLE
    )
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


def build_evidence_lineage(blocks: dict[str, dict[str, Any]]) -> dict[str, Any]:
    payload = {
        "schema_version": EVIDENCE_LINEAGE_SCHEMA,
        "blocks": blocks,
    }
    validate_evidence_lineage(payload)
    payload["lineage_manifest_hash"] = sha256_json(payload)
    return payload
