"""Catalogue-keyed game tracking. Visibility preferences are presentation only."""
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Path, Query,Response,Request
from pydantic import BaseModel, Field
from sqlalchemy import case, func, literal, select, union_all
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
from schemas_games import (AchievementOut, AchievementPage, ChildPatch, FavoritePatch,
                           GameEntryOut, GameListOut, GameListProfile, GamePlayingOut, GamePatch, GameStateBatch, GameStatus, GameFavoriteOrder)
from schemas_game_statistics import GameStatsResponse,GameStatsSection
from core.limiter import limiter

router = APIRouter(prefix='/tracking/games', tags=['games'], dependencies=[Depends(approved_games_viewer)])
DB = Annotated[AsyncSession, Depends(get_db)]
Reader = Annotated[User, Depends(get_current_user_or_api_key)]
Writer = Annotated[User, Depends(get_current_user)]
Viewer = Annotated[User | None, Depends(get_optional_user_or_api_key)]
async def canonical_game_id(work_id: Annotated[int, Path(gt=0)], db: DB) -> int:
    from core.game_identity_reconciliation import alias_target
    return await alias_target(db, work_id) or work_id


WorkId = Annotated[int, Depends(canonical_game_id)]
Admin = Annotated[User, Depends(require_admin)]


class BannerChoice(BaseModel):
    model_config={'extra':'forbid'}
    image:str|None=Field(default=None,max_length=2048)


@router.put('/works/{work_id}/banner')
async def save_game_banner(work_id:WorkId,body:BannerChoice,db:DB,viewer:Writer) -> dict:
    from models import UserProfileData
    from core.catalogue_normalize import image
    await tracking.work(db,work_id)
    if body.image and not image(body.image):raise HTTPException(422,'Choose an HTTPS artwork image')
    # Account lock also serializes creating a profile and independent game pins.
    await db.execute(select(User.id).where(User.id==viewer.id).with_for_update())
    profile=await db.scalar(select(UserProfileData).where(UserProfileData.user_id==viewer.id).with_for_update())
    if not profile:profile=UserProfileData(user_id=viewer.id);db.add(profile)
    choices=dict(profile.game_banners or {})
    if body.image:choices[str(work_id)]=body.image
    else:choices.pop(str(work_id),None)
    profile.game_banners=choices
    await db.commit()
    return {'pinned_banner':body.image}


@router.get('/works/{work_id}/background')
async def game_background(work_id:WorkId,db:DB,viewer:Reader,animated:bool=False,
        provider:Literal['steamgriddb','igdb']='steamgriddb') -> dict:
    from core.game_artwork import images,cycling
    from core.media_details import primary_details
    work=await tracking.work(db,work_id)
    # IGDB primary scenes are already cached: mobile needs no banner fan-out.
    rows,state=([],{'state':'ready'}) if provider=='igdb' else await images(db,work,'heroes_animated' if animated else 'heroes')
    rows=rows[:15]
    primary=primary_details(work)
    if provider=='igdb' and not primary['backdrop_mobile']:
        rows,state=await images(db,work,'heroes')
        rows=rows[:15]
        primary=primary_details(work)
    from models import UserProfileData
    pinned=await db.scalar(select(UserProfileData.game_banners[str(work_id)].astext).where(UserProfileData.user_id==viewer.id))
    if provider=='igdb':
        scenes=primary['backdrop_fallbacks'] or [row['url'] for row in rows]
        return {**primary,'pinned_banner':pinned,'backdrop':pinned or primary['backdrop_mobile'],
            'banners':scenes,'cycle_banners':scenes[:6],'background_status':state}
    return {**primary,'pinned_banner':pinned,'backdrop':pinned or (rows[0]['url'] if animated and rows else primary['backdrop']),
        'banners':[row['url'] for row in rows],
        'cycle_banners':cycling(rows),
        'background_status':state}


@router.get('/works/{work_id}/images')
async def game_images(work_id:WorkId,db:DB,viewer:Reader) -> dict:
    from core.game_artwork import images
    from core.media_details import primary_details
    from core.media_images import unique_images
    work=await tracking.work(db,work_id)
    rows,state=await images(db,work,'grids')
    primary=primary_details(work)
    alternatives=unique_images([row['url'] for row in rows],limit=None)
    from core.media_images import image_key
    alternatives=[url for url in alternatives if image_key(url)!=image_key(primary['poster'] or '')][:10]
    return {**primary,'posters':unique_images([primary['poster'],*alternatives],limit=None),
        'image_status':state}


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


