from types import SimpleNamespace

import pytest

from app.wallet_forensics import compute_execution_mix, fetch_public_execution_mix

WALLET = "0x" + "d" * 40


def trade(timestamp: int, index: int) -> dict:
    return {
        "timestamp": timestamp,
        "transactionHash": f"0xtrade-{index}",
        "asset": f"asset-{index}",
        "side": "BUY",
        "price": 0.5,
        "size": 20,
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
