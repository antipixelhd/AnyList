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
    # Provenance for ordering provider observations against an explicit local
    # status decision.  ``updated_at`` also changes for notes, scores and
    # favourites, so it cannot safely answer which side last changed status.
    status_source: Mapped[str | None] = mapped_column(String(64))
    status_changed_at: Mapped[datetime | None] = mapped_column(DateTime)
    # Only Completed entries created by an initial history import receive this
    # marker. Rating-only activity is quiet for the following seven days.
    initial_import_completed_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class TrackingActivity(Base):
    __tablename__ = "tracking_activity"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    media_id: Mapped[int] = mapped_column(ForeignKey("media.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16))
    score: Mapped[float | None] = mapped_column(Float)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class TrackingDeliveryJob(Base):
    """Durable receipt for one AnyList entry change and its provider fan-out."""
    __tablename__ = "tracking_delivery_jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    media_id: Mapped[int] = mapped_column(ForeignKey("media.id", ondelete="CASCADE"), index=True)
    # queued, dispatching, attempted_unverified, failed, or no_external_changes. Fan-out
    # currently swallows some per-provider errors, so never claim delivery.
    state: Mapped[str] = mapped_column(String(32), nullable=False, server_default="queued")
    changes: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    detail: Mapped[str | None] = mapped_column(String(240))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


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
    combine_lists: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    default_sort: Mapped[str] = mapped_column(String(16), default="title", server_default="title")
    low_priority_notifications: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    low_priority_retention_days: Mapped[int] = mapped_column(Integer, default=7, server_default="7")
    show_new_ratings_popup: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class SyncReview(Base):
    __tablename__ = "tracking_reviews"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    media_id: Mapped[int | None] = mapped_column(ForeignKey("media.id", ondelete="CASCADE"))
    connection_id: Mapped[int | None] = mapped_column(ForeignKey("media_server_connections.id", ondelete="CASCADE"))
    provider: Mapped[str | None] = mapped_column(String(24))
    kind: Mapped[str] = mapped_column(String(32))
    state: Mapped[str] = mapped_column(String(16), default="pending")
    previous_status: Mapped[str | None] = mapped_column(String(16))
    proposed_status: Mapped[str | None] = mapped_column(String(16))
    previous_score: Mapped[float | None] = mapped_column(Float)
    proposed_score: Mapped[float | None] = mapped_column(Float)
    season_number: Mapped[int | None] = mapped_column(Integer)
    message: Mapped[str] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(String(16), default="low", server_default="low")
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    seen_at: Mapped[datetime | None] = mapped_column(DateTime)
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ProviderIgnore(Base):
    __tablename__ = "tracking_provider_ignores"
    __table_args__ = (UniqueConstraint("user_id", "provider", "external_key", name="uq_tracking_provider_ignore"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(24), nullable=False)
    external_key: Mapped[str] = mapped_column(String(255), nullable=False)
    title: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ProviderMatch(Base):
    __tablename__ = "tracking_provider_matches"
    __table_args__ = (UniqueConstraint("user_id", "provider", "external_key", name="uq_tracking_provider_match"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(24), nullable=False)
    external_key: Mapped[str] = mapped_column(String(255), nullable=False)
    media_id: Mapped[int] = mapped_column(ForeignKey("media.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class StreamBaseline(Base):
    __tablename__ = "tracking_baselines"
    connection_id: Mapped[int] = mapped_column(ForeignKey("media_server_connections.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    snapshot: Mapped[dict] = mapped_column(JSONB, default=dict)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class CloudBaseline(Base):
    __tablename__ = "tracking_cloud_baselines"
    __table_args__ = (UniqueConstraint("user_id", "provider", name="uq_tracking_cloud_user_provider"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(24), nullable=False)
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


class CloudAction(Base):
    """A durable local decision waiting to be acknowledged by a cloud tracker."""
    __tablename__ = "tracking_cloud_actions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(24), index=True)
    media_id: Mapped[int] = mapped_column(ForeignKey("media.id", ondelete="CASCADE"))
    action: Mapped[str] = mapped_column(String(24))
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)
    state: Mapped[str] = mapped_column(String(16), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class WebPushSubscription(Base):
    """One browser/PWA push endpoint belonging to a user device."""
    __tablename__ = "web_push_subscriptions"
    __table_args__ = (UniqueConstraint("endpoint", name="uq_web_push_endpoint"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    endpoint: Mapped[str] = mapped_column(Text, nullable=False)
    p256dh: Mapped[str] = mapped_column(String(255), nullable=False)
    auth: Mapped[str] = mapped_column(String(255), nullable=False)
    user_agent: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
