"""Catalogue-keyed game tracking. Visibility preferences are presentation only."""
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import func, literal, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from core import game_tracking as tracking
from core.game_types import capabilities
from core.tracking_access import profile_access, profile_header
from db import get_db
from dependencies import get_current_user, get_current_user_or_api_key, get_optional_user_or_api_key
from models import User
from models.catalogue import CatalogueEntity
from models.games import GameAchievement, GameAchievementUnlock, GameChildState, GameEntry, GameFavorite
from models import UserProfileData
from models.base import PrivacyLevel
from sqlalchemy import or_, and_
from schemas_games import (AchievementOut, AchievementPage, ChildPatch, FavoritePatch,
                           GameEntryOut, GameListOut, GameListProfile, GamePlayingOut, GamePatch, GameStateBatch, GameStatus, GameFavoriteOrder)

router = APIRouter(prefix='/tracking/games', tags=['games'])
DB = Annotated[AsyncSession, Depends(get_db)]
Reader = Annotated[User, Depends(get_current_user_or_api_key)]
Writer = Annotated[User, Depends(get_current_user)]
Viewer = Annotated[User | None, Depends(get_optional_user_or_api_key)]
WorkId = Annotated[int, Path(gt=0)]


@router.post('/state')
async def batch_state(body: GameStateBatch, db: DB, viewer: Reader) -> list[GameEntryOut]:
    return await tracking.states(db, viewer.id, body.work_ids)


@router.get('/works/{work_id}')
async def editor_state(work_id: WorkId, db: DB, viewer: Reader) -> GameEntryOut:
    entity = await tracking.work(db, work_id)
    state = (await tracking.states(db, viewer.id, [work_id]))[0]
    if capabilities(entity.attributes).playable_child and state.base_work_id is None:
        state.base_work_id = await tracking.base_work_id(db, entity)
    return state


@router.patch('/entries/{work_id}')
async def save_entry(work_id: WorkId, body: GamePatch, db: DB, viewer: Writer) -> GameEntryOut:
    return await tracking.save_entry(db, viewer.id, work_id, body)


@router.delete('/entries/{work_id}')
async def remove_entry(work_id: WorkId, db: DB, viewer: Writer,
                       revision: Annotated[int, Query(ge=1)],
                       confirmed: bool = False) -> dict:
    if not confirmed:
        raise HTTPException(409, 'Confirm removal of the base game and its listed expansions')
    await tracking.remove_entry(db, viewer.id, work_id, revision)
    return {'deleted': True}


@router.patch('/children/{work_id}')
async def save_child(work_id: WorkId, body: ChildPatch, db: DB, viewer: Writer,
                     listed: bool = True) -> GameEntryOut:
    return await tracking.save_child(db, viewer.id, work_id, body, listed=listed)


@router.delete('/children/{work_id}')
async def unlist_child(work_id: WorkId, db: DB, viewer: Writer,
                       revision: Annotated[int, Query(ge=1)]) -> GameEntryOut:
    await tracking.lock_owner(db, viewer.id)
    await tracking.work(db, work_id)
    row = await db.scalar(select(GameChildState).where(
        GameChildState.user_id == viewer.id, GameChildState.work_id == work_id))
    tracking.require_revision(row, revision)
    if row:
        row.base_entry_id = None
        row.revision += 1
    await db.commit()
    return (await tracking.states(db, viewer.id, [work_id]))[0]


@router.put('/favorites/order')
async def favorite_order(body: GameFavoriteOrder, db: DB, viewer: Writer) -> dict:
    await tracking.reorder_favorites(db, viewer.id, body.work_ids)
    return {'work_ids': body.work_ids}


@router.put('/favorites/{work_id}')
async def set_favorite(work_id: WorkId, body: FavoritePatch, db: DB, viewer: Writer) -> GameEntryOut:
    await tracking.lock_owner(db, viewer.id)
    entity = await tracking.work(db, work_id)
    if not capabilities(entity.attributes).independent:
        raise HTTPException(422, 'Only independently playable games can be favorites')
    await tracking.set_favorite(db, viewer.id, work_id, body.favorite)
    await db.commit()
    return (await tracking.states(db, viewer.id, [work_id]))[0]


@router.get('/works/{work_id}/achievements')
async def achievement_page(work_id: WorkId, db: DB, viewer: Reader,
                            offset: Annotated[int, Query(ge=0)] = 0,
                            limit: Annotated[int, Query(ge=1, le=200)] = 50) -> AchievementPage:
    await tracking.work(db, work_id)
    query = select(GameAchievement, GameAchievementUnlock.achievement_id).outerjoin(
        GameAchievementUnlock, (GameAchievementUnlock.achievement_id == GameAchievement.id)
        & (GameAchievementUnlock.user_id == viewer.id)).where(GameAchievement.work_id == work_id)
    total = await db.scalar(select(func.count()).select_from(GameAchievement).where(GameAchievement.work_id == work_id))
    rows = (await db.execute(query.order_by(GameAchievement.id).offset(offset).limit(limit))).all()
    return AchievementPage(results=[AchievementOut.model_validate(row).model_copy(update={'unlocked': unlocked is not None})
        for row, unlocked in rows], total=total, offset=offset, limit=limit)


