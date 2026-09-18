"""Tracked lists are independent of connected streaming-library membership."""
from datetime import date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, delete, or_, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db import get_db
from dependencies import get_current_user, get_optional_user
from core.tracking_rules import TrackingStatus, normalize_score, effective_score, default_dates
from models import Media, User, UserSettings, UserProfileData, GlobalSettings, Follow, Rating, Show, WatchEvent, Collection, CollectionFile, PlaybackProgress, PlaybackSession, MediaServerConnection, List, ListItem, ShowRewatch
from models.base import MediaType, PrivacyLevel
from models.tracking import TrackedEntry, TrackingActivity, TrackingDeletion, TrackingPreferences, SyncReview, StreamBaseline

router = APIRouter()


@router.delete('/entry/{media_id}')
async def remove_entry(media_id:int,confirmed:bool=False,db:AsyncSession=Depends(get_db),viewer:User=Depends(get_current_user)):
    if not confirmed:raise HTTPException(409,'Confirm deletion of all personal tracking data for this title')
    media=await db.get(Media,media_id)
    if not media or media.media_type not in (MediaType.movie,MediaType.series):raise HTTPException(404,'Title not found')
    await db.execute(select(User.id).where(User.id==viewer.id).with_for_update())
    ids={media_id};show_ids=set()
    if media.media_type==MediaType.series:
        terms=[]
        if media.tmdb_id:terms.append(Show.tmdb_id==media.tmdb_id)
        if media.tvdb_id:terms.append(Show.tvdb_id==media.tvdb_id)
        if terms:show_ids=set((await db.execute(select(Show.id).where(or_(*terms)))).scalars())
        if show_ids:ids.update((await db.execute(select(Media.id).where(Media.show_id.in_(show_ids)))).scalars())
    for model in (WatchEvent,PlaybackProgress,PlaybackSession,Rating,TrackingActivity,SyncReview,TrackedEntry):
        await db.execute(delete(model).where(model.user_id==viewer.id,model.media_id.in_(ids)))
    from models.tracking import StreamAction
    await db.execute(delete(StreamAction).where(StreamAction.user_id==viewer.id,StreamAction.media_id.in_(ids)))
    await db.execute(delete(ListItem).where(ListItem.media_id.in_(ids),ListItem.list_id.in_(select(List.id).where(List.user_id==viewer.id))))
    if show_ids:await db.execute(delete(ShowRewatch).where(ShowRewatch.user_id==viewer.id,ShowRewatch.show_id.in_(show_ids)))
    settings=(await db.execute(select(UserSettings).where(UserSettings.user_id==viewer.id))).scalar_one_or_none()
    if settings:
        settings.dropped_movies=[i for i in (settings.dropped_movies or []) if i not in ids]
        settings.dropped_shows=[i for i in (settings.dropped_shows or []) if i not in show_ids]
        settings.next_up_hidden_shows=[i for i in (settings.next_up_hidden_shows or []) if i not in show_ids]
    baselines=(await db.execute(select(StreamBaseline).where(StreamBaseline.user_id==viewer.id))).scalars()
    for baseline in baselines:
        snapshot=dict(baseline.snapshot)
        mapping=snapshot.get('mappings',{})
        snapshot['progress']={key:row for key,row in snapshot.get('progress',{}).items() if not (mapping.get(key)==media.tmdb_id and row.get('content_type')==media.media_type.value)}
        snapshot['resume']={key:row for key,row in snapshot.get('resume',{}).items() if not (mapping.get(key)==media.tmdb_id and row.get('content_type')==media.media_type.value)}
        records = dict(snapshot.get('records', {}))
        removed_keys = set()
        for category in ('watched', 'progress'):
            rows = records.get(category, [])
            removed_keys.update(str(row.get('content_id')) for row in rows
                if mapping.get(str(row.get('content_id'))) == media.tmdb_id and row.get('content_type') == media.media_type.value)
            records[category] = [row for row in rows if str(row.get('content_id')) not in removed_keys]
        if records:
            snapshot['records'] = records
        snapshot['watched'] = [key for key in snapshot.get('watched', [])
            if not any(key.startswith(content_id + ':') for content_id in removed_keys)]
        baseline.snapshot=snapshot
    connections=(await db.execute(select(MediaServerConnection.id).where(MediaServerConnection.user_id==viewer.id))).scalars().all()
    pending=[f'connection:{i}' for i in connections]
    if settings:
        for provider in ('trakt','simkl','mdblist','bingebase'):
            if any(getattr(settings,f'{provider}_{suffix}',None) for suffix in ('access_token','api_key')):pending.append(provider)
    marker=(await db.execute(select(TrackingDeletion).where(TrackingDeletion.user_id==viewer.id,TrackingDeletion.media_id==media_id))).scalar_one_or_none()
    if not marker:marker=TrackingDeletion(user_id=viewer.id,media_id=media_id);db.add(marker)
    marker.pending_connections=pending
    marker.deleted_at=datetime.utcnow()
    from core.stream_actions import queue_resets
    await queue_resets(db,viewer.id,media,marker.deleted_at)
    if pending:db.add(SyncReview(user_id=viewer.id,media_id=media_id,kind='outbound_pending',message='Local tracking data was deleted. Connected-service resets are pending; streaming-library membership is preserved.'))
    await db.commit()
    return {'deleted':True,'pending_connections':len(pending),'library_preserved':True}


