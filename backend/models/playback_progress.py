from typing import TYPE_CHECKING
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .users import User
    from .media import Media



class PlaybackProgress(Base):
    __tablename__ = "playback_progress"
    __table_args__ = (
        UniqueConstraint("user_id", "media_id", name="uq_playback_progress_user_media"),
        Index("idx_playback_progress_user_media", "user_id", "media_id"),
    )

    id               : Mapped[int]            = mapped_column(Integer, primary_key=True)
    user_id          : Mapped[int]            = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    media_id         : Mapped[int]            = mapped_column(ForeignKey("media.id", ondelete="CASCADE"), nullable=False)
    progress_percent : Mapped[float]          = mapped_column(Float, default=0.0, nullable=False)
    progress_seconds : Mapped[int]            = mapped_column(Integer, default=0, nullable=False)
    updated_at       : Mapped[datetime]       = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    user  : Mapped["User"]  = relationship()
    media : Mapped["Media"] = relationship()
