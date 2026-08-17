from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.source_strategy import (
    DIRECTIONAL_CANDIDATE,
    INSUFFICIENT_EVIDENCE,
    NON_DIRECTIONAL_FULL_SET,
    NON_DIRECTIONAL_TWO_SIDED,
    UNAVAILABLE,
    ActivityHistoryEvidence,
    SourceActivityHistory,
    SourceStrategyPolicy,
    canonical_hash,
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


def authoritative(events):
    if isinstance(events, SourceActivityHistory):
        return events
    rows = list(events)
    evidence = ActivityHistoryEvidence(
        status="COMPLETE",
        scope="FULL_AVAILABLE_FILTERED_HISTORY",
        requested_limit=max(len(rows) + 1, 1),
        returned_rows=len(rows),
        pages_fetched=1,
        page_size=max(len(rows) + 1, 1),
        exhausted=True,
        has_more=False,
        malformed_rows=0,
        invalid_timestamp_rows=0,
        source_hash=canonical_hash("authoritative-test-fixture"),
    )
    return SourceActivityHistory(rows, evidence)


def classify(events):
    return classify_source_strategy(
        WALLET,
        authoritative(events),
        cutoff_at=1_900_000_000,
        policy=POLICY,
    )


def test_raw_list_without_completeness_metadata_cannot_authorize_directional():
    profile = classify_source_strategy(
        WALLET,
        [trade(i, f"condition-{i}") for i in range(6)],
        cutoff_at=1_900_000_000,
        policy=POLICY,
    )
    assert profile.classification == UNAVAILABLE
    assert profile.directional is False
    assert profile.rejection_reason == "source_strategy_history_evidence_missing"


def test_directional_candidate_has_recomputable_hash():
    profile = classify([trade(i, f"condition-{i}") for i in range(6)])
    assert profile.classification == DIRECTIONAL_CANDIDATE
    assert profile.rejection_reason is None
    assert profile.directional is True
    assert profile.attributable_trade_count == 6
    assert profile_hash_valid(profile.to_dict()) is True


def test_maker_rebate_is_execution_style_not_directionality():
    events = [trade(i, f"condition-{i}") for i in range(6)]
    events.append({"type": "MAKER_REBATE", "timestamp": 1_800_000_100})
    profile = classify(events)
    assert profile.maker_rebate_count == 1
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


def test_activity_fetch_proves_short_page_is_exhaustive():
    class Client:
        settings = SimpleNamespace(data_api_base="https://data.test")

        def __init__(self):
            self.calls: list[dict] = []

        def _get(self, url, params=None):
            assert url == "https://data.test/activity"
            self.calls.append(dict(params or {}))
            return [trade(1, "a")]

    client = Client()
    rows = fetch_public_activity_events(
        client,
        WALLET,
        cutoff_at=1_900_000_000,
        limit=30,
    )
    assert len(rows) == 1
    assert rows.evidence.authoritative is True
    assert rows.evidence.exhausted is True
    assert rows.evidence.has_more is False
    assert len(client.calls) == 1
    assert client.calls[0]["start"] == 1
    assert client.calls[0]["end"] == 1_900_000_000
    assert client.calls[0]["limit"] == 30
    assert "TRADE" in client.calls[0]["type"]
    assert "MERGE" in client.calls[0]["type"]
    assert "MAKER_REBATE" in client.calls[0]["type"]
    assert "TAKER_REBATE" not in client.calls[0]["type"]


def test_exact_limit_activity_page_requires_empty_probe_to_be_authoritative():
    class Client:
        settings = SimpleNamespace(data_api_base="https://data.test")

        def _get(self, _url, params=None):
            if params["offset"] == 0:
                return [trade(i, f"condition-{i}") for i in range(6)]
            assert params["offset"] == 6
            assert params["limit"] == 1
            return []

    rows = fetch_public_activity_events(
        Client(),
        WALLET,
        cutoff_at=1_900_000_000,
        limit=6,
    )
    assert rows.evidence.authoritative is True
    assert rows.evidence.exhausted is True
    profile = classify(rows)
    assert profile.classification == DIRECTIONAL_CANDIDATE
    assert profile.activity_history["status"] == "COMPLETE"


def test_exact_limit_activity_page_with_row_1001_fails_closed():
    class Client:
        settings = SimpleNamespace(data_api_base="https://data.test")

        def _get(self, _url, params=None):
            if params["offset"] == 0:
                return [trade(i, f"condition-{i}") for i in range(6)]
            return [
                {
                    "type": "MERGE",
                    "conditionId": "condition-0",
                    "transactionHash": "0xolder-structural",
                    "timestamp": 1_700_000_000,
                }
            ]

    rows = fetch_public_activity_events(
        Client(),
        WALLET,
        cutoff_at=1_900_000_000,
        limit=6,
    )
    assert rows.evidence.authoritative is False
    assert rows.evidence.has_more is True
    profile = classify(rows)
    assert profile.classification == UNAVAILABLE
    assert profile.rejection_reason == "source_strategy_history_incomplete"


def test_malformed_activity_row_makes_sample_non_authoritative():
    class Client:
        settings = SimpleNamespace(data_api_base="https://data.test")

        def _get(self, _url, params=None):
            return [trade(1, "a"), "not-a-row"]

    rows = fetch_public_activity_events(
        Client(),
        WALLET,
        cutoff_at=1_900_000_000,
        limit=30,
    )
    assert rows.evidence.authoritative is False
    assert rows.evidence.malformed_rows == 1
    assert classify(rows).classification == UNAVAILABLE


def test_timestampless_activity_row_makes_sample_non_authoritative():
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
    assert rows.evidence.authoritative is False
    assert rows.evidence.invalid_timestamp_rows == 1
    assert classify(rows).classification == UNAVAILABLE


def test_transport_shape_error_fails_closed():
    class Client:
        settings = SimpleNamespace(data_api_base="https://data.test")

        def _get(self, _url, params=None):
            return {"error": "not a list"}

    with pytest.raises(ValueError, match="public_activity_response_not_list"):
        fetch_public_activity_events(
            Client(),
            WALLET,
            cutoff_at=1_900_000_000,
            limit=30,
        )


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


def test_transport_order_permutation_preserves_authoritative_source_hash_and_profile():
    rows = [trade(i, f"condition-{i}") for i in range(6)]

    class Client:
        settings = SimpleNamespace(data_api_base="https://data.test")

        def __init__(self, payload):
            self.payload = payload

        def _get(self, _url, params=None):
            return self.payload if int(params.get("offset") or 0) == 0 else []

    one = fetch_public_activity_events(Client(rows), WALLET, cutoff_at=1_900_000_000, limit=30)
    two = fetch_public_activity_events(
        Client(list(reversed(rows))), WALLET, cutoff_at=1_900_000_000, limit=30
    )
    assert one.evidence.source_hash == two.evidence.source_hash
    first = classify_source_strategy(WALLET, one, cutoff_at=1_900_000_000, policy=POLICY)
    second = classify_source_strategy(WALLET, two, cutoff_at=1_900_000_000, policy=POLICY)
    assert first.activity_sample_hash == second.activity_sample_hash
    assert first.evidence_hash == second.evidence_hash
