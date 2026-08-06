import os
from datetime import UTC, datetime

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("APP_ENV", "development")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.config import Settings  # noqa: E402
from app.db import Base  # noqa: E402
from app.models import PaperOrder, Signal, Wallet  # noqa: E402
from app.paper import (  # noqa: E402
    PaperEngine,
    activity_source_keys,
    ingest_wallet_activity,
)
from app.trial import render_markdown  # noqa: E402


class ActivityProbe:
    def __init__(self, activities: list[dict] | None = None) -> None:
        self.start: int | None = None
        self.limit: int | None = None
        self.activities = activities or []
        self.midpoint_calls = 0

    def activity(self, _wallet: str, start: int, limit: int = 100) -> list[dict]:
        self.start = start
        self.limit = limit
        return self.activities

    def midpoint(self, _asset_id: str) -> float:
        self.midpoint_calls += 1
        return 0.5


def memory_engine():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return engine


def wallet(address_digit: str = "1") -> Wallet:
    return Wallet(
        address="0x" + address_digit * 40,
        selected=True,
        score=80,
        last_activity_at=0,
    )


def trade(*, tx: str, timestamp: int, size: float = 40) -> dict:
    return {
        "type": "TRADE",
        "transactionHash": tx,
        "asset": "asset-1",
        "conditionId": "condition-1",
        "side": "BUY",
        "timestamp": timestamp,
        "price": 0.5,
        "size": size,
        "usdcSize": size * 0.5,
        "outcomeIndex": 0,
        "title": "Test market",
        "outcome": "YES",
    }


def paper_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "trading_mode": "PAPER",
        "activity_lookback_seconds": 500,
        "risk_max_signal_age_seconds": 30,
    }
    values.update(overrides)
    return Settings(**values)


def test_delayed_trial_profile_does_not_enable_live() -> None:
    settings = paper_settings(
        activity_lookback_seconds=14400,
        risk_max_signal_age_seconds=14400,
        live_trading_enabled=False,
    )
    engine = PaperEngine(settings, ActivityProbe())
    assert settings.live_trading_enabled is False
    assert engine.policy.maximum_signal_age_seconds == 14400


def test_activity_lookback_and_fetch_limit_are_configurable(monkeypatch) -> None:
    probe = ActivityProbe()
    settings = paper_settings(activity_lookback_seconds=14400, activity_fetch_limit=2000)

    monkeypatch.setattr("app.paper.time.time", lambda: 100000)
    with Session(memory_engine()) as db:
        db.add(wallet())
        db.commit()
        processed = ingest_wallet_activity(
            db,
            probe,
            settings,
            PaperEngine(settings, probe),
        )

    assert processed == 0
    assert probe.start == 85600
    assert probe.limit == 2000


def test_existing_cursor_requeries_last_second_for_safe_deduplication() -> None:
    probe = ActivityProbe()
    settings = paper_settings(activity_fetch_limit=500)

    with Session(memory_engine()) as db:
        tracked = wallet("2")
        tracked.last_activity_at = 999
        db.add(tracked)
        db.commit()
        ingest_wallet_activity(
            db,
            probe,
            settings,
            PaperEngine(settings, probe),
        )

    assert probe.start == 999


def test_stale_signal_is_rejected_without_midpoint_request(monkeypatch) -> None:
    probe = ActivityProbe([trade(tx="0xstale", timestamp=900)])
    settings = paper_settings()
    monkeypatch.setattr("app.paper.time.time", lambda: 1000)

    with Session(memory_engine()) as db:
        db.add(wallet())
        db.commit()
        processed = ingest_wallet_activity(
            db,
            probe,
            settings,
            PaperEngine(settings, probe),
        )
        order = db.query(PaperOrder).one()

    assert processed == 1
    assert probe.midpoint_calls == 0
    assert order.rejection_reason == "stale_signal"


def test_midpoint_is_fetched_once_per_asset_per_cycle(monkeypatch) -> None:
    probe = ActivityProbe(
        [
            trade(tx="0xone", timestamp=999),
            trade(tx="0xtwo", timestamp=999, size=20),
        ]
    )
    settings = paper_settings()
    monkeypatch.setattr("app.paper.time.time", lambda: 1000)

    with Session(memory_engine()) as db:
        db.add(wallet())
        db.commit()
        processed = ingest_wallet_activity(
            db,
            probe,
            settings,
            PaperEngine(settings, probe),
        )
        orders = db.query(PaperOrder).all()

    assert processed == 2
    assert len(orders) == 2
    assert probe.midpoint_calls == 1


def test_activity_identity_does_not_collapse_distinct_fills() -> None:
    first = trade(tx="0xsame", timestamp=999, size=20)
    second = trade(tx="0xsame", timestamp=999, size=21)
    first_key, first_legacy = activity_source_keys(wallet().address, first)
    second_key, second_legacy = activity_source_keys(wallet().address, second)
    assert first_key != second_key
    assert first_legacy == second_legacy


def test_legacy_identity_only_deduplicates_matching_fill(monkeypatch) -> None:
    first = trade(tx="0xlegacy", timestamp=999, size=20)
    second = trade(tx="0xlegacy", timestamp=999, size=21)
    _, legacy_key = activity_source_keys(wallet().address, first)
    probe = ActivityProbe([second])
    monkeypatch.setattr("app.paper.time.time", lambda: 1000)

    with Session(memory_engine()) as db:
        tracked = wallet()
        db.add(tracked)
        db.add(
            Signal(
                source_key=legacy_key,
                wallet_address=tracked.address,
                wallet_score=80,
                condition_id="condition-1",
                asset_id="asset-1",
                market_title="Test market",
                outcome="YES",
                side="BUY",
                source_price=0.5,
                source_size=20,
                source_usdc=10,
                source_timestamp=999,
                transaction_hash="0xlegacy",
            )
        )
        db.commit()
        processed = ingest_wallet_activity(
            db,
            probe,
            paper_settings(),
            PaperEngine(paper_settings(), probe),
        )

    assert processed == 1


def test_trial_markdown_declares_delayed_paper_safety() -> None:
    report = {
        "run": {
            "status": "PASS",
            "profile": "GITHUB_DELAYED_PAPER",
            "completed_at": datetime.now(UTC).isoformat(),
            "github_sha": "abc123",
            "errors": [],
        },
        "portfolio": {
            "equity": 300.0,
            "cash": 290.0,
            "exposure": 10.0,
            "realized_pnl": 0.0,
            "unrealized_pnl": 0.0,
            "drawdown": 0.0,
        },
        "cycle": {
            "selected_wallets": 3,
            "positions_settled": 1,
            "positions_marked": 1,
            "signals_processed": 2,
            "ai_report_created": False,
        },
        "totals": {
            "wallets": 10,
            "signals": 2,
            "orders": 2,
            "filled_orders": 1,
            "rejected_orders": 1,
            "open_positions": 1,
            "settled_positions": 1,
        },
        "safety": {
            "trading_mode": "PAPER",
            "signal_age_limit_seconds": 14400,
            "activity_lookback_seconds": 14400,
        },
        "selected_wallets": [],
        "recent_orders": [],
    }
    markdown = render_markdown(report)
    assert "GITHUB_DELAYED_PAPER" in markdown
    assert "LIVE execution: `UNAVAILABLE`" in markdown
    assert "not a 24/7 executor" in markdown
    assert "Settled" in markdown
