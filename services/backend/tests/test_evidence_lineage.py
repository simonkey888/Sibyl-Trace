import pytest

from app.evidence_lineage import (
    CURRENT,
    INCOMPATIBLE,
    STALE_HISTORICAL,
    build_evidence_block,
    build_evidence_lineage,
    validate_evidence_lineage,
)


def block(*, observed_at: int, now: int, compatibility: str = "COMPATIBLE"):
    return build_evidence_block(
        source_workflow="Research V4",
        source_run_id=123,
        source_sha="a" * 40,
        generated_at=observed_at,
        observed_at=observed_at,
        now=now,
        max_age_seconds=100,
        compatibility_status=compatibility,
        content_hash="b" * 64,
    )


def test_current_and_stale_blocks_are_independent():
    v5 = build_evidence_block(
        source_workflow="GitHub PAPER V5 Truthful Execution",
        source_run_id=200,
        source_sha="c" * 40,
        generated_at=1_000,
        observed_at=1_000,
        now=1_050,
        max_age_seconds=100,
        compatibility_status="COMPATIBLE",
        content_hash="d" * 64,
    )
    old_v4 = block(observed_at=100, now=1_050)
    lineage = build_evidence_lineage({"paper_v5": v5, "research_v4": old_v4})
    assert lineage["blocks"]["paper_v5"]["freshness_status"] == CURRENT
    assert lineage["blocks"]["research_v4"]["freshness_status"] == STALE_HISTORICAL
    assert lineage["blocks"]["research_v4"]["effective_status"] == STALE_HISTORICAL


def test_incompatible_block_cannot_be_current():
    incompatible = block(observed_at=1_000, now=1_050, compatibility="INCOMPATIBLE_SCHEMA")
    assert incompatible["freshness_status"] == CURRENT
    assert incompatible["effective_status"] == INCOMPATIBLE


def test_lineage_hash_tampering_fails_closed():
    payload = build_evidence_lineage({"research_v4": block(observed_at=1_000, now=1_050)})
    payload["blocks"]["research_v4"]["source_run_id"] = 999
    with pytest.raises(ValueError, match="hash_invalid"):
        validate_evidence_lineage(payload)
