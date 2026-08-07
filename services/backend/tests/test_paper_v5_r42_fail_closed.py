from __future__ import annotations

import json

from app.paper_v5_r42 import _write_ledger_r42


class _Rows:
    def all(self):
        return []


class _FakeDb:
    def get(self, _model, _key):
        return None

    def scalars(self, _statement):
        return _Rows()


def test_shadow_execution_hash_cannot_be_hidden_by_missing_provenance(tmp_path):
    path = tmp_path / "ledger.jsonl"

    def original_writer(_db, destination):
        row = {
            "prediction_id": 999,
            "source_price": 0.50,
            "side": "BUY",
            "execution": {
                "status": "NO_FILL",
                "decision_book_hash": "shadow-synthetic-test",
                "arrival_book_hash": None,
                "decision_best_price": 0.51,
                "average_fill_price": None,
                "effective_price": None,
                "filled_shares": 0,
                "fee_usd": 0,
                "fee_rate": 0.05,
                "fee_exponent": 1.0,
            },
        }
        destination.write_text(json.dumps(row) + "\n", encoding="utf-8")

    _write_ledger_r42(original_writer, _FakeDb(), path)
    row = json.loads(path.read_text(encoding="utf-8"))

    assert row["book_provenance"] is None
    assert row["shadow_self_impact_applied"] is True