@router.post('/import-history')
async def import_history(db:AsyncSession=Depends(get_db),viewer:User=Depends(get_current_user)):
    from core.tracking_import import import_tracking_history
    return {'added':await import_tracking_history(db,viewer.id)}


class PreferencePatch(BaseModel):
    auto_confirm: bool


@router.get('/preferences')
async def preferences(db: AsyncSession = Depends(get_db), viewer: User = Depends(get_current_user)):
    row=await db.get(TrackingPreferences,viewer.id)
    return {'auto_confirm': bool(row and row.auto_confirm)}


@router.patch('/preferences')
async def set_preferences(body: PreferencePatch, db: AsyncSession = Depends(get_db), viewer: User = Depends(get_current_user)):
    row=await db.get(TrackingPreferences,viewer.id)
    if not row: row=TrackingPreferences(user_id=viewer.id);db.add(row)
    row.auto_confirm=body.auto_confirm
    await db.commit()
    return {'auto_confirm':row.auto_confirm}


@router.get('/recent-events')
async def recent_events(db: AsyncSession = Depends(get_db), viewer: User = Depends(get_current_user)):
    rows=(await db.execute(select(SyncReview,Media).outerjoin(Media,Media.id==SyncReview.media_id).where(SyncReview.user_id==viewer.id).order_by(SyncReview.created_at.desc()).limit(100))).all()
    from models.tracking import StreamAction
    actions=(await db.execute(select(StreamAction,Media,MediaServerConnection).join(Media,Media.id==StreamAction.media_id)
        .join(MediaServerConnection,MediaServerConnection.id==StreamAction.connection_id)
        .where(StreamAction.user_id==viewer.id,StreamAction.state.in_(['pending','conflict']))
        .order_by(StreamAction.id).limit(100))).all()
    pending=await db.scalar(select(func.count()).select_from(SyncReview).where(SyncReview.user_id==viewer.id,SyncReview.state=='pending'))
    return {'pending':pending,'outbound':[{'id':a.id,'title':m.title,'connection':c.name,'state':a.state,'attempts':a.attempts,'error':a.last_error} for a,m,c in actions],
        'results':[{'id':r.id,'kind':r.kind,'state':r.state,'message':r.message,'previous_status':r.previous_status,'proposed_status':r.proposed_status,'media':media_data(m) if m else None,'created_at':r.created_at} for r,m in rows]}


class ReviewResolution(BaseModel):
    action: Literal['confirm','keep','change']
    status: TrackingStatus | None = None


