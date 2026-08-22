from __future__ import annotations

import datetime as dt
import tempfile
import unittest
from pathlib import Path

from sibyl_v6.evidence_store import (
    FileEvidenceStore,
    GCSEvidenceStore,
    R2EvidenceStore,
    evidence_store_from_env,
)
from sibyl_v6.limitless_ws import classify_ws_snapshot
from sibyl_v6.poly_fee import (
    PolyFeeDetails,
    parse_clob_fee_details,
    protocol_fee_for_fills,
)
from sibyl_v6.quote_math import BookTop, book_top, clip_price, compute_buy_prices, norm_price
from sibyl_v6.quote_parity import observer_results
from sibyl_v6.quote_safety import (
    LIMITLESS_EXECUTION_TICK,
    LIMITLESS_VENUE_TICK,
    assess_buy_quote,
    executable_buy_cost,
    floor_buy_cap,
)


class QuoteMathTests(unittest.TestCase):
    def test_pinned_upstream_examples(self):
        self.assertEqual(compute_buy_prices(0.60, 0.62, 100), {"yes": 0.59, "no": 0.37})
        self.assertEqual(compute_buy_prices(0.55, 0.57, 0), {"yes": 0.55, "no": 0.43})
        self.assertEqual(compute_buy_prices(0.50, 0.99, 100), {"yes": 0.49, "no": 0.01})
        self.assertEqual(
            compute_buy_prices(0.69, 0.71, 0, BookTop(0.60, 0.65)),
            {"yes": 0.64, "no": 0.29},
        )

    def test_both_limitless_sides_are_buy_caps(self):
        result = compute_buy_prices(0.60, 0.62, 100, BookTop(0.99, 0.05))
        self.assertLessEqual(result["yes"], 0.59)
        self.assertLessEqual(result["no"], 0.37)
        self.assertEqual(result, {"yes": 0.04, "no": 0.01})

    def test_book_top_normalizes_cents_and_fractional_prices(self):
        top = book_top(
            {
                "bids": [{"price": "60"}, {"price": "0.62"}],
                "asks": [{"price": "65"}, {"price": "0.63"}],
            }
        )
        self.assertEqual(top, BookTop(0.62, 0.63))
        self.assertEqual(norm_price(63), 0.63)
        self.assertEqual(clip_price(0.627), 0.63)

    def test_observer_fixture_shape_is_deterministic(self):
        fixtures = [
            {"poly_bid": 0.6, "poly_ask": 0.62, "margin_bps": 100, "yes_book": None},
            {
                "poly_bid": 0.69,
                "poly_ask": 0.71,
                "margin_bps": 0,
                "yes_book": {"bid": 0.6, "ask": 0.65},
            },
        ]
        self.assertEqual(
            observer_results(fixtures),
            [{"yes": 0.59, "no": 0.37}, {"yes": 0.64, "no": 0.29}],
        )


