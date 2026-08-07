from typing import Any

import httpx

from app.config import Settings


class PolymarketError(RuntimeError):
    pass


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

    def closed_positions(self, wallet: str, limit: int = 200) -> list[dict]:
        results: list[dict] = []
        target = min(max(limit, 0), 200)
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
            page = data if isinstance(data, list) else []
            results.extend(page)
            if len(page) < page_limit:
                break
        return results

    def activity(self, wallet: str, start: int, limit: int = 500) -> list[dict]:
        target = min(max(limit, 0), 10000)
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