@router.post('/recent-events/{event_id}')
async def resolve_event(event_id:int,body:ReviewResolution,db:AsyncSession=Depends(get_db),viewer:User=Depends(get_current_user)):
    event=(await db.execute(select(SyncReview).where(SyncReview.id==event_id,SyncReview.user_id==viewer.id).with_for_update())).scalar_one_or_none()
    if not event:raise HTTPException(404,'Event not found')
    if event.state!='pending':raise HTTPException(409,'This event has already been resolved')
    if event.kind=='initial_import':
        if body.action!='confirm':raise HTTPException(422,'Confirm this summary before allowing outbound sync')
        baseline=await db.get(StreamBaseline,event.connection_id)
        if not baseline:raise HTTPException(409,'Import this connection again')
        baseline.approved=True
    elif event.kind=='outbound_pending':
        raise HTTPException(409,'This operation requires the connection dispatcher; it cannot be marked successful manually')
    elif event.kind=='deletion_conflict':
        if body.action=='change':raise HTTPException(422,'Retry deletion or keep the remote history')
        from models.tracking import StreamAction
        actions=(await db.execute(select(StreamAction).where(StreamAction.user_id==viewer.id,
            StreamAction.connection_id==event.connection_id,StreamAction.media_id==event.media_id,
            StreamAction.action=='reset',StreamAction.state=='conflict'))).scalars().all()
        for action in actions:
            action.last_error=None
            if body.action=='confirm':
                from datetime import timezone
                action.payload={**action.payload,'deleted_at':datetime.now(timezone.utc).isoformat()}
                action.state='pending'
            else:
                action.state='cancelled';action.payload={}
        if body.action=='keep':
            marker=(await db.execute(select(TrackingDeletion).where(TrackingDeletion.user_id==viewer.id,TrackingDeletion.media_id==event.media_id))).scalar_one_or_none()
            if marker:marker.pending_connections=[p for p in marker.pending_connections if p!=f'connection:{event.connection_id}']
            pending=(await db.execute(select(SyncReview).where(SyncReview.user_id==viewer.id,SyncReview.media_id==event.media_id,SyncReview.kind=='outbound_pending'))).scalars().all()
            if marker and not marker.pending_connections:
                for review in pending:review.state='corrected'
    else:
        entry=(await db.execute(select(TrackedEntry).where(TrackedEntry.user_id==viewer.id,TrackedEntry.media_id==event.media_id))).scalar_one_or_none()
        if not entry:raise HTTPException(409,'The tracked entry no longer exists')
        status=body.status.value if body.action=='change' and body.status else event.previous_status if body.action=='keep' else event.proposed_status
        if not status:raise HTTPException(422,'Choose a status')
        entry.status=status
        if status=='watching':
            from core.stream_actions import queue_restorations
            await queue_restorations(db,viewer.id,await db.get(Media,entry.media_id))
        db.add(TrackingActivity(user_id=viewer.id,media_id=entry.media_id,status=status,score=effective_score(entry.rating_mode,entry.manual_score,entry.season_scores)))
    event.state='confirmed' if body.action=='confirm' else 'corrected'
    await db.commit()
    return {'state':event.state}


@router.get("/people/{username}")
async def person(username: str, db: AsyncSession = Depends(get_db), viewer: User | None = Depends(get_optional_user)):
    user, owner = await profile_access(db, username, viewer)
    entries = (await db.execute(select(TrackedEntry, Media).join(Media, Media.id == TrackedEntry.media_id).where(TrackedEntry.user_id == user.id))).all()
    following_ids = (await db.execute(select(Follow.following_id).where(Follow.follower_id == user.id))).scalars().all()
    followers_ids = (await db.execute(select(Follow.follower_id).where(Follow.following_id == user.id))).scalars().all()
    people = (await db.execute(select(User, UserProfileData).join(UserProfileData, UserProfileData.user_id == User.id).where(User.id.in_(set(following_ids + followers_ids)), UserProfileData.privacy_level == PrivacyLevel.public))).all()
    visible = [{"username":u.username, "display_name":p.display_name or u.username, "following":u.id in following_ids, "follower":u.id in followers_ids} for u,p in people]
    return {"id":user.id,"username":user.username,"display_name":user.display_name,"bio":user.profile.bio if user.profile else None,
        "owner":owner,"following":bool(viewer and viewer.id in followers_ids),"has_avatar":bool(user.profile and user.profile.avatar_path),
        "counts":{"movies":sum(m.media_type==MediaType.movie for e,m in entries),"series":sum(m.media_type==MediaType.series for e,m in entries),
                  "completed":sum(e.status=='completed' for e,m in entries),"following":len(following_ids),"followers":len(followers_ids)},
        "favorites":[media_data(m) for e,m in entries if e.favorite],"people":visible}


