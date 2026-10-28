"""Public provider evidence, separate from canonical identities and viewer state."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db import Base


class GameMappingGeneration(Base):
    __tablename__ = 'game_mapping_generations'
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    source: Mapped[str] = mapped_column(String(24), nullable=False, index=True)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime)
    coverage: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class GameMappingReference(Base):
    __tablename__ = 'game_mapping_references'
    generation: Mapped[str] = mapped_column(String(40), ForeignKey('game_mapping_generations.id', ondelete='CASCADE'), primary_key=True)
    native: Mapped[str] = mapped_column(String(200), primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    year: Mapped[int | None] = mapped_column(Integer)
    names: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    imdb_ids: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    igdb_ids: Mapped[list[int]] = mapped_column(ARRAY(Integer), nullable=False, default=list)
    data: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    __table_args__ = (
        Index('ix_game_mapping_names', 'names', postgresql_using='gin'),
        Index('ix_game_mapping_imdb', 'imdb_ids', postgresql_using='gin'),
        Index('ix_game_mapping_igdb', 'igdb_ids', postgresql_using='gin'),
    )