@router.get('/works/{work_id}/offers')
async def game_offers(work_id:WorkId,db:DB,viewer:Reader,
    country:Annotated[str|None,Query(pattern=r'^[A-Za-z]{2}$')]=None) -> dict:
    from core.game_offers import view,notify_refresh
    from core.catalogue_providers import ProviderError
    work=await tracking.work(db,work_id)
    try:result=await view(db,work,viewer.id,hint=country,enqueue=True)
    except ProviderError:raise HTTPException(422,'Invalid store country') from None
    await db.commit()
    notify_refresh(result)
    return result


@router.get('/works/{work_id}/ratings')
async def game_ratings(work_id:WorkId,db:DB,viewer:Reader) -> dict:
    from core.game_ratings import view
    result=await view(db,await tracking.work(db,work_id),enqueue=True)
    await db.commit()
    return result


@router.get('/people/{native}/imdb')
async def imdb_person_status(native: Annotated[str,Path(pattern=r'^nm[0-9]+$',max_length=30)],db:DB,viewer:Reader,
    work_id:Annotated[list[Annotated[int,Field(gt=0)]],Query(max_length=100)]=[],
    kind:Literal['actor','staff']='actor') -> dict:
    from core.game_imdb_person import view
    from core.catalogue import resolve_identity
    identity=await resolve_identity(db,'imdb.person',native)
    if not identity:raise HTTPException(404,'Person not found')
    # Status-only polls neither enqueue more people nor read raw filmographies.
    result=await view(db,native,include_profile=False)
    from core.game_bulk import status as bulk_status
    person=await db.get(CatalogueEntity,identity.entity_id)
    result=await bulk_status(db,person.canonical_person_id or person.id,kind,result)
    if work_id:
        from core.game_contributor_works import role_captions
        person=await db.get(CatalogueEntity,identity.entity_id)
        result['roles']=await role_captions(db,person.canonical_person_id or person.id,kind,set(work_id))
        result['characters_pending']=any(row.get('characters_pending') for row in result['roles'])
    return result


class PersonImageBatch(BaseModel):
    ids:list[Annotated[int,Field(gt=0,strict=True)]]=Field(min_length=1,max_length=16)


@router.post('/people/images')
async def game_person_images(body:PersonImageBatch,db:DB,viewer:Reader) -> dict:
    from core.game_person_images import portraits
    return await portraits(db,list(dict.fromkeys(body.ids)))


@router.get('/companies/{company_id}/works/status')
async def company_works_status(company_id:Annotated[int,Path(gt=0)],db:DB,viewer:Reader) -> dict:
    from core.game_company_works import status
    result=await status(db,company_id)
    if not result:raise HTTPException(404,'Company source not found')
    from core.game_bulk import status as bulk_status
    return await bulk_status(db,company_id,'studio',result)


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
            if work:
                from core.game_achievements import view as achievement_catalogue
                await achievement_catalogue(db,work,enqueue=True)
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


@router.get('/works/{work_id}/info')
async def game_store_info(work_id:WorkId,db:DB,viewer:Reader,country:Annotated[str|None,Query(min_length=2,max_length=2)]=None) -> dict:
    from core.game_info import view
    from core.game_offers import country as effective_country
    from core.catalogue_providers import ProviderError
    work=await tracking.work(db,work_id)
    try:region=await effective_country(db,viewer.id,country)
    except ProviderError:raise HTTPException(422,'Invalid country') from None
    result=await view(db,work,enqueue=True);await db.commit()
    return {**result,'country':region['country'],'country_source':region['source']}


@router.get('/works/{work_id}/credits')
async def game_credits(work_id: WorkId, db: DB, viewer: Reader, request_context:Request,
        revision:Annotated[str|None,Query(max_length=64)]=None) -> dict:
    from core.media_details import work_details
    from core.game_imdb_enrichment import request,status
    from core.game_credit_updates import revision as current_revision,wait_for_revision,Disconnected
    work=await tracking.work(db,work_id)
    state=await request(db,work_id)
    await db.commit()
    token,waitable=await current_revision(db,work_id)
    await db.commit()
    if revision==token and waitable:
        try:
            admitted=await wait_for_revision(db.bind,work_id,token,disconnected=request_context.is_disconnected)
        except Disconnected:
            raise HTTPException(499,'Request cancelled') from None
        # Approval may change while this read is held. Never reuse viewer data
        # from another subscriber or project against the old ORM snapshot.
        fresh_viewer=await db.get(User,viewer.id,populate_existing=True)
        from core.game_access import require
        require(fresh_viewer)
        await db.refresh(work)
        state=await status(db,work_id)
        token,waitable=await current_revision(db,work_id)
        waitable=waitable and admitted
    details=await work_details(db,work,extras=False)
    character_state=details.get('game_character_status') or {}
    combined={**state}
    if character_state.get('pending'):
        combined.update(pending=True,retry_after=max(state.get('retry_after',2),character_state.get('retry_after',2)),
            error=state.get('error') or character_state.get('error'))
    return {'people':details['people'],'organizations':details['organizations'],
        'game_characters':details.get('game_characters',[]),'game_character_status':character_state,'character_status':combined,
        'game_credit_status':state,'credit_updates':{'revision':token,'waitable':waitable}}


