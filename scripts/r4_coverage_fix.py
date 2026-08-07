from pathlib import Path

p = Path("services/backend/tests/test_paper_v5_r4.py")
text = p.read_text()
extra = r'''

def test_official_seconds_delay_invalid_values_fail_closed():
    info = {
        "mts": "0.01",
        "mos": "1",
        "fd": {"r": "0.05", "e": 1, "to": True},
    }
    with pytest.raises(ValueError, match="invalid_official_seconds_delay"):
        _rules_from_official_metadata(info, market(secondsDelay="not-a-number"))
    with pytest.raises(ValueError, match="unsupported_official_seconds_delay"):
        _rules_from_official_metadata(info, market(secondsDelay=-1))
    with pytest.raises(ValueError, match="unsupported_official_seconds_delay"):
        _rules_from_official_metadata(info, market(secondsDelay=301))


def test_sell_without_position_is_rejected_after_real_decision_book():
    local = factory()
    client = FakeClient([book(asks=[(0.51, 100)], bids=[(0.49, 100)], suffix="1")])
    sell = activity("0xsell-none")
    sell["side"] = "SELL"
    with local() as db:
        initialize_state(db, settings())
        wallet = add_wallet(db)
        PaperEngineV5R4(settings(), client).process(db, wallet, sell)
        prediction = db.scalar(select(PaperV5Prediction))
        execution = db.scalar(select(PaperV5Execution))
        evidence = db.scalar(select(PaperV5ExecutionEvidence))
        assert prediction.result == "REJECTED"
        assert execution.status == "REJECTED"
        assert execution.reason == "no_paper_position_to_sell"
        assert execution.decision_book_hash == "r4-book-1"
        assert evidence is not None
        assert evidence.decision_received_at_ms is not None
        assert evidence.arrival_received_at_ms is None
'''
if "test_sell_without_position_is_rejected_after_real_decision_book" not in text:
    text += extra
p.write_text(text)
