from app.benchmark_v4 import replay_episode, seal_episode
from app.event_tape_v4 import TapeEvent, TapeLevel
from app.forecast_skill_v4 import (
    brier_decomposition,
    market_relative_forecast_alpha,
    minimum_sample_for_alpha,
)


def _events() -> tuple[TapeEvent, ...]:
    return (
        TapeEvent(
            schema_version=1,
            venue="POLYMARKET",
            asset_id="YES",
            kind="SNAPSHOT",
            receive_timestamp_ms=100,
            sequence=1,
            levels=(TapeLevel("BID", 0.49, 10.0), TapeLevel("ASK", 0.51, 10.0)),
        ),
        TapeEvent(
            schema_version=1,
            venue="POLYMARKET",
            asset_id="YES",
            kind="DELTA",
            receive_timestamp_ms=110,
            sequence=2,
            levels=(TapeLevel("BID", 0.49, 8.0),),
        ),
    )


def test_episode_id_is_stable_under_input_ordering() -> None:
    kwargs = {
        "venue": "POLYMARKET",
        "asset_id": "YES",
        "settlement": {"outcome": "YES"},
        "strategy_config": {"name": "shadow"},
        "expected_invariants": ("no_sequence_gaps", "book_reconstructable"),
    }
    first = seal_episode(events=_events(), **kwargs)
    second = seal_episode(events=tuple(reversed(_events())), **kwargs)
    assert first.episode_id == second.episode_id
    replay = replay_episode(first)
    assert replay["status"] == "PASS"
    assert replay["reconstruction_status"] == "RECONSTRUCTED"


def test_forecast_skill_is_relative_to_market_and_power_gated() -> None:
    model = [0.90, 0.10, 0.80, 0.20, 0.75, 0.25]
    market = [0.60, 0.40, 0.55, 0.45, 0.52, 0.48]
    outcomes = [1, 0, 1, 0, 1, 0]
    result = market_relative_forecast_alpha(
        model,
        market,
        outcomes,
        bootstrap_samples=500,
    )
    assert result.alpha is not None and result.alpha > 0
    assert result.model_brier is not None and result.market_brier is not None
    assert result.model_brier < result.market_brier
    assert result.status == "UNDERPOWERED"
    assert minimum_sample_for_alpha(0.02) == 347
    assert minimum_sample_for_alpha(0.01) >= 4 * 346


def test_brier_decomposition_is_non_negative() -> None:
    result = brier_decomposition([0.8, 0.7, 0.3, 0.2], [1, 1, 0, 0], bins=5)
    assert result.sample_size == 4
    assert result.brier is not None
    assert result.reliability is not None and result.reliability >= 0
    assert result.resolution is not None and result.resolution >= 0
    assert result.uncertainty is not None and result.uncertainty >= 0
