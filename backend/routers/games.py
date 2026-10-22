"""Catalogue-keyed game tracking. Visibility preferences are presentation only."""
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Path, Query,Response
from pydantic import BaseModel, Field
from sqlalchemy import func, literal, select, union_all
from sqlalchemy.ext.asyncio import AsyncSession

from core import game_tracking as tracking
from core.game_types import capabilities
from core.game_access import approved_games_viewer
from core.tracking_access import profile_access, profile_header
from core.tracking_access import catalog_access
from core.catalogue import IdentityConflict
from db import get_db
from dependencies import get_current_user, get_current_user_or_api_key, get_optional_user_or_api_key, require_admin
from models import User
from models.catalogue import CatalogueEntity
from models.games import GameAchievement, GameAchievementUnlock, GameChildState, GameEntry, GameFavorite
from models import UserProfileData
from models.base import PrivacyLevel
from sqlalchemy import or_, and_
from schemas_games import (AchievementOut, AchievementPage, ChildPatch, FavoritePatch,
                           GameEntryOut, GameListOut, GameListProfile, GamePlayingOut, GamePatch, GameStateBatch, GameStatus, GameFavoriteOrder)
from schemas_game_statistics import GameStatsResponse,GameStatsSection

router = APIRouter(prefix='/tracking/games', tags=['games'], dependencies=[Depends(approved_games_viewer)])
DB = Annotated[AsyncSession, Depends(get_db)]
Reader = Annotated[User, Depends(get_current_user_or_api_key)]
Writer = Annotated[User, Depends(get_current_user)]
Viewer = Annotated[User | None, Depends(get_optional_user_or_api_key)]
WorkId = Annotated[int, Path(gt=0)]
Admin = Annotated[User, Depends(require_admin)]


@router.get('/users/{username}/statistics')
async def game_statistics(username:str,response:Response,db:DB,viewer:Reader,section:GameStatsSection='overview',
    sort:Literal['count','mean_score','time']='count',page:Annotated[int,Query(ge=1,le=5000)]=1,
    generation:Annotated[int|None,Query(ge=1)]=None) -> GameStatsResponse:
    from core.game_statistics import read,GameStatisticsChanged
    user,owner=await profile_access(db,username,viewer)
    header=await profile_header(db,user,owner,viewer)
    response.headers['Cache-Control']='private, no-store'
    try:result=await read(db,user.id,section,sort=sort,page=page,generation=generation)
    except GameStatisticsChanged:raise HTTPException(409,'Game statistics changed. Refresh to continue.') from None
    return GameStatsResponse.model_validate({'profile':header,'owner':owner,'following':header['following'],
        'follows_you':header['follows_you'],'combine_lists':header['combine_lists'],**result})


@router.get('/people/{native}/imdb')
async def imdb_person_status(native: Annotated[str,Path(pattern=r'^nm[0-9]+$',max_length=30)],db:DB,viewer:Reader) -> dict:
    from core.game_imdb_person import view
    from core.catalogue import resolve_identity
    if not await resolve_identity(db,'imdb.person',native):raise HTTPException(404,'Person not found')
    # Status-only polls neither enqueue more people nor read raw filmographies.
    return await view(db,native,include_profile=False)


@router.get('/companies/{company_id}/works/status')
async def company_works_status(company_id:Annotated[int,Path(gt=0)],db:DB,viewer:Reader) -> dict:
    from core.game_company_works import status
    result=await status(db,company_id)
    if not result:raise HTTPException(404,'Company source not found')
    return result


@router.get('/people/{native}/imdb/titles')
async def imdb_person_titles(native: Annotated[str,Path(pattern=r'^nm[0-9]+$',max_length=30)],db:DB,viewer:Admin,
        page:Annotated[int,Query(ge=1,le=500)]=1,q:Annotated[str,Query(max_length=100)]='',
        scope:Literal['unmapped','all']='unmapped',
        generation:Annotated[str|None,Query(pattern=r'^[0-9a-f]{32}$')]=None,
        bindings:Annotated[str|None,Query(pattern=r'^(?:[0-9a-f]{32}|none)$')]=None) -> dict:
    from core.game_imdb_review import review_titles,ReviewChanged
    try:result=await review_titles(db,native,page=page,query=q.strip(),scope=scope,generation=generation,bindings=bindings)
    except ReviewChanged as error:raise HTTPException(409,str(error)) from None
    if result is None:raise HTTPException(404,'Person not found')
    return result


@router.get('/people/{native}/imdb/titles/{title_id}')
async def imdb_person_title(native: Annotated[str,Path(pattern=r'^nm[0-9]+$',max_length=30)],
        title_id:Annotated[str,Path(pattern=r'^tt[0-9]+$',max_length=30)],db:DB,viewer:Admin,
        generation:Annotated[str|None,Query(pattern=r'^[0-9a-f]{32}$')]=None) -> dict:
    from core.game_imdb_review import review_titles,ReviewChanged
    try:result=await review_titles(db,native,title_id=title_id,scope='all',generation=generation)
    except ReviewChanged as error:raise HTTPException(409,str(error)) from None
    if not result:raise HTTPException(404,'Person not found')
    if not result['ready']:raise HTTPException(409,'Cached credits are being prepared. Refresh this review.')
    if not result['results']:raise HTTPException(404,'Game title not found in this cached connection')
    return {'person':result['person'],'generation':result['generation'],'title':result['results'][0]}


