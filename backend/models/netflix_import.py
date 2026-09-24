from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, LargeBinary, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class NetflixImportSession(Base):
    """A private, resumable draft for one Netflix viewing-history import."""

    __tablename__ = "netflix_import_sessions"
    __table_args__ = (
        UniqueConstraint("user_id", "idempotency_key", name="uq_netflix_import_user_idempotency"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="preparing", server_default="preparing")
    phase: Mapped[str] = mapped_column(String(16), nullable=False, default="parse", server_default="parse")
    progress: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # Kept only until matching finishes or the session is cancelled. Parsed
    # rows and review decisions live in payload while the draft is active.
    source_csv: Mapped[bytes | None] = mapped_column(LargeBinary)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default="{}")
    error_message: Mapped[str | None] = mapped_column(Text)
    idempotency_key: Mapped[str | None] = mapped_column(String(128))
    result: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
