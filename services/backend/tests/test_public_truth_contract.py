from app.cloudflare_snapshot_r45 import (
    CANONICAL_PUBLISHER_WORKFLOW,
    COHORT_ID,
    PUBLIC_SNAPSHOT_MAX_AGE_SECONDS,
    SCORE_SEMANTICS,
)
from app.domain import (
    QUALITY_SCORE_ALPHA_CLAIM,
    QUALITY_SCORE_CALIBRATED_PROBABILITY,
    QUALITY_SCORE_EXPECTED_RETURN_CLAIM,
    QUALITY_SCORE_GLOBAL_FORMULA,
    QUALITY_SCORE_KIND,
)


def test_r45_public_truth_contract_is_single_writer_and_time_bounded() -> None:
    assert COHORT_ID == "PAPER_V5_R4_5_REGIME_EVIDENCE_2026_08_09"
    assert CANONICAL_PUBLISHER_WORKFLOW == "publish-cloudflare-terminal-v5.yml"
    assert PUBLIC_SNAPSHOT_MAX_AGE_SECONDS == 10_800


def test_public_score_semantics_match_domain_contract() -> None:
    assert SCORE_SEMANTICS["kind"] == QUALITY_SCORE_KIND
    assert SCORE_SEMANTICS["global_formula"] == QUALITY_SCORE_GLOBAL_FORMULA
    assert SCORE_SEMANTICS["calibrated_probability"] is QUALITY_SCORE_CALIBRATED_PROBABILITY
    assert SCORE_SEMANTICS["expected_return_claim"] is QUALITY_SCORE_EXPECTED_RETURN_CLAIM
    assert SCORE_SEMANTICS["alpha_claim"] is QUALITY_SCORE_ALPHA_CLAIM
    assert SCORE_SEMANTICS["calibrated_probability"] is False
    assert SCORE_SEMANTICS["expected_return_claim"] is False
    assert SCORE_SEMANTICS["alpha_claim"] is False