@router.get("/library")
async def library(db: AsyncSession = Depends(get_db), viewer: User = Depends(get_current_user)):
    rows = (await db.execute(select(Media).join(Collection, Collection.media_id == Media.id).join(CollectionFile, CollectionFile.collection_id == Collection.id)
        .where(Collection.user_id == viewer.id, CollectionFile.source.in_(['stremio','nuvio']), Media.media_type.in_([MediaType.movie,MediaType.series])).distinct().order_by(Media.title))).scalars().all()
    return {"results":[media_data(m) for m in rows]}


class EntryPatch(BaseModel):
    status: TrackingStatus | None = None
    manual_score: float | None = None
    rating_mode: Literal["manual", "average"] | None = None
    season_scores: dict[int, float | None] | None = None
    favorite: bool | None = None
    notes: str | None = Field(None, max_length=10000)
    start_date: date | None = None
    finish_date: date | None = None
    rewatch_count: int | None = Field(None, ge=0, le=10000)
    progress: int | None = Field(None, ge=0)
    confirm_rollback: bool = False
    mark_released_watched: bool = False

    @field_validator("manual_score")
    @classmethod
    def score(cls, value):
        return normalize_score(value)

    @field_validator("season_scores")
    @classmethod
    def seasons(cls, value):
        if value is None:
            return value
        if any(k < 0 or k > 1000 for k in value):
            raise ValueError("Invalid season")
        return {str(k): normalize_score(v) for k, v in value.items()}


async def catalog_access(db, viewer):
    if viewer:
        return
    settings = await db.get(GlobalSettings, 1)
    if not settings or not settings.enable_logged_out_navigation:
        raise HTTPException(401, "Sign in to browse")


async def profile_access(db, username, viewer):
    user = (await db.execute(select(User).where(User.username == username).options(selectinload(User.profile)))).scalar_one_or_none()
    if not user:
        raise HTTPException(404, "Profile not found")
    owner = bool(viewer and viewer.id == user.id)
    if not owner and (not user.profile or user.profile.privacy_level != PrivacyLevel.public):
        raise HTTPException(403, "This profile is private")
    await catalog_access(db, viewer)
    return user, owner


def media_data(media):
    data = media.tmdb_data or {}
    return {
        "id": media.id, "title": media.title, "type": media.media_type.value,
        "poster": media.poster_path, "backdrop": media.backdrop_path,
        "overview": media.overview, "year": (media.release_date or "")[:4],
        "release_status": media.status or data.get('status'), "genres": [g if isinstance(g, str) else g["name"] for g in data.get("genres", []) if isinstance(g, str) or (isinstance(g, dict) and g.get("name"))],
        "tmdb_id": media.tmdb_id, "tvdb_id": media.tvdb_id, "imdb_id": media.imdb_id,
    }


def entry_data(entry, media, owner=False):
    result = {
        **media_data(media), "status": entry.status, "rating_mode": entry.rating_mode,
        "score": effective_score(entry.rating_mode, entry.manual_score, entry.season_scores),
        "season_scores": entry.season_scores, "progress": entry.progress,
        "favorite": entry.favorite, "start_date": entry.start_date, "finish_date": entry.finish_date,
        "rewatch_count": entry.rewatch_count, "updated_at": entry.updated_at,
    }
    if owner:
        result.update(notes=entry.notes, manual_score=entry.manual_score)
    return result


