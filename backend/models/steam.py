"""Private Steam onboarding and incremental checkpoints on a shared connection."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class SteamConnection(Base):
    __tablename__ = 'steam_connections'
    __table_args__ = (UniqueConstraint('user_id', 'steam_id', name='uq_steam_owner_profile'),)

    connection_id: Mapped[int] = mapped_column(ForeignKey('media_server_connections.id', ondelete='CASCADE'), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    steam_id: Mapped[str] = mapped_column(String(20))
    sync_achievements: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default='true')
    sync_playtime: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default='true')
    imported: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default='false')
    # Server-owned library snapshot. Client selections may reference only these app IDs.
    library: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default='[]')
    selection: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default='[]')
    checkpoints: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default='{}')
    draft: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default='{}')
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default='1')
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime)