@router.get('/works/{work_id}/metadata')
async def game_metadata_status(work_id:WorkId,db:DB,viewer:Reader,
    primary:bool=False,revision:Annotated[str|None,Query(max_length=100)]=None) -> dict:
    from core.game_metadata import GameMetadata
    from sqlalchemy.ext.asyncio import async_sessionmaker
    work=await tracking.work(db,work_id)
    service=GameMetadata(async_sessionmaker(db.bind,expire_on_commit=False),scope=db.bind)
    # The browser's primary read joins publication, with its SQL connection released.
    # Ready data need not wait for another browser polling interval. Warm reads
    # still return immediately and never acquire optional character payloads.
    state,_=await service.ensure(db,work,wait_seconds=15 if primary else 2)
    if primary and state['primary_ready'] and state.get('primary_revision')!=revision:
        from core.media_details import game_primary_details
        await db.refresh(work)
        return {**state,'primary':await game_primary_details(db,work)}
    return state


@router.get('/works/{work_id}/primary')
async def game_primary(work_id:WorkId,db:DB,viewer:Reader,response:Response) -> dict:
    from core.media_details import game_primary_details
    response.headers['Cache-Control']='private, no-store'
    return await game_primary_details(db,await tracking.work(db,work_id))


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
        from core.game_content import hide_erotic_games
        hide_erotic=await hide_erotic_games(db,viewer.id if viewer else None)
        await db.close()
        return await game_discovery.search(db, q.strip(),hide_erotic_games=hide_erotic)
    except ProviderError:
        raise HTTPException(503, 'Game search is temporarily unavailable') from None


@router.post('/warm/{namespace}/{identifier}',status_code=204)
@limiter.limit('12/minute')
async def warm_game(request:Request,namespace:Literal['work','igdb'],
        identifier:Annotated[int,Path(gt=0,le=2147483647)],db:DB,viewer:Reader,response:Response):
    from core.game_metadata import GameMetadata,native_id
    from core.game_work import work_context,Priority
    from sqlalchemy.ext.asyncio import async_sessionmaker
    await catalog_access(db,viewer)
    native=str(identifier)
    if namespace=='work':
        work=await tracking.work(db,await canonical_game_id(identifier,db))
        native=await native_id(db,work)
    if native:
        # Speculation yields to clicks; demand promotes the existing job later.
        with work_context(Priority.FINISH):
            await GameMetadata(async_sessionmaker(db.bind,expire_on_commit=False),scope=db.bind).warm(db,native)
    response.headers['Cache-Control']='private, no-store'
    response.status_code=204
    return response


