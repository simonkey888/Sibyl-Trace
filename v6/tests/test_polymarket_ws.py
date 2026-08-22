from __future__ import annotations

import json
import subprocess
import unittest

from sibyl_v6.polymarket_ws import (
    classify_ws_books,
    desired_token_ids,
    fetch_polymarket_ws_snapshot,
)
from sibyl_v6.runtime_market_data import _top_matches, _untimestamped_reconciliation_copy


TOKENS = {"YES": "111", "NO": "222"}


class PolymarketWsFreshnessTests(unittest.TestCase):
    def snapshot(self, *, connected=True, yes_ts=1_000_000, no_ts=1_000_000):
        books = {
            "111": {"bids": [{"price": "0.4", "size": "5"}], "asks": [{"price": "0.5", "size": "5"}], "timestamp": yes_ts},
            "222": {"bids": [{"price": "0.5", "size": "5"}], "asks": [{"price": "0.6", "size": "5"}], "timestamp": no_ts},
        }
        return {
            "connected": connected,
            "event_received": True,
            "books": books,
            "received_at_ms": {"111": 1_000_010, "222": 1_000_011},
            "reconnects": 0,
            "resubscribe_count": 1,
            "pong_count": 0,
            "desired_token_ids": ["111", "222"],
            "error": None,
        }

    def test_exact_binary_token_set_is_sorted_and_required(self):
        self.assertEqual(desired_token_ids(TOKENS), ["111", "222"])
        self.assertEqual(desired_token_ids({"YES": "111"}), [])
        self.assertEqual(desired_token_ids({"YES": "abc", "NO": "222"}), [])

    def test_fresh_books_are_fresh_from_ws_source_timestamps(self):
        state = classify_ws_books(
            self.snapshot(), token_ids=["111", "222"], observed_at_ms=1_000_100
        )
        self.assertEqual(state["status"], "FRESH")
        self.assertEqual(state["per_token"]["111"]["status"], "FRESH")
        self.assertEqual(state["per_token"]["222"]["status"], "FRESH")

    def test_stale_one_outcome_never_yields_overall_fresh(self):
        state = classify_ws_books(
            self.snapshot(yes_ts=980_000, no_ts=1_000_000),
            token_ids=["111", "222"],
            observed_at_ms=1_000_100,
            max_age_ms=15_000,
        )
        self.assertEqual(state["per_token"]["111"]["status"], "STALE")
        self.assertNotEqual(state["status"], "FRESH")

    def test_disconnected_stream_is_fail_closed(self):
        state = classify_ws_books(
            self.snapshot(connected=False), token_ids=["111", "222"], observed_at_ms=1_000_100
        )
        self.assertEqual(state["per_token"]["111"]["status"], "DISCONNECTED")
        self.assertEqual(state["per_token"]["222"]["status"], "DISCONNECTED")

    def test_missing_book_is_no_event(self):
        snap = self.snapshot()
        del snap["books"]["222"]
        state = classify_ws_books(snap, token_ids=["111", "222"], observed_at_ms=1_000_100)
        self.assertEqual(state["per_token"]["222"]["status"], "NO_EVENT")
        self.assertNotEqual(state["status"], "FRESH")

    def test_missing_source_timestamp_is_unknown(self):
        snap = self.snapshot()
        snap["books"]["222"]["timestamp"] = None
        state = classify_ws_books(snap, token_ids=["111", "222"], observed_at_ms=1_000_100)
        self.assertEqual(state["per_token"]["222"]["status"], "UNKNOWN")
        self.assertNotEqual(state["status"], "FRESH")

    def test_wrapper_passes_complete_set_without_shell_interpolation(self):
        seen = {}

        def runner(argv, **kwargs):
            seen["argv"] = argv
            payload = self.snapshot()
            payload["reconnects"] = 1
            payload["resubscribe_count"] = 2
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(payload) + "\n", stderr="")

        result = fetch_polymarket_ws_snapshot(
            token_ids=["222", "111"], timeout_ms=5000, max_reconnects=1, runner=runner
        )
        self.assertEqual(json.loads(seen["argv"][2]), ["111", "222"])
        self.assertNotIsInstance(seen["argv"], str)
        self.assertEqual(result["desired_token_ids"], ["111", "222"])
        self.assertEqual(result["reconnects"], 1)
        self.assertEqual(result["resubscribe_count"], 2)

    def test_rest_reconciliation_never_contributes_timestamp_freshness(self):
        rest = {"bids": [], "asks": [], "timestamp": "999999", "hash": "abc"}
        stripped = _untimestamped_reconciliation_copy(rest)
        self.assertNotIn("timestamp", stripped)
        self.assertEqual(stripped["hash"], "abc")

    def test_ws_rest_top_reconciliation_detects_desync(self):
        ws = {"bids": [{"price": "0.40"}], "asks": [{"price": "0.50"}]}
        same = {"bids": [{"price": "0.40"}], "asks": [{"price": "0.50"}]}
        moved = {"bids": [{"price": "0.41"}], "asks": [{"price": "0.50"}]}
        self.assertTrue(_top_matches(ws, same))
        self.assertFalse(_top_matches(ws, moved))


if __name__ == "__main__":
    unittest.main()