@router.get("/profile/{username}/{media_type}")
async def profile_list(username: str, media_type: Literal["movie", "series"], db: AsyncSession = Depends(get_db), viewer: User | None = Depends(get_optional_user)):
    user, owner = await profile_access(db, username, viewer)
    rows = (await db.execute(select(TrackedEntry, Media).join(Media, Media.id == TrackedEntry.media_id).where(
        TrackedEntry.user_id == user.id, Media.media_type == MediaType(media_type)
    ).order_by(Media.title))).all()
    following = False
    if viewer and not owner:
        following = (await db.execute(select(Follow.id).where(Follow.follower_id == viewer.id, Follow.following_id == user.id))).scalar_one_or_none() is not None
    entries = [entry_data(e, m, owner) for e, m in rows]
    if media_type == 'series':
        # One batched query for all catalogue episode IDs; don't confuse status
        # Completed with history covering newly released episodes.
        catalogue_ids = {episode_id for _, m in rows for episode_id in (m.tmdb_data or {}).get('tracking_episode_ids', [])}
        released = (await db.execute(select(Media.id, Media.tmdb_id).where(Media.media_type == MediaType.episode,
            Media.tmdb_id.in_(catalogue_ids), Media.season_number > 0, Media.release_date.is_not(None),
            Media.release_date <= date.today().isoformat()))).all() if catalogue_ids else []
        watched = set((await db.execute(select(WatchEvent.media_id).where(WatchEvent.user_id == user.id,
            WatchEvent.media_id.in_([r.id for r in released]), WatchEvent.completed.is_(True)))).scalars()) if released else set()
        for result, (_, media) in zip(entries, rows):
            ids = set((media.tmdb_data or {}).get('tracking_episode_ids', []))
            if (media.tmdb_data or {}).get('tracking_catalogue_refreshed_at'):
                title_episodes = [r.id for r in released if r.tmdb_id in ids]
                result['released_episodes'] = len(title_episodes)
                result['unwatched_episodes'] = sum(episode_id not in watched for episode_id in title_episodes)
                result['progress'] = len(title_episodes) - result['unwatched_episodes']
    return {
        "profile": {"id": user.id, "username": user.username, "display_name": user.display_name,
                    "bio": user.profile.bio if user.profile else None, "has_avatar": bool(user.profile and user.profile.avatar_path)},
        "owner": owner, "following": following,
        "entries": entries,
    }


@router.get("/catalog")
async def catalog(q: str = "", media_type: Literal["movie", "series"] = "movie", db: AsyncSession = Depends(get_db), viewer: User | None = Depends(get_optional_user)):
    await catalog_access(db, viewer)
    rows = (await db.execute(select(Media).where(Media.media_type == MediaType(media_type), Media.title.ilike(f"%{q[:200]}%")).order_by(Media.title).limit(80))).scalars().all()
    results = [media_data(m) for m in rows]
    notice = None
    if q.strip():
        from routers.media import get_user_tmdb_key
        from core import tmdb
        key = await get_user_tmdb_key(db, viewer.id if viewer else -1)
        if key:
            try:
                search = tmdb.search_movies if media_type == 'movie' else tmdb.search_shows
                remote = await search(q.strip()[:200], api_key=key)
                known = {m.tmdb_id for m in rows if m.tmdb_id}
                for item in remote.get('results', []):
                    if item['id'] in known or item.get('adult'):
                        continue
                    results.append({'id':None,'tmdb_id':item['id'],'type':media_type,'title':item.get('title') or item.get('name'),
                        'poster':item.get('poster_path'),'year':(item.get('release_date') or item.get('first_air_date') or '')[:4]})
            except Exception:
                notice = 'Metadata search is temporarily unavailable. Showing local matches.'
        else:
            notice = 'Add a TMDB key in Settings, or ask your administrator, to search beyond the local catalogue.'
    return {"results": results, "notice": notice}


@router.post("/catalog/{media_type}/{tmdb_id}")
async def import_catalog_title(media_type: Literal['movie','series'], tmdb_id: int, db: AsyncSession = Depends(get_db), viewer: User = Depends(get_current_user)):
    from routers.media import get_user_tmdb_key
    from core.enrichment import create_media_safely, enrich_media
    key = await get_user_tmdb_key(db, viewer.id)
    if not key:
        raise HTTPException(409, 'A TMDB key is required to load this title')
    media = (await db.execute(select(Media).where(Media.tmdb_id == tmdb_id, Media.media_type == MediaType(media_type)))).scalar_one_or_none()
    if media is None:
        try:
            media, _ = await create_media_safely(db, tmdb_id, MediaType(media_type), title='')
            await enrich_media(media, api_key=key)
            if not media.title:
                raise ValueError('No title returned')
            if media.media_type == MediaType.series:
                from core.tracking_metadata import hydrate_tracking_episodes
                await hydrate_tracking_episodes(db, media, key)
            await db.commit()
        except Exception:
            await db.rollback()
            raise HTTPException(502, 'Unable to load title metadata; try again later')
    return {'id':media.id}


