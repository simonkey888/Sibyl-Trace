import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.db import Base
from app.models import PaperOrder, PaperPosition, Signal
from app.paper import PaperEngine
from app.repository import current_portfolio


class NoopClient:
    pass


def session() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return Session(engine)


def test_capped_sell_records_actual_proceeds_not_requested_notional() -> None:
    with session() as db:
        db.add(
            PaperPosition(
                asset_id="asset",
                condition_id="condition",
                market_title="Market",
                outcome="YES",
                shares=1.0,
                average_price=0.60,
                current_price=0.60,
                realized_pnl=0.0,
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
                requested_usd=0.60,
                filled_usd=0.60,
                source_price=0.60,
                observed_price=0.60,
                fill_price=0.60,
                slippage=0.0,
                status="FILLED",
            )
        )
        db.commit()

        signal = Signal(
            source_key="sell",
            wallet_address="0x" + "1" * 40,
            wallet_score=90,
            condition_id="condition",
            asset_id="asset",
            market_title="Market",
            outcome="YES",
            side="SELL",
            source_price=0.50,
            source_size=2,
            source_usdc=1,
            source_timestamp=1,
            transaction_hash="tx",
        )
        engine = PaperEngine(
            Settings(trading_mode="PAPER", paper_trading_enabled=True),
            NoopClient(),
        )
        actual = engine._apply_fill(db, signal, amount=0.51, price=0.50)
        db.add(
            PaperOrder(
                signal_id=2,
                asset_id="asset",
                condition_id="condition",
                market_title="Market",
                outcome="YES",
                side="SELL",
                requested_usd=0.51,
                filled_usd=actual,
                source_price=0.50,
                observed_price=0.50,
                fill_price=0.50,
                slippage=0.0,
                status="FILLED",
            )
        )
        db.commit()
        portfolio = current_portfolio(db, 300)
        position = db.get(PaperPosition, "asset")

    assert actual == pytest.approx(0.50, abs=1e-9)
    assert position is not None and position.shares == pytest.approx(0.0, abs=1e-9)
    assert position.realized_pnl == pytest.approx(-0.10, abs=1e-9)
    assert portfolio["cash"] == pytest.approx(299.90, abs=1e-9)
    assert portfolio["equity"] == pytest.approx(299.90, abs=1e-9)
    assert portfolio["realized_pnl"] == pytest.approx(-0.10, abs=1e-9)
