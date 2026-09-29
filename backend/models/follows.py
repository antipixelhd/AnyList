from typing import TYPE_CHECKING
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base

if TYPE_CHECKING:
    from .users import User



class Follow(Base):
    __tablename__ = "follows"
    __table_args__ = (
        UniqueConstraint("follower_id", "following_id", name="uq_follow"),
    )

    id           : Mapped[int]      = mapped_column(Integer, primary_key=True)
    follower_id  : Mapped[int]      = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    following_id : Mapped[int]      = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    created_at   : Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), nullable=False)

    follower  : Mapped["User"] = relationship(foreign_keys=[follower_id])
    following : Mapped["User"] = relationship(foreign_keys=[following_id])
