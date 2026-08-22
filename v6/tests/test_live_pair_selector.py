from __future__ import annotations

import unittest

from sibyl_v6.live_pair_selector import select_current_exact_pair


def row(limitless: str, polymarket: str) -> dict:
    return {
        "limitless_slug": limitless,
        "polymarket_slug": polymarket,
        "comparison": {
            "state": "EXACT_EQUIVALENT",
            "unknown_fields": [],
            "differing_fields": [],
            "left_rule_fingerprint": "a" * 64,
            "right_rule_fingerprint": "a" * 64,
            "comparison_fingerprint": "b" * 64,
        },
    }


class LivePairSelectionTests(unittest.TestCase):
    def test_preferred_pair_is_used_only_when_current_audit_still_exact(self):
        old = ("old-limitless", "old-poly")
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

    def test_no_current_exact_pair_returns_none(self):
        self.assertIsNone(select_current_exact_pair({"exact_pairs": []}, set()))


if __name__ == "__main__":
    unittest.main()