@router.get("/title/{media_id}")
async def title(media_id: int, db: AsyncSession = Depends(get_db), viewer: User | None = Depends(get_optional_user)):
    await catalog_access(db, viewer)
    media = await db.get(Media, media_id)
    if not media or media.media_type not in (MediaType.movie, MediaType.series):
        raise HTTPException(404, "Title not found")
    entry = None
    friends = []
    if viewer:
        entry = (await db.execute(select(TrackedEntry).where(TrackedEntry.user_id == viewer.id, TrackedEntry.media_id == media.id))).scalar_one_or_none()
        rows = (await db.execute(select(TrackedEntry, User).join(User, User.id == TrackedEntry.user_id)
            .join(UserProfileData, UserProfileData.user_id == User.id)
            .join(Follow, Follow.following_id == User.id)
            .where(Follow.follower_id == viewer.id, UserProfileData.privacy_level == PrivacyLevel.public, TrackedEntry.media_id == media.id))).all()
        for e, user in rows:
            score = effective_score(e.rating_mode, e.manual_score, e.season_scores)
            if score is not None:
                friends.append({"username": user.username, "score": score, "status": e.status})
    seasons = [dict(s) for s in (media.tmdb_data or {}).get("seasons", [])]
    episodes = await released_episodes(db, media)
    watched = set()
    if viewer and episodes:
        watched = set((await db.execute(select(WatchEvent.media_id).where(
            WatchEvent.user_id == viewer.id, WatchEvent.media_id.in_([e.id for e in episodes]),
            WatchEvent.completed.is_(True)))).scalars())
    for season in seasons:
        released = [e for e in episodes if e.season_number == season.get('season_number')]
        season['released_count'] = len(released) if (media.tmdb_data or {}).get('tracking_catalogue_refreshed_at') else None
        season['watched_count'] = sum(e.id in watched for e in released)
    return {**media_data(media), "entry": entry_data(entry, media, True) if entry else None,
            "seasons": seasons, "friends": friends,
            "friends_average": sum(f["score"] for f in friends) / len(friends) if friends else None}


@router.post('/title/{media_id}/refresh-episodes')
async def refresh_episodes(media_id: int, db: AsyncSession = Depends(get_db), viewer: User = Depends(get_current_user)):
    from routers.media import get_user_tmdb_key
    from core.tracking_metadata import hydrate_tracking_episodes
    media = await db.get(Media, media_id)
    if not media or media.media_type != MediaType.series:
        raise HTTPException(404, 'Series not found')
    key = await get_user_tmdb_key(db, viewer.id)
    if not key:
        raise HTTPException(409, 'Add a TMDB key in Settings, or ask your administrator, to load episodes')
    try:
        count = await hydrate_tracking_episodes(db, media, key)
        await db.commit()
    except ValueError as exc:
        await db.rollback()
        raise HTTPException(409, str(exc))
    except Exception:
        await db.rollback()
        raise HTTPException(502, 'Unable to refresh episode metadata; existing history was preserved')
    return {'episodes': count}


async def released_episodes(db, media):
    if media.media_type == MediaType.movie:
        return [media] if media.release_date and media.release_date[:10] <= date.today().isoformat() else []
    terms = []
    if media.tmdb_id:
        terms.append(Show.tmdb_id == media.tmdb_id)
    if media.tvdb_id:
        terms.append(Show.tvdb_id == media.tvdb_id)
    if not terms:
        return []
    show = (await db.execute(select(Show).where(or_(*terms)))).scalars().first()
    if not show:
        return []
    query = select(Media).where(Media.show_id == show.id, Media.media_type == MediaType.episode,
        Media.season_number > 0, Media.release_date.is_not(None), Media.release_date <= date.today().isoformat()
    )
    catalogue_ids = (media.tmdb_data or {}).get('tracking_episode_ids')
    if catalogue_ids is not None:
        query = query.where(Media.tmdb_id.in_(catalogue_ids))
    return (await db.execute(query.order_by(Media.season_number, Media.episode_number))).scalars().all()


