from __future__ import annotations

import json
import sys
from pathlib import Path

from .quote_math import BookTop, compute_buy_prices


def observer_results(fixtures: list[dict]) -> list[dict[str, float]]:
    out: list[dict[str, float]] = []
    for fixture in fixtures:
        raw_book = fixture.get("yes_book")
        top = None if raw_book is None else BookTop(raw_book.get("bid"), raw_book.get("ask"))
        out.append(
            compute_buy_prices(
                float(fixture["poly_bid"]),
                float(fixture["poly_ask"]),
                int(fixture["margin_bps"]),
                top,
            )
        )
    return out


def verify(fixtures_path: Path, upstream_path: Path) -> dict:
    fixtures = json.loads(fixtures_path.read_text(encoding="utf-8"))
    upstream = json.loads(upstream_path.read_text(encoding="utf-8"))
    observer = observer_results(fixtures)
    if len(observer) != len(upstream):
        raise RuntimeError("QUOTE_MATH_PARITY_LENGTH_MISMATCH")
    mismatches = []
    for index, (left, right) in enumerate(zip(observer, upstream)):
        for side in ("yes", "no"):
            if abs(float(left[side]) - float(right[side])) > 1e-12:
                mismatches.append({"index": index, "side": side, "observer": left[side], "upstream": right[side]})
    if mismatches:
        raise RuntimeError("QUOTE_MATH_PARITY_FAIL:" + json.dumps(mismatches, sort_keys=True))
    return {"QUOTE_MATH_PARITY": "PASS", "fixture_count": len(fixtures), "observer": observer, "upstream": upstream}


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit("usage: python -m sibyl_v6.quote_parity FIXTURES UPSTREAM_RESULTS")
    print(json.dumps(verify(Path(sys.argv[1]), Path(sys.argv[2])), sort_keys=True))
