from types import SimpleNamespace

import pytest

from app.source_strategy import SourceStrategyPolicy, classify_source_strategy
from app.wallet_forensics import (
    DIRECTIONAL_COPY_RESEARCH,
    compute_execution_mix,
    compute_wallet_forensics,
    fetch_public_execution_mix,
)

WALLET = "0x" + "d" * 40


def trade(timestamp: int, index: int) -> dict:
    return {
        "type": "TRADE",
        "timestamp": timestamp,
        "transactionHash": f"0xtrade-{index}",
        "conditionId": f"condition-{index}",
        "asset": f"asset-{index}",
        "side": "BUY",
        "outcomeIndex": 0,
        "outcome": "Yes",
        "price": 0.5,
        "size": 20,
        "usdcSize": 10,
    }


def test_execution_mix_hashes_the_reconciled_samples() -> None:
    all_rows = [trade(100 + index, index) for index in range(5)]
    taker_rows = [all_rows[1], all_rows[2]]
    mix = compute_execution_mix(all_rows, taker_rows, row_limit=500)
    assert mix["proven"] is True
    assert mix["maker"] == 3
    assert mix["taker"] == 2
    assert len(mix["all_sample_hash"]) == 64
    assert len(mix["taker_sample_hash"]) == 64
    assert mix["all_sample_hash"] != mix["taker_sample_hash"]


def test_public_execution_mix_rejects_non_list_api_shapes() -> None:
    class Client:
        settings = SimpleNamespace(data_api_base="https://data-api.polymarket.com")

        def _get(self, _url: str, params: dict):
            return {} if params.get("takerOnly") is False else []

    with pytest.raises(ValueError, match="public_execution_mix_all_response_not_list"):
        fetch_public_execution_mix(Client(), WALLET)


def test_maker_heavy_mix_is_execution_style_not_directionality() -> None:
    cutoff = 2_000
    events = [trade(1_900 + index, index) for index in range(30)]
    source = classify_source_strategy(
        WALLET,
        events,
        cutoff_at=cutoff,
        policy=SourceStrategyPolicy(min_trade_count=20),
    ).to_dict()
    mix = compute_execution_mix(
        [trade(1_000 + index, index) for index in range(100)],
        [trade(1_000 + index, index) for index in range(10)],
        row_limit=500,
    )
    profile = compute_wallet_forensics(
        WALLET,
        events,
        [{"realizedPnl": 1.0} for _ in range(20)],
        cutoff_at=cutoff,
        source_strategy_profile=source,
        execution_mix=mix,
    ).to_dict()
    assert profile["lane"] == DIRECTIONAL_COPY_RESEARCH
    assert profile["copyable_directional"] is True
    assert profile["execution_style"] == "MAKER_HEAVY"
    assert profile["selection_veto"] is False
    assert profile["execution_gate"] is False
