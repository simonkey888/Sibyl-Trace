from __future__ import annotations

import unittest

from sibyl_v6.runtime_market_data import _resolve_polymarket_ws_authority


class PolymarketRuntimeAuthorityTests(unittest.TestCase):
    @staticmethod
    def book(bid: str, ask: str, timestamp: str | None = None):
        out = {
            "bids": [{"price": bid, "size": "10"}],
            "asks": [{"price": ask, "size": "10"}],
        }
        if timestamp is not None:
            out["timestamp"] = timestamp
        return out

    def test_later_fresh_ws_supersedes_older_rest_even_when_top_moved(self):
        rest = self.book("0.40", "0.41", "1787421000000")
        ws = self.book("0.42", "0.43", "1787421000500")
        authoritative, status, source, top_match = _resolve_polymarket_ws_authority(
            ws, rest, "FRESH"
        )
        self.assertEqual(status, "FRESH")
        self.assertEqual(source, "POLYMARKET_MARKET_WS_BOOK")
        self.assertFalse(top_match)
        self.assertEqual(authoritative["timestamp"], "1787421000500")
        self.assertEqual(authoritative["bids"][0]["price"], "0.42")

    def test_stale_ws_never_becomes_fresh_from_rest(self):
        rest = self.book("0.40", "0.41", "1787421000000")
        ws = self.book("0.40", "0.41", "1787420000000")
        authoritative, status, source, top_match = _resolve_polymarket_ws_authority(
            ws, rest, "STALE"
        )
        self.assertEqual(status, "STALE")
        self.assertEqual(source, "POLYMARKET_MARKET_WS_BOOK")
        self.assertTrue(top_match)
        self.assertIsNone(authoritative["timestamp"])

    def test_disconnected_or_missing_ws_uses_untimestamped_rest_only(self):
        rest = self.book("0.40", "0.41", "1787421000000")
        authoritative, status, source, top_match = _resolve_polymarket_ws_authority(
            None, rest, "DISCONNECTED"
        )
        self.assertEqual(status, "DISCONNECTED")
        self.assertEqual(source, "REST_RECONCILIATION_ONLY")
        self.assertFalse(top_match)
        self.assertNotIn("timestamp", authoritative)
        self.assertEqual(authoritative["bids"][0]["price"], "0.40")

    def test_unknown_ws_timestamp_remains_fail_closed(self):
        rest = self.book("0.40", "0.41")
        ws = self.book("0.40", "0.41")
        authoritative, status, source, top_match = _resolve_polymarket_ws_authority(
            ws, rest, "UNKNOWN"
        )
        self.assertEqual(status, "UNKNOWN")
        self.assertEqual(source, "POLYMARKET_MARKET_WS_BOOK")
        self.assertTrue(top_match)
        self.assertIsNone(authoritative.get("timestamp"))


if __name__ == "__main__":
    unittest.main()
