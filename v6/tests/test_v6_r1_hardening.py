from __future__ import annotations

import unittest
from pathlib import Path

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


def exact_pair() -> dict:
    return {
        "polymarket_slug": "poly-btc-100k",
        "limitless_slug": "lmts-btc-100k",
        "comparison": dict(EXACT_COMPARISON),
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
