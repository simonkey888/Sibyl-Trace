from pathlib import Path

# Restore protected shared Polymarket client exactly; R4 owns its additive Gamma lookup.
import subprocess
subprocess.run(["git", "checkout", "83aae1a4024645d911b322cd9a16d14dee2ddfeb", "--", "services/backend/app/polymarket.py"], check=True)

p = Path("services/backend/app/paper_v5_r4.py")
text = p.read_text()
anchor = "\ndef _market_state(market: dict[str, Any]) -> dict[str, Any]:\n"
helper = '''\ndef _market_by_condition(client: Any, condition_id: str) -> dict[str, Any]:\n    data = client._get(\n        f"{client.settings.gamma_api_base}/markets",\n        {"condition_ids": [condition_id], "limit": 10},\n    )\n    rows = (\n        data\n        if isinstance(data, list)\n        else (data.get("markets") or [] if isinstance(data, dict) else [])\n    )\n    for market in rows:\n        if not isinstance(market, dict):\n            continue\n        current = str(market.get("conditionId") or market.get("condition_id") or "")\n        if current == condition_id:\n            return market\n    raise legacy.PolymarketError("Gamma market details did not match requested condition")\n\n'''
if "def _market_by_condition" not in text:
    if anchor not in text:
        raise SystemExit("R4 helper anchor missing")
    text = text.replace(anchor, helper + anchor)
text = text.replace("self.client.market_by_condition(condition_id)", "_market_by_condition(self.client, condition_id)")
p.write_text(text)

p = Path("services/backend/tests/test_paper_v5_r4.py")
text = p.read_text()
text = text.replace("import time\n", "import json\nimport time\nfrom types import SimpleNamespace\n")
text = text.replace(
    "from app.paper_v5_r4 import PaperEngineV5R4, _rules_from_official_metadata\n",
    "from app import paper_v5 as legacy\nfrom app.paper_v5_r4 import (\n    PaperEngineV5R4,\n    _apply_r4_report,\n    _canonical_hash,\n    _market_by_condition,\n    _rules_from_official_metadata,\n    _status_counts,\n    _write_ledger_r4,\n)\n",
)
old = '''class FakeClient:\n    def __init__(self, books, market_data=None):\n        self.books = list(books)\n        self.market_data = market_data or market()\n\n    def market_by_condition(self, _condition_id):\n        return dict(self.market_data)\n'''
new = '''class FakeClient:\n    def __init__(self, books, market_data=None):\n        self.books = list(books)\n        self.market_data = market_data or market()\n        self.settings = SimpleNamespace(gamma_api_base="https://gamma.test")\n\n    def _get(self, url, params=None):\n        assert url == "https://gamma.test/markets"\n        assert params == {"condition_ids": ["condition-r4"], "limit": 10}\n        return [dict(self.market_data)]\n'''
if old not in text:
    raise SystemExit("FakeClient anchor missing")
text = text.replace(old, new)
extra = r'''

def test_market_lookup_and_hash_are_deterministic():
    client = FakeClient([])
    resolved = _market_by_condition(client, "condition-r4")
    assert resolved["conditionId"] == "condition-r4"
    first = _canonical_hash({"b": 2, "a": 1})
    second = _canonical_hash({"a": 1, "b": 2})
    assert first == second
    assert len(first) == 64

    client.market_data = {"conditionId": "other"}
    with pytest.raises(Exception, match="did not match requested condition"):
        _market_by_condition(client, "condition-r4")


def test_r4_report_reconciles_cycle_counts_and_ledger(tmp_path, monkeypatch):
    local = factory()
    client = FakeClient(
        [
            book(asks=[(0.51, 100)], bids=[(0.49, 100)], suffix="1"),
            book(asks=[(0.51, 100)], bids=[(0.48, 100)], suffix="2"),
        ]
    )
    monkeypatch.setattr("app.paper_v5_r4.time.sleep", lambda _seconds: None)
    with local() as db:
        initialize_state(db, settings())
        wallet = add_wallet(db)
        baseline = _status_counts(db)
        assert baseline["predictions"] == 0
        PaperEngineV5R4(settings(), client).process(db, wallet, activity("0xreconcile"))
        current = _status_counts(db)
        assert current["predictions"] == current["executions"] == 1
        assert current["FILLED"] + current["PARTIAL_FILLED"] == 1
        assert current["decision_books"] == current["arrival_books"] == 1

        report = {
            "status": "PASS",
            "run": {"errors": []},
            "methodology": {"midpoint_fills": False},
            "cycle": {"signals_processed": 1},
        }
        reconciled = _apply_r4_report(report, db, baseline)
        assert reconciled["status"] == "PASS"
        assert reconciled["evidence_reconciliation"]["state"] == "PASS"
        assert reconciled["cycle"]["new_predictions_created"] == 1
        assert reconciled["cycle"]["new_executions_created"] == 1
        assert reconciled["methodology"]["synthetic_canonical_latency"] is False
        assert reconciled["methodology"]["summary_ledger_reconciliation"] is True

        ledger = tmp_path / "ledger.jsonl"
        _write_ledger_r4(legacy._write_ledger, db, ledger)
        rows = [json.loads(line) for line in ledger.read_text().splitlines()]
        assert len(rows) == 1
        evidence = rows[0]["execution_evidence"]
        assert len(evidence["execution_evidence_hash"]) == 64
        assert evidence["actual_gap_ms"] >= 0
        assert evidence["market_state"]["acceptingOrders"] is True


def test_reconciliation_fails_closed_on_cycle_count_contradiction():
    local = factory()
    with local() as db:
        initialize_state(db, settings())
        report = {
            "status": "PASS",
            "run": {"errors": []},
            "methodology": {},
            "cycle": {"signals_processed": 1},
        }
        reconciled = _apply_r4_report(report, db, _status_counts(db))
        assert reconciled["status"] == "DEGRADED"
        assert reconciled["evidence_reconciliation"]["state"] == "FAIL"
        assert any("cycle_processed_prediction_mismatch" in error for error in reconciled["run"]["errors"])
'''
if "test_market_lookup_and_hash_are_deterministic" not in text:
    text += extra
p.write_text(text)
