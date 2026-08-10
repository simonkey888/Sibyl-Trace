from types import SimpleNamespace

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db import Base
from app.models import Wallet, WalletForensicsProfile
from app.scanner import scan_wallets

WALLET = "0x" + "c" * 40


def event(timestamp: int, index: int) -> dict:
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


def closed_positions() -> list[dict]:
    return (
        [{"realizedPnl": 2.0} for _ in range(15)]
        + [{"realizedPnl": -1.0} for _ in range(5)]
        + [{"realizedPnl": 0.0} for _ in range(5)]
    )


class HighMakerClient:
    settings = SimpleNamespace(data_api_base="https://data-api.polymarket.com")

    def leaderboard(self, _period: str, _limit: int) -> list[dict]:
        return [{"proxyWallet": WALLET, "pnl": 500, "vol": 50_000, "userName": "makerish"}]

    def closed_positions(self, address: str) -> list[dict]:
        assert address == WALLET
        return closed_positions()

    def _get(self, url: str, params: dict) -> list[dict]:
        assert params["user"] == WALLET
        if url.endswith("/trades"):
            all_rows = [event(500 + index, index) for index in range(100)]
            return all_rows[:10] if params.get("takerOnly") is True else all_rows
        return [event(100 + index, index) for index in range(30)]


def test_high_confidence_maker_mix_vetoes_directional_scanner_selection(monkeypatch) -> None:
    monkeypatch.setattr("app.scanner.time.time", lambda: 1_000.0)
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        selected = scan_wallets(
            db,
            HighMakerClient(),
            Settings(
                candidate_limit=3,
                tracked_wallet_limit=1,
                source_strategy_min_trade_count=5,
                source_strategy_activity_limit=30,
            ),
            prospective=True,
            source_strategy_gate=True,
        )
        assert selected == []
        wallet = db.get(Wallet, WALLET)
        profile = db.get(WalletForensicsProfile, WALLET)
        assert wallet is not None
        assert wallet.selected is False
        assert wallet.rejection_reason == "execution_mix_structural_maker"
        assert profile is not None
        assert profile.lane == "STRUCTURAL_MAKER_RESEARCH"
        assert profile.copyable_directional is False
        assert profile.research_only is True
