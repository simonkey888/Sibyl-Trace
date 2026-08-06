from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Float, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class Wallet(Base):
    __tablename__ = "wallets"

    address: Mapped[str] = mapped_column(String(42), primary_key=True)
    username: Mapped[str | None] = mapped_column(String(120))
    score: Mapped[float] = mapped_column(Float, default=0)
    win_rate: Mapped[float] = mapped_column(Float, default=0)
    profit_factor: Mapped[float] = mapped_column(Float, default=0)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0)
    volume: Mapped[float] = mapped_column(Float, default=0)
    closed_count: Mapped[int] = mapped_column(Integer, default=0)
    concentration: Mapped[float] = mapped_column(Float, default=1)
    selected: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    rejection_reason: Mapped[str | None] = mapped_column(String(240))
    last_activity_at: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WalletSnapshot(Base):
    __tablename__ = "wallet_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    wallet_address: Mapped[str] = mapped_column(String(42), index=True)
    score: Mapped[float] = mapped_column(Float)
    win_rate: Mapped[float] = mapped_column(Float)
    profit_factor: Mapped[float] = mapped_column(Float)
    realized_pnl: Mapped[float] = mapped_column(Float)
    concentration: Mapped[float] = mapped_column(Float)
    closed_count: Mapped[int] = mapped_column(Integer)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class Signal(Base):
    __tablename__ = "signals"
    __table_args__ = (UniqueConstraint("source_key", name="uq_signal_source_key"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source_key: Mapped[str] = mapped_column(String(220))
    wallet_address: Mapped[str] = mapped_column(String(42), index=True)
    wallet_score: Mapped[float] = mapped_column(Float)
    condition_id: Mapped[str] = mapped_column(String(66), index=True)
    asset_id: Mapped[str] = mapped_column(String(90))
    market_title: Mapped[str] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(String(64))
    side: Mapped[str] = mapped_column(String(8))
    source_price: Mapped[float] = mapped_column(Float)
    source_size: Mapped[float] = mapped_column(Float)
    source_usdc: Mapped[float] = mapped_column(Float)
    source_timestamp: Mapped[int] = mapped_column(Integer)
    transaction_hash: Mapped[str] = mapped_column(String(100))
    decision: Mapped[str] = mapped_column(String(20), default="PENDING")
    decision_reason: Mapped[str | None] = mapped_column(String(240))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PaperOrder(Base):
    __tablename__ = "paper_orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    signal_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    asset_id: Mapped[str] = mapped_column(String(90), index=True)
    condition_id: Mapped[str] = mapped_column(String(66), index=True)
    market_title: Mapped[str] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(String(64))
    side: Mapped[str] = mapped_column(String(8))
    requested_usd: Mapped[float] = mapped_column(Float)
    filled_usd: Mapped[float] = mapped_column(Float, default=0)
    source_price: Mapped[float] = mapped_column(Float)
    observed_price: Mapped[float | None] = mapped_column(Float)
    fill_price: Mapped[float | None] = mapped_column(Float)
    slippage: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(24))
    rejection_reason: Mapped[str | None] = mapped_column(String(240))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class PaperPosition(Base):
    __tablename__ = "paper_positions"

    asset_id: Mapped[str] = mapped_column(String(90), primary_key=True)
    condition_id: Mapped[str] = mapped_column(String(66), index=True)
    market_title: Mapped[str] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(String(64))
    shares: Mapped[float] = mapped_column(Float, default=0)
    average_price: Mapped[float] = mapped_column(Float, default=0)
    current_price: Mapped[float] = mapped_column(Float, default=0)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cash: Mapped[float] = mapped_column(Float)
    exposure: Mapped[float] = mapped_column(Float)
    equity: Mapped[float] = mapped_column(Float)
    realized_pnl: Mapped[float] = mapped_column(Float)
    unrealized_pnl: Mapped[float] = mapped_column(Float)
    drawdown: Mapped[float] = mapped_column(Float)
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class SystemState(Base):
    __tablename__ = "system_state"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True)
    severity: Mapped[str] = mapped_column(String(16), default="INFO")
    message: Mapped[str] = mapped_column(Text)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


Index("ix_wallet_score_selected", Wallet.selected, Wallet.score)


class AIAnalysis(Base):
    __tablename__ = "ai_analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model: Mapped[str] = mapped_column(String(80))
    input_hash: Mapped[str] = mapped_column(String(64), index=True)
    response_id: Mapped[str | None] = mapped_column(String(120))
    report_json: Mapped[str] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