class QuoteSafetyTests(unittest.TestCase):
    @staticmethod
    def book(price: float, size: float = 10.0):
        return {
            "asks": [{"price": str(price), "size": str(size)}],
            "bids": [
                {"price": str(max(0.001, price - 0.001)), "size": str(size)}
            ],
        }

    def assess(self, **overrides):
        args = {
            "side": "YES",
            "raw_cap": 0.50,
            "upstream_price": 0.50,
            "hedge_book": self.book(0.47),
            "hedge_book_status": "FRESH",
            "maker_book_status": "FRESH",
            "hedge_token": "NO_TOKEN",
            "quote_size": 5.0,
            "polymarket_fee_details": PolyFeeDetails(
                rate=0.0, exponent=0.0, taker_only=True
            ),
            "minimum_net_edge_bps": 100.0,
            "limitless_maker_fee_bps": 0.0,
        }
        args.update(overrides)
        return assess_buy_quote(**args)

    def test_execution_grid_stays_pinned_cent_while_venue_supports_mill(self):
        self.assertEqual(LIMITLESS_EXECUTION_TICK, 0.01)
        self.assertEqual(LIMITLESS_VENUE_TICK, 0.001)

    def test_negative_cap_is_not_quoteable(self):
        result = self.assess(raw_cap=-0.008, upstream_price=0.01)
        self.assertFalse(result["QUOTEABLE"])
        self.assertIsNone(result["SAFE_QUOTE_PRICE"])
        self.assertIn("RAW_CAP_BELOW_MIN_TICK", result["REJECTION_REASON"])

    def test_cap_below_execution_tick_is_not_quoteable(self):
        result = self.assess(raw_cap=0.005, upstream_price=0.01)
        self.assertFalse(result["QUOTEABLE"])
        self.assertIsNone(result["SAFE_QUOTE_PRICE"])
        self.assertIn("RAW_CAP_BELOW_MIN_TICK", result["REJECTION_REASON"])

    def test_0986_cap_floors_to_098_not_099(self):
        self.assertEqual(floor_buy_cap(0.986), 0.98)
        result = self.assess(
            raw_cap=0.986,
            upstream_price=0.99,
            hedge_book=self.book(0.004),
        )
        self.assertFalse(result["UPSTREAM_CAP_COMPLIANT"])
        self.assertEqual(result["SAFE_QUOTE_PRICE"], 0.98)
        self.assertTrue(result["CAP_COMPLIANT"])

    def test_round_up_that_would_breach_cap_is_floored(self):
        result = self.assess(
            raw_cap=0.586,
            upstream_price=0.59,
            hedge_book=self.book(0.39),
        )
        self.assertFalse(result["UPSTREAM_CAP_COMPLIANT"])
        self.assertEqual(result["SAFE_QUOTE_PRICE"], 0.58)
        self.assertLessEqual(result["SAFE_QUOTE_PRICE"], result["RAW_CAP"])

    def test_protocol_fees_can_erase_edge(self):
        result = self.assess(
            raw_cap=0.50,
            upstream_price=0.50,
            hedge_book=self.book(0.48),
            polymarket_fee_details=PolyFeeDetails(
                rate=0.20, exponent=1.0, taker_only=True
            ),
            minimum_net_edge_bps=100.0,
        )
        self.assertFalse(result["QUOTEABLE"])
        self.assertLess(result["EXPECTED_NET_EDGE"], result["MIN_EXPECTED_NET_EDGE"])
        self.assertIn("NET_EDGE_BELOW_MINIMUM", result["REJECTION_REASON"])
        self.assertIsNotNone(result["FEES"]["EXPECTED_PROTOCOL_FEE_USDC"])
        self.assertIsNone(result["FEES"]["REALIZED_FEE_USDC"])

    def test_safety_buffer_is_separate_from_protocol_fee(self):
        base = self.assess(
            minimum_net_edge_bps=0.0,
            fee_safety_buffer_bps=0.0,
        )
        buffered = self.assess(
            minimum_net_edge_bps=0.0,
            fee_safety_buffer_bps=100.0,
        )
        self.assertEqual(base["FEES"]["EXPECTED_PROTOCOL_FEE_USDC"], 0.0)
        self.assertGreater(buffered["FEES"]["SAFETY_FEE_BUFFER_USDC"], 0.0)
        self.assertLess(buffered["EXPECTED_NET_EDGE"], base["EXPECTED_NET_EDGE"])

    def test_insufficient_hedge_depth_rejects(self):
        result = self.assess(hedge_book=self.book(0.47, size=1.0), quote_size=5.0)
        self.assertFalse(result["QUOTEABLE"])
        self.assertFalse(result["HEDGE_DEPTH_SUFFICIENT"])
        self.assertIn("INSUFFICIENT_HEDGE_DEPTH", result["REJECTION_REASON"])

    def test_stale_hedge_book_rejects(self):
        result = self.assess(hedge_book_status="STALE")
        self.assertFalse(result["QUOTEABLE"])
        self.assertIn("HEDGE_BOOK_NOT_FRESH", result["REJECTION_REASON"])

    def test_unknown_limitless_competition_book_rejects(self):
        result = self.assess(maker_book_status="UNKNOWN")
        self.assertFalse(result["QUOTEABLE"])
        self.assertIn("LIMITLESS_BOOK_NOT_FRESH", result["REJECTION_REASON"])

    def test_unknown_fee_rejects(self):
        result = self.assess(polymarket_fee_details=None)
        self.assertFalse(result["QUOTEABLE"])
        self.assertIsNone(result["EXPECTED_NET_EDGE"])
        self.assertIn("FEE_UNKNOWN", result["REJECTION_REASON"])

    def test_non_tradeable_market_rejects(self):
        result = self.assess(market_tradeable=False)
        self.assertFalse(result["QUOTEABLE"])
        self.assertIn("MARKET_NOT_TRADEABLE", result["REJECTION_REASON"])

    def test_executable_cost_consumes_real_ask_depth(self):
        book = {
            "asks": [
                {"price": "0.40", "size": "2"},
                {"price": "0.45", "size": "3"},
            ]
        }
        result = executable_buy_cost(book, 5.0)
        self.assertTrue(result["depth_sufficient"])
        self.assertEqual(result["levels_consumed"], 2)
        self.assertAlmostEqual(result["vwap"], 0.43)


