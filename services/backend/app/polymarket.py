from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.config import Settings


class PolymarketError(RuntimeError):
    pass


TAKER_FEE_RATES = {
    "crypto": 0.07,
    "sports": 0.03,
    "finance": 0.04,
    "politics": 0.04,
    "economics": 0.05,
    "culture": 0.05,
    "weather": 0.05,
    "mentions": 0.04,
    "tech": 0.04,
    "geopolitics": 0.0,
    "world": 0.0,
    "other": 0.05,
    "general": 0.05,
}


def taker_fee_rate_for_category(category: str | None) -> float:
    normalized = str(category or "").strip().casefold()
    categories = sorted(TAKER_FEE_RATES.items(), key=lambda item: len(item[0]), reverse=True)
    for key, rate in categories:
        if key in normalized:
            return rate
    return TAKER_FEE_RATES["general"]


class PolymarketClient:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = httpx.Client(
            timeout=settings.http_timeout_seconds,
            headers={"User-Agent": f"sibyl-trace/{settings.app_version}"},
            follow_redirects=True,
        )

    def close(self) -> None:
        self.client.close()

    def _get(self, url: str, params: dict[str, Any] | None = None) -> Any:
        response = self.client.get(url, params=params)
        response.raise_for_status()
        return response.json()

    def leaderboard(self, period: str, limit: int) -> list[dict]:
        data = self._get(
            f"{self.settings.data_api_base}/v1/leaderboard",
            {"timePeriod": period, "orderBy": "PNL", "limit": limit, "offset": 0},
        )
        return data if isinstance(data, list) else []

    def leaderboard_username(
        self,
        username: str,
        *,
        category: str = "OVERALL",
        period: str = "ALL",
    ) -> dict | None:
        data = self._get(
            f"{self.settings.data_api_base}/v1/leaderboard",
            {
                "category": category,
                "timePeriod": period,
                "orderBy": "PNL",
                "limit": 50,
                "offset": 0,
                "userName": username,
            },
        )
        rows = data if isinstance(data, list) else []
        wanted = username.casefold()
        return next(
            (
                row
                for row in rows
                if isinstance(row, dict)
                and str(row.get("userName") or "").casefold() == wanted
            ),
            None,
        )

    def _closed_positions(self, wallet: str, limit: int) -> list[dict]:
        results: list[dict] = []
        target = min(max(limit, 0), 5000)
        for offset in range(0, target, 50):
            page_limit = min(50, target - offset)
            data = self._get(
                f"{self.settings.data_api_base}/closed-positions",
                {
                    "user": wallet,
                    "limit": page_limit,
                    "offset": offset,
                    "sortBy": "TIMESTAMP",
                    "sortDirection": "DESC",
                },
            )
            page = (
                [item for item in data if isinstance(item, dict)]
                if isinstance(data, list)
                else []
            )
            results.extend(page)
            if len(page) < page_limit:
                break
        return results

    def closed_positions(self, wallet: str, limit: int = 200) -> list[dict]:
        return self._closed_positions(wallet, min(limit, 200))

    def research_closed_positions(self, wallet: str, limit: int = 1000) -> list[dict]:
        return self._closed_positions(wallet, limit)

    def activity(self, wallet: str, start: int, limit: int = 500) -> list[dict]:
        target = min(max(limit, 0), 5000)
        if target == 0:
            return []

        results: list[dict] = []
        seen: set[tuple[str, str, str, int, str, str]] = set()
        page_size = min(500, target)
        for offset in range(0, target, page_size):
            current_limit = min(page_size, target - offset)
            data = self._get(
                f"{self.settings.data_api_base}/activity",
                {
                    "user": wallet,
                    "start": max(start, 0),
                    "limit": current_limit,
                    "offset": offset,
                    "type": "TRADE",
                    "sortBy": "TIMESTAMP",
                    "sortDirection": "ASC",
                },
            )
            page = data if isinstance(data, list) else []
            for item in page:
                key = (
                    str(item.get("transactionHash") or ""),
                    str(item.get("asset") or ""),
                    str(item.get("side") or ""),
                    int(item.get("timestamp") or 0),
                    str(item.get("price") or ""),
                    str(item.get("size") or ""),
                )
                if key in seen:
                    continue
                seen.add(key)
                results.append(item)
            if len(page) < current_limit:
                break
        return results

    def recent_trades(self, limit: int = 20) -> list[dict]:
        data = self._get(
            f"{self.settings.data_api_base}/trades",
            {"limit": min(max(limit, 1), 100), "offset": 0},
        )
        return data if isinstance(data, list) else []

    def closed_markets(self, condition_ids: list[str]) -> list[dict]:
        unique = list(dict.fromkeys(value for value in condition_ids if value))[:100]
        if not unique:
            return []
        data = self._get(
            f"{self.settings.gamma_api_base}/markets",
            {
                "condition_ids": unique,
                "closed": "true",
                "limit": len(unique),
            },
        )
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            markets = data.get("markets")
            if isinstance(markets, list):
                return [item for item in markets if isinstance(item, dict)]
        return []

    def active_btc_short_markets(self, *, horizon_minutes: int = 20) -> list[dict]:
        now = datetime.now(UTC)
        data = self._get(
            f"{self.settings.gamma_api_base}/markets",
            {
                "limit": 500,
                "offset": 0,
                "order": "endDate",
                "ascending": True,
                "closed": False,
                "end_date_min": now.isoformat(),
                "end_date_max": (now + timedelta(minutes=horizon_minutes)).isoformat(),
            },
        )
        rows = [item for item in data if isinstance(item, dict)] if isinstance(data, list) else []
        matches: list[dict] = []
        for market in rows:
            text = f"{market.get('question') or ''} {market.get('slug') or ''}".casefold()
            if not (("btc" in text or "bitcoin" in text) and "up" in text and "down" in text):
                continue
            if market.get("active") is False or market.get("closed") is True:
                continue
            if market.get("acceptingOrders") is False:
                continue
            matches.append(market)
        return matches

    def clob_market_info(self, condition_id: str) -> dict:
        data = self._get(f"{self.settings.clob_api_base}/clob-markets/{condition_id}")
        if not isinstance(data, dict):
            raise PolymarketError("CLOB market info was not an object")
        return data

    def order_book(self, asset_id: str) -> dict:
        data = self._get(f"{self.settings.clob_api_base}/book", {"token_id": asset_id})
        if not isinstance(data, dict) or str(data.get("asset_id") or "") != asset_id:
            raise PolymarketError("order book response did not match requested asset")
        return data

    def fee_rate_bps(self, asset_id: str) -> int:
        data = self._get(f"{self.settings.clob_api_base}/fee-rate", {"token_id": asset_id})
        if not isinstance(data, dict) or data.get("base_fee") is None:
            raise PolymarketError("fee-rate response did not contain base_fee")
        value = int(data["base_fee"])
        if value < 0:
            raise PolymarketError("invalid negative base fee")
        return value

    def tick_size(self, asset_id: str) -> float:
        data = self._get(f"{self.settings.clob_api_base}/tick-size", {"token_id": asset_id})
        if not isinstance(data, dict) or data.get("minimum_tick_size") is None:
            raise PolymarketError("tick-size response did not contain minimum_tick_size")
        value = float(data["minimum_tick_size"])
        if value <= 0:
            raise PolymarketError("invalid tick size")
        return value

    def midpoint(self, asset_id: str) -> float:
        data = self._get(f"{self.settings.clob_api_base}/midpoint", {"token_id": asset_id})
        value = None
        if isinstance(data, dict):
            value = data.get("mid_price")
            if value is None:
                value = data.get("mid")
        if value is None:
            raise PolymarketError("midpoint response did not contain a price")
        midpoint = float(value)
        if not 0 < midpoint < 1:
            raise PolymarketError("invalid midpoint")
        return midpoint

    def geoblock(self) -> dict:
        data = self._get(self.settings.geoblock_url)
        return data if isinstance(data, dict) else {"blocked": True, "reason": "invalid_response"}
