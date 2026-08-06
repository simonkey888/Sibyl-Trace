from __future__ import annotations

import json

from app.config import Settings
from app.polymarket import PolymarketClient


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def main() -> None:
    client = PolymarketClient(Settings(app_env="contract", app_version="contract-smoke"))
    summary: dict[str, object] = {}
    try:
        leaders = client.leaderboard("WEEK", 3)
        require(bool(leaders), "leaderboard returned no rows")
        wallet = str(leaders[0].get("proxyWallet") or "")
        require(wallet.startswith("0x") and len(wallet) == 42, "leaderboard wallet shape changed")
        summary["leaderboard"] = len(leaders)

        closed = client.closed_positions(wallet, limit=1)
        require(isinstance(closed, list), "closed positions contract changed")
        summary["closed_positions"] = len(closed)

        activity = client.activity(wallet, start=0, limit=1)
        require(isinstance(activity, list), "activity contract changed")
        summary["activity"] = len(activity)

        trades = client.recent_trades(20)
        require(bool(trades), "recent trades returned no rows")
        midpoint = None
        for trade in trades:
            asset = str(trade.get("asset") or "")
            if not asset:
                continue
            try:
                midpoint = client.midpoint(asset)
                break
            except Exception:
                continue
        require(midpoint is not None, "no recent trade had an available midpoint")
        summary["midpoint"] = midpoint

        geoblock = client.geoblock()
        require(isinstance(geoblock.get("blocked"), bool), "geoblock contract changed")
        summary["geoblock_blocked"] = geoblock["blocked"]
    finally:
        client.close()
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
