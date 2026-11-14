"""Shared season membership and private completion evidence are independent."""
from datetime import date
from sqlalchemy import Boolean, Date, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base


class SeriesSeason(Base):
    __tablename__ = 'series_seasons'
    __table_args__ = (UniqueConstraint('media_id', 'episode_order', 'season_number', 'membership_key'),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    media_id: Mapped[int] = mapped_column(ForeignKey('media.id', ondelete='CASCADE'), index=True)
    episode_order: Mapped[str] = mapped_column(String(40))
    season_number: Mapped[int] = mapped_column(Integer)
    membership_key: Mapped[str] = mapped_column(String(64))
    episode_ids: Mapped[list] = mapped_column(JSONB)
    metadata_payload: Mapped[dict] = mapped_column(JSONB, default=dict)


class SeasonTracking(Base):
    __tablename__ = 'season_tracking'
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    season_id: Mapped[int] = mapped_column(ForeignKey('series_seasons.id', ondelete='CASCADE'), primary_key=True)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    start_date: Mapped[date | None] = mapped_column(Date)
    start_source: Mapped[str | None] = mapped_column(String(16))
    start_manual: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    start_evidence: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    series_finish_hint: Mapped[date | None] = mapped_column(Date)
    series_finish_source: Mapped[str | None] = mapped_column(String(16))
    completion_date: Mapped[date | None] = mapped_column(Date)
    date_source: Mapped[str | None] = mapped_column(String(16))
    evidence: Mapped[dict] = mapped_column(JSONB, default=dict)
    # Clearing a manual date is a protected decision too.
    date_manual: Mapped[bool] = mapped_column(Boolean, default=False)
    watched_episodes: Mapped[int] = mapped_column(Integer, default=0)
    current: Mapped[bool] = mapped_column(Boolean, default=True)
    activity_suppressed: Mapped[bool] = mapped_column(Boolean, default=False)


class SeasonReconcileJob(Base):
    __tablename__ = 'season_reconcile_jobs'
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    media_id: Mapped[int] = mapped_column(ForeignKey('media.id', ondelete='CASCADE'), primary_key=True)
