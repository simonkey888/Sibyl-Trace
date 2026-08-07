from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db import Base
from app.models import AuditEvent, PaperOrder, PaperPosition
from app.repository import current_portfolio
from app.settlement import settle_closed_positions
from app.settlement_models import PaperSettlement


def session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


class ClosedMarketClient:
    def __init__(self, price: str = "1") -> None:
        self.price = price

    def closed_markets(self, condition_ids: list[str]) -> list[dict]:
        assert condition_ids == ["condition"]
        return [
            {
                "conditionId": "condition",
                "closed": True,
                "clobTokenIds": '["asset", "other"]',
                "outcomePrices": f'["{self.price}", "0"]',
            }
        ]


def add_open_position(db: Session) -> None:
    db.add(
        PaperPosition(
            asset_id="asset",
            condition_id="condition",
            market_title="Resolved market",
            outcome="YES",
            shares=10,
            average_price=0.4,
            current_price=0.4,
            realized_pnl=0,
        )
    )
    db.add(
        PaperOrder(
            signal_id=1,
            asset_id="asset",
            condition_id="condition",
            market_title="Resolved market",
            outcome="YES",
            side="BUY",
            requested_usd=4,
            filled_usd=4,
            source_price=0.4,
            observed_price=0.4,
            fill_price=0.4,
            slippage=0,
            status="FILLED",
        )
    )
    db.commit()


def test_winning_position_settles_once_and_updates_cash() -> None:
    with session() as db:
        add_open_position(db)
        settings = Settings()
        settled = settle_closed_positions(db, ClosedMarketClient(), settings)
        again = settle_closed_positions(db, ClosedMarketClient(), settings)
        position = db.get(PaperPosition, "asset")
        settlement = db.get(PaperSettlement, "asset")
        portfolio = current_portfolio(db, settings.initial_bankroll_usd)

    assert settled == 1
    assert again == 0
    assert position is not None
    assert position.shares == 0
    assert position.realized_pnl == 6
    assert settlement is not None
    assert settlement.proceeds == 10
    assert settlement.realized_pnl == 6
    assert portfolio["cash"] == 306
    assert portfolio["equity"] == 306
    assert portfolio["realized_pnl"] == 6


def test_nonterminal_closed_price_is_deferred() -> None:
    with session() as db:
        add_open_position(db)
        settled = settle_closed_positions(db, ClosedMarketClient("0.5"), Settings())
        position = db.get(PaperPosition, "asset")

    assert settled == 0
    assert position is not None
    assert position.shares == 10


def test_reopened_settled_asset_reconciles_without_losing_cash() -> None:
    with session() as db:
        add_open_position(db)
        settings = Settings()
        assert settle_closed_positions(db, ClosedMarketClient(), settings) == 1

        position = db.get(PaperPosition, "asset")
        assert position is not None
        position.shares = 2
        position.average_price = 0.5
        position.current_price = 0.5
        db.add(
            PaperOrder(
                signal_id=2,
                asset_id="asset",
                condition_id="condition",
                market_title="Resolved market",
                outcome="YES",
                side="BUY",
                requested_usd=1,
                filled_usd=1,
                source_price=0.5,
                observed_price=0.5,
                fill_price=0.5,
                slippage=0,
                status="FILLED",
            )
        )
        db.commit()

        assert settle_closed_positions(db, ClosedMarketClient(), settings) == 1
        settlement = db.get(PaperSettlement, "asset")
        portfolio = current_portfolio(db, settings.initial_bankroll_usd)
        event = (
            db.query(AuditEvent)
            .filter_by(event_type="paper_position_reopened_after_settlement")
            .one()
        )

    assert settlement is not None
    assert settlement.shares == 12
    assert settlement.cost_basis == 5
    assert settlement.proceeds == 12
    assert settlement.realized_pnl == 7
    assert position.shares == 0
    assert position.realized_pnl == 7
    assert portfolio["cash"] == 307
    assert portfolio["equity"] == 307
    assert portfolio["realized_pnl"] == 7
    assert event.severity == "WARN"