async def request_game_metadata(work_id,engine):
    import logging
    from sqlalchemy.ext.asyncio import async_sessionmaker
    from core.game_imdb_enrichment import request
    try:
        async with async_sessionmaker(engine,expire_on_commit=False)() as db:
            await request(db,work_id)
            from core.game_time_to_beat import view as estimates
            work=await db.get(CatalogueEntity,work_id)
            if work:await estimates(db,work,enqueue=True)
            await db.commit()
    except Exception:
        # Tracking already committed. Metadata admission must not turn a
        # successful personal write into a misleading optimistic rollback.
        logging.getLogger(__name__).warning('Game metadata could not be queued')


@router.get('/works/{work_id}/time-to-beat')
async def game_completion_estimates(work_id:WorkId,db:DB,viewer:Reader) -> dict:
    from core.game_time_to_beat import view
    work=await tracking.work(db,work_id)
    result=await view(db,work,enqueue=True)
    await db.commit()
    return result


@router.get('/works/{work_id}/credits')
async def game_credits(work_id: WorkId, db: DB, viewer: Reader) -> dict:
    from core.media_details import work_details
    from core.game_imdb_enrichment import request
    work=await tracking.work(db,work_id)
    state=await request(db,work_id)
    await db.commit()
    details=await work_details(db,work,extras=False)
    return {'people':details['people'],'organizations':details['organizations'],
        'game_characters':details.get('game_characters',[]),'character_status':state,
        'game_credit_status':state}


class IMDbBindingReview(BaseModel):
    imdb_id: str = Field(pattern=r'^tt[0-9]+$',max_length=30)
    reason: str = Field(min_length=3,max_length=1000)
    confirmed_game_and_edition: Literal[True]


@router.get('/works/{work_id}/imdb')
async def imdb_binding(work_id: WorkId, db: DB, viewer: Reader) -> dict:
    from core.game_imdb_bindings import context, binding_view
    from core.game_imdb_enrichment import status
    try:work,native,binding,_=await context(db,work_id)
    except IdentityConflict as error:raise HTTPException(409,str(error)) from None
    selected=binding_view(binding) if binding else None
    if selected and not viewer.is_admin:selected.pop('evidence',None)
    return {'game':{'id':work.id,'name':work.name,'igdb_id':native,
        'igdb_slug':(work.attributes or {}).get('igdb_slug'),
        'release_date':(work.attributes or {}).get('release_date'),
        'game_class':(work.attributes or {}).get('game_class')},
        'binding':selected,
        'credit_status':await status(db,work_id)}


@router.get('/works/{work_id}/imdb/candidates')
async def imdb_candidates(work_id: WorkId, db: DB, viewer: Admin,
        q: Annotated[str, Query(min_length=2,max_length=100)]) -> dict:
    from core.game_imdb_source import IMDbGameSource
    from core.catalogue_providers import ProviderHTTP, ProviderError
    from sqlalchemy.ext.asyncio import async_sessionmaker
    await tracking.work(db,work_id)
    sessions=async_sessionmaker(db.bind,expire_on_commit=False)
    await db.close()
    try:return {'results':await IMDbGameSource(ProviderHTTP(session_factory=sessions)).candidates(q)}
    except ProviderError as error:raise HTTPException(503,'IMDb candidates are temporarily unavailable') from error


@router.get('/works/{work_id}/imdb/candidates/{title_id}')
async def imdb_candidate(work_id: WorkId,title_id: Annotated[str,Path(pattern=r'^tt[0-9]+$',max_length=30)],
        db: DB,viewer: Admin) -> dict:
    from core.game_imdb_source import IMDbGameSource
    from core.catalogue_providers import ProviderHTTP, ProviderError
    from sqlalchemy.ext.asyncio import async_sessionmaker
    await tracking.work(db,work_id)
    sessions=async_sessionmaker(db.bind,expire_on_commit=False)
    await db.close()
    try:return await IMDbGameSource(ProviderHTTP(session_factory=sessions)).title(title_id)
    except ProviderError as error:
        raise HTTPException(409 if error.code=='identity_conflict' else 503,
            'Select a verified IMDb video game' if error.code=='identity_conflict' else 'IMDb is temporarily unavailable') from None


