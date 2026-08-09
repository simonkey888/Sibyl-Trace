from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.source_strategy import (
    DIRECTIONAL_CANDIDATE,
    INSUFFICIENT_EVIDENCE,
    NON_DIRECTIONAL_FULL_SET,
    NON_DIRECTIONAL_MAKER,
    NON_DIRECTIONAL_TWO_SIDED,
    SourceStrategyPolicy,
    classify_source_strategy,
    fetch_public_activity_events,
    profile_hash_valid,
)


WALLET = "0x1111111111111111111111111111111111111111"
POLICY = SourceStrategyPolicy(
    min_trade_count=6,
    min_paired_conditions=2,
    max_paired_trade_fraction=0.50,
)


def trade(i: int, condition: str, outcome: int = 0) -> dict:
    return {
        "type": "TRADE",
        "transactionHash": f"0x{i:064x}",
        "conditionId": condition,
        "asset": f"asset-{condition}-{outcome}",
        "side": "BUY",
        "outcomeIndex": outcome,
        "timestamp": 1_800_000_000 + i,
        "price": "0.50",
        "size": "10",
        "usdcSize": "5",
    }


def classify(events):
    return classify_source_strategy(
        WALLET,
        events,
        cutoff_at=1_900_000_000,
        policy=POLICY,
    )


def test_directional_candidate_has_recomputable_hash():
    profile = classify([trade(i, f"condition-{i}") for i in range(6)])
    assert profile.classification == DIRECTIONAL_CANDIDATE
    assert profile.rejection_reason is None
    assert profile.directional is True
    assert profile.attributable_trade_count == 6
    assert profile_hash_valid(profile.to_dict()) is True


def test_maker_rebate_rejects_profitable_looking_wallet():
    events = [trade(i, f"condition-{i}") for i in range(6)]
    events.append({"type": "MAKER_REBATE", "timestamp": 1_800_000_100})
    profile = classify(events)
    assert profile.classification == NON_DIRECTIONAL_MAKER
    assert profile.rejection_reason == "source_strategy_maker_rebate"


def test_taker_rebate_is_recorded_without_inventing_direction_semantics():
    events = [trade(i, f"condition-{i}") for i in range(6)]
    events.append({"type": "TAKER_REBATE", "timestamp": 1_800_000_100})
    profile = classify(events)
    assert profile.taker_rebate_count == 1
    assert profile.classification == DIRECTIONAL_CANDIDATE
    assert profile.rejection_reason is None


def test_split_merge_or_conversion_rejects_full_set_strategy():
    for event_type in ("SPLIT", "MERGE", "CONVERSION"):
        events = [trade(i, f"condition-{i}") for i in range(6)]
        events.append(
            {
                "type": event_type,
                "conditionId": "condition-0",
                "transactionHash": f"0x{event_type.lower()}",
                "timestamp": 1_800_000_100,
            }
        )
        profile = classify(events)
        assert profile.classification == NON_DIRECTIONAL_FULL_SET
        assert profile.rejection_reason == "source_strategy_full_set_or_conversion"


def test_repeated_two_sided_conditions_are_not_directional_alpha():
    events = [
        trade(1, "a", 0),
        trade(2, "a", 1),
        trade(3, "b", 0),
        trade(4, "b", 1),
        trade(5, "c", 0),
        trade(6, "d", 0),
    ]
    profile = classify(events)
    assert profile.classification == NON_DIRECTIONAL_TWO_SIDED
    assert profile.paired_condition_count == 2
    assert profile.paired_trade_count == 4
    assert profile.paired_trade_fraction == pytest.approx(4 / 6, abs=1e-6)


def test_single_flip_does_not_overclassify_two_sided_strategy():
    events = [
        trade(1, "a", 0),
        trade(2, "a", 1),
        trade(3, "b", 0),
        trade(4, "c", 0),
        trade(5, "d", 0),
        trade(6, "e", 0),
    ]
    profile = classify(events)
    assert profile.classification == DIRECTIONAL_CANDIDATE


