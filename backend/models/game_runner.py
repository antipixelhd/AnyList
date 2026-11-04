"""Public scheduling state. No viewer library payloads live in this queue."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class GameWorkDemand(Base):
    __tablename__ = 'game_work_demand'
    key: Mapped[str] = mapped_column(String(320), primary_key=True)
    origin: Mapped[int] = mapped_column(Integer, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    owner: Mapped[str] = mapped_column(String(80), nullable=False)
    active_until: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class GameProviderTicket(Base):
    __tablename__ = 'game_provider_tickets'
    __table_args__ = (Index('ix_game_provider_ticket_live', 'provider', 'expires_at'),)
    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    owner: Mapped[str] = mapped_column(String(80), nullable=False)
    scopes: Mapped[list] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class GameProviderTurn(Base):
    __tablename__ = 'game_provider_turns'
    key: Mapped[str] = mapped_column(String(600), primary_key=True)
    served_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class GameMapperState(Base):
    __tablename__ = 'game_mapper_state'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    heartbeat: Mapped[datetime | None] = mapped_column(DateTime)
    lease_token: Mapped[str | None] = mapped_column(String(40))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime)


class GameMapperItem(Base):
    __tablename__ = 'game_mapper_items'
    __table_args__ = (Index('ix_game_mapper_due', 'next_at', 'category', 'weight'),)
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    native: Mapped[str] = mapped_column(String(40), nullable=False)
    entity_id: Mapped[int | None] = mapped_column(ForeignKey('catalogue_entities.id', ondelete='SET NULL'))
    category: Mapped[int] = mapped_column(Integer, nullable=False, default=4)
    weight: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    counts: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    next_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    checked_at: Mapped[datetime | None] = mapped_column(DateTime)
    processed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quanta: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(String(80))


class GameMapperInterest(Base):
    __tablename__ = 'game_mapper_interests'
    __table_args__ = (Index('ix_game_mapper_interest_game', 'game', 'expires_at'),
        Index('ix_game_mapper_interest_scan','category','source','game'))
    source: Mapped[str] = mapped_column(String(120), primary_key=True)
    game: Mapped[str] = mapped_column(String(40), primary_key=True)
    category: Mapped[int] = mapped_column(Integer, nullable=False)
    user_id: Mapped[int | None] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'))
    expires_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class GameMapperEdge(Base):
    __tablename__ = 'game_mapper_edges'
    __table_args__ = (Index('ix_game_mapper_edge_target','target'),)
    game: Mapped[str] = mapped_column(String(40), primary_key=True)
    target: Mapped[str] = mapped_column(ForeignKey('game_mapper_items.key', ondelete='CASCADE'), primary_key=True)