@router.get('/users/{username}/list')
async def user_list(username: str, db: DB, viewer: Viewer,
                    status: GameStatus | None = None,
                    sort: Literal['title', 'score', 'progress', 'updated'] = 'title',
                    q: Annotated[str, Query(max_length=200)] = '',
                    genre: Annotated[str, Query(max_length=200)] = '',
                    year: Annotated[str | None, Query(pattern=r'^\d{4}$')] = None,
                    favorites: bool = False,
                    offset: Annotated[int, Query(ge=0)] = 0,
                    limit: Annotated[int, Query(ge=1, le=200)] = 50) -> GameListOut:
    user, owner = await profile_access(db, username, viewer)
    # Membership is unioned, never added into main-game statistics. Both kinds
    # use their own status, title and score for list filters and sorting.
    entries = select(GameEntry.work_id, GameEntry.status, GameEntry.score,
        GameEntry.total_seconds, GameEntry.updated_at).where(GameEntry.user_id == user.id)
    children = select(GameChildState.work_id, GameChildState.status, GameChildState.score,
        literal(None).label('total_seconds'), GameChildState.updated_at).where(
        GameChildState.user_id == user.id, GameChildState.base_entry_id.is_not(None))
    memberships = union_all(entries, children).subquery()
    status_counts = dict((await db.execute(select(memberships.c.status, func.count())
        .group_by(memberships.c.status))).all())
    # Transfer only distinct descriptive facet combinations, never the whole
    # catalogue/credit payload or personal entries outside the displayed page.
    facets = (await db.execute(select(CatalogueEntity.attributes['genres'],
        func.substr(CatalogueEntity.attributes['release_date'].astext, 1, 4))
        .join(memberships, memberships.c.work_id == CatalogueEntity.id).distinct())).all()
    genres = sorted({genre for values, _ in facets if isinstance(values, list) for genre in values if isinstance(genre, str)})
    years = sorted({value for _, value in facets if value and len(value) == 4 and value.isdigit()}, reverse=True)
    query = select(memberships.c.work_id).join(CatalogueEntity, CatalogueEntity.id == memberships.c.work_id)
    if status:
        query = query.where(memberships.c.status == status)
    if q.strip():
        query = query.where(CatalogueEntity.name.icontains(q.strip(), autoescape=True))
    if genre:
        query = query.where(CatalogueEntity.attributes['genres'].contains([genre]))
    if year:
        query = query.where(CatalogueEntity.attributes['release_date'].astext.startswith(year, autoescape=True))
    if favorites:
        query = query.where(memberships.c.work_id.in_(select(GameFavorite.work_id).where(GameFavorite.user_id == user.id)))
    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    ordering = {'title': func.lower(CatalogueEntity.name).asc(),
                'score': memberships.c.score.desc().nullslast(),
                'progress': memberships.c.total_seconds.desc().nullslast(),
                'updated': memberships.c.updated_at.desc()}[sort]
    ids = list((await db.scalars(query.order_by(ordering, memberships.c.work_id).offset(offset).limit(limit))).all())
    rows = {r.subject.catalogue_id: r for r in await tracking.states(db, user.id, ids, owner=owner)}
    return GameListOut(results=[rows[i] for i in ids], total=total, offset=offset, limit=limit,
        profile=GameListProfile.model_validate(await profile_header(db, user, owner, viewer)),
        status_counts=status_counts, genres=genres, years=years)


@router.get('/users/{username}/favorites')
async def favorites(username: str, db: DB, viewer: Viewer,
                    offset: Annotated[int, Query(ge=0)] = 0,
                    limit: Annotated[int, Query(ge=1, le=200)] = 50) -> GameListOut:
    user, owner = await profile_access(db, username, viewer)
    query = select(GameFavorite.work_id).where(GameFavorite.user_id == user.id)
    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    ids = list((await db.scalars(query.order_by(GameFavorite.position.asc().nullslast(), GameFavorite.work_id)
        .offset(offset).limit(limit))).all())
    # Favorite cards need no private editor notes or revision. Fetch the full
    # viewer-scoped editor state on interaction rather than copying every note.
    rows = {row.subject.catalogue_id: row for row in await tracking.states(db, user.id, ids, owner=False)}
    return GameListOut(results=[rows[identifier] for identifier in ids], total=total, offset=offset, limit=limit,
        profile=GameListProfile.model_validate(await profile_header(db, user, owner, viewer)))


@router.get('/playing')
async def playing_games(db: DB, viewer: Reader,
                       limit: Annotated[int, Query(ge=1, le=50)] = 12) -> GamePlayingOut:
    # Dashboard is a small, focused main-game shelf; the complete Games list
    # remains the source for all statuses and separately listed expansions.
    query = select(GameEntry.work_id).where(GameEntry.user_id == viewer.id, GameEntry.status == 'playing')
    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    ids = list((await db.scalars(query.order_by(GameEntry.updated_at.desc(), GameEntry.work_id).limit(limit))).all())
    states = {state.subject.catalogue_id: state for state in await tracking.states(db, viewer.id, ids)}
    return GamePlayingOut(results=[states[identifier] for identifier in ids], total=total)


@router.get('/activity')
async def following_activity(db: DB, viewer: Reader) -> dict:
    from core import game_activity
    return {'results': await game_activity.rows(db, game_activity.following_ids(viewer.id), public_only=True)}


@router.get('/users/{username}/activity')
async def profile_activity(username: str, db: DB, viewer: Viewer,
                           limit: Annotated[int, Query(ge=1, le=60)] = 12) -> dict:
    from core import game_activity
    user, _ = await profile_access(db, username, viewer)
    return {'results': await game_activity.rows(db, [user.id], limit=limit)}


@router.get('/works/{work_id}/social')
async def game_social(work_id: WorkId, db: DB, viewer: Reader) -> dict:
    from core import game_activity
    await tracking.work(db, work_id)
    people = await game_activity.social_people(db, viewer.id, work_id)
    visible = select(User.id).outerjoin(UserProfileData, UserProfileData.user_id == User.id).where(or_(
        User.id == viewer.id, and_(User.id.in_(game_activity.following_ids(viewer.id)), UserProfileData.privacy_level == PrivacyLevel.public)))
    return {'people': people, 'activities': await game_activity.rows(db, visible, limit=30, work_id=work_id)}
