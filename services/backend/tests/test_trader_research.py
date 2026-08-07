from app.trader_research import (
    infer_weather_location,
    trader_reconstruction,
    weather_hypothesis_status,
)


def position(price: float, pnl: float, title: str, bought: float = 100) -> dict:
    return {
        "avgPrice": price,
        "realizedPnl": pnl,
        "totalBought": bought,
        "title": title,
    }


def test_weather_location_is_extracted_without_hardcoding_a_city() -> None:
    assert infer_weather_location("Highest temperature in Auckland on August 7?") == "Auckland"
    assert infer_weather_location("Highest temperature in Tokyo on August 8?") == "Tokyo"


def test_reconstruction_separates_price_buckets_and_payout_asymmetry() -> None:
    rows = [
        position(0.05, 2, "Highest temperature in Auckland on day 1?", 20),
        position(0.08, -1, "Highest temperature in Auckland on day 2?", 10),
        position(0.60, 5, "Highest temperature in Tokyo on day 1?", 30),
        position(0.65, -2, "Highest temperature in Tokyo on day 2?", 20),
    ]
    summary = trader_reconstruction(rows)
    assert summary["sample_size"] == 4
    assert summary["price_buckets"]["LOW_01_10"]["sample_size"] == 2
    assert summary["price_buckets"]["MID_50_70"]["sample_size"] == 2
    assert summary["locations"]["Auckland"]["sample_size"] == 2
    assert summary["overall"]["payoff_ratio"] > 1


def test_weather_claim_stays_unproven_with_small_sample() -> None:
    summary = trader_reconstruction(
        [position(0.05, 1, "Highest temperature in Busan on August 7?", 5)]
    )
    status = weather_hypothesis_status(summary)
    assert status["status"] == "UNPROVEN"
    assert status["reasons"]