class SeasonProgressPatch(BaseModel):
    watched: bool
    confirm_rollback: bool = False


@router.patch('/entry/{media_id}/season/{season_number}')
async def save_season_progress(media_id: int, season_number: int, body: SeasonProgressPatch,
                               db: AsyncSession = Depends(get_db), viewer: User = Depends(get_current_user)):
    media = await db.get(Media, media_id)
    if not media or media.media_type != MediaType.series or season_number <= 0:
        raise HTTPException(404, 'Regular season not found')
    await db.execute(select(User.id).where(User.id == viewer.id).with_for_update())
    episodes = await released_episodes(db, media)
    positions = [i for i, e in enumerate(episodes) if e.season_number == season_number]
    if not positions:
        raise HTTPException(409, 'Released episode metadata is needed before changing this season')
    target = positions[-1] + 1 if body.watched else positions[0]
    later_ids = [e.id for e in episodes if e.season_number > season_number]
    later_watched = bool(later_ids and (await db.execute(select(WatchEvent.id).where(
        WatchEvent.user_id == viewer.id, WatchEvent.media_id.in_(later_ids),
        WatchEvent.completed.is_(True)).limit(1))).first())
    if not body.watched and later_watched and not body.confirm_rollback:
        raise HTTPException(409, 'Marking this season unwatched also clears later watched seasons. Confirm to continue.')
    return await save_entry(media_id, EntryPatch(progress=target, confirm_rollback=True), db, viewer)