def test_insufficient_history_fails_closed():
    profile = classify([trade(1, "a"), trade(2, "b")])
    assert profile.classification == INSUFFICIENT_EVIDENCE
    assert profile.rejection_reason == "source_strategy_insufficient_evidence"


def test_unattributable_trades_do_not_satisfy_directional_minimum():
    events = []
    for i in range(6):
        row = trade(i, f"condition-{i}")
        row.pop("conditionId")
        row.pop("outcomeIndex")
        events.append(row)
    profile = classify(events)
    assert profile.trade_count == 6
    assert profile.attributable_trade_count == 0
    assert profile.unattributable_trade_count == 6
    assert profile.classification == INSUFFICIENT_EVIDENCE


def test_missing_or_future_timestamps_cannot_authorize_directional_source():
    events = [trade(i, f"condition-{i}") for i in range(6)]
    for row in events[:3]:
        row.pop("timestamp")
    for row in events[3:]:
        row["timestamp"] = 1_900_000_001
    profile = classify(events)
    assert profile.invalid_timestamp_event_count == 6
    assert profile.event_count == 0
    assert profile.attributable_trade_count == 0
    assert profile.classification == INSUFFICIENT_EVIDENCE


def test_outcome_zero_and_one_are_distinct_in_activity_evidence():
    zero = classify([trade(i, f"condition-{i}", 0) for i in range(6)])
    one = classify([trade(i, f"condition-{i}", 1) for i in range(6)])
    assert zero.activity_sample_hash != one.activity_sample_hash


def test_fallback_outcome_is_part_of_activity_hash():
    yes_rows = [trade(i, f"condition-{i}") for i in range(6)]
    no_rows = [trade(i, f"condition-{i}") for i in range(6)]
    for row in yes_rows:
        row.pop("outcomeIndex")
        row["outcome"] = "Yes"
    for row in no_rows:
        row.pop("outcomeIndex")
        row["outcome"] = "No"
    yes_profile = classify(yes_rows)
    no_profile = classify(no_rows)
    assert yes_profile.activity_sample_hash != no_profile.activity_sample_hash
    assert yes_profile.evidence_hash != no_profile.evidence_hash


def test_activity_fetch_uses_full_history_cutoff_and_copyability_types():
    class Client:
        settings = SimpleNamespace(data_api_base="https://data.test")

        def __init__(self):
            self.params = None

        def _get(self, url, params=None):
            assert url == "https://data.test/activity"
            self.params = params
            return [trade(1, "a")]

    client = Client()
    rows = fetch_public_activity_events(
        client,
        WALLET,
        cutoff_at=1_900_000_000,
        limit=30,
    )
    assert len(rows) == 1
    assert client.params["start"] == 1
    assert client.params["end"] == 1_900_000_000
    assert client.params["limit"] == 30
    assert "TRADE" in client.params["type"]
    assert "MERGE" in client.params["type"]
    assert "MAKER_REBATE" in client.params["type"]
    assert "TAKER_REBATE" in client.params["type"]


def test_activity_fetch_drops_timestampless_rows_before_hashing():
    class Client:
        settings = SimpleNamespace(data_api_base="https://data.test")

        def _get(self, _url, params=None):
            invalid = trade(1, "a")
            invalid.pop("timestamp")
            return [invalid, trade(2, "b")]

    rows = fetch_public_activity_events(
        Client(),
        WALLET,
        cutoff_at=1_900_000_000,
        limit=30,
    )
    assert len(rows) == 1
    assert rows[0]["conditionId"] == "b"


def test_event_order_does_not_change_sample_or_evidence_hash():
    events = [trade(i, f"condition-{i}") for i in range(6)]
    one = classify(events)
    two = classify(list(reversed(events)))
    assert one.activity_sample_hash == two.activity_sample_hash
    assert one.evidence_hash == two.evidence_hash


def test_hash_detects_profile_tampering():
    profile = classify([trade(i, f"condition-{i}") for i in range(6)]).to_dict()
    assert profile_hash_valid(profile) is True
    profile["trade_count"] += 1
    assert profile_hash_valid(profile) is False
