from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.evidence_v1 import (
    BinancePublicFeed,
    build_github_oos_registration_proof,
    build_prospective_oos_cohort,
    canonicalize_closed_positions,
    classify_lead_lag,
    deterministic_score_payload,
    evaluate_oos_control,
    external_markout,
    history_evidence,
    infer_market_bias,
    make_score_provenance,
    persist_prospective_oos_cohort,
    persist_trusted_oos_registration,
    score_input_hash,
)
from app.scoring import score_matrix


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeHttp:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def get(self, url, params):
        self.calls.append((url, params))
        return FakeResponse(self.payload)


def rows(n: int = 100) -> list[dict]:
    return [
        {
            "realizedPnl": 2.0 if index % 3 else -1.0,
            "timestamp": n - index,
            "transactionHash": f"tx-{index:04d}",
            "conditionId": f"condition-{index:04d}",
            "asset": f"asset-{index:04d}",
            "outcome": "Yes" if index % 2 else "No",
        }
        for index in range(n)
    ]


def db_session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def kline(close_timestamp: int, close: float) -> list:
    return [
        close_timestamp - 999,
        str(close),
        str(close),
        str(close),
        str(close),
        "1",
        close_timestamp,
        "1",
        1,
        "1",
        "1",
        "0",
    ]


def test_history_gate_rejects_exact_limit_without_exhaustion():
    evidence = history_evidence(
        [rows(50), rows(50)],
        requested_limit=100,
        page_size=50,
        source_payload=[rows(50), rows(50)],
    )
    assert evidence.status == "INCOMPLETE"
    assert evidence.scope == "BOUNDED_WINDOW"
    assert evidence.has_more is True
    assert not evidence.authoritative


def test_history_gate_accepts_short_final_page():
    evidence = history_evidence(
        [rows(50), rows(20)],
        requested_limit=100,
        page_size=50,
        source_payload=[rows(50), rows(20)],
    )
    assert evidence.status == "COMPLETE"
    assert evidence.scope == "FULL_AVAILABLE_HISTORY"
    assert evidence.has_more is False


def test_score_provenance_is_reconstructable_and_hashed():
    evidence = history_evidence(
        [rows(20)],
        requested_limit=20,
        page_size=50,
        source_payload=rows(20),
    )
    provenance = make_score_provenance(
        code_sha="abc123",
        source_endpoint="/closed-positions",
        history=evidence,
        short_rows=rows(20),
        long_rows=rows(20),
        decided_row_count=20,
        volume=123.0,
        score=72.5,
        rejection_reason=None,
    )
    assert provenance.schema_version == "SCORE_PROVENANCE_V1"
    assert provenance.code_sha == "abc123"
    assert len(provenance.input_hash) == 64
    assert provenance.source_hash == evidence.source_hash


def test_score_determinism_payload_is_invariant_to_repeated_construction():
    first = deterministic_score_payload(
        short_rows=rows(50),
        long_rows=rows(100),
        volume=100.0,
        score=83.5,
    )
    second = deterministic_score_payload(
        short_rows=rows(50),
        long_rows=rows(100),
        volume=100.0,
        score=83.5,
    )
    assert first == second


def test_score_input_hash_and_score_ignore_source_order_and_rows_after_200():
    original = rows(250)
    shuffled = list(reversed(original))
    canonical = canonicalize_closed_positions(original)
    first_hash = score_input_hash(
        short_rows=original,
        long_rows=original,
        volume=100.0,
        algorithm_version="SCORE_60_40_V1",
    )
    second_hash = score_input_hash(
        short_rows=shuffled,
        long_rows=shuffled,
        volume=100.0,
        algorithm_version="SCORE_60_40_V1",
    )
    assert first_hash == second_hash

    changed_old_tail = list(canonical[:200])
    changed_old_tail.extend(
        {
            **row,
            "realizedPnl": -9999.0,
            "transactionHash": f"old-tail-{index}",
        }
        for index, row in enumerate(canonical[200:])
    )
    with db_session() as db:
        first = score_matrix(db, "0x" + "1" * 40, original, volume=100.0)
        second = score_matrix(db, "0x" + "1" * 40, shuffled, volume=100.0)
        tail_changed = score_matrix(
            db,
            "0x" + "1" * 40,
            changed_old_tail,
            volume=100.0,
        )
    assert first.global_score == second.global_score
    assert first.global_score == tail_changed.global_score
    assert first.long_metrics.closed_count <= 200
    assert first.short_metrics.closed_count <= 50