class PolymarketV2FeeTests(unittest.TestCase):
    def test_parse_current_clob_v2_fee_contract(self):
        details = parse_clob_fee_details(
            {"fd": {"r": 0.07, "e": 1, "to": True}}
        )
        self.assertEqual(
            details,
            PolyFeeDetails(rate=0.07, exponent=1.0, taker_only=True),
        )

    def test_unknown_or_malformed_fee_contract_fails_closed(self):
        self.assertIsNone(parse_clob_fee_details(None))
        self.assertIsNone(parse_clob_fee_details({"fd": {"r": 0.07}}))
        self.assertIsNone(
            parse_clob_fee_details({"fd": {"r": 0.07, "e": 0, "to": True}})
        )

    def test_fee_curve_matches_v2_formula_at_required_prices(self):
        details = PolyFeeDetails(rate=0.07, exponent=1.0, taker_only=True)
        for price in (0.01, 0.10, 0.25, 0.50, 0.75, 0.90, 0.99):
            with self.subTest(price=price):
                result = protocol_fee_for_fills(
                    [{"price": price, "size": 100.0}], details
                )
                expected = round(100.0 * 0.07 * price * (1.0 - price), 5)
                self.assertAlmostEqual(result["expected_usdc"], expected, places=5)
                self.assertGreaterEqual(
                    result["conservative_usdc"], result["expected_usdc"]
                )

    def test_dynamic_fee_parameter_change_changes_economics(self):
        fills = [{"price": 0.50, "size": 100.0}]
        low = protocol_fee_for_fills(
            fills, PolyFeeDetails(rate=0.04, exponent=1.0, taker_only=True)
        )
        high = protocol_fee_for_fills(
            fills, PolyFeeDetails(rate=0.07, exponent=1.0, taker_only=True)
        )
        curved = protocol_fee_for_fills(
            fills, PolyFeeDetails(rate=0.07, exponent=2.0, taker_only=True)
        )
        self.assertLess(low["expected_usdc"], high["expected_usdc"])
        self.assertLess(curved["expected_usdc"], high["expected_usdc"])

    def test_multilevel_fill_evaluates_fee_per_execution_price(self):
        details = PolyFeeDetails(rate=0.07, exponent=1.0, taker_only=True)
        fills = [
            {"price": 0.25, "size": 40.0},
            {"price": 0.75, "size": 60.0},
        ]
        result = protocol_fee_for_fills(fills, details)
        expected = round(
            40 * 0.07 * 0.25 * 0.75 + 60 * 0.07 * 0.75 * 0.25,
            5,
        )
        self.assertAlmostEqual(result["expected_usdc"], expected, places=5)


