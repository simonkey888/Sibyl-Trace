from __future__ import annotations

import unittest

from sibyl_v6.poly_fee import (
    MAX_SUPPORTED_FEE_EXPONENT,
    MAX_SUPPORTED_FEE_RATE,
    PolyFeeDetails,
    parse_clob_fee_details,
    protocol_fee_raw,
)


class PolyFeeHardeningTests(unittest.TestCase):
    def test_current_v2_contract_is_supported(self):
        self.assertEqual(
            parse_clob_fee_details({"fd": {"r": 0.07, "e": 1, "to": True}}),
            PolyFeeDetails(rate=0.07, exponent=1.0, taker_only=True),
        )

    def test_max_integral_exponent_is_supported(self):
        self.assertIsNotNone(
            parse_clob_fee_details(
                {"fd": {"r": 0.07, "e": MAX_SUPPORTED_FEE_EXPONENT, "to": True}}
            )
        )

    def test_exponent_above_local_bound_fails_closed(self):
        self.assertIsNone(
            parse_clob_fee_details(
                {
                    "fd": {
                        "r": 0.07,
                        "e": MAX_SUPPORTED_FEE_EXPONENT + 1,
                        "to": True,
                    }
                }
            )
        )

    def test_non_integral_exponent_fails_closed(self):
        self.assertIsNone(
            parse_clob_fee_details({"fd": {"r": 0.07, "e": 1.5, "to": True}})
        )

    def test_rate_above_local_bound_fails_closed(self):
        self.assertIsNone(
            parse_clob_fee_details(
                {"fd": {"r": MAX_SUPPORTED_FEE_RATE + 0.000001, "e": 1, "to": True}}
            )
        )

    def test_direct_unbounded_exponent_cannot_bypass_parser(self):
        with self.assertRaisesRegex(ValueError, "UNSUPPORTED_FEE_DETAILS"):
            protocol_fee_raw(
                5.0,
                0.5,
                PolyFeeDetails(
                    rate=0.07,
                    exponent=MAX_SUPPORTED_FEE_EXPONENT + 1,
                    taker_only=True,
                ),
            )

    def test_direct_unbounded_rate_cannot_bypass_parser(self):
        with self.assertRaisesRegex(ValueError, "UNSUPPORTED_FEE_DETAILS"):
            protocol_fee_raw(
                5.0,
                0.5,
                PolyFeeDetails(
                    rate=MAX_SUPPORTED_FEE_RATE + 1,
                    exponent=1.0,
                    taker_only=True,
                ),
            )


if __name__ == "__main__":
    unittest.main()