def test_score_is_deterministic_under_heterogeneous_timestamp_ties():
    tied = rows(80)
    for row in tied:
        row["timestamp"] = 12345
    permutations = [tied, list(reversed(tied)), tied[::2] + tied[1::2]]
    hashes = {
        score_input_hash(
            short_rows=value,
            long_rows=value,
            volume=100.0,
            algorithm_version="SCORE_60_40_V1",
        )
        for value in permutations
    }
    assert len(hashes) == 1
    with db_session() as db:
        scores = {
            score_matrix(db, "0x" + "2" * 40, value, volume=100.0).global_score
            for value in permutations
        }
    assert len(scores) == 1


def test_external_markout_classifies_leading_market_move_without_future_t0():
    base = 100_000.0
    payload = [
        kline(40_000, base * 0.9985),
        kline(100_000, base),
        kline(110_000, base * 1.001),
        kline(130_000, base * 1.003),
        kline(160_000, base * 1.006),
        kline(400_000, base * 1.012),
    ]
    fake = FakeHttp(payload)
    result = external_markout(
        BinancePublicFeed(fake),
        market_title="Bitcoin Up or Down?",
        outcome="Up",
        source_timestamp_ms=100_000,
        entry_price=0.55,
    )
    assert result.status == "VERIFIED"
    assert result.provider == "BINANCE_PUBLIC"
    assert result.markout["60s"] > 0
    assert result.lead_lag == "LEADING"
    assert result.point_timestamps_ms["t0"] <= result.source_timestamp_ms
    assert len(result.raw_response_hash) == 64
    assert fake.calls


def test_external_down_outcome_is_bearish_even_when_title_contains_up_and_down():
    assert infer_market_bias("Bitcoin Up or Down?", "Down") == -1
    assert infer_market_bias("Bitcoin Up or Down?", "Up") == 1


def test_external_t0_refuses_in_progress_future_close():
    payload = [
        kline(99_999, 100.0),
        kline(100_999, 200.0),
        kline(109_999, 99.0),
        kline(129_999, 98.0),
        kline(159_999, 97.0),
    ]
    result = external_markout(
        BinancePublicFeed(FakeHttp(payload)),
        market_title="Will Bitcoin be lower?",
        outcome="Yes",
        source_timestamp_ms=100_000,
        entry_price=0.5,
    )
    assert result.point_timestamps_ms["t0"] == 99_999
    assert result.prices["t0"] == 100.0


def test_external_unknown_market_never_invents_zero_markout():
    fake = FakeHttp([])
    result = external_markout(
        BinancePublicFeed(fake),
        market_title="Will a random event happen?",
        outcome="Yes",
        source_timestamp_ms=100_000,
        entry_price=0.5,
    )
    assert result.status == "UNKNOWN"
    assert result.markout == {}
    assert result.lead_lag == "UNKNOWN"
    assert not fake.calls


def test_external_lead_lag_thresholds_are_explicit():
    assert classify_lead_lag(0.002, 0.004, 1) == "LEADING"
    assert classify_lead_lag(0.0, 0.004, 1) == "LAGGING"
    assert classify_lead_lag(None, 0.004, 1) == "UNKNOWN"


def prospective_cohort():
    return build_prospective_oos_cohort(
        cohort_id="test-oos-001",
        created_at=15,
        selection_cutoff=20,
        algorithm_source_sha="a" * 40,
        algorithm_input_hash="b" * 64,
        treatment_wallets={"treatment"},
        control_definition="all other preregistered eligible wallets",
        feature_contract={"value_key": "realized_pnl", "version": 1},
    )


def trusted_registration(cohort=None, *, created_at="1970-01-01T00:00:15Z"):
    cohort = cohort or prospective_cohort()
    return build_github_oos_registration_proof(
        cohort,
        github_run={
            "id": 100,
            "status": "completed",
            "conclusion": "success",
            "head_sha": cohort.algorithm_source_sha,
            "created_at": "1970-01-01T00:00:10Z",
        },
        github_artifact={
            "id": 200,
            "created_at": created_at,
            "digest": "sha256:" + "c" * 64,
        },
        registered_cohort_payload=cohort.to_dict(),
    )


