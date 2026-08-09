from __future__ import annotations

from app.source_strategy import (
    DIRECTIONAL_CANDIDATE,
    INSUFFICIENT_EVIDENCE,
    NON_DIRECTIONAL_FULL_SET,
    NON_DIRECTIONAL_MAKER,
    NON_DIRECTIONAL_TWO_SIDED,
    SourceStrategyPolicy,
    classify_source_strategy,
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
    assert profile_hash_valid(profile.to_dict()) is True


def test_maker_rebate_rejects_profitable_looking_wallet():
    events = [trade(i, f"condition-{i}") for i in range(6)]
    events.append({"type": "MAKER_REBATE", "timestamp": 1_800_000_100})
    profile = classify(events)
    assert profile.classification == NON_DIRECTIONAL_MAKER
    assert profile.rejection_reason == "source_strategy_maker_rebate"


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
    assert profile.paired_trade_fraction == 4 / 6


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
