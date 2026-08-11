from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db import Base
from app.domain import compute_wallet_metrics, wallet_score
from app.evidence_v1 import BinancePublicFeed, classify_lead_lag, deterministic_score_payload, evaluate_oos_control, external_markout, history_evidence, make_score_provenance
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


def rows(n=100):
    return [{"realizedPnl": 2.0, "timestamp": n - index, "transactionHash": f"tx-{index}"} for index in range(n)]


def db_session() -> Session:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return Session(engine)


def test_history_gate_rejects_exact_limit_without_exhaustion():
    evidence = history_evidence([rows(50), rows(50)], requested_limit=100, page_size=50, source_payload=[rows(50), rows(50)])
    assert evidence.status == "INCOMPLETE"
    assert evidence.scope == "BOUNDED_WINDOW"
    assert evidence.has_more is True
    metrics = compute_wallet_metrics(rows(100))
    score, reason = wallet_score(metrics, history_complete=evidence.authoritative)
    assert score == 0.0
    assert reason == "incomplete_history"


def test_history_gate_accepts_short_final_page():
    evidence = history_evidence([rows(50), rows(20)], requested_limit=100, page_size=50, source_payload=[rows(50), rows(20)])
    assert evidence.status == "COMPLETE"
    assert evidence.scope == "FULL_AVAILABLE_HISTORY"
    assert evidence.has_more is False


def test_score_provenance_is_reconstructable_and_hashed():
    evidence = history_evidence([rows(20)], requested_limit=20, page_size=50, source_payload=rows(20))
    provenance = make_score_provenance(code_sha="abc123", source_endpoint="/closed-positions", history=evidence, short_rows=rows(20), long_rows=rows(20), decided_row_count=20, volume=123.0, score=72.5, rejection_reason=None)
    assert provenance.schema_version == "SCORE_PROVENANCE_V1"
    assert provenance.code_sha == "abc123"
    assert len(provenance.input_hash) == 64
    assert provenance.source_hash == evidence.source_hash


def test_score_determinism_payload_is_invariant_to_repeated_construction():
    first = deterministic_score_payload(short_rows=rows(50), long_rows=rows(100), volume=100.0, score=83.5)
    second = deterministic_score_payload(short_rows=rows(50), long_rows=rows(100), volume=100.0, score=83.5)
    assert first == second


def test_score_matrix_is_order_deterministic():
    original = rows(100)
    shuffled = list(reversed(original))
    with db_session() as db:
        first = score_matrix(db, "0x" + "1" * 40, original, volume=100.0)
        second = score_matrix(db, "0x" + "1" * 40, shuffled, volume=100.0)
    assert first.global_score == second.global_score
    assert first.short_score == second.short_score
    assert first.long_score == second.long_score
    assert first.provenance["input_hash"] == second.provenance["input_hash"]


def test_external_markout_classifies_leading_market_move():
    base = 100_000.0
    payload = []
    for ts, close in [(40000, base * 0.9985), (100000, base), (110000, base * 1.001), (130000, base * 1.003), (160000, base * 1.006), (400000, base * 1.012)]:
        payload.append([ts, str(close), str(close), str(close), str(close), "1", ts + 999, "1", 1, "1", "1", "0"])
    fake = FakeHttp(payload)
    result = external_markout(BinancePublicFeed(fake), market_title="Bitcoin Up or Down?", outcome="Up", source_timestamp_ms=100000, entry_price=0.55)
    assert result.status == "VERIFIED"
    assert result.provider == "BINANCE_PUBLIC"
    assert result.markout["60s"] > 0
    assert result.lead_lag == "LEADING"
    assert fake.calls


def test_external_unknown_market_never_invents_zero_markout():
    fake = FakeHttp([])
    result = external_markout(BinancePublicFeed(fake), market_title="Will a random event happen?", outcome="Yes", source_timestamp_ms=100000, entry_price=0.5)
    assert result.status == "UNKNOWN"
    assert result.markout == {}
    assert result.lead_lag == "UNKNOWN"
    assert not fake.calls


def test_external_lead_lag_thresholds_are_explicit():
    assert classify_lead_lag(0.002, 0.004, 1) == "LEADING"
    assert classify_lead_lag(0.0, 0.004, 1) == "LAGGING"
    assert classify_lead_lag(None, 0.004, 1) == "UNKNOWN"


def test_oos_control_group_is_strictly_time_split():
    observations = [{"wallet": "treatment", "timestamp": 10, "realized_pnl": 1.0}, {"wallet": "control", "timestamp": 10, "realized_pnl": 2.0}, {"wallet": "treatment", "timestamp": 20, "realized_pnl": 5.0}, {"wallet": "control", "timestamp": 20, "realized_pnl": 1.0}]
    result = evaluate_oos_control(observations, cutoff_timestamp=20, treatment_wallets={"treatment"})
    assert result.in_sample_count == 2
    assert result.out_of_sample_count == 2
    assert result.control_count == 1
    assert result.treatment_oos_mean == 5.0
    assert result.control_oos_mean == 1.0
    assert result.treatment_vs_control_delta == 4.0
    assert result.treatment_oos_percentile == 1.0
    assert result.status == "VERIFIED"
