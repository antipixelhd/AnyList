from sqlalchemy import BigInteger, Index, Integer
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class ScreenActorAppearance(Base):
    """Published TMDB season cards, including revisions needed by continuations."""

    __tablename__ = 'screen_actor_appearances'
    __table_args__ = (Index('ix_screen_actor_appearance_lookup', 'person_id', 'series_id', 'id'),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    person_id: Mapped[int] = mapped_column(Integer, nullable=False)
    series_id: Mapped[int] = mapped_column(Integer, nullable=False)
    projection: Mapped[dict] = mapped_column(JSONB, nullable=False)