@router.post('/works/{work_id}/imdb')
async def review_imdb(work_id: WorkId,body:IMDbBindingReview,db:DB,viewer:Admin) -> dict:
    from core.game_imdb_bindings import GameIMDbBindings
    from core.catalogue import IdentityConflict
    from core.catalogue_providers import ProviderError
    from core.game_imdb_enrichment import request
    from sqlalchemy.ext.asyncio import async_sessionmaker
    await tracking.work(db,work_id)
    sessions=async_sessionmaker(db.bind,expire_on_commit=False)
    await db.close()
    try:
        result=await GameIMDbBindings(sessions).review(work_id,body.imdb_id,viewer.id,body.reason)
    except (IdentityConflict,ProviderError) as error:
        raise HTTPException(409 if isinstance(error,IdentityConflict) or error.code=='identity_conflict' else 503,
            str(error) if isinstance(error,IdentityConflict) else 'IMDb identity could not be verified') from None
    await request(db,work_id)
    await db.commit()
    return result


@router.get('/search')
async def search_games(db: DB, viewer: Viewer, q: Annotated[str, Query(min_length=2, max_length=100)]) -> dict:
    from core import game_discovery
    from core.catalogue_providers import ProviderError
    await catalog_access(db, viewer)
    if len(q.strip()) < 2:
        raise HTTPException(422, 'Enter at least two characters')
    try:
        return await game_discovery.search(db, q.strip())
    except ProviderError:
        raise HTTPException(503, 'Game search is temporarily unavailable') from None


@router.post('/resolve/{igdb_id}')
async def resolve_game(igdb_id: WorkId, db: DB, viewer: Viewer) -> dict:
    from core import catalogue
    from core.catalogue_providers import CatalogueProviders, ProviderHTTP, ProviderError
    from sqlalchemy.ext.asyncio import async_sessionmaker
    await catalog_access(db, viewer)
    sessions=async_sessionmaker(db.bind,expire_on_commit=False)
    await db.close()
    try:
        result = await catalogue.refresh_metadata(db, CatalogueProviders(ProviderHTTP(session_factory=sessions)),
            'igdb', 'game', str(igdb_id))
    except ProviderError:
        raise HTTPException(503, 'Game details are temporarily unavailable') from None
    identifier = result.get('entity_id')
    if not identifier:
        raise HTTPException(503, 'Game details are still loading. Please try again.')
    entity = await tracking.work(db, identifier)
    if not capabilities(entity.attributes).searchable:
        raise HTTPException(422, 'This add-on does not have a separate game page')
    return {'id': identifier}


@router.get('/browse/facets')
async def browse_facets(db: DB, viewer: Viewer) -> dict:
    from core.game_browse_sources import GameBrowseSources
    from core.catalogue_providers import ProviderError
    await catalog_access(db, viewer)
    await db.close()
    try:
        return await GameBrowseSources().facets()
    except ProviderError:
        raise HTTPException(503, 'Game filters are temporarily unavailable') from None


@router.get('/browse')
async def browse_games(db: DB, viewer: Viewer,
        category: Literal['trending','most_played','sellers','newest','popular','score'] = 'trending',
        genres: Annotated[str, Query(max_length=1000)] = '',
        platforms: Annotated[str, Query(max_length=5000)] = '',
        start: str | None = None, end: str | None = None,
        q: Annotated[str, Query(max_length=100)] = '',
        timeline: bool = False,
        min_metacritic: Annotated[int, Query(ge=0,le=100)] = 0,
        limit: Annotated[int, Query(ge=1,le=48)] = 24,
        cursor: Annotated[str | None, Query(max_length=512)] = None) -> dict:
    from core import game_browse
    from core.catalogue_providers import ProviderError
    await catalog_access(db, viewer)
    viewer_id = viewer.id if viewer else None
    # Authentication/catalog policy reads must not occupy a pooled connection
    # while a cold public ranking is waiting on providers.
    await db.close()
    service = game_browse.GameBrowse()
    try:
        query = await game_browse.filters(service.sources, category=category,genres=genres,platforms=platforms,
            start=start,end=end,q=q,min_metacritic=min_metacritic,timeline=timeline)
        page = await service.page(query,limit=limit,cursor=cursor)
        return await game_browse.project(db,viewer_id,page)
    except ProviderError as cause:
        code = 409 if cause.code=='cursor_expired' else 422 if cause.code in {'invalid_request','invalid_cursor'} else 503
        message = 'This Games page expired. Refresh the results.' if code==409 else 'Invalid game filters or page.' if code==422 else 'Games are temporarily unavailable.'
        raise HTTPException(code,message) from None


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


@router.get('/works/{work_id}/children')
async def game_children(work_id:WorkId,db:DB,viewer:Reader,
        offset:Annotated[int,Query(ge=0)]=0,limit:Annotated[int,Query(ge=1,le=50)]=24) -> dict:
    from core.game_children import children
    return await children(db,work_id,viewer.id,offset=offset,limit=limit)


@router.patch('/entries/{work_id}')
async def save_entry(work_id: WorkId, body: GamePatch, db: DB, viewer: Writer,background:BackgroundTasks) -> GameEntryOut:
    result=await tracking.save_entry(db, viewer.id, work_id, body)
    background.add_task(request_game_metadata,work_id,db.bind)
    return result


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
