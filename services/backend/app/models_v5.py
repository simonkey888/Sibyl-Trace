from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class PaperV5Prediction(Base):
    __tablename__ = "paper_v5_predictions"
    __table_args__ = (UniqueConstraint("source_key", name="uq_paper_v5_source_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_key: Mapped[str] = mapped_column(String(220), index=True)
    wallet_address: Mapped[str] = mapped_column(String(42), index=True)
    wallet_score: Mapped[float] = mapped_column(Float)
    condition_id: Mapped[str] = mapped_column(String(66), index=True)
    asset_id: Mapped[str] = mapped_column(String(90), index=True)
    market_title: Mapped[str] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(String(64))
    side: Mapped[str] = mapped_column(String(8))
    source_price: Mapped[float] = mapped_column(Float)
    source_size: Mapped[float] = mapped_column(Float)
    source_usdc: Mapped[float] = mapped_column(Float)
    source_timestamp: Mapped[int] = mapped_column(Integer)
    transaction_hash: Mapped[str] = mapped_column(String(100))
    source_payload_hash: Mapped[str] = mapped_column(String(64))
    decision: Mapped[str] = mapped_column(String(32), default="PENDING", index=True)
    decision_reason: Mapped[str | None] = mapped_column(String(240))
    resolution_status: Mapped[str] = mapped_column(String(24), default="OPEN", index=True)
    resolution_price: Mapped[float | None] = mapped_column(Float)
    result: Mapped[str] = mapped_column(String(24), default="UNRESOLVED", index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class PaperV5Execution(Base):
    __tablename__ = "paper_v5_executions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    prediction_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    order_type: Mapped[str] = mapped_column(String(12), default="FAK")
    requested_usd: Mapped[float] = mapped_column(Float, default=0)
    requested_shares: Mapped[float] = mapped_column(Float, default=0)
    decision_book_hash: Mapped[str | None] = mapped_column(String(160))
    decision_book_timestamp_ms: Mapped[int | None] = mapped_column(Integer)
    arrival_book_hash: Mapped[str | None] = mapped_column(String(160))
    arrival_book_timestamp_ms: Mapped[int | None] = mapped_column(Integer)
    decision_best_price: Mapped[float | None] = mapped_column(Float)
    worst_price_limit: Mapped[float | None] = mapped_column(Float)
    tick_size: Mapped[float | None] = mapped_column(Float)
    minimum_order_size: Mapped[float | None] = mapped_column(Float)
    fee_rate: Mapped[float | None] = mapped_column(Float)
    fee_exponent: Mapped[float | None] = mapped_column(Float)
    simulated_latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    filled_shares: Mapped[float] = mapped_column(Float, default=0)
    gross_notional: Mapped[float] = mapped_column(Float, default=0)
    fee_usd: Mapped[float] = mapped_column(Float, default=0)
    net_cash_delta: Mapped[float] = mapped_column(Float, default=0)
    average_fill_price: Mapped[float | None] = mapped_column(Float)
    effective_price: Mapped[float | None] = mapped_column(Float)
    slippage: Mapped[float | None] = mapped_column(Float)
    fill_fraction: Mapped[float] = mapped_column(Float, default=0)
    levels_consumed: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), index=True)
    reason: Mapped[str | None] = mapped_column(String(240))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class PaperV5Position(Base):
    __tablename__ = "paper_v5_positions"

    asset_id: Mapped[str] = mapped_column(String(90), primary_key=True)
    condition_id: Mapped[str] = mapped_column(String(66), index=True)
    market_title: Mapped[str] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(String(64))
    shares: Mapped[float] = mapped_column(Float, default=0)
    cost_basis_usd: Mapped[float] = mapped_column(Float, default=0)
    mark_value_usd: Mapped[float] = mapped_column(Float, default=0)
    mark_price: Mapped[float] = mapped_column(Float, default=0)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PaperV5Settlement(Base):
    __tablename__ = "paper_v5_settlements"

    asset_id: Mapped[str] = mapped_column(String(90), primary_key=True)
    condition_id: Mapped[str] = mapped_column(String(66), index=True)
    market_title: Mapped[str] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(String(64))
    settlement_price: Mapped[float] = mapped_column(Float)
    shares: Mapped[float] = mapped_column(Float)
    proceeds: Mapped[float] = mapped_column(Float)
    cost_basis: Mapped[float] = mapped_column(Float)
    realized_pnl: Mapped[float] = mapped_column(Float)
    settled_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class PaperV5PortfolioSnapshot(Base):
    __tablename__ = "paper_v5_portfolio_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cash: Mapped[float] = mapped_column(Float)
    exposure: Mapped[float] = mapped_column(Float)
    equity: Mapped[float] = mapped_column(Float)
    realized_pnl: Mapped[float] = mapped_column(Float)
    unrealized_pnl: Mapped[float] = mapped_column(Float)
    drawdown: Mapped[float] = mapped_column(Float)
    accounting_ok: Mapped[bool] = mapped_column(Boolean, default=True)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
