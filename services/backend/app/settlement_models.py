from datetime import UTC, datetime

from sqlalchemy import DateTime, Float, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class PaperSettlement(Base):
    __tablename__ = "paper_settlements"

    asset_id: Mapped[str] = mapped_column(String(90), primary_key=True)
    condition_id: Mapped[str] = mapped_column(String(66), index=True)
    market_title: Mapped[str] = mapped_column(String(500))
    outcome: Mapped[str] = mapped_column(String(64))
    settlement_price: Mapped[float] = mapped_column(Float)
    shares: Mapped[float] = mapped_column(Float)
    proceeds: Mapped[float] = mapped_column(Float)
    cost_basis: Mapped[float] = mapped_column(Float)
    realized_pnl: Mapped[float] = mapped_column(Float)
    settled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
