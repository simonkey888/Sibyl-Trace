from datetime import UTC, datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db import Base
from app.models import AuditEvent, PaperOrder, PaperPosition, PortfolioSnapshot, SystemState, Wallet
from app.paper import refresh_position_prices
from app.repository import current_portfolio, initialize_state
from app.scanner import scan_wallets


def session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def add_position_and_buy(db: Session) -> None:
    db.add(
        PaperPosition(
            asset_id="asset",
            condition_id="condition",
            market_title="Market",
            outcome="YES",
            shares=20,
            average_price=0.5,
            current_price=0.6,
            realized_pnl=0,
        )
    )
    db.add(
        PaperOrder(
            signal_id=1,
            asset_id="asset",
            condition_id="condition",
            market_title="Market",
            outcome="YES",
            side="BUY",
            requested_usd=10,
            filled_usd=10,
            source_price=0.5,
            observed_price=0.5,
            fill_price=0.5,
            slippage=0,
            status="FILLED",
        )
    )


def test_daily_pnl_uses_initial_bankroll_without_prior_close() -> None:
    with session() as db:
        db.add(
            PortfolioSnapshot(
                cash=300,
                exposure=0,
                equity=300,
                realized_pnl=0,
                unrealized_pnl=0,
                drawdown=0,
                captured_at=datetime.now(UTC),
            )
        )
        add_position_and_buy(db)
        db.commit()
        portfolio = current_portfolio(db, 300)
        assert portfolio["equity"] == 302
        assert portfolio["daily_pnl"] == 2


def test_daily_pnl_uses_last_snapshot_before_utc_day() -> None:
    with session() as db:
        now = datetime.now(UTC)
        prior_day = (now - timedelta(days=1)).replace(hour=23, minute=59)
        db.add(
            PortfolioSnapshot(
                cash=295,
                exposure=0,
                equity=295,
                realized_pnl=-5,
                unrealized_pnl=0,
                drawdown=0.02,
                captured_at=prior_day,
            )
        )
        add_position_and_buy(db)
        db.commit()
        portfolio = current_portfolio(db, 300)
        assert portfolio["equity"] == 302
        assert portfolio["daily_pnl"] == 7


def test_initialize_state_reconciles_stale_paper_mode_to_read_only() -> None:
    with session() as db:
        db.add(SystemState(key="mode", value="PAPER"))
        db.commit()
        initialize_state(db, Settings())
        mode = db.get(SystemState, "mode")
        event = db.query(AuditEvent).filter_by(event_type="runtime_mode_reconciled").one()

    assert mode is not None
    assert mode.value == "READ_ONLY"
    assert "PAPER" in event.message
    assert "READ_ONLY" in event.message


class PriceClient:
    def midpoint(self, _: str) -> float:
        return 0.65


def test_open_positions_are_periodically_marked() -> None:
    with session() as db:
        db.add(
            PaperPosition(
                asset_id="asset",
                condition_id="condition",
                market_title="Market",
                outcome="YES",
                shares=10,
                average_price=0.4,
                current_price=0.4,
                realized_pnl=0,
            )
        )
        db.commit()
        updated = refresh_position_prices(db, PriceClient(), Settings())
        assert updated == 1
        assert db.get(PaperPosition, "asset").current_price == 0.65


class ScannerClient:
    def leaderboard(self, _: str, __: int) -> list[dict]:
        return [
            {
                "proxyWallet": "0x" + "2" * 40,
                "pnl": 300,
                "vol": 5000,
                "userName": "repeatable",
            }
        ]

    def closed_positions(self, _: str, limit: int = 1000) -> list[dict]:
        return [{"realizedPnl": 4.0} for _ in range(min(30, limit))]


def test_rescan_clears_stale_selected_wallets() -> None:
    with session() as db:
        stale = Wallet(address="0x" + "1" * 40, selected=True, score=99)
        db.add(stale)
        db.commit()
        selected = scan_wallets(db, ScannerClient(), Settings())
        assert len(selected) == 1
        assert selected[0].address == "0x" + "2" * 40
        assert db.get(Wallet, stale.address).selected is False