@router.patch("/entry/{media_id}")
async def save_entry(media_id: int, body: EntryPatch, db: AsyncSession = Depends(get_db), viewer: User = Depends(get_current_user)):
    media = await db.get(Media, media_id)
    if not media or media.media_type not in (MediaType.movie, MediaType.series):
        raise HTTPException(404, "Title not found")
    # Serialize edits for this user, including first insertion of a title.
    await db.execute(select(User.id).where(User.id == viewer.id).with_for_update())
    entry = (await db.execute(select(TrackedEntry).where(TrackedEntry.user_id == viewer.id, TrackedEntry.media_id == media_id))).scalar_one_or_none()
    previous = entry.status if entry else None
    old_score = effective_score(entry.rating_mode, entry.manual_score, entry.season_scores) if entry else None
    if entry is None:
        # An explicit user edit re-adds a previously deleted entry. Imports never
        # do this; they honor the marker until the user makes this choice.
        await db.execute(delete(TrackingDeletion).where(TrackingDeletion.user_id==viewer.id,TrackingDeletion.media_id==media_id))
        await db.execute(delete(SyncReview).where(SyncReview.user_id==viewer.id,SyncReview.media_id==media_id,SyncReview.kind=='outbound_pending'))
        entry = TrackedEntry(user_id=viewer.id, media_id=media_id, status="planning", rating_mode="manual", season_scores={}, progress=0, favorite=False, rewatch_count=0)
        db.add(entry)
    fields = body.model_fields_set
    if body.season_scores is not None and media.media_type != MediaType.series:
        raise HTTPException(422, "Movies do not have season ratings")
    if body.rating_mode == 'average' and media.media_type != MediaType.series:
        raise HTTPException(422, "Only shows can average season ratings")
    if body.season_scores and any(body.season_scores.values()) and not any(entry.season_scores.values()) and body.rating_mode is None:
        raise HTTPException(409, "Choose average seasons or a separate show score")
    status = body.status.value if body.status else entry.status
    entry.start_date, entry.finish_date = default_dates(previous, status, entry.start_date, entry.finish_date, date.today())
    entry.status = status
    for name in ("manual_score", "rating_mode", "favorite", "notes", "start_date", "finish_date", "rewatch_count"):
        if name in fields:
            value = getattr(body, name)
            if value is None and name in ("rating_mode", "favorite", "rewatch_count"):
                raise HTTPException(422, f"{name} cannot be null")
            setattr(entry, name, value)
    if body.season_scores is not None:
        entry.season_scores = {**entry.season_scores, **body.season_scores}
    if body.progress is not None or body.mark_released_watched:
        if media.media_type == MediaType.series and not (media.tmdb_data or {}).get('tracking_catalogue_refreshed_at'):
            raise HTTPException(409, 'Refresh episode metadata on the title page before changing progress')
        episodes = await released_episodes(db, media)
        target = len(episodes) if body.mark_released_watched else body.progress
        if not episodes or target > len(episodes):
            raise HTTPException(409, "Released episode metadata is needed before changing progress")
        ids = [m.id for m in episodes]
        watched = set((await db.execute(select(WatchEvent.media_id).where(WatchEvent.user_id == viewer.id, WatchEvent.media_id.in_(ids), WatchEvent.completed.is_(True)))).scalars())
        rollback = target < entry.progress or bool(watched.intersection(ids[target:]))
        if rollback and not body.confirm_rollback:
            raise HTTPException(409, "Confirm marking later episodes unwatched")
        for episode in episodes[:target]:
            if episode.id not in watched:
                db.add(WatchEvent(user_id=viewer.id, media_id=episode.id, completed=True, watched_at=None, provisional=True))
        if rollback:
            await db.execute(delete(WatchEvent).where(WatchEvent.user_id == viewer.id, WatchEvent.media_id.in_(ids[target:])))
        entry.progress = target
        if 'status' not in fields and target > 0:
            # Watching later episodes resumes paused/dropped titles, while a
            # completed title remains completed even when correcting history.
            from core.tracking_rules import observed_status
            entry.status = observed_status(previous, target == len(episodes), True)
            entry.start_date, entry.finish_date = default_dates(previous, entry.status, entry.start_date, entry.finish_date, date.today())
    # Mirror the effective value into the existing provider-facing rating rows.
    if entry.status=='watching' and previous!='watching':
        from core.stream_actions import queue_restorations
        await queue_restorations(db,viewer.id,media)
    score = effective_score(entry.rating_mode, entry.manual_score, entry.season_scores)
    scores = {None: score, **{int(k): v for k, v in entry.season_scores.items()}}
    for season, value in scores.items():
        row = (await db.execute(select(Rating).where(Rating.user_id == viewer.id, Rating.media_id == media_id, Rating.season_number == season, Rating.episode_order.is_(None)))).scalar_one_or_none()
        if value is None:
            if row:
                await db.delete(row)
        elif row:
            row.rating, row.rated_at = value, datetime.utcnow()
        else:
            db.add(Rating(user_id=viewer.id, media_id=media_id, season_number=season, rating=value))
    if previous != entry.status or old_score != score:
        recent = (await db.execute(select(TrackingActivity).where(TrackingActivity.user_id == viewer.id, TrackingActivity.media_id == media_id,
            TrackingActivity.created_at >= datetime.utcnow() - timedelta(minutes=5)).order_by(TrackingActivity.created_at.desc()).limit(1))).scalar_one_or_none()
        if recent:
            recent.status, recent.score, recent.created_at = entry.status, score, datetime.utcnow()
        else:
            db.add(TrackingActivity(user_id=viewer.id, media_id=media_id, status=entry.status, score=score))
    await db.commit()
    await db.refresh(entry)
    return entry_data(entry, media, True)


@router.get("/activity")
async def activity(db: AsyncSession = Depends(get_db), viewer: User = Depends(get_current_user)):
    followed = select(Follow.following_id).where(Follow.follower_id == viewer.id)
    rows = (await db.execute(select(TrackingActivity, Media, User).join(Media, Media.id == TrackingActivity.media_id)
        .join(User, User.id == TrackingActivity.user_id).outerjoin(UserProfileData, UserProfileData.user_id == User.id)
        .where(or_(User.id == viewer.id, (User.id.in_(followed)) & (UserProfileData.privacy_level == PrivacyLevel.public)))
        .order_by(TrackingActivity.created_at.desc()).limit(60))).all()
    return {"results": [{"username": u.username, "status": a.status, "score": a.score, "created_at": a.created_at, "media": media_data(m)} for a, m, u in rows]}