@router.post('/resolve/{igdb_id}')
async def resolve_game(igdb_id: Annotated[int,Path(gt=0,le=2147483647)], db: DB, viewer: Viewer) -> dict:
    from core.game_metadata import GameMetadata
    from core.catalogue_providers import ProviderError
    from sqlalchemy.ext.asyncio import async_sessionmaker
    await catalog_access(db, viewer)
    sessions=async_sessionmaker(db.bind,expire_on_commit=False)
    try:identifier=await GameMetadata(sessions,scope=db.bind).resolve(db,str(igdb_id))
    except ProviderError:raise HTTPException(503,'Game details are temporarily unavailable') from None
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
async def browse_games(db: DB, viewer: Viewer, background:BackgroundTasks,
        category: Literal['trending','most_played','sellers','newest','popular','score'] = 'trending',
        genres: Annotated[str, Query(max_length=1000)] = '',
        platforms: Annotated[str, Query(max_length=5000)] = '',
        start: str | None = None, end: str | None = None,
        q: Annotated[str, Query(max_length=100)] = '',
        timeline: bool = False,
        status: Literal['released','upcoming'] | None = None,
        today: Annotated[str | None, Query(max_length=10)] = None,
        unfiltered: bool = False,
        min_metacritic: Annotated[int, Query(ge=0,le=100)] = 0,
        limit: Annotated[int, Query(ge=1,le=48)] = 24,
        cursor: Annotated[str | None, Query(max_length=512)] = None) -> dict:
    from core import game_browse
    from core import game_browse_policy
    from core.catalogue_providers import ProviderError
    await catalog_access(db, viewer)
    viewer_id = viewer.id if viewer else None
    saved = await game_browse_policy.viewer_preferences(db, viewer_id)
    hide_erotic = saved.get('hide_erotic_games') is not False
    policy = game_browse_policy.policy(saved, upcoming=status=='upcoming',
        released=status=='released' or status is None and (timeline or category=='newest'),
        unfiltered=unfiltered)
    # Authentication/catalog policy reads must not occupy a pooled connection
    # while a cold public ranking is waiting on providers.
    await db.close()
    service = game_browse.GameBrowse()
    try:
        query = await game_browse.filters(service.sources, category=category,genres=genres,platforms=platforms,
            start=start,end=end,q=q,min_metacritic=min_metacritic,timeline=timeline,
            hide_erotic_games=hide_erotic,status=status,today=today,policy=policy)
        page = await service.page(query,limit=limit,cursor=cursor,
            wait_seconds=.15 if min_metacritic else None)
        result=await game_browse.project(db,viewer_id,page)
        if cursor is None and not q and not genres and not platforms and not start and not end and not timeline and not status:
            from core.game_background import observe_browse
            background.add_task(observe_browse,db.bind,viewer_id,category,result['results'])
        return result
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


@router.get('/works/{work_id}/steam-progress')
async def steam_progress(work_id: WorkId, db: DB, viewer: Writer, response: Response) -> dict:
    from core.game_steam_progress import context
    await tracking.work(db, work_id)
    response.headers['Cache-Control'] = 'private, no-store'
    return await context(db, viewer.id, work_id)


@router.post('/steam-progress/warnings/{field}')
async def acknowledge_steam_warning(field: Literal['playtime', 'achievements'], db: DB, viewer: Writer) -> dict:
    from core.game_steam_progress import acknowledge
    await acknowledge(db, viewer.id, field)
    return {'acknowledged': field}


@router.get('/works/{work_id}/children')
async def game_children(work_id:WorkId,db:DB,viewer:Reader,
        offset:Annotated[int,Query(ge=0)]=0,limit:Annotated[int,Query(ge=1,le=50)]=24) -> dict:
    from core.game_children import children
    return await children(db,work_id,viewer.id,offset=offset,limit=limit)


@router.patch('/entries/{work_id}')
async def save_entry(work_id: WorkId, body: GamePatch, db: DB, viewer: Writer,background:BackgroundTasks) -> GameEntryOut:
    result=await tracking.save_entry(db, viewer.id, work_id, body)
    background.add_task(request_game_metadata,work_id,db.bind)
    from core.game_background import observe_work
    background.add_task(observe_work,work_id,db.bind,viewer.id,1)
    return result


@router.delete('/entries/{work_id}')
async def remove_entry(work_id: WorkId, db: DB, viewer: Writer,background:BackgroundTasks,
                       revision: Annotated[int, Query(ge=1)],
                       confirmed: bool = False) -> dict:
    if not confirmed:
        raise HTTPException(409, 'Confirm removal of the base game and its listed expansions')
    await tracking.remove_entry(db, viewer.id, work_id, revision)
    from core.game_background import observe_work
    background.add_task(observe_work,work_id,db.bind,viewer.id,1)
    return {'deleted': True}


@router.patch('/children/{work_id}')
async def save_child(work_id: WorkId, body: ChildPatch, db: DB, viewer: Writer,background:BackgroundTasks,
                     listed: bool = True) -> GameEntryOut:
    result=await tracking.save_child(db, viewer.id, work_id, body, listed=listed)
    from core.game_background import observe_work
    background.add_task(observe_work,work_id,db.bind,viewer.id,1)
    return result


