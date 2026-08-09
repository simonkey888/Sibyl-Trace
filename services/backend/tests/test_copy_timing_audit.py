from app.copy_timing_audit import SOURCE_PRICE_SEMANTICS, build_copy_timing_analysis


def _row(
    prediction_id: int,
    *,
    source_timestamp: int,
    decision_received_at_ms: int | None,
    arrival_received_at_ms: int | None,
    decision_decay: float | None,
) -> dict:
    return {
        "prediction_id": prediction_id,
        "wallet_address": "0xabc",
        "market": f"market-{prediction_id}",
        "asset_id": f"asset-{prediction_id}",
        "side": "BUY",
        "source_timestamp": source_timestamp,
        "source_price": 0.4,
        "execution": {
            "decision_best_price": 0.42,
            "effective_price": 0.43,
            "status": "FILLED",
        },
        "execution_evidence": {
            "decision_received_at_ms": decision_received_at_ms,
            "arrival_received_at_ms": arrival_received_at_ms,
        },
        "copy_decay": {
            "decision_vs_source": decision_decay,
            "effective_fill_vs_source": 0.03,
        },
    }


def test_copy_timing_measures_source_to_book_without_importing_filter() -> None:
    rows = [
        _row(
            1,
            source_timestamp=1_000,
            decision_received_at_ms=1_015_000,
            arrival_received_at_ms=1_015_300,
            decision_decay=0.02,
        ),
        _row(
            2,
            source_timestamp=2_000,
            decision_received_at_ms=2_030_000,
            arrival_received_at_ms=2_030_500,
            decision_decay=0.08,
        ),
    ]
    result = build_copy_timing_analysis(rows)

    assert result["source_price_semantics"] == SOURCE_PRICE_SEMANTICS
    assert result["automatic_execution_gate"] is False
    assert result["arbitrary_max_age_filter_imported"] is False
    assert result["rows_total"] == 2
    assert result["decision_timing_observations"] == 2
    assert result["source_to_decision_book_ms"] == {
        "min": 15_000,
        "p50": 15_000,
        "p95": 30_000,
        "max": 30_000,
    }
    assert result["worst_source_to_decision_age"][0]["prediction_id"] == 2
    assert result["worst_positive_decision_copy_decay"][0]["prediction_id"] == 2


def test_copy_timing_surfaces_clock_anomaly_instead_of_clamping_it() -> None:
    rows = [
        _row(
            3,
            source_timestamp=3_000,
            decision_received_at_ms=2_999_000,
            arrival_received_at_ms=2_999_500,
            decision_decay=-0.01,
        )
    ]
    result = build_copy_timing_analysis(rows)

    assert result["negative_clock_age_observations"] == 2
    assert result["source_to_decision_book_ms"]["min"] == -1_000
    assert result["source_to_arrival_book_ms"]["min"] == -500
