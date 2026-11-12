"""Portable private Games backups. Archive IDs never become local primary keys.

Restore fills absent personal records; it never overwrites a current entry or
publishes archive metadata into the shared catalogue.
"""
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from core import catalogue, game_tracking
from core.catalogue_normalize import image
from core.game_types import capabilities, playable_child
from models import User, UserProfileData
from models.catalogue import CatalogueEntity, CatalogueIdentity
from models.games import GameEntry, GameChildState, GameFavorite, GameAchievement, GameAchievementUnlock, GameActivity
from schemas_games import GamePatch, ChildPatch, GameStatus

ENTRY_FIELDS = ('status', 'score', 'notes', 'start_date', 'finish_date', 'total_seconds',
    'completion_seconds', 'completion_observed_at', 'completion_source', 'replay_count',
    'achievement_mode', 'achievement_count', 'achievement_percentage', 'updated_at')
CHILD_FIELDS = ('status', 'score', 'start_date', 'finish_date', 'updated_at')


class Record(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Work(Record):
    # Multiple exact references can detect contradictions on another instance.
    identities: dict[str, str]
    title: str


class Entry(GamePatch):
    status: GameStatus
    completion_observed_at: datetime | None = None
    completion_source: str | None = Field(None, max_length=32)
    updated_at: datetime


class Child(ChildPatch):
    status: GameStatus
    base: Work
    listed: bool
    updated_at: datetime


class Unlock(Record):
    app_id: str = Field(pattern=r'^[1-9][0-9]{0,9}$')
    api_name: str = Field(min_length=1, max_length=200)
    source: str = Field(max_length=32)
    observed_at: datetime


class Activity(Record):
    status: GameStatus
    score: float | None = Field(None, ge=.5, le=10, allow_inf_nan=False)
    payload: dict
    created_at: datetime


class Game(Record):
    work: Work
    entry: Entry | None = None
    child: Child | None = None
    favorite: bool = False
    position: int | None = Field(None, ge=0)
    banner: str | None = None
    unlocks: list[Unlock] = Field(default_factory=list)
    activities: list[Activity] = Field(default_factory=list)


class Backup(Record):
    version: Literal[1] = 1
    games: list[Game] = Field(default_factory=list, max_length=100000)


async def export_games(db, user_id):
    entries = {r.work_id: r for r in await db.scalars(select(GameEntry).where(GameEntry.user_id == user_id))}
    children = {r.work_id: r for r in await db.scalars(select(GameChildState).where(GameChildState.user_id == user_id))}
    favorites = {r.work_id: r for r in await db.scalars(select(GameFavorite).where(GameFavorite.user_id == user_id))}
    profile = await db.scalar(select(UserProfileData).where(UserProfileData.user_id == user_id))
    banners = (profile.game_banners or {}) if profile else {}
    events = {}
    for row in await db.scalars(select(GameActivity).where(GameActivity.user_id == user_id).order_by(GameActivity.id)):
        events.setdefault(row.work_id, []).append({k: getattr(row, k) for k in ('status', 'score', 'payload', 'created_at')})
    ids = set(entries) | set(children) | set(favorites) | set(events) | {int(k) for k in banners if k.isdecimal()}
    ids.update(r.base_work_id for r in children.values())
    works = {r.id: {'title': r.name, 'identities': {}} for r in await db.scalars(select(CatalogueEntity).where(CatalogueEntity.id.in_(ids)))}
    for row in await db.scalars(select(CatalogueIdentity).where(CatalogueIdentity.entity_id.in_(ids),
            CatalogueIdentity.namespace.in_(('igdb.game', 'steam.app')))):
        # Legacy multi-app roots still carry their primary IGDB reference.
        works[row.entity_id]['identities'].setdefault(row.namespace, row.external_id)
    unlocks = {}
    for unlock, definition in (await db.execute(select(GameAchievementUnlock, GameAchievement).join(
            GameAchievement, GameAchievement.id == GameAchievementUnlock.achievement_id).where(GameAchievementUnlock.user_id == user_id))).all():
        unlocks.setdefault(unlock.work_id, []).append({'app_id': definition.app_id, 'api_name': definition.api_name,
            'source': unlock.source, 'observed_at': unlock.observed_at})
    games = []
    for work_id in sorted(ids):
        entry, child, favorite = entries.get(work_id), children.get(work_id), favorites.get(work_id)
        activities = events.get(work_id, [])
        if not (entry or child or favorite or activities or str(work_id) in banners):
            continue
        games.append({'work': works[work_id], 'entry': {k: getattr(entry, k) for k in ENTRY_FIELDS} if entry else None,
            'child': {**{k: getattr(child, k) for k in CHILD_FIELDS}, 'base': works[child.base_work_id],
                'listed': child.base_entry_id is not None} if child else None,
            'favorite': favorite is not None, 'position': favorite.position if favorite else None,
            'banner': banners.get(str(work_id)), 'unlocks': unlocks.get(work_id, []), 'activities': activities})
    return Backup.model_validate({'games': games}).model_dump(mode='json')


async def resolve_work(db, reference):
    identities = reference.identities
    if not identities or set(identities) - {'igdb.game', 'steam.app'}:
        raise ValueError('Game backup requires exact provider identities')
    found = set()
    for namespace, native in identities.items():
        catalogue.validate_identity(namespace, native, 'game')
        identity = await catalogue.resolve_identity(db, namespace, native)
        if identity:
            found.add(identity.entity_id)
    if len(found) > 1:
        raise ValueError('Game backup identities conflict')
    if not found and identities.get('igdb.game'):
        # Trusted provider hydration, never title matching or archive catalogue writes.
        from sqlalchemy.ext.asyncio import async_sessionmaker
        from core.game_metadata import GameMetadata
        identifier = await GameMetadata(async_sessionmaker(db.bind, expire_on_commit=False)).resolve(db, identities['igdb.game'])
        if identifier:
            found.add(identifier)
    if not found:
        raise ValueError('Game identity unavailable; retain the backup and retry')
    work = await game_tracking.work(db, next(iter(found)))
    if identities.get('igdb.game'):
        natives = set(await db.scalars(select(CatalogueIdentity.external_id).where(
            CatalogueIdentity.entity_id == work.id, CatalogueIdentity.namespace == 'igdb.game')))
        if identities['igdb.game'] not in natives:
            raise ValueError('Game backup canonical identity conflicts')
    return work


async def restore_games(db, user_id, backup, *, cancelled):
    if not backup.games:
        return {'games': 0, 'errors': 0}
    from core.game_access import require
    from core.sync_jobs import SyncCancelled
    require(await db.get(User, user_id))
    restored = errors = 0
    # Parents before children; source IDs and favorite slots are never reused.
    for game in sorted(backup.games, key=lambda r: (r.child is not None, r.position is None, r.position or 0)):
        await cancelled()
        try:
            work = await resolve_work(db, game.work)
            base = await resolve_work(db, game.child.base) if game.child else None
            if game.entry and game.child or game.unlocks and not game.entry:
                raise ValueError('Invalid game backup state')
            if (game.entry or game.favorite) and not capabilities(work.attributes).independent:
                raise ValueError('An independent game is required')
            if game.child and (not playable_child(work.attributes, work.name)
                    or await game_tracking.base_work_id(db, work) != base.id):
                raise ValueError('Expansion base identity conflicts')
            if game.unlocks:
                # Definitions come from Steam, never from untrusted archive labels.
                missing = False
                for unlock in game.unlocks:
                    missing |= not bool(await db.scalar(select(GameAchievement.id).where(GameAchievement.work_id == work.id,
                        GameAchievement.app_id == unlock.app_id, GameAchievement.api_name == unlock.api_name)))
                if missing:
                    from core.game_achievements import GameAchievements, view
                    from sqlalchemy.ext.asyncio import async_sessionmaker
                    await view(db, work, enqueue=True)
                    await db.commit()
                    await GameAchievements(async_sessionmaker(db.bind, expire_on_commit=False)).run(work.id)
            await db.commit()  # Hydration finishes before the private write boundary.
            await game_tracking.lock_owner(db, user_id)
            await cancelled()
            require(await db.get(User, user_id, populate_existing=True))
            async with db.begin_nested():
                existing = await db.scalar(select(GameEntry).where(GameEntry.user_id == user_id, GameEntry.work_id == work.id))
                retained = await db.scalar(select(GameChildState).where(GameChildState.user_id == user_id, GameChildState.work_id == work.id))
                if not existing and not retained:
                    if game.entry:
                        db.add(GameEntry(user_id=user_id, work_id=work.id, revision=1,
                            **game.entry.model_dump(include=set(ENTRY_FIELDS))))
                        await db.flush()
                        for unlock in game.unlocks:
                            definition = await db.scalar(select(GameAchievement).where(GameAchievement.work_id == work.id,
                                GameAchievement.app_id == unlock.app_id, GameAchievement.api_name == unlock.api_name))
                            if not definition:
                                raise ValueError('Achievement definition unavailable; retry after catalogue refresh')
                            db.add(GameAchievementUnlock(user_id=user_id, work_id=work.id, achievement_id=definition.id,
                                source=unlock.source, observed_at=unlock.observed_at))
                    if game.child:
                        parent = await db.scalar(select(GameEntry).where(GameEntry.user_id == user_id, GameEntry.work_id == base.id))
                        if game.child.listed and not parent:
                            raise ValueError('Listed expansion requires its restored base entry')
                        db.add(GameChildState(user_id=user_id, work_id=work.id, base_work_id=base.id,
                            base_entry_id=parent.id if game.child.listed else None, revision=1,
                            **game.child.model_dump(include=set(CHILD_FIELDS))))
                    for event in game.activities:
                        # Stable evidence makes re-import idempotent even for activity-only works.
                        present = await db.scalar(select(GameActivity.id).where(GameActivity.user_id == user_id,
                            GameActivity.work_id == work.id, GameActivity.created_at == event.created_at,
                            GameActivity.payload == event.payload).limit(1))
                        if not present:
                            db.add(GameActivity(user_id=user_id, work_id=work.id, **event.model_dump()))
                if game.favorite:
                    await game_tracking.set_favorite(db, user_id, work.id, True)
                if game.banner:
                    if not image(game.banner):
                        raise ValueError('Invalid game banner')
                    profile = await db.scalar(select(UserProfileData).where(UserProfileData.user_id == user_id))
                    if profile is None:
                        profile = UserProfileData(user_id=user_id); db.add(profile)
                    profile.game_banners = {str(work.id): game.banner, **(profile.game_banners or {})}
                await db.flush()
            from core.list_categories import restore_populated
            await restore_populated(db, user_id)
            await db.commit()
            restored += 1
        except SyncCancelled:
            await db.rollback()
            raise
        except Exception:
            await db.rollback()
            errors += 1
    return {'games': restored, 'errors': errors}
