import gzip
import json
from pathlib import Path

import pytest

from app.kalshi_v4 import rank_btc_markets
from app.research_v4_operational import _v3_events, validate_v3_source


def test_v3_source_must_be_pass_paper_zero_cost() -> None:
    valid = {
        "status": "PASS",
        "evidence_generation": "SIBYL_RESEARCH_V3",
        "safety": {
            "trading_mode": "PAPER",
            "live_available": False,
            "real_money": False,
            "cost_authorized_usd": 0,
            "paid_apis": False,
        },
    }
    validate_v3_source(valid)
    invalid = {**valid, "safety": {**valid["safety"], "live_available": True}}
    with pytest.raises(ValueError):
        validate_v3_source(invalid)


def test_v3_journal_is_rehydrated_for_long_horizon_features(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl.gz"
    rows = [
        {
            "payload": {
                "source": "BINANCE",
                "event_type": "TRADE",
                "source_timestamp_ms": 1000,
                "receive_timestamp_ms": 1010,
                "price": 100.0,
                "size": 2.0,
                "aggressor_side": "BUY",
                "asset_id": None,
                "sequence": 1,
            }
        },
        {
            "payload": {
                "source": "BINANCE",
                "event_type": "TRADE",
                "source_timestamp_ms": 61000,
                "receive_timestamp_ms": 61010,
                "price": 101.0,
                "size": 1.0,
                "aggressor_side": "SELL",
                "asset_id": None,
                "sequence": 2,
            }
        },
    ]
    raw = "\n".join(json.dumps(row) for row in rows).encode()
    path.write_bytes(gzip.compress(raw, mtime=0))
    events = _v3_events(path)
    assert len(events) == 2
    assert events[-1].price == 101.0


def test_kalshi_candidate_ranking_filters_non_btc_markets() -> None:
    markets = [
        {"ticker": "KXBTC", "title": "Will Bitcoin be above 100k on Friday?"},
        {"ticker": "KXRAIN", "title": "Will it rain in New York?"},
        {"ticker": "KXBTC2", "title": "Bitcoin price on Friday"},
    ]
    ranked = rank_btc_markets(markets, "Will BTC be above 100000 on Friday?")
    assert [market["ticker"] for market in ranked] == ["KXBTC", "KXBTC2"]