@router.delete('/children/{work_id}')
async def unlist_child(work_id: WorkId, db: DB, viewer: Writer,background:BackgroundTasks,
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
    from core.game_background import observe_work
    background.add_task(observe_work,work_id,db.bind,viewer.id,1)
    return (await tracking.states(db, viewer.id, [work_id]))[0]


@router.put('/favorites/order')
async def favorite_order(body: GameFavoriteOrder, db: DB, viewer: Writer) -> dict:
    await tracking.reorder_favorites(db, viewer.id, body.work_ids)
    return {'work_ids': body.work_ids}


@router.put('/favorites/{work_id}')
async def set_favorite(work_id: WorkId, body: FavoritePatch, db: DB, viewer: Writer,background:BackgroundTasks) -> GameEntryOut:
    await tracking.lock_owner(db, viewer.id)
    entity = await tracking.work(db, work_id)
    if not capabilities(entity.attributes).independent:
        raise HTTPException(422, 'Only independently playable games can be favorites')
    await tracking.set_favorite(db, viewer.id, work_id, body.favorite)
    await db.commit()
    from core.game_background import observe_work
    background.add_task(observe_work,work_id,db.bind,viewer.id,1)
    return (await tracking.states(db, viewer.id, [work_id]))[0]


@router.get('/works/{work_id}/achievements')
async def achievement_page(work_id: WorkId, db: DB, viewer: Reader,
                            offset: Annotated[int, Query(ge=0)] = 0,
                            limit: Annotated[int, Query(ge=1, le=200)] = 50) -> AchievementPage:
    work=await tracking.work(db, work_id)
    from core.game_ratings import app_id
    app=await app_id(db,work)
    query = select(GameAchievement, GameAchievementUnlock.achievement_id).outerjoin(
        GameAchievementUnlock, (GameAchievementUnlock.achievement_id == GameAchievement.id)
        & (GameAchievementUnlock.user_id == viewer.id)).where(GameAchievement.work_id == work_id,
            GameAchievement.active.is_(True),GameAchievement.app_id==app)
    total = await db.scalar(select(func.count()).select_from(GameAchievement).where(GameAchievement.work_id==work_id,
        GameAchievement.active.is_(True),GameAchievement.app_id==app))
    listed = await db.scalar(select(GameEntry.work_id).where(GameEntry.user_id==viewer.id,GameEntry.work_id==work_id))
    order = GameAchievement.sort_order.asc().nulls_last() if listed is not None else GameAchievement.global_percentage.desc().nulls_last()
    rows = (await db.execute(query.order_by(order,GameAchievement.id).offset(offset).limit(limit))).all()
    return AchievementPage(results=[AchievementOut.model_validate(row).model_copy(update={'unlocked': unlocked is not None})
        for row, unlocked in rows], total=total, offset=offset, limit=limit)


@router.get('/works/{work_id}/achievement-catalogue')
async def achievement_catalogue(work_id:WorkId,db:DB,viewer:Reader) -> dict:
    from core.game_achievements import view
    result=await view(db,await tracking.work(db,work_id),enqueue=True)
    await db.commit()
    return result


@router.get('/users/{username}/list')
async def user_list(username: str, db: DB, viewer: Viewer,
                    status: GameStatus | None = None,
                    sort: Literal['title', 'score', 'progress', 'updated'] = 'title',
                    q: Annotated[str, Query(max_length=200)] = '',
                    genre: Annotated[str, Query(max_length=200)] = '',
                    year: Annotated[str | None, Query(pattern=r'^\d{4}$')] = None,
                    favorites: bool = False,
                    complete: bool = False,
                    grouped: bool = False,
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
    group_counts = {}
    if grouped:
        filtered = query.with_only_columns(memberships.c.status).subquery()
        group_counts = dict((await db.execute(select(filtered.c.status, func.count()).group_by(filtered.c.status))).all())
    group_order = case({name: index for index, name in enumerate(('playing','completed','paused','dropped','planning'))}, value=memberships.c.status, else_=5)
    ordered = query.order_by(*([group_order] if grouped else []), ordering, memberships.c.work_id)
    ids = list((await db.scalars(ordered if complete else ordered.offset(offset).limit(limit))).all())
    rows = {r.subject.catalogue_id: r for r in await tracking.states(db, user.id, ids, owner=owner)}
    return GameListOut(results=[rows[i] for i in ids], total=total, offset=0 if complete else offset, limit=total if complete else limit,
        profile=GameListProfile.model_validate(await profile_header(db, user, owner, viewer)),
        status_counts=status_counts, group_counts=group_counts, genres=genres, years=years)


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
    work=await tracking.work(db, work_id)
    people = await game_activity.social_people(db, viewer.id, work_id)
    return {'people': people,'game_class':capabilities(work.attributes).game_class,
        'activities': await game_activity.rows(db, [viewer.id], limit=30, work_id=work_id),
        'following_activities': await game_activity.rows(db, game_activity.following_ids(viewer.id),
            limit=30, work_id=work_id, public_only=True)}