class LimitlessWebSocketFreshnessTests(unittest.TestCase):
    def test_fresh_timestamped_orderbook_update_is_fresh(self):
        source_ms = 1_787_500_000_000
        result = classify_ws_snapshot(
            {
                "connected": True,
                "event_received": True,
                "timestamp": source_ms,
                "received_at_ms": source_ms + 100,
                "orderbook": {"bids": [], "asks": []},
                "reconnects": 0,
                "resubscribe_count": 1,
                "desired_market_slugs": ["a", "b"],
            },
            observed_at_ms=source_ms + 500,
        )
        self.assertEqual(result["status"], "FRESH")
        self.assertEqual(result["age_ms"], 500)
        self.assertEqual(result["desired_market_slugs"], ["a", "b"])

    def test_stale_timestamped_update_is_not_fresh(self):
        source_ms = 1_787_500_000_000
        result = classify_ws_snapshot(
            {
                "connected": True,
                "event_received": True,
                "timestamp": source_ms,
                "received_at_ms": source_ms + 100,
                "orderbook": {"bids": [], "asks": []},
            },
            observed_at_ms=source_ms + 15_001,
        )
        self.assertEqual(result["status"], "STALE")

    def test_disconnected_stream_is_not_fresh(self):
        result = classify_ws_snapshot(
            {
                "connected": False,
                "event_received": False,
                "timestamp": None,
                "orderbook": None,
            },
            observed_at_ms=1_787_500_000_000,
        )
        self.assertEqual(result["status"], "DISCONNECTED")

    def test_quiet_connected_stream_is_distinct_from_stale(self):
        result = classify_ws_snapshot(
            {
                "connected": True,
                "event_received": False,
                "timestamp": None,
                "orderbook": None,
                "error": "NO_ORDERBOOK_EVENT_WITHIN_TIMEOUT",
            },
            observed_at_ms=1_787_500_000_000,
        )
        self.assertEqual(result["status"], "NO_EVENT")
        self.assertIsNone(result["source_timestamp_ms"])

    def test_missing_source_timestamp_never_becomes_fresh(self):
        result = classify_ws_snapshot(
            {
                "connected": True,
                "event_received": True,
                "timestamp": None,
                "received_at_ms": 1_787_500_000_000,
                "orderbook": {"bids": [], "asks": []},
            },
            observed_at_ms=1_787_500_000_100,
        )
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertIsNone(result["age_ms"])


class _Response:
    def __init__(self, status: int, payload: bytes = b""):
        self.status = status
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self) -> bytes:
        return self._payload


class EvidenceStoreTests(unittest.TestCase):
    def test_file_store_is_local_runtime_only(self):
        with tempfile.TemporaryDirectory() as td:
            store = FileEvidenceStore(Path(td))
            store.put_bytes("a/b.json", b"payload")
            self.assertEqual(store.get_bytes("a/b.json"), b"payload")
            contract = store.contract()
            self.assertEqual(contract["backend"], "FILE")
            self.assertFalse(contract["restart_durable"])
            self.assertFalse(contract["restart_durability_claimed"])
            self.assertEqual(contract["claim"], "LOCAL_RUNTIME_EVIDENCE_ONLY")

    def test_default_boot_never_requires_gcp_metadata(self):
        store = evidence_store_from_env({})
        self.assertIsInstance(store, FileEvidenceStore)
        self.assertEqual(store.contract()["backend"], "FILE")

    def test_gcs_store_contract_with_injected_token(self):
        requests = []

        def opener(request, timeout=0):
            requests.append(request)
            return _Response(200, b"gcs")

        store = GCSEvidenceStore(
            "evidence-bucket", token_provider=lambda: "TOKEN", opener=opener
        )
        store.put_bytes("k.json", b"x")
        self.assertEqual(store.get_bytes("k.json"), b"gcs")
        self.assertEqual(store.contract()["backend"], "GCS")
        self.assertTrue(store.contract()["restart_durable"])
        self.assertEqual(len(requests), 2)

    def test_r2_standard_storage_sigv4_contract(self):
        requests = []

        def opener(request, timeout=0):
            requests.append(request)
            return _Response(200, b"r2")

        store = R2EvidenceStore(
            "acct",
            "bucket",
            "AKID",
            "SECRET",
            opener=opener,
            now=lambda: dt.datetime(2026, 8, 21, 12, 0, tzinfo=dt.timezone.utc),
        )
        store.put_bytes("evidence/x.json", b"payload")
        self.assertEqual(store.get_bytes("evidence/x.json"), b"r2")
        contract = store.contract()
        self.assertEqual(contract["backend"], "R2")
        self.assertEqual(contract["storage_class"], "STANDARD")
        self.assertTrue(contract["restart_durable"])
        self.assertFalse(contract["trading_secrets_required"])
        self.assertEqual(len(requests), 2)
        for request in requests:
            self.assertIn("AWS4-HMAC-SHA256", request.get_header("Authorization"))
            self.assertIn("acct.r2.cloudflarestorage.com", request.full_url)


if __name__ == "__main__":
    unittest.main()
