from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.event_tape_v4 import TapeEvent, TapeLevel
from app.market_data_v3 import V3Event
from app.market_identity_v4 import MarketContract, compare_contracts
from app.research_v4 import build_research_v4_summary
from app.venue_v3 import NormalizedBook, PriceLevel


def _contract(venue: str, market_id: str) -> MarketContract:
    return MarketContract(
        venue=venue,
        market_id=market_id,
        title="Synthetic BTC close benchmark",
        underlying="BTCUSD",
        event="BTC close",
        outcome="above threshold",
        strike="100000",
        cutoff_iso="2026-08-07T20:00:00Z",
        timezone="UTC",
        resolution_source="synthetic benchmark",
        resolution_rule="same close observation",
    )


def build_smoke_summary() -> dict:
    tape = (
        TapeEvent(
            schema_version=1,
            venue="POLYMARKET",
            asset_id="YES",
            kind="SNAPSHOT",
            receive_timestamp_ms=1_000,
            sequence=1,
            levels=(TapeLevel("BID", 0.50, 10.0), TapeLevel("ASK", 0.52, 10.0)),
        ),
        TapeEvent(
            schema_version=1,
            venue="POLYMARKET",
            asset_id="YES",
            kind="DELTA",
            receive_timestamp_ms=1_100,
            sequence=2,
            levels=(TapeLevel("BID", 0.50, 8.0), TapeLevel("ASK", 0.515, 4.0)),
        ),
    )
    events = (
        V3Event("BINANCE", "TRADE", 1_000, 1_000, price=100.0, size=1.0, aggressor_side="BUY"),
        V3Event("BINANCE", "TRADE", 61_000, 61_000, price=101.0, size=2.0, aggressor_side="BUY"),
        V3Event("COINBASE", "TRADE", 1_000, 1_000, price=100.1, size=1.0, aggressor_side="BUY"),
        V3Event("COINBASE", "TRADE", 61_500, 61_500, price=101.1, size=1.0, aggressor_side="BUY"),
    )
    left = NormalizedBook(
        venue="POLYMARKET",
        asset_id="p1",
        bids=(PriceLevel(0.50, 10.0),),
        asks=(PriceLevel(0.52, 10.0),),
        source_timestamp_ms=None,
        receive_timestamp_ms=1,
    )
    right = NormalizedBook(
        venue="KALSHI",
        asset_id="k1",
        bids=(PriceLevel(0.56, 8.0),),
        asks=(PriceLevel(0.58, 8.0),),
        source_timestamp_ms=None,
        receive_timestamp_ms=1,
    )
    identity = compare_contracts(_contract("POLYMARKET", "p1"), _contract("KALSHI", "k1"))
    return build_research_v4_summary(
        events=events,
        tape=tape,
        left_book=left,
        right_book=right,
        identity=identity,
        model_probabilities=[0.9, 0.1, 0.8, 0.2, 0.75, 0.25],
        market_probabilities=[0.6, 0.4, 0.55, 0.45, 0.52, 0.48],
        outcomes=[1, 0, 1, 0, 1, 0],
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summary = build_smoke_summary()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    if summary["safety"]["order_placement"] or summary["safety"]["private_keys"]:
        raise SystemExit("unsafe V4 smoke summary")
    if summary["l2_reconstruction_v4"]["status"] != "RECONSTRUCTED":
        raise SystemExit("V4 L2 smoke did not reconstruct")


if __name__ == "__main__":
    main()
