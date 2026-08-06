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
            data = self._get(
                f"{self.settings.data_api_base}/closed-positions",
                {
                    "user": wallet,
                    "limit": min(50, target - offset),
                    "offset": offset,
                    "sortBy": "TIMESTAMP",
                    "sortDirection": "DESC",
                },
            )
            page = data if isinstance(data, list) else []
            results.extend(page)
            if len(page) < min(50, target - offset):
                break
        return results

    def activity(self, wallet: str, start: int, limit: int = 100) -> list[dict]:
        data = self._get(
            f"{self.settings.data_api_base}/activity",
            {
                "user": wallet,
                "start": max(start, 0),
                "limit": min(limit, 500),
                "type": "TRADE",
                "sortBy": "TIMESTAMP",
                "sortDirection": "ASC",
            },
        )
        return data if isinstance(data, list) else []

    def midpoint(self, asset_id: str) -> float:
        data = self._get(f"{self.settings.clob_api_base}/midpoint", {"token_id": asset_id})
        value = data.get("mid") if isinstance(data, dict) else None
        midpoint = float(value)
        if not 0 < midpoint < 1:
            raise PolymarketError("invalid midpoint")
        return midpoint

    def geoblock(self) -> dict:
        data = self._get(self.settings.geoblock_url)
        return data if isinstance(data, dict) else {"blocked": True, "reason": "invalid_response"}
