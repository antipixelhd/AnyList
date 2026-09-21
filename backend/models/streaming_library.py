"""Explicit AnyList streaming-library intent and per-account delivery state."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class StreamingLibraryIntent(Base):
    __tablename__ = "streaming_library_intents"
    __table_args__ = (UniqueConstraint("user_id", "media_id", name="uq_streaming_library_intent"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    media_id: Mapped[int] = mapped_column(ForeignKey("media.id", ondelete="CASCADE"), nullable=False, index=True)
    desired: Mapped[bool] = mapped_column(Boolean, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)


class StreamingLibraryDelivery(Base):
    __tablename__ = "streaming_library_deliveries"
    __table_args__ = (UniqueConstraint("intent_id", "connection_id", name="uq_streaming_library_delivery"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    intent_id: Mapped[int] = mapped_column(ForeignKey("streaming_library_intents.id", ondelete="CASCADE"), nullable=False, index=True)
    connection_id: Mapped[int] = mapped_column(ForeignKey("media_server_connections.id", ondelete="CASCADE"), nullable=False, index=True)
    desired: Mapped[bool] = mapped_column(Boolean, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, server_default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_error: Mapped[str | None] = mapped_column(String(200))
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)
