from __future__ import annotations

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db import Base
from app.latency import CaptureResult, FeedEvent
from app.research_cycle import (
    _target_from_market,
    preregister_default_hypotheses,
    research_totals,
    run_latency_lab,
    run_reference_research,
    run_research_cycle,
)
from app.research_models import (
    ResearchCheckpoint,
    ResearchExperiment,
    ResearchHypothesis,
    ResearchObservation,
    WatchdogEvent,
)


def session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def paper_settings(**overrides) -> Settings:
    values = {
        "trading_mode": "PAPER",
        "paper_trading_enabled": True,
        "research_enabled": True,
        "evidence_generation": "SIBYL_PAPER_V2",
    }
    values.update(overrides)
    return Settings(**values)


class TargetClient:
    def active_btc_short_markets(self, *, horizon_minutes: int = 20) -> list[dict]:
        assert horizon_minutes == 30
        return [
            {
                "conditionId": "condition",
                "question": "Bitcoin Up or Down - 5 Minutes",
                "category": "Crypto",
                "endDate": "2026-08-07T06:00:00Z",
                "orderPriceMinTickSize": 0.01,
            }
        ]

    def clob_market_info(self, condition_id: str) -> dict:
        assert condition_id == "condition"
        return {
            "t": [
                {"o": "Up", "t": "up-asset"},
                {"o": "Down", "t": "down-asset"},
            ],
            "mts": 0.01,
            "fd": {"r": 0.07},
        }


class ReferenceClient:
    def leaderboard_username(self, username: str, *, category: str, period: str) -> dict:
        assert period == "ALL"
        if username == "okkokok":
            assert category == "WEATHER"
        return {
            "proxyWallet": "0x" + "1" * 40,
            "userName": username,
            "pnl": 123.0,
            "vol": 456.0,
            "rank": 7,
        }

    def research_closed_positions(self, wallet: str, limit: int = 1000) -> list[dict]:
        assert wallet == "0x" + "1" * 40
        assert limit == 1000
        return [
            {
                "avgPrice": 0.05,
                "realizedPnl": 2.0,
                "totalBought": 20,
                "title": "Highest temperature in Auckland on August 7?",
                "timestamp": 10,
            },
            {
                "avgPrice": 0.60,
                "realizedPnl": -1.0,
                "totalBought": 10,
                "title": "Highest temperature in Tokyo on August 7?",
                "timestamp": 11,
            },
        ]


def test_default_hypotheses_are_preregistered_idempotently() -> None:
    with session() as db:
        first = preregister_default_hypotheses(db)
        second = preregister_default_hypotheses(db)
        count = db.scalar(select(func.count()).select_from(ResearchHypothesis))
    assert first == second
    assert len(first) == 5
    assert count == 5


def test_target_uses_exact_clob_fee_metadata() -> None:
    market = TargetClient().active_btc_short_markets(horizon_minutes=30)[0]
    target = _target_from_market(TargetClient(), market)
    assert target.outcome_assets == {"Up": "up-asset", "Down": "down-asset"}
    assert target.fee_rate == 0.07
    assert target.tick_size == 0.01
    assert target.end_timestamp_ms > 0


def test_latency_lab_persists_executable_observation(monkeypatch) -> None:
    async def fake_capture(*_args, **_kwargs) -> CaptureResult:
        return CaptureResult(
            events=(
                FeedEvent("BINANCE", 1000, 1000, price=100.0),
                FeedEvent("COINBASE", 1000, 1000, price=100.0),
                FeedEvent(
                    "POLYMARKET",
                    1900,
                    1900,
                    bid=0.49,
                    ask=0.50,
                    bid_size=20,
                    ask_size=20,
                    asset_id="up-asset",
                ),
                FeedEvent("BINANCE", 2000, 2000, price=100.1),
                FeedEvent("COINBASE", 2000, 2000, price=100.1),
                FeedEvent(
                    "POLYMARKET",
                    2250,
                    2250,
                    bid=0.56,
                    ask=0.57,
                    bid_size=20,
                    ask_size=20,
                    asset_id="up-asset",
                ),
            ),
            errors=(),
        )

    monkeypatch.setattr("app.research_cycle.capture_latency_window", fake_capture)
    settings = paper_settings(latency_lab_enabled=True, latency_capture_seconds=2)
    with session() as db:
        summary = run_latency_lab(db, TargetClient(), settings, "run-1")
        observations = list(db.scalars(select(ResearchObservation)).all())
        checkpoints = list(db.scalars(select(ResearchCheckpoint)).all())
        experiments = list(db.scalars(select(ResearchExperiment)).all())
        watchdogs = list(db.scalars(select(WatchdogEvent)).all())
    assert summary["status"] == "CAPTURED"
    assert summary["events"] == 6
    assert summary["executable_divergences"] >= 1
    assert len(observations) >= 1
    assert observations[0].fillable is True
    assert len(checkpoints) == 1
    assert len(experiments) == 1
    assert watchdogs[0].state == "GREEN"


def test_reference_research_is_append_only_for_same_snapshot() -> None:
    settings = paper_settings(
        reference_research_enabled=True,
        reference_usernames="okkokok",
    )
    with session() as db:
        first = run_reference_research(db, ReferenceClient(), settings)
        second = run_reference_research(db, ReferenceClient(), settings)
        observations = int(
            db.scalar(select(func.count()).select_from(ResearchObservation)) or 0
        )
    assert first["traders"]["okkokok"]["status"] == "RECONSTRUCTED"
    assert second["traders"]["okkokok"]["sample_size"] == 2
    assert observations == 1


def test_full_research_cycle_without_optional_capture_is_safe() -> None:
    settings = paper_settings(latency_lab_enabled=False, reference_research_enabled=False)
    with session() as db:
        result = run_research_cycle(db, TargetClient(), settings, "run-full")
        totals = research_totals(db)
    assert result["status"] == "COMPLETE"
    assert result["latency"]["status"] == "DISABLED"
    assert result["watchdog_state"] == "YELLOW"
    assert totals["hypotheses"] == 5
