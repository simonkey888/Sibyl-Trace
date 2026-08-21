from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from sibyl_v6.discovery import load_verified_pairs
from sibyl_v6.feeds import _freshness
from sibyl_v6.probe import Sample, summarize_samples
from sibyl_v6.upstream_adapter import build_upstream_config


EXACT_COMPARISON = {
    "state": "EXACT_EQUIVALENT",
    "left_rule_fingerprint": "a" * 64,
    "right_rule_fingerprint": "a" * 64,
    "differing_fields": [],
    "unknown_fields": [],
    "comparison_fingerprint": "b" * 64,
}


RULE = {
    "underlying": "BTC/USD",
    "polarity": "ABOVE",
    "threshold": "100000",
    "comparison_operator": ">",
    "reference_source": "CHAINLINK BTC/USD",
    "window_start_utc": "2026-08-21T12:00:00Z",
    "window_end_utc": "2026-08-21T13:00:00Z",
    "resolution_instant_utc": "2026-08-21T13:00:00Z",
    "price_to_beat_construction": "FIXED_THRESHOLD_100000_USD",
    "equality_tie_handling": "NO_IF_EQUAL",
    "invalid_market_rules": "VOID_IF_REFERENCE_UNAVAILABLE",
    "cancellation_rules": "VOID_IF_EVENT_CANCELLED",
    "fallback_oracle_failure_rules": "NO_FALLBACK_VOID",
    "settlement_semantics": "YES_IF_REFERENCE_STRICTLY_ABOVE_THRESHOLD_AT_INSTANT",
}


def exact_pair() -> dict:
    return {
        "polymarket_slug": "poly-btc-100k",
        "limitless_slug": "lmts-btc-100k",
        "comparison": dict(EXACT_COMPARISON),
    }


def verified_pair_record() -> dict:
    return {
        "polymarket_id": "123",
        "polymarket_slug": "poly-btc-100k",
        "polymarket_title": "BTC > 100k",
        "limitless_slug": "lmts-btc-100k",
        "limitless_title": "Bitcoin above 100000",
        "polymarket_rule": dict(RULE),
        "limitless_rule": dict(RULE),
        "polymarket_rule_source_url": "https://gamma-api.polymarket.com/markets/123",
        "limitless_rule_source_url": "https://api.limitless.exchange/markets/lmts-btc-100k",
        "polymarket_rule_payload_hash": "1" * 64,
        "limitless_rule_payload_hash": "2" * 64,
        "verified_at_utc": "2026-08-21T12:00:00Z",
    }


class UpstreamBindingTests(unittest.TestCase):
    def test_only_exact_equivalent_can_reach_upstream_config(self):
        row = exact_pair()
        row["comparison"]["state"] = "CANDIDATE"
        with self.assertRaisesRegex(RuntimeError, "UPSTREAM_PAIR_NOT_EXACT_EQUIVALENT"):
            build_upstream_config([row])

    def test_fingerprint_mismatch_fails_closed(self):
        row = exact_pair()
        row["comparison"]["right_rule_fingerprint"] = "c" * 64
        with self.assertRaisesRegex(RuntimeError, "UPSTREAM_PAIR_FINGERPRINT_MISMATCH"):
            build_upstream_config([row])

    def test_config_is_dry_run_and_contains_only_verified_slugs(self):
        config = build_upstream_config([exact_pair()])
        self.assertIs(config["dry_run"], True)
        self.assertEqual(
            config["market_pairs"],
            [
                {
                    "polymarket_slug": "poly-btc-100k",
                    "limitless_slug": "lmts-btc-100k",
                }
            ],
        )
        self.assertEqual(config["binding"]["source"], "EXACT_EQUIVALENT_ONLY")
        self.assertNotIn("TARGET_NET_24H_USD", config)
        self.assertNotIn("target_80", str(config).casefold())

    def test_empty_exact_set_is_refused(self):
        with self.assertRaisesRegex(RuntimeError, "NO_EXACT_EQUIVALENT_PAIR"):
            build_upstream_config([])

    def test_runner_does_not_accept_caller_upstream_config_path(self):
        source = (Path(__file__).resolve().parents[1] / "sibyl_v6/runner.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('config_path = Path("/tmp/sibyl-v6-cross-market-mm.config.json")', source)
        self.assertNotIn('os.environ.get("CROSS_MARKET_MM_CONFIG_PATH"', source)


class ExactPairProvenanceTests(unittest.TestCase):
    def _load(self, row: dict) -> list[dict]:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "pairs.json"
            path.write_text(json.dumps({"pairs": [row]}), encoding="utf-8")
            return load_verified_pairs(path)

    def test_exact_pair_without_source_provenance_is_refused(self):
        row = verified_pair_record()
        del row["polymarket_rule_payload_hash"]
        with self.assertRaisesRegex(RuntimeError, "EXACT_PAIR_PROVENANCE_MISSING"):
            self._load(row)

    def test_exact_pair_persists_source_payload_hashes_in_comparison(self):
        rows = self._load(verified_pair_record())
        self.assertEqual(len(rows), 1)
        comparison = rows[0]["comparison"]
        self.assertEqual(comparison["state"], "EXACT_EQUIVALENT")
        self.assertEqual(comparison["left_source_payload_hash"], "2" * 64)
        self.assertEqual(comparison["right_source_payload_hash"], "1" * 64)
        self.assertEqual(
            comparison["left_rule_fingerprint"], comparison["right_rule_fingerprint"]
        )


class FeedStalenessTests(unittest.TestCase):
    def test_missing_source_timestamp_is_unknown_not_fresh(self):
        age, status = _freshness(None, 100_000, 15_000)
        self.assertIsNone(age)
        self.assertEqual(status, "UNKNOWN")

    def test_stale_and_clock_skew_are_explicit(self):
        self.assertEqual(_freshness(80_000, 100_000, 15_000), (20_000, "STALE"))
        self.assertEqual(_freshness(110_000, 100_000, 15_000), (-10_000, "CLOCK_SKEW"))


class RegionProbeSummaryTests(unittest.TestCase):
    def test_rest_and_ws_report_median_and_p95_for_all_required_metrics(self):
        samples = [
            Sample("polymarket_rest", 10.0, 20.0, 30.0, None, 200, None),
            Sample("polymarket_rest", 12.0, 22.0, 32.0, None, 200, None),
            Sample("polymarket_ws", 11.0, 21.0, 31.0, 63.0, 101, None),
            Sample("polymarket_ws", 13.0, 23.0, 33.0, 69.0, 101, None),
        ]
        summary = summarize_samples(samples)
        rest = summary["polymarket_rest"]
        ws = summary["polymarket_ws"]
        for metric in ("connect", "tls", "ttfb"):
            self.assertIn("median_ms", rest[metric])
            self.assertIn("p95_ms", rest[metric])
            self.assertIn("median_ms", ws[metric])
            self.assertIn("p95_ms", ws[metric])
        self.assertEqual(ws["ws_connect"]["median_ms"], 66.0)
        self.assertEqual(ws["expected_status"], 101)
        self.assertEqual(ws["protocol_successful_samples"], 2)

    def test_geoblock_451_is_preserved_as_evidence(self):
        samples = [
            Sample("limitless_rest", 1.0, 2.0, 3.0, None, 451, None),
        ]
        summary = summarize_samples(samples)
        self.assertTrue(summary["limitless_rest"]["geoblock_451_observed"])
        self.assertEqual(summary["limitless_rest"]["http_statuses"], [451])


if __name__ == "__main__":
    unittest.main()
