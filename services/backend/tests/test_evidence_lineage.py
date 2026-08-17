import pytest

from app.evidence_lineage import (
    CURRENT,
    INCOMPATIBLE,
    STALE_HISTORICAL,
    build_evidence_block,
    build_evidence_lineage,
    compatibility_contract,
    paper_v5_metadata,
    research_v4_metadata,
    validate_evidence_lineage,
)


def v4_payload():
    return {
        "status": "PASS",
        "evidence_generation": "SIBYL_RESEARCH_V4_OPERATIONAL",
        "edge_status": "UNPROVEN",
        "safety": {
            "mode": "PAPER_SHADOW_ONLY",
            "trading_mode": "PAPER",
            "live_available": False,
            "real_money": False,
            "cost_authorized_usd": 0,
            "paid_apis": False,
            "order_placement": False,
            "private_keys": False,
        },
    }


def block(*, observed_at: int, now: int, payload=None):
    return build_evidence_block(
        source_workflow="Research V4",
        source_run_id=123,
        source_sha="a" * 40,
        generated_at=observed_at,
        observed_at=observed_at,
        now=now,
        max_age_seconds=100,
        source_metadata=research_v4_metadata(payload or v4_payload()),
        consumer_contract=compatibility_contract("research_v4"),
        content_hash="b" * 64,
    )


def test_current_and_stale_blocks_are_independent():
    paper = {
        "cohort_id": "PAPER_V5_R4_5_REGIME_EVIDENCE_2026_08_09",
        "methodology": {
            "source_strategy_gate": True,
            "source_strategy_complete_history_required": True,
            "regime_execution_gate": False,
            "midpoint_fills": False,
        },
        "safety": {
            "live_available": False,
            "order_placement": False,
            "cost_authorized_usd": 0,
        },
    }
    v5 = build_evidence_block(
        source_workflow="GitHub PAPER V5 Truthful Execution",
        source_run_id=200,
        source_sha="c" * 40,
        generated_at=1_000,
        observed_at=1_000,
        now=1_050,
        max_age_seconds=100,
        source_metadata=paper_v5_metadata(paper),
        consumer_contract=compatibility_contract("paper_v5"),
        content_hash="d" * 64,
    )
    old_v4 = block(observed_at=100, now=1_050)
    lineage = build_evidence_lineage({"paper_v5": v5, "research_v4": old_v4})
    assert lineage["blocks"]["paper_v5"]["freshness_status"] == CURRENT
    assert lineage["blocks"]["research_v4"]["freshness_status"] == STALE_HISTORICAL
    assert lineage["blocks"]["research_v4"]["effective_status"] == STALE_HISTORICAL


def test_incompatible_block_is_computed_from_semantics_not_caller_claim():
    payload = v4_payload()
    payload["safety"]["order_placement"] = True
    incompatible = block(observed_at=1_000, now=1_050, payload=payload)
    assert incompatible["freshness_status"] == CURRENT
    assert incompatible["compatibility_status"] == INCOMPATIBLE
    assert incompatible["effective_status"] == INCOMPATIBLE
    assert incompatible["compatibility_reason"] == "compatibility_semantic_contract_mismatch"


def test_missing_compatibility_metadata_fails_closed():
    result = build_evidence_block(
        source_workflow="Research V4",
        source_run_id=123,
        source_sha="a" * 40,
        generated_at=1_000,
        observed_at=1_000,
        now=1_010,
        max_age_seconds=100,
        source_metadata={},
        consumer_contract=compatibility_contract("research_v4"),
        content_hash="b" * 64,
    )
    assert result["compatibility_status"] == INCOMPATIBLE
    assert result["effective_status"] == INCOMPATIBLE


def test_caller_cannot_force_compatibility_status():
    with pytest.raises(TypeError):
        build_evidence_block(
            source_workflow="Research V4",
            source_run_id=123,
            source_sha="a" * 40,
            generated_at=1_000,
            observed_at=1_000,
            now=1_010,
            max_age_seconds=100,
            source_metadata=research_v4_metadata(v4_payload()),
            consumer_contract=compatibility_contract("research_v4"),
            content_hash="b" * 64,
            compatibility_status="COMPATIBLE",
        )


def test_lineage_hash_tampering_fails_closed():
    payload = build_evidence_lineage({"research_v4": block(observed_at=1_000, now=1_050)})
    payload["blocks"]["research_v4"]["source_run_id"] = 999
    with pytest.raises(ValueError, match="hash_invalid"):
        validate_evidence_lineage(payload)


def test_parse_rfc3339_and_unknown_contract_fail_closed():
    from app.evidence_lineage import parse_rfc3339

    assert parse_rfc3339("1970-01-01T00:16:40Z") == 1000
    assert parse_rfc3339("1970-01-01T00:16:40") == 1000
    with pytest.raises(ValueError, match="unknown_evidence_compatibility_kind"):
        compatibility_contract("unknown")


def test_compatibility_rejects_missing_schema_methodology_and_semantic_contract():
    from app.evidence_lineage import compute_compatibility

    contract = compatibility_contract("research_v4")
    assert compute_compatibility(None, contract)["status"] == INCOMPATIBLE
    missing = compute_compatibility({}, contract)
    assert missing["reason"] == "compatibility_required_metadata_missing"

    meta = research_v4_metadata(v4_payload())
    bad_schema = dict(meta, schema_version="UNSUPPORTED")
    assert (
        compute_compatibility(bad_schema, contract)["reason"] == "compatibility_schema_unsupported"
    )
    bad_method = dict(meta, methodology_compatibility_version="UNSUPPORTED")
    assert (
        compute_compatibility(bad_method, contract)["reason"]
        == "compatibility_methodology_unsupported"
    )
    bad_semantic = dict(meta, semantic_contract=None)
    assert (
        compute_compatibility(bad_semantic, contract)["reason"]
        == "compatibility_semantic_contract_missing"
    )


def test_evidence_block_identity_fields_are_strictly_validated():
    base = dict(
        source_workflow="Research V4",
        source_run_id=123,
        source_sha="a" * 40,
        generated_at=1_000,
        observed_at=1_000,
        now=1_010,
        max_age_seconds=100,
        source_metadata=research_v4_metadata(v4_payload()),
        consumer_contract=compatibility_contract("research_v4"),
        content_hash="b" * 64,
    )
    for field, value, reason in (
        ("source_run_id", 0, "source_run_id_invalid"),
        ("source_sha", "bad", "source_sha_invalid"),
        ("generated_at", 0, "timestamp_invalid"),
        ("max_age_seconds", 0, "max_age_invalid"),
        ("content_hash", "bad", "content_hash_invalid"),
    ):
        invalid = dict(base)
        invalid[field] = value
        with pytest.raises(ValueError, match=reason):
            build_evidence_block(**invalid)
