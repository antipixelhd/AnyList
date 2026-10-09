"""Durable derived statistics; personal facts and catalogue rows remain authoritative."""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, ForeignKeyConstraint, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class StatsMetadataRevision(Base):
    __tablename__ = "stats_metadata_revision"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")


class UserStatsSnapshot(Base):
    __tablename__ = "user_stats_snapshots"
    __table_args__ = (UniqueConstraint("id", "user_id", name="uq_stats_snapshot_owner"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    source_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    metadata_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    contract_version: Mapped[int] = mapped_column(Integer, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)


class UserStatsState(Base):
    __tablename__ = "user_stats_state"
    __table_args__ = (
        ForeignKeyConstraint(
            ["active_snapshot_id", "user_id"], ["user_stats_snapshots.id", "user_stats_snapshots.user_id"],
            name="fk_stats_active_owner", deferrable=True, initially="DEFERRED",
        ),
    )
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    source_revision: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default="0")
    dirty: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    next_due_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), index=True)
    active_snapshot_id: Mapped[int | None] = mapped_column(Integer)
    lease_token: Mapped[str | None] = mapped_column(String(36))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    error_code: Mapped[str | None] = mapped_column(String(32))
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime)
