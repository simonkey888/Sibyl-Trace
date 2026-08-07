from __future__ import annotations

import math
import re
from dataclasses import asdict
from statistics import fmean
from typing import Any

from app.research import payout_asymmetry, weather_price_bucket

_LOCATION_PATTERNS = (
    re.compile(r"\bin\s+([A-Z][A-Za-z .'-]+?)(?:\s+on\b|\?|$)"),
    re.compile(r"\b([A-Z][A-Za-z .'-]+?)\s+(?:temperature|weather)\b", re.IGNORECASE),
)


def infer_weather_location(title: str) -> str | None:
    cleaned = " ".join(str(title).split())
    for pattern in _LOCATION_PATTERNS:
        match = pattern.search(cleaned)
        if not match:
            continue
        value = " ".join(match.group(1).strip(" -:,.?").split())
        if 2 <= len(value) <= 80:
            return value
    return None


def _position_notional(position: dict[str, Any]) -> float:
    return max(float(position.get("avgPrice") or 0.0), 0.0) * max(
        float(position.get("totalBought") or 0.0), 0.0
    )


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    mean_x = fmean(xs)
    mean_y = fmean(ys)
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True))
    sum_x = sum((x - mean_x) ** 2 for x in xs)
    sum_y = sum((y - mean_y) ** 2 for y in ys)
    denominator = math.sqrt(sum_x * sum_y)
    return numerator / denominator if denominator > 0 else None


def trader_reconstruction(positions: list[dict[str, Any]]) -> dict[str, Any]:
    valid = [
        position
        for position in positions
        if isinstance(position, dict) and position.get("realizedPnl") is not None
    ]
    overall = payout_asymmetry(float(position.get("realizedPnl") or 0.0) for position in valid)
    price_buckets: dict[str, list[dict[str, Any]]] = {}
    cities: dict[str, list[dict[str, Any]]] = {}
    prices: list[float] = []
    notionals: list[float] = []

    for position in valid:
        price = float(position.get("avgPrice") or 0.0)
        if 0 <= price <= 1:
            price_buckets.setdefault(weather_price_bucket(price), []).append(position)
            notional = _position_notional(position)
            if notional > 0:
                prices.append(price)
                notionals.append(notional)
        location = infer_weather_location(str(position.get("title") or ""))
        if location:
            cities.setdefault(location, []).append(position)

    def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
        asymmetry = payout_asymmetry(float(row.get("realizedPnl") or 0.0) for row in rows)
        return {
            **asdict(asymmetry),
            "realized_pnl": round(sum(float(row.get("realizedPnl") or 0.0) for row in rows), 6),
            "average_entry_price": (
                round(fmean(float(row.get("avgPrice") or 0.0) for row in rows), 6)
                if rows
                else None
            ),
            "average_notional": (
                round(fmean(_position_notional(row) for row in rows), 6) if rows else None
            ),
        }

    return {
        "sample_size": len(valid),
        "overall": {**asdict(overall), "realized_pnl": round(overall.expectancy_cash * overall.sample_size, 6)},
        "price_buckets": {key: summarize(rows) for key, rows in sorted(price_buckets.items())},
        "locations": {key: summarize(rows) for key, rows in sorted(cities.items())},
        "price_size_correlation": _pearson(prices, notionals),
    }


def weather_hypothesis_status(summary: dict[str, Any]) -> dict[str, Any]:
    low = summary.get("price_buckets", {}).get("LOW_01_10", {})
    mid = summary.get("price_buckets", {}).get("MID_50_70", {})
    correlation = summary.get("price_size_correlation")
    sample = int(summary.get("sample_size") or 0)
    reasons: list[str] = []
    if sample < 100:
        reasons.append("overall_sample_below_100")
    if int(low.get("sample_size") or 0) < 20:
        reasons.append("low_price_bucket_below_20")
    if int(mid.get("sample_size") or 0) < 20:
        reasons.append("mid_price_bucket_below_20")
    if correlation is None:
        reasons.append("sizing_relationship_unmeasurable")
    return {
        "status": "UNPROVEN" if reasons else "MEASURED_NOT_VALIDATED",
        "reasons": reasons,
        "positive_price_size_relationship": correlation is not None and correlation > 0,
    }