def test_oos_control_group_requires_preregistered_immutable_membership():
    observations = [
        {"wallet": "treatment", "timestamp": 10, "realized_pnl": 1.0},
        {"wallet": "control", "timestamp": 10, "realized_pnl": 2.0},
        {"wallet": "treatment", "timestamp": 20, "realized_pnl": 5.0},
        {"wallet": "control", "timestamp": 20, "realized_pnl": 1.0},
    ]
    cohort = prospective_cohort()
    result = evaluate_oos_control(
        observations, cohort=cohort, trusted_registration=trusted_registration(cohort)
    )
    assert result.in_sample_count == 2
    assert result.out_of_sample_count == 2
    assert result.control_count == 1
    assert result.treatment_oos_mean == 5.0
    assert result.control_oos_mean == 1.0
    assert result.treatment_vs_control_delta == 4.0
    assert result.treatment_oos_percentile == 1.0
    assert result.status == "VERIFIED"
    assert len(result.membership_hash) == 64
    assert result.trusted_registration_run_id == 100
    assert result.trusted_registration_artifact_id == 200


def test_oos_cohort_cannot_be_created_at_or_after_cutoff():
    with pytest.raises(ValueError, match="created_before_cutoff"):
        build_prospective_oos_cohort(
            cohort_id="retrospective",
            created_at=20,
            selection_cutoff=20,
            algorithm_source_sha="a" * 40,
            algorithm_input_hash="b" * 64,
            treatment_wallets={"winner-after-looking"},
            control_definition="others",
            feature_contract={"version": 1},
        )


def test_oos_cohort_registry_is_write_once(tmp_path: Path):
    path = tmp_path / "cohorts" / "oos.json"
    cohort = prospective_cohort()
    persist_prospective_oos_cohort(path, cohort)
    assert path.is_file()
    with pytest.raises(FileExistsError):
        persist_prospective_oos_cohort(path, cohort)


def test_oos_evaluation_rejects_backdated_local_created_at_without_trusted_registration():
    cohort = prospective_cohort()
    observations = [
        {"wallet": "treatment", "timestamp": 20, "realized_pnl": 5.0},
        {"wallet": "control", "timestamp": 20, "realized_pnl": 1.0},
    ]
    with pytest.raises(ValueError, match="trusted_registration_required"):
        evaluate_oos_control(observations, cohort=cohort)


def test_oos_trusted_registration_must_predate_cutoff():
    cohort = prospective_cohort()
    proof = trusted_registration(cohort, created_at="1970-01-01T00:00:21Z")
    with pytest.raises(ValueError, match="not_before_cutoff"):
        evaluate_oos_control(
            [{"wallet": "treatment", "timestamp": 20, "realized_pnl": 1.0}],
            cohort=cohort,
            trusted_registration=proof,
        )


def test_oos_registration_binding_rejects_changed_membership():
    original = prospective_cohort()
    proof = trusted_registration(original)
    changed = build_prospective_oos_cohort(
        cohort_id="test-oos-002",
        created_at=15,
        selection_cutoff=20,
        algorithm_source_sha="a" * 40,
        algorithm_input_hash="b" * 64,
        treatment_wallets={"different-treatment"},
        control_definition="all other preregistered eligible wallets",
        feature_contract={"value_key": "realized_pnl", "version": 1},
    )
    with pytest.raises(ValueError, match="registration_binding_mismatch"):
        evaluate_oos_control(
            [{"wallet": "different-treatment", "timestamp": 20, "realized_pnl": 1}],
            cohort=changed,
            trusted_registration=proof,
        )


def test_oos_registration_artifact_is_write_once(tmp_path: Path):
    path = tmp_path / "registrations" / "oos.json"
    proof = trusted_registration()
    persist_trusted_oos_registration(path, proof)
    with pytest.raises(FileExistsError):
        persist_trusted_oos_registration(path, proof)


def test_github_registration_rejects_wrong_source_sha():
    cohort = prospective_cohort()
    with pytest.raises(ValueError, match="source_sha_mismatch"):
        build_github_oos_registration_proof(
            cohort,
            github_run={
                "id": 100,
                "status": "completed",
                "conclusion": "success",
                "head_sha": "d" * 40,
                "created_at": "1970-01-01T00:00:10Z",
            },
            github_artifact={
                "id": 200,
                "created_at": "1970-01-01T00:00:15Z",
                "digest": "sha256:" + "c" * 64,
            },
            registered_cohort_payload=cohort.to_dict(),
        )
