from __future__ import annotations

import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from sibyl_v6.economics import EconomicsLedger, Target80Status
from sibyl_v6.execution_evidence import BookSnapshot, FeeQuote, L2Level, evaluate_fill_to_hedge
from sibyl_v6.matcher import MarketDescriptor, PairState, ResolutionRule, compare_markets
from sibyl_v6.preflight import dry_run_preflight, sanitized_upstream_env
from sibyl_v6.risk import RiskLimits, RiskState

D = Decimal


def rule(**overrides):
    base = {
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
    base.update(overrides)
    return ResolutionRule(**base)


def market(mid: str, title: str, r: ResolutionRule | None):
    return MarketDescriptor("X", mid, title, r)


def book(ts: int, levels):
    return BookSnapshot("POLYMARKET", "m", "BUY", ts, tuple(L2Level(D(p), D(s)) for p, s in levels), "PUBLIC_CLOB")


class MatcherTests(unittest.TestCase):
    def test_upstream_sha_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src/strategies/cross-market-mm").mkdir(parents=True)
            (root / ".sibyl-upstream-sha").write_text("wrong\n", encoding="utf-8")
            result = dry_run_preflight(root, {})
            self.assertEqual(result.DRY_RUN_PREFLIGHT, "FAIL")
            self.assertEqual(result.reason, "UPSTREAM_SHA_MISMATCH")

    def test_identical_title_different_rule_rejected(self):
        title = "Will BTC be above $100k?"
        got = compare_markets(market("a", title, rule()), market("b", title, rule(equality_tie_handling="YES_IF_EQUAL")))
        self.assertEqual(got.state, PairState.RULE_MISMATCH)

    def test_polarity_inversion_rejected(self):
        got = compare_markets(market("a", "same", rule()), market("b", "same", rule(polarity="BELOW")))
        self.assertEqual(got.state, PairState.POLARITY_MISMATCH)

    def test_same_threshold_different_oracle_rejected(self):
        got = compare_markets(market("a", "same", rule()), market("b", "same", rule(reference_source="PYTH BTC/USD")))
        self.assertEqual(got.state, PairState.RULE_MISMATCH)
        self.assertIn("reference_source", got.differing_fields)

    def test_unknown_rule_field_never_exact(self):
        got = compare_markets(market("a", "same", rule()), market("b", "same", rule(cancellation_rules=None)))
        self.assertEqual(got.state, PairState.UNVERIFIED_TITLE_ONLY)

    def test_same_complete_rules_exact_equivalent(self):
        got = compare_markets(market("a", "different titles allowed", rule()), market("b", "other wording", rule()))
        self.assertEqual(got.state, PairState.EXACT_EQUIVALENT)
        self.assertEqual(got.left_rule_fingerprint, got.right_rule_fingerprint)


class ExecutionEvidenceTests(unittest.TestCase):
    def evaluate(self, *, decision=None, arrival=None, size="5", fee="0", hedge=True, now=1000, market_ts=990, hedge_end=1050):
        decision = decision or book(950, [("0.50", "10")])
        arrival = arrival or book(980, [("0.51", "10")])
        return evaluate_fill_to_hedge(
            decision_book=decision,
            arrival_book=arrival,
            requested_size=D(size),
            maker_fill_price=D("0.40"),
            fee=FeeQuote("PUBLIC_FEE_SCHEDULE", D(fee)),
            decision_timestamp_ms=now,
            market_timestamp_ms=market_ts,
            hedge_started_ms=now,
            hedge_finished_ms=hedge_end,
            hedge_success=hedge,
            max_quote_age_ms=100,
            max_market_age_ms=100,
        )

    def test_stale_book_rejected(self):
        got = self.evaluate(decision=book(800, [("0.50", "10")]))
        self.assertEqual(got.rejection_reason, "STALE_BOOK")

    def test_insufficient_l2_depth_is_partial_and_rejected(self):
        got = self.evaluate(arrival=book(980, [("0.51", "2"), ("0.52", "1")]), size="5")
        self.assertTrue(got.partial_fill)
        self.assertEqual(got.filled_size, "3")
        self.assertEqual(got.L2_levels_consumed, 2)
        self.assertEqual(got.rejection_reason, "INSUFFICIENT_L2_DEPTH")

    def test_zero_fill_rejected(self):
        got = self.evaluate(arrival=book(980, []))
        self.assertEqual(got.filled_size, "0")
        self.assertEqual(got.rejection_reason, "ZERO_FILL")

    def test_full_l2_fill_records_vwap_worst_and_green(self):
        got = self.evaluate(arrival=book(980, [("0.50", "2"), ("0.52", "3")]))
        self.assertEqual(got.status, "GREEN")
        self.assertEqual(got.L2_levels_consumed, 2)
        self.assertEqual(got.worst_price, "0.52")
        self.assertEqual(D(got.VWAP), D("0.512"))

    def test_fee_change_changes_net_edge(self):
        zero = self.evaluate(fee="0")
        costly = self.evaluate(fee="100")
        self.assertGreater(D(zero.net_edge_after_all_costs), D(costly.net_edge_after_all_costs))
        self.assertGreater(D(costly.fee_usd), D("0"))

    def test_adverse_movement_before_hedge_visible(self):
        got = self.evaluate(arrival=book(980, [("0.65", "10")]))
        self.assertLess(D(got.net_edge_before_cost), D("0"))

    def test_hedge_failure_never_green(self):
        got = self.evaluate(hedge=False)
        self.assertEqual(got.hedge_fill_status, "FAILED")
        self.assertEqual(got.status, "REJECTED")
        self.assertGreater(D(got.orphan_exposure), D("0"))


class RiskAndEconomicTests(unittest.TestCase):
    def state(self):
        return RiskState(RiskLimits(max_net_shares=D("10"), max_hedge_failures=3))

    def test_repeated_hedge_failure_pulls_quotes(self):
        s = self.state()
        for _ in range(3):
            s.observe_hedge(False)
        self.assertTrue(s.quote_pulled)

    def test_max_inventory_pulls_quotes(self):
        s = self.state()
        s.observe_inventory(D("10"))
        self.assertTrue(s.quote_pulled)

    def test_kill_flattens_and_pulls(self):
        s = self.state()
        s.kill()
        self.assertTrue(s.killed)
        self.assertTrue(s.quote_pulled)
        self.assertTrue(s.flattened)

    def test_stale_position_read_rejected(self):
        s = self.state()
        s.observe_position_read(1000)
        self.assertFalse(s.position_read_fresh(17000))

    def test_duplicate_event_idempotent(self):
        s = self.state()
        self.assertTrue(s.accept_event_once("fill-1"))
        self.assertFalse(s.accept_event_once("fill-1"))

    def test_target_80_cannot_increase_risk(self):
        s = self.state()
        same = s.target_80_adjusted_limits(D("80"))
        self.assertIs(same, s.limits)
        self.assertEqual(same.max_net_shares, D("10"))

    def test_unattributed_rebate_cannot_be_realized(self):
        ledger = EconomicsLedger()
        with self.assertRaises(ValueError):
            ledger.record_realized("limitless_maker_rebate_realized", D("5"), attributable=False)

    def test_target_unproven_without_24h_prospective_attribution(self):
        ledger = EconomicsLedger(cross_venue_spread_realized=D("100"))
        self.assertEqual(ledger.target_80_status(), Target80Status.UNPROVEN)


class LiveGateTests(unittest.TestCase):
    def test_missing_live_armed_keeps_live_preflight_not_run(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src/strategies/cross-market-mm").mkdir(parents=True)
            (root / ".sibyl-upstream-sha").write_text("e35ad881f88c7b5d60388461095ee11b7aa161c5\n", encoding="utf-8")
            result = dry_run_preflight(root, {})
            self.assertEqual(result.DRY_RUN_PREFLIGHT, "PASS")
            self.assertEqual(result.LIVE_PREFLIGHT, "NOT_RUN")

    def test_live_armed_is_forbidden_in_r1(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "src/strategies/cross-market-mm").mkdir(parents=True)
            (root / ".sibyl-upstream-sha").write_text("e35ad881f88c7b5d60388461095ee11b7aa161c5\n", encoding="utf-8")
            result = dry_run_preflight(root, {"LIVE_ARMED": "1"})
            self.assertEqual(result.DRY_RUN_PREFLIGHT, "FAIL")
            self.assertEqual(result.LIVE_PREFLIGHT, "NOT_RUN")

    def test_wrapper_strips_trading_secrets_and_forces_dry_run(self):
        env = sanitized_upstream_env({"PRIVATE_KEY": "secret", "LMTS_TOKEN_SECRET": "secret2", "DRY_RUN": "false"})
        self.assertNotIn("PRIVATE_KEY", env)
        self.assertNotIn("LMTS_TOKEN_SECRET", env)
        self.assertEqual(env["DRY_RUN"], "true")
        self.assertEqual(env["SIBYL_V6_LIVE_ALLOWED"], "false")


if __name__ == "__main__":
    unittest.main()
