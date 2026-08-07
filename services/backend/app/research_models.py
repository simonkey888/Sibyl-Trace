from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC)


class ResearchExperiment(Base):
    __tablename__ = "research_experiments"

    experiment_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    strategy_version: Mapped[str] = mapped_column(String(40))
    evidence_generation: Mapped[str] = mapped_column(String(40), index=True)
    config_hash: Mapped[str] = mapped_column(String(64))
    code_sha: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="RESEARCH", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ResearchHypothesis(Base):
    __tablename__ = "research_hypotheses"

    hypothesis_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    parent_id: Mapped[str | None] = mapped_column(String(32), index=True)
    kind: Mapped[str] = mapped_column(String(40), index=True)
    thesis: Mapped[str] = mapped_column(Text)
    config_json: Mapped[str] = mapped_column(Text)
    preregistration_json: Mapped[str] = mapped_column(Text)
    result_json: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default="PREREGISTERED", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ResearchObservation(Base):
    __tablename__ = "research_observations"
    __table_args__ = (
        UniqueConstraint("observation_key", name="uq_research_observation_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    observation_key: Mapped[str] = mapped_column(String(128))
    experiment_id: Mapped[str] = mapped_column(String(96), index=True)
    source: Mapped[str] = mapped_column(String(32), index=True)
    market_id: Mapped[str | None] = mapped_column(String(96), index=True)
    asset_id: Mapped[str | None] = mapped_column(String(90), index=True)
    category: Mapped[str | None] = mapped_column(String(48), index=True)
    location: Mapped[str | None] = mapped_column(String(120), index=True)
    source_timestamp_ms: Mapped[int | None] = mapped_column(Integer)
    receive_timestamp_ms: Mapped[int] = mapped_column(Integer, index=True)
    market_price: Mapped[float | None] = mapped_column(Float)
    model_probability: Mapped[float | None] = mapped_column(Float)
    gross_edge: Mapped[float | None] = mapped_column(Float)
    costs: Mapped[float | None] = mapped_column(Float)
    net_edge: Mapped[float | None] = mapped_column(Float)
    fillable: Mapped[bool] = mapped_column(Boolean, default=False)
    payload_hash: Mapped[str] = mapped_column(String(64))
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ResearchCheckpoint(Base):
    __tablename__ = "research_checkpoints"
    __table_args__ = (
        UniqueConstraint("run_id", "phase", name="uq_research_checkpoint_run_phase"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(String(96), index=True)
    phase: Mapped[str] = mapped_column(String(32))
    state_hash: Mapped[str] = mapped_column(String(64))
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EvidenceCorrection(Base):
    __tablename__ = "evidence_corrections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    evidence_ref: Mapped[str] = mapped_column(String(160), index=True)
    old_value_json: Mapped[str] = mapped_column(Text)
    corrected_value_json: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text)
    code_sha: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WatchdogEvent(Base):
    __tablename__ = "watchdog_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    watchdog: Mapped[str] = mapped_column(String(64), index=True)
    state: Mapped[str] = mapped_column(String(12), index=True)
    message: Mapped[str] = mapped_column(Text)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
