"""Catalogue-rooted game facts; dependent entries never become game totals."""
from datetime import date, datetime

from sqlalchemy import (BigInteger, Boolean, CheckConstraint, Date, DateTime, Float,
                        ForeignKey, ForeignKeyConstraint, Index, Integer, String,
                        Text, UniqueConstraint, func)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base


class GameEntry(Base):
    __tablename__ = 'game_entries'
    __table_args__ = (
        UniqueConstraint('user_id', 'work_id', name='uq_game_entry_owner_work'),
        UniqueConstraint('id', 'user_id', 'work_id', name='uq_game_entry_reference'),
        CheckConstraint("status IN ('planning','playing','paused','completed','dropped')", name='ck_game_entry_status'),
        CheckConstraint('score IS NULL OR (score >= 0.5 AND score <= 10)', name='ck_game_entry_score'),
        CheckConstraint('total_seconds IS NULL OR total_seconds >= 0', name='ck_game_entry_total'),
        CheckConstraint('completion_seconds IS NULL OR completion_seconds >= 0', name='ck_game_entry_completion'),
        CheckConstraint('replay_count IS NULL OR replay_count >= 0', name='ck_game_entry_replay'),
        CheckConstraint("achievement_mode IS NULL OR achievement_mode IN ('percentage','count','targeted')", name='ck_game_entry_achievement_mode'),
        CheckConstraint('achievement_count IS NULL OR achievement_count >= 0', name='ck_game_entry_achievement_count'),
        CheckConstraint('achievement_percentage IS NULL OR (achievement_percentage >= 0 AND achievement_percentage <= 100)', name='ck_game_entry_achievement_percent'),
        CheckConstraint('revision >= 1', name='ck_game_entry_revision'),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    work_id: Mapped[int] = mapped_column(ForeignKey('catalogue_entities.id', ondelete='RESTRICT'), index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default='planning', server_default='planning')
    score: Mapped[float | None] = mapped_column(Float)
    notes: Mapped[str | None] = mapped_column(Text)
    start_date: Mapped[date | None] = mapped_column(Date)
    finish_date: Mapped[date | None] = mapped_column(Date)
    total_seconds: Mapped[int | None] = mapped_column(BigInteger)
    completion_seconds: Mapped[int | None] = mapped_column(BigInteger)
    completion_observed_at: Mapped[datetime | None] = mapped_column(DateTime)
    completion_source: Mapped[str | None] = mapped_column(String(32))
    replay_count: Mapped[int | None] = mapped_column(Integer)
    achievement_mode: Mapped[str | None] = mapped_column(String(16))
    achievement_count: Mapped[int | None] = mapped_column(Integer)
    achievement_percentage: Mapped[float | None] = mapped_column(Float)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default='1')
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class GameFavorite(Base):
    __tablename__ = 'game_favorites'
    __table_args__ = (
        UniqueConstraint('user_id', 'position', name='uq_game_favorite_position', deferrable=True, initially='DEFERRED'),
        CheckConstraint('position IS NULL OR position >= 0', name='ck_game_favorite_position'),
    )
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), primary_key=True)
    work_id: Mapped[int] = mapped_column(ForeignKey('catalogue_entities.id', ondelete='RESTRICT'), primary_key=True)
    position: Mapped[int | None] = mapped_column(Integer)


class GameChildState(Base):
    __tablename__ = 'game_child_states'
    __table_args__ = (
        UniqueConstraint('user_id', 'work_id', name='uq_game_child_owner_work'),
        ForeignKeyConstraint(['base_entry_id', 'user_id', 'base_work_id'],
                             ['game_entries.id', 'game_entries.user_id', 'game_entries.work_id'],
                             name='fk_game_child_owner_base', deferrable=True, initially='DEFERRED'),
        CheckConstraint('work_id <> base_work_id', name='ck_game_child_distinct'),
        CheckConstraint("status IN ('planning','playing','paused','completed','dropped')", name='ck_game_child_status'),
        CheckConstraint('score IS NULL OR (score >= 0.5 AND score <= 10)', name='ck_game_child_score'),
        CheckConstraint('revision >= 1', name='ck_game_child_revision'),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    work_id: Mapped[int] = mapped_column(ForeignKey('catalogue_entities.id', ondelete='RESTRICT'), index=True)
    base_work_id: Mapped[int] = mapped_column(ForeignKey('catalogue_entities.id', ondelete='RESTRICT'), index=True)
    # SET NULL unlists the child, retaining private status/date/rating. The
    # deferred composite FK enforces the owner and exact base work on listing.
    base_entry_id: Mapped[int | None] = mapped_column(ForeignKey('game_entries.id', ondelete='SET NULL'), index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default='planning', server_default='planning')
    score: Mapped[float | None] = mapped_column(Float)
    start_date: Mapped[date | None] = mapped_column(Date)
    finish_date: Mapped[date | None] = mapped_column(Date)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default='1')
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())


class GameAchievement(Base):
    __tablename__ = 'game_achievements'
    __table_args__ = (
        UniqueConstraint('app_id', 'api_name', name='uq_game_achievement_identity'),
        UniqueConstraint('id', 'work_id', name='uq_game_achievement_work'),
        ForeignKeyConstraint(['work_id', 'namespace', 'app_id'],
                             ['catalogue_identities.entity_id', 'catalogue_identities.namespace', 'catalogue_identities.external_id'],
                             name='fk_game_achievement_steam', ondelete='RESTRICT'),
        CheckConstraint("namespace = 'steam.app'", name='ck_game_achievement_namespace'),
        CheckConstraint('global_percentage IS NULL OR (global_percentage >= 0 AND global_percentage <= 100)', name='ck_game_achievement_percent'),
        Index('ix_game_achievements_work_id', 'work_id'),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    work_id: Mapped[int] = mapped_column(Integer, nullable=False)
    namespace: Mapped[str] = mapped_column(String(80), nullable=False, default='steam.app', server_default='steam.app')
    app_id: Mapped[str] = mapped_column(String(200), nullable=False)
    api_name: Mapped[str] = mapped_column(String(200), nullable=False)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    icon_url: Mapped[str | None] = mapped_column(Text)
    locked_icon_url: Mapped[str | None] = mapped_column(Text)
    hidden: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default='false')
    global_percentage: Mapped[float | None] = mapped_column(Float)
    active: Mapped[bool] = mapped_column(Boolean,nullable=False,default=True,server_default='true')
    fetched_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class GameAchievementUnlock(Base):
    __tablename__ = 'game_achievement_unlocks'
    __table_args__ = (
        ForeignKeyConstraint(['achievement_id', 'work_id'], ['game_achievements.id', 'game_achievements.work_id'],
                             name='fk_game_unlock_work', ondelete='CASCADE'),
        ForeignKeyConstraint(['user_id', 'work_id'], ['game_entries.user_id', 'game_entries.work_id'],
                             name='fk_game_unlock_owner', ondelete='CASCADE'),
    )
    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    achievement_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    work_id: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default='manual', server_default='manual')
    observed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())


class GameActivity(Base):
    __tablename__ = 'game_activity'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'), index=True)
    work_id: Mapped[int] = mapped_column(ForeignKey('catalogue_entities.id', ondelete='RESTRICT'), index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    score: Mapped[float | None] = mapped_column(Float)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default='{}')
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, server_default=func.now())
