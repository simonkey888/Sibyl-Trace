from __future__ import annotations

import unittest

from sibyl_v6.exact_pair_contract import validate_exact_pair_cycle
from sibyl_v6.live_pair_selector import select_current_exact_pair


def row(limitless: str, polymarket: str, marker: str = "a") -> dict:
    return {
        "limitless_slug": limitless,
        "polymarket_slug": polymarket,
        "limitless_rule_source_url": f"https://api.limitless.exchange/markets/{limitless}",
        "polymarket_rule_source_url": f"https://gamma-api.polymarket.com/markets/slug/{polymarket}",
        "limitless_rule_payload_hash": "1" * 64,
        "polymarket_rule_payload_hash": "2" * 64,
        "comparison": {
            "state": "EXACT_EQUIVALENT",
            "unknown_fields": [],
            "differing_fields": [],
            "left_rule_fingerprint": marker * 64,
            "right_rule_fingerprint": marker * 64,
            "comparison_fingerprint": "b" * 64,
        },
    }


def cycle_for(selected: dict, audit: dict) -> dict:
    comparison = selected["comparison"]
    return {
        "DISCOVERY_CYCLE": "PASS",
        "MARKET_DATA_CYCLE": "PASS",
        "CANDIDATE_PAIR_COUNT": audit["CANDIDATE_PAIR_COUNT"],
        "EXACT_EQUIVALENT_PAIR_COUNT": audit["EXACT_EQUIVALENT_PAIR_COUNT"],
        "exact_pair": {
            "limitless_slug": selected["limitless_slug"],
            "polymarket_slug": selected["polymarket_slug"],
            "comparison_state": comparison["state"],
            "comparison_fingerprint": comparison["comparison_fingerprint"],
            "left_rule_fingerprint": comparison["left_rule_fingerprint"],
            "right_rule_fingerprint": comparison["right_rule_fingerprint"],
            "unknown_fields": comparison["unknown_fields"],
            "differing_fields": comparison["differing_fields"],
            "limitless_rule_source_url": selected["limitless_rule_source_url"],
            "polymarket_rule_source_url": selected["polymarket_rule_source_url"],
            "limitless_rule_payload_hash": selected["limitless_rule_payload_hash"],
            "polymarket_rule_payload_hash": selected["polymarket_rule_payload_hash"],
        },
    }


class LivePairSelectionTests(unittest.TestCase):
    def test_preferred_pair_is_used_only_when_current_audit_still_exact(self):
        old = ("62000-1786954111732", "will-bitcoin-dip-to-62k-august-17-23-2026")
        fresh = row("new-limitless", "new-poly")
        audit = {"exact_pairs": [fresh]}
        self.assertEqual(select_current_exact_pair(audit, {old}), fresh)

    def test_current_preferred_exact_pair_wins_without_slug_hardcode(self):
        preferred = row("preferred-limitless", "preferred-poly")
        other = row("aaa-limitless", "aaa-poly")
        audit = {"exact_pairs": [other, preferred]}
        selected = select_current_exact_pair(
            audit, {("preferred-limitless", "preferred-poly")}
        )
        self.assertEqual(selected, preferred)

    def test_different_current_exact_pair_satisfies_same_cycle_harness(self):
        replacement = row("btc-current-limitless", "btc-current-polymarket", "c")
        audit = {
            "authoritative_audit_hash": "d" * 64,
            "CANDIDATE_PAIR_COUNT": 7,
            "EXACT_EQUIVALENT_PAIR_COUNT": 1,
            "exact_pairs": [replacement],
        }
        selected = select_current_exact_pair(
            audit,
            {("62000-1786954111732", "will-bitcoin-dip-to-62k-august-17-23-2026")},
        )
        self.assertEqual(selected, replacement)
        self.assertEqual(validate_exact_pair_cycle(cycle_for(selected, audit), audit), cycle_for(selected, audit)["exact_pair"])

    def test_harness_rejects_pair_not_from_same_audit(self):
        current = row("current-limitless", "current-poly", "c")
        stale = row("stale-limitless", "stale-poly", "e")
        audit = {
            "authoritative_audit_hash": "d" * 64,
            "CANDIDATE_PAIR_COUNT": 2,
            "EXACT_EQUIVALENT_PAIR_COUNT": 1,
            "exact_pairs": [current],
        }
        with self.assertRaisesRegex(AssertionError, "SAME_AUDIT"):
            validate_exact_pair_cycle(cycle_for(stale, audit), audit)

    def test_no_current_exact_pair_returns_none(self):
        self.assertIsNone(select_current_exact_pair({"exact_pairs": []}, set()))


if __name__ == "__main__":
    unittest.main()
