from pathlib import Path

p = Path("services/backend/app/paper_v5_r4.py")
text = p.read_text()
if "from app.polymarket import PolymarketError" not in text:
    text = text.replace(
        "from app.paper_v5_r3 import _mark_position_from_book, _status_code\n",
        "from app.paper_v5_r3 import _mark_position_from_book, _status_code\nfrom app.polymarket import PolymarketError\n",
    )
text = text.replace(
    'raise legacy.PolymarketError("Gamma market details did not match requested condition")',
    'raise PolymarketError("Gamma market details did not match requested condition")',
)
p.write_text(text)

p = Path("services/backend/tests/test_paper_v5_r4.py")
text = p.read_text()
extra = r'''

def test_empty_decision_book_is_evidence_backed_no_fill():
    local = factory()
    client = FakeClient([book(asks=[], bids=[], suffix="1")])
    with local() as db:
        initialize_state(db, settings())
        wallet = add_wallet(db)
        assert PaperEngineV5R4(settings(), client).process(db, wallet, activity("0xempty")) is True
        prediction = db.scalar(select(PaperV5Prediction))
        execution = db.scalar(select(PaperV5Execution))
        evidence = db.scalar(select(PaperV5ExecutionEvidence))
        assert prediction.result == "NO_FILL"
        assert execution.status == "NO_FILL"
        assert execution.reason == "empty_executable_book"
        assert evidence is not None
        assert evidence.decision_received_at_ms is not None
        assert evidence.arrival_received_at_ms is None


def test_large_decision_slippage_is_rejected_before_arrival():
    local = factory()
    client = FakeClient([book(asks=[(0.80, 100)], bids=[(0.79, 100)], suffix="1")])
    with local() as db:
        initialize_state(db, settings())
        wallet = add_wallet(db)
        PaperEngineV5R4(settings(), client).process(db, wallet, activity("0xslip"))
        prediction = db.scalar(select(PaperV5Prediction))
        execution = db.scalar(select(PaperV5Execution))
        evidence = db.scalar(select(PaperV5ExecutionEvidence))
        assert prediction.result == "REJECTED"
        assert execution.status == "REJECTED"
        assert execution.decision_book_hash == "r4-book-1"
        assert execution.arrival_book_hash is None
        assert evidence is not None


def test_active_market_arrival_404_is_data_failure(monkeypatch):
    class Response:
        status_code = 404

    class MissingBook(Exception):
        response = Response()

    class Client(FakeClient):
        def __init__(self):
            super().__init__([book(asks=[(0.51, 100)], bids=[(0.49, 100)], suffix="1")])
            self.calls = 0

        def order_book(self, _asset_id):
            self.calls += 1
            if self.calls == 1:
                return self.books[0]
            raise MissingBook("arrival missing")

    local = factory()
    monkeypatch.setattr("app.paper_v5_r4.time.sleep", lambda _seconds: None)
    with local() as db:
        initialize_state(db, settings())
        wallet = add_wallet(db)
        PaperEngineV5R4(settings(), Client()).process(db, wallet, activity("0xarrival404"))
        prediction = db.scalar(select(PaperV5Prediction))
        execution = db.scalar(select(PaperV5Execution))
        evidence = db.scalar(select(PaperV5ExecutionEvidence))
        assert prediction.result == "REJECTED"
        assert execution.status == "REJECTED"
        assert "active_market_book_404" in execution.reason
        assert execution.decision_book_hash == "r4-book-1"
        assert evidence is not None
        assert evidence.decision_received_at_ms is not None
        assert evidence.arrival_received_at_ms is None


def test_non_paper_mode_fails_before_market_access(monkeypatch):
    local = factory()
    client = FakeClient([])

    def fake_state(_db, key, default=None):
        return "LIVE" if key == "mode" else default

    monkeypatch.setattr("app.paper_v5_r4.get_state", fake_state)
    with local() as db:
        wallet = add_wallet(db)
        PaperEngineV5R4(settings(), client).process(db, wallet, activity("0xmode"))
        execution = db.scalar(select(PaperV5Execution))
        assert execution.status == "REJECTED"
        assert execution.reason == "system_not_in_paper_mode"
'''
if "test_empty_decision_book_is_evidence_backed_no_fill" not in text:
    text += extra
p.write_text(text)
