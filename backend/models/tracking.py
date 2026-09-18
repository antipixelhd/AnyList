from datetime import date, datetime
from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base


class TrackedEntry(Base):
    __tablename__ = "tracked_entries"
    __table_args__ = (UniqueConstraint("user_id", "media_id", name="uq_tracked_user_media"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    media_id: Mapped[int] = mapped_column(ForeignKey("media.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="planning")
    rating_mode: Mapped[str] = mapped_column(String(16), default="manual")
    manual_score: Mapped[float | None] = mapped_column(Float)
    season_scores: Mapped[dict] = mapped_column(JSONB, default=dict)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    favorite: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str | None] = mapped_column(Text)
    start_date: Mapped[date | None] = mapped_column(Date)
    finish_date: Mapped[date | None] = mapped_column(Date)
    rewatch_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class TrackingActivity(Base):
    __tablename__ = "tracking_activity"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    media_id: Mapped[int] = mapped_column(ForeignKey("media.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16))
    score: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class TrackingDeletion(Base):
    __tablename__ = "tracking_deletions"
    __table_args__ = (UniqueConstraint("user_id", "media_id", name="uq_tracking_deletion"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    media_id: Mapped[int] = mapped_column(ForeignKey("media.id", ondelete="CASCADE"))
    deleted_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    pending_connections: Mapped[list] = mapped_column(JSONB, default=list)


class TrackingPreferences(Base):
    __tablename__ = "tracking_preferences"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    auto_confirm: Mapped[bool] = mapped_column(Boolean, default=False)


class SyncReview(Base):
    __tablename__ = "tracking_reviews"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    media_id: Mapped[int | None] = mapped_column(ForeignKey("media.id", ondelete="CASCADE"))
    connection_id: Mapped[int | None] = mapped_column(ForeignKey("media_server_connections.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(32))
    state: Mapped[str] = mapped_column(String(16), default="pending")
    previous_status: Mapped[str | None] = mapped_column(String(16))
    proposed_status: Mapped[str | None] = mapped_column(String(16))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class StreamBaseline(Base):
    __tablename__ = "tracking_baselines"
    connection_id: Mapped[int] = mapped_column(ForeignKey("media_server_connections.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class StreamAction(Base):
    __tablename__ = 'tracking_stream_actions'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    connection_id: Mapped[int] = mapped_column(ForeignKey('media_server_connections.id', ondelete='CASCADE'), index=True)
    media_id: Mapped[int] = mapped_column(ForeignKey('media.id', ondelete='CASCADE'))
    action: Mapped[str] = mapped_column(String(24))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    state: Mapped[str] = mapped_column(String(16), default='pending')
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
