import os
from datetime import UTC, datetime

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("APP_ENV", "development")

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.config import Settings  # noqa: E402
from app.db import Base  # noqa: E402
from app.models import Wallet  # noqa: E402
from app.paper import PaperEngine, ingest_wallet_activity  # noqa: E402
from app.trial import render_markdown  # noqa: E402


class ActivityProbe:
    def __init__(self) -> None:
        self.start: int | None = None

    def activity(self, _wallet: str, start: int, limit: int = 100) -> list[dict]:
        self.start = start
        return []

    def midpoint(self, _asset_id: str) -> float:
        return 0.5


def test_delayed_trial_profile_does_not_enable_live() -> None:
    settings = Settings(
        activity_lookback_seconds=14400,
        risk_max_signal_age_seconds=14400,
        live_trading_enabled=False,
    )
    engine = PaperEngine(settings, ActivityProbe())
    assert settings.live_trading_enabled is False
    assert engine.policy.maximum_signal_age_seconds == 14400


def test_activity_lookback_is_configurable(monkeypatch) -> None:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    probe = ActivityProbe()
    settings = Settings(activity_lookback_seconds=14400)

    monkeypatch.setattr("app.paper.time.time", lambda: 100000)
    with Session(engine) as db:
        db.add(
            Wallet(
                address="0x" + "1" * 40,
                selected=True,
                score=80,
                last_activity_at=0,
            )
        )
        db.commit()
        processed = ingest_wallet_activity(
            db,
            probe,
            settings,
            PaperEngine(settings, probe),
        )

    assert processed == 0
    assert probe.start == 85600


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
