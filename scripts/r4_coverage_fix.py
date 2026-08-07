from pathlib import Path

p = Path("services/backend/app/paper_v5_r4.py")
text = p.read_text()
dead = '''        if side == "SELL":\n            if position is None or position.shares <= 0:\n                self._reject(db, prediction, "no_paper_position_to_sell")\n                _record_evidence(\n                    db,\n                    prediction,\n                    market,\n                    decision_book=decision_book,\n                    decision_received_at_ms=decision_received_ms,\n                    fee_rate_bps_crosscheck=fee_bps,\n                )\n                return True\n            requested_shares = min(position.shares, decision.amount_usd / observed)\n'''
replacement = '''        if side == "SELL":\n            # SELL-without-position is rejected by RiskPolicy.preflight before market I/O.\n            requested_shares = min(position.shares, decision.amount_usd / observed)\n'''
if dead not in text:
    raise SystemExit("unreachable sell branch anchor missing")
p.write_text(text.replace(dead, replacement))

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


def test_sell_without_position_rejects_before_market_io():
    class NoMarketClient(FakeClient):
        def _get(self, _url, params=None):
            raise AssertionError("market I/O must not run for impossible SELL")

        def order_book(self, _asset_id):
            raise AssertionError("book I/O must not run for impossible SELL")

    local = factory()
    sell = activity("0xsell-none")
    sell["side"] = "SELL"
    with local() as db:
        initialize_state(db, settings())
        wallet = add_wallet(db)
        PaperEngineV5R4(settings(), NoMarketClient([])).process(db, wallet, sell)
        prediction = db.scalar(select(PaperV5Prediction))
        execution = db.scalar(select(PaperV5Execution))
        evidence = db.scalar(select(PaperV5ExecutionEvidence))
        assert prediction.result == "REJECTED"
        assert execution.status == "REJECTED"
        assert execution.reason == "no_paper_position_to_sell"
        assert execution.decision_book_hash is None
        assert evidence is None
'''
if "test_sell_without_position_rejects_before_market_io" not in text:
    text += extra
p.write_text(text)
