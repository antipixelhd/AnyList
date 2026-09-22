"""Tracked lists are independent of connected streaming-library membership."""
import asyncio
import re
from datetime import date, datetime, timedelta, timezone
from difflib import SequenceMatcher
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, delete, or_, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db import get_db
from dependencies import get_current_user, get_optional_user
from core.tracking_rules import TrackingStatus, normalize_score, effective_score, default_dates
from core.status_provenance import mark_status_change
from models import Media, User, UserSettings, UserProfileData, GlobalSettings, Follow, Rating, Show, WatchEvent, Collection, CollectionFile, PlaybackProgress, PlaybackSession, MediaServerConnection, List, ListItem, ShowRewatch
from models.base import MediaType, PrivacyLevel
from models.tracking import TrackedEntry, TrackingActivity, TrackingDeletion, TrackingPreferences, SyncReview, StreamBaseline, ProviderIgnore, ProviderMatch, CloudAction
from models.sync import SyncJob, SyncStatus

router = APIRouter()


def fuzzy_remote_terms(term: str) -> list[str]:
    """Return a small, conservative set of useful typo corrections for TMDB.

    PostgreSQL's trigram search handles titles already in AnyList, but a title
    not imported yet has to be found by TMDB. TMDB treats a misspelling as a
    literal query, so recover the common cases without a wide edit-distance
    search against the remote API.
    """
    candidates: list[str] = []

    def add(value: str) -> None:
        value = value.strip()
        if value and value.casefold() != term.casefold() and value not in candidates:
            candidates.append(value)

    # Correct one repeated run at a time: “Thee Odyssey” should produce
    # “The Odyssey”, not also strip the legitimate double-s in “Odyssey”.
    for match in re.finditer(r"(.)\1+", term, flags=re.IGNORECASE):
        add(f"{term[:match.start()]}{match.group(1)}{term[match.end():]}")
    # These reciprocal substitutions cover ordinary keyboard/vowel slips such
    # as Mutany -> Mutiny. Generated results are checked against the original
    # spelling before they reach the user.
    substitutions = (("a", "i"), ("i", "a"), ("e", "a"), ("a", "e"),
                     ("o", "u"), ("u", "o"), ("e", "i"), ("i", "e"))
    for wrong, right in substitutions:
        for match in re.finditer(wrong, term, flags=re.IGNORECASE):
            add(f"{term[:match.start()]}{right}{term[match.end():]}")
    return candidates[:8]


def is_close_title_match(query: str, title: str) -> bool:
    """Avoid showing unrelated results from a generated fallback query."""
    compact = lambda value: re.sub(r"[^\w]", "", value.casefold())
    return SequenceMatcher(None, compact(query), compact(title)).ratio() >= 0.6


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
    episodes=(await db.execute(select(Media).where(Media.id.in_(ids),Media.media_type==MediaType.episode))).scalars().all()
    for model in (WatchEvent,PlaybackProgress,PlaybackSession,Rating,TrackingActivity,SyncReview,TrackedEntry):
        await db.execute(delete(model).where(model.user_id==viewer.id,model.media_id.in_(ids)))
    from models.tracking import StreamAction
    await db.execute(delete(StreamAction).where(StreamAction.user_id==viewer.id,StreamAction.media_id.in_(ids)))
    await db.execute(delete(CloudAction).where(CloudAction.user_id==viewer.id,CloudAction.media_id.in_(ids)))
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
    connections=(await db.execute(select(MediaServerConnection.id).where(
        MediaServerConnection.user_id==viewer.id,
        MediaServerConnection.type.in_(['stremio','nuvio'])))).scalars().all()
    pending=[f'connection:{i}' for i in connections]
    marker=(await db.execute(select(TrackingDeletion).where(TrackingDeletion.user_id==viewer.id,TrackingDeletion.media_id==media_id))).scalar_one_or_none()
    if not marker:marker=TrackingDeletion(user_id=viewer.id,media_id=media_id);db.add(marker)
    marker.deleted_at=datetime.utcnow()
    from core.stream_actions import queue_resets
    from core.cloud_actions import queue_cloud_resets
    await queue_resets(db,viewer.id,media,marker.deleted_at)
    pending.extend(await queue_cloud_resets(db,viewer.id,media,episodes))
    marker.pending_connections=list(pending)
    if pending:db.add(SyncReview(user_id=viewer.id,media_id=media_id,kind='outbound_pending',message='Local tracking data was deleted. Connected-service resets are pending; streaming-library membership is preserved.'))
    await db.commit()
    return {'deleted':True,'pending_connections':len(pending),'library_preserved':True}


@router.post('/import-history')
async def import_history(db:AsyncSession=Depends(get_db),viewer:User=Depends(get_current_user)):
    from core.tracking_import import import_tracking_history
    return {'added':await import_tracking_history(db,viewer.id)}


class PreferencePatch(BaseModel):
    auto_confirm: bool | None = None
    combine_lists: bool | None = None
    default_sort: Literal['title', 'score', 'progress', 'updated'] | None = None
    low_priority_notifications: bool | None = None
    low_priority_retention_days: int | None = Field(None, ge=1, le=90)


@router.get('/preferences')
async def preferences(db: AsyncSession = Depends(get_db), viewer: User = Depends(get_current_user)):
    row=await db.get(TrackingPreferences,viewer.id)
    return {
        'auto_confirm': bool(row and row.auto_confirm),
        'combine_lists': True if row is None else row.combine_lists,
        'default_sort': 'title' if row is None else row.default_sort,
        'low_priority_notifications': True if row is None else row.low_priority_notifications,
        'low_priority_retention_days': 7 if row is None else row.low_priority_retention_days,
    }


@router.patch('/preferences')
async def set_preferences(body: PreferencePatch, db: AsyncSession = Depends(get_db), viewer: User = Depends(get_current_user)):
    row=await db.get(TrackingPreferences,viewer.id)
    if not row: row=TrackingPreferences(user_id=viewer.id);db.add(row)
    for name in body.model_fields_set:
        value=getattr(body,name)
        if value is None:raise HTTPException(422,f'{name} cannot be null')
        setattr(row,name,value)
    await db.commit()
    return await preferences(db,viewer)


def review_priority(review: SyncReview) -> str:
    if review.kind in {'initial_import','initial_cloud_import','conflict','cloud_conflict','rating_conflict','deletion_conflict','unmatched_import'}:
        return 'high'
    if review.kind == 'outbound_pending':
        return 'medium'
    return review.priority or 'low'


_AUTH_FAILURE_MARKERS = (
    '401', '403', 'auth', 'forbidden', 'invalid token', 'permission',
    'refresh token', 'token expired', 'unauthorized',
)


def connection_failure_events(jobs, connection_names):
    """Project durable job history into one current alert per provider/connection."""
    groups = {}
    for job in jobs:
        source = job.source.value if hasattr(job.source, 'value') else str(job.source)
        if job.connection_id is not None:
            key = f'connection:{job.connection_id}'
            title = connection_names.get(job.connection_id, source.title())
            resolve_url = f'/connections#conn-body-{job.connection_id}'
        elif source in {'trakt', 'simkl', 'mdblist'}:
            key = f'provider:{source}'
            title = source.title() if source != 'mdblist' else 'MDBList'
            resolve_url = f'/connections#{source}-body'
        else:
            continue
        group = groups.setdefault(key, {
            'title': title, 'provider': source, 'resolve_url': resolve_url,
            'failures': [], 'closed': False,
        })
        if group['closed']:
            continue
        if job.status == SyncStatus.completed:
            group['closed'] = True
        elif job.status == SyncStatus.failed:
            group['failures'].append(job)

    results = []
    for key, group in groups.items():
        failures = group['failures']
        if not failures:
            continue
        error = (failures[0].error_message or '').lower()
        auth_failure = any(marker in error for marker in _AUTH_FAILURE_MARKERS)
        if not auth_failure and len(failures) < 2:
            continue
        title = group['title']
        message = (f'{title} needs authorization before AnyList can sync again.' if auth_failure else
            f'{title} has failed to sync repeatedly. Check the connection and retry it.')
        results.append({
            'id': f'connection-failure:{key}', 'kind': 'connection_failure',
            'state': 'pending', 'provider': group['provider'], 'message': message,
            'previous_status': None, 'proposed_status': None, 'previous_score': None,
            'proposed_score': None, 'season_number': None, 'priority': 'high',
            'dismissible': False, 'payload': {
                'title': title, 'resolve_url': group['resolve_url'],
                'reason': 'authorization' if auth_failure else 'repeated_failure',
            }, 'media': None, 'created_at': failures[0].updated_at,
        })
    return results


def group_outbound_delivery(stream_actions, cloud_actions, review_rows, markers, connection_names, library_actions=()):
    """Project unresolved per-service actions as one card per title."""
    by_media={}
    def add(media_id:int,title:str,poster:str|None,key:str,connection:str,state:str,attempts:int,error:str|None):
        group=by_media.setdefault(media_id,{'media_id':media_id,'title':title,'poster':poster,'_deliveries':{}})
        delivery=group['_deliveries'].get(key)
        if delivery:
            delivery['state']='conflict' if 'conflict' in (delivery['state'],state) else 'pending'
            delivery['attempts']=max(delivery['attempts'],attempts)
            if error:delivery['error']=error
        else:
            group['_deliveries'][key]={'connection':connection,'state':state,'attempts':attempts,'error':error}
    for action,media,connection in stream_actions:
        add(media.id,media.title,media.poster_path,f'connection:{connection.id}',connection.name,
            action.state,action.attempts,action.last_error)
    for action,media in cloud_actions:
        add(media.id,media.title,media.poster_path,action.provider,action.provider.title(),
            action.state,action.attempts,action.last_error)
    for delivery,media,connection in library_actions:
        add(media.id,media.title,media.poster_path,f'library:{connection.id}',
            f'{connection.name} · Library',delivery.state,delivery.attempts,delivery.last_error)
    for review,media in review_rows:
        marker=markers.get(review.media_id)
        if review.state!='pending' or not marker or not marker.pending_connections:continue
        by_media.setdefault(review.media_id,{'media_id':review.media_id,
            'title':media.title if media else 'Deleted entry','poster':media.poster_path if media else None,'_deliveries':{}})
    for media_id,marker in markers.items():
        group=by_media.get(media_id)
        if not group:continue
        for key in marker.pending_connections:
            if key in group['_deliveries']:continue
            connection=connection_names.get(int(key.split(':',1)[1]),'Connected service') if key.startswith('connection:') else key.title()
            add(media_id,group['title'],group['poster'],key,connection,'pending',0,None)
    outbound=[]
    for group in by_media.values():
        deliveries=list(group.pop('_deliveries').values())
        if not deliveries:continue
        group['deliveries']=deliveries
        group['state']='conflict' if any(item['state']=='conflict' for item in deliveries) else 'pending'
        outbound.append(group)
    return outbound


@router.get('/recent-events')
async def recent_events(db: AsyncSession = Depends(get_db), viewer: User = Depends(get_current_user)):
    prefs=await db.get(TrackingPreferences,viewer.id)
    retention=prefs.low_priority_retention_days if prefs else 7
    cutoff=datetime.utcnow()-timedelta(days=retention)
    expiring=(await db.execute(select(SyncReview).where(SyncReview.user_id==viewer.id,SyncReview.dismissed_at.is_(None),
        SyncReview.state!='pending',SyncReview.created_at<cutoff))).scalars().all()
    for review in expiring:
        if review_priority(review)=='low':review.dismissed_at=datetime.utcnow()
    query=select(SyncReview,Media).outerjoin(Media,Media.id==SyncReview.media_id).where(
        SyncReview.user_id==viewer.id,SyncReview.dismissed_at.is_(None)).order_by(SyncReview.created_at.desc()).limit(100)
    rows=(await db.execute(query)).all()
    if prefs and not prefs.low_priority_notifications:
        rows=[row for row in rows if review_priority(row[0])!='low' or row[0].state=='pending']
    # A direct local edit is already understood by the user. Its delivery can
    # still need attention, but that belongs to the operational connection
    # queue rather than the provider-change review inbox.
    outbound_review_rows=[row for row in rows if row[0].kind=='outbound_pending' and row[0].state=='pending']
    rows=[row for row in rows if row[0].kind!='outbound_pending']
    from models.tracking import StreamAction
    actions=(await db.execute(select(StreamAction,Media,MediaServerConnection).join(Media,Media.id==StreamAction.media_id)
        .join(MediaServerConnection,MediaServerConnection.id==StreamAction.connection_id)
        .where(StreamAction.user_id==viewer.id,StreamAction.state.in_(['pending','conflict']))
        .order_by(StreamAction.id).limit(100))).all()
    await db.commit()
    cloud_actions=(await db.execute(select(CloudAction,Media).join(Media,Media.id==CloudAction.media_id)
        .where(CloudAction.user_id==viewer.id,CloudAction.state.in_(['pending','conflict']))
        .order_by(CloudAction.id).limit(100))).all()
    from models.streaming_library import StreamingLibraryIntent, StreamingLibraryDelivery
    library_actions=(await db.execute(select(StreamingLibraryDelivery,Media,MediaServerConnection)
        .join(StreamingLibraryIntent,StreamingLibraryIntent.id==StreamingLibraryDelivery.intent_id)
        .join(Media,Media.id==StreamingLibraryIntent.media_id)
        .join(MediaServerConnection,MediaServerConnection.id==StreamingLibraryDelivery.connection_id)
        # A disabled collection push is not an outstanding delivery.  Keep the
        # persisted row so that enabling the connection can resume it, but do
        # not surface a retry/error for a destination the owner has disabled.
        .where(StreamingLibraryIntent.user_id==viewer.id,StreamingLibraryDelivery.state=='pending',
            MediaServerConnection.push_collection.is_(True))
        .order_by(StreamingLibraryDelivery.id).limit(100))).all()
    review_media_ids={review.media_id for review,_ in outbound_review_rows if review.media_id is not None}
    marker_media_ids=review_media_ids|{media.id for _,media,_ in actions}|{media.id for _,media in cloud_actions}
    markers={row.media_id:row for row in (await db.execute(select(TrackingDeletion).where(
        TrackingDeletion.user_id==viewer.id,TrackingDeletion.media_id.in_(marker_media_ids)))).scalars()} if marker_media_ids else {}
    connection_names={row.id:row.name for row in (await db.execute(select(MediaServerConnection.id,MediaServerConnection.name).where(
        MediaServerConnection.user_id==viewer.id))).all()}
    recent_jobs=(await db.execute(select(SyncJob).where(SyncJob.user_id==viewer.id)
        .order_by(SyncJob.updated_at.desc(),SyncJob.id.desc()).limit(200))).scalars().all()
    failure_events=connection_failure_events(recent_jobs,connection_names)
    pending=sum(1 for r,_ in rows if r.state=='pending' and r.kind!='outbound_pending')+len(failure_events)
    outbound=group_outbound_delivery(actions,cloud_actions,outbound_review_rows,markers,connection_names,library_actions)
    return {'pending':pending,'outbound':outbound,
        'results':failure_events+[{'id':r.id,'kind':r.kind,'state':r.state,'provider':r.provider,'message':r.message,'previous_status':r.previous_status,'proposed_status':r.proposed_status,
                    'previous_score':r.previous_score,'proposed_score':r.proposed_score,'season_number':r.season_number,
                    'priority':review_priority(r),'dismissible':r.state!='pending','payload':r.payload or {},
                    'media':media_data(m) if m else None,'created_at':r.created_at} for r,m in rows]}


class SeenReviews(BaseModel):
    ids: list[int] = Field(default_factory=list, max_length=100)


@router.post('/recent-events/seen')
async def mark_events_seen(body: SeenReviews,db:AsyncSession=Depends(get_db),viewer:User=Depends(get_current_user)):
    rows=(await db.execute(select(SyncReview).where(SyncReview.user_id==viewer.id,SyncReview.id.in_(body.ids)))).scalars().all()
    now=datetime.utcnow()
    for row in rows:
        row.seen_at=now
        if row.state!='pending' and review_priority(row)=='low':row.dismissed_at=now
    await db.commit()
    return {'seen':len(rows)}


@router.delete('/recent-events/{event_id}')
async def dismiss_event(event_id:int,db:AsyncSession=Depends(get_db),viewer:User=Depends(get_current_user)):
    row=(await db.execute(select(SyncReview).where(SyncReview.id==event_id,SyncReview.user_id==viewer.id))).scalar_one_or_none()
    if not row:raise HTTPException(404,'Event not found')
    if row.state=='pending':raise HTTPException(409,'Resolve this event before dismissing it')
    row.dismissed_at=datetime.utcnow();await db.commit()
    return {'dismissed':True}


@router.get('/provider-ignores')
async def provider_ignores(db:AsyncSession=Depends(get_db),viewer:User=Depends(get_current_user)):
    rows=(await db.execute(select(ProviderIgnore).where(ProviderIgnore.user_id==viewer.id).order_by(ProviderIgnore.provider,ProviderIgnore.title))).scalars().all()
    return {'results':[{'id':r.id,'provider':r.provider,'external_key':r.external_key,'title':r.title,'created_at':r.created_at} for r in rows]}


@router.delete('/provider-ignores/{ignore_id}')
async def remove_provider_ignore(ignore_id:int,db:AsyncSession=Depends(get_db),viewer:User=Depends(get_current_user)):
    result=await db.execute(delete(ProviderIgnore).where(ProviderIgnore.id==ignore_id,ProviderIgnore.user_id==viewer.id))
    if not result.rowcount:raise HTTPException(404,'Ignored item not found')
    await db.commit();return {'removed':True}


class ReviewResolution(BaseModel):
    action: Literal['confirm','keep','change','match','ignore']
    status: TrackingStatus | None = None
    media_id: int | None = None


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
    elif event.kind=='initial_cloud_import':
        if body.action!='confirm':raise HTTPException(422,'Confirm this summary before allowing outbound sync')
        from models.tracking import CloudBaseline
        baseline=(await db.execute(select(CloudBaseline).where(
            CloudBaseline.user_id==viewer.id,CloudBaseline.provider==event.provider))).scalar_one_or_none()
        if not baseline:raise HTTPException(409,'Import this provider again')
        baseline.approved=True
    elif event.kind=='unmatched_import':
        external_key=(event.payload or {}).get('external_key')
        title=(event.payload or {}).get('title')
        if not event.provider or not external_key:raise HTTPException(409,'This unmatched import is incomplete; import the provider again')
        if body.action=='ignore':
            existing=(await db.execute(select(ProviderIgnore).where(
                ProviderIgnore.user_id==viewer.id,ProviderIgnore.provider==event.provider,
                ProviderIgnore.external_key==external_key))).scalar_one_or_none()
            if not existing:db.add(ProviderIgnore(user_id=viewer.id,provider=event.provider,external_key=external_key,title=title))
            event.state='corrected'
        elif body.action=='match':
            if body.media_id is None:raise HTTPException(422,'Choose a catalogue title')
            media=await db.get(Media,body.media_id)
            if not media or media.media_type not in (MediaType.movie,MediaType.series):raise HTTPException(404,'Catalogue title not found')
            existing=(await db.execute(select(ProviderMatch).where(
                ProviderMatch.user_id==viewer.id,ProviderMatch.provider==event.provider,
                ProviderMatch.external_key==external_key))).scalar_one_or_none()
            if existing:
                existing.media_id=media.id;existing.title=title
            else:
                db.add(ProviderMatch(user_id=viewer.id,provider=event.provider,external_key=external_key,media_id=media.id,title=title))
            event.media_id=media.id
            event.state='corrected'
        else:raise HTTPException(422,'Match this import or ignore it for this provider')
        await db.commit()
        return {'state':event.state}
    elif event.kind=='outbound_pending':
        raise HTTPException(409,'This operation requires the connection dispatcher; it cannot be marked successful manually')
    elif event.kind=='rating_conflict':
        if body.action=='change':raise HTTPException(422,'Use the title editor to choose a different rating')
        if event.proposed_score is None or event.previous_score is None:raise HTTPException(409,'This rating conflict is incomplete; import the provider again')
        if body.action=='confirm':
            entry=(await db.execute(select(TrackedEntry).where(
                TrackedEntry.user_id==viewer.id,TrackedEntry.media_id==event.media_id))).scalar_one_or_none()
            if entry:
                if event.season_number is None:
                    entry.manual_score=event.proposed_score
                    entry.rating_mode='manual'
                else:
                    entry.season_scores={**(entry.season_scores or {}),str(event.season_number):event.proposed_score}
                from core.activity import record_daily_activity
                await record_daily_activity(db,user_id=viewer.id,media_id=entry.media_id,status=entry.status,
                    score=effective_score(entry.rating_mode,entry.manual_score,entry.season_scores),rating_changed=True)
            rating_query=select(Rating).where(Rating.user_id==viewer.id,Rating.media_id==event.media_id,
                Rating.episode_order.is_(None))
            rating_query=rating_query.where(Rating.season_number.is_(None)) if event.season_number is None else rating_query.where(Rating.season_number==event.season_number)
            rating=(await db.execute(rating_query)).scalar_one_or_none()
            if rating:
                rating.rating=event.proposed_score
                rating.rated_at=datetime.now(timezone.utc).replace(tzinfo=None)
            else:
                db.add(Rating(user_id=viewer.id,media_id=event.media_id,season_number=event.season_number,
                    rating=event.proposed_score,rated_at=datetime.now(timezone.utc).replace(tzinfo=None)))
    elif event.kind=='deletion_conflict':
        if body.action=='change':raise HTTPException(422,'Retry deletion or keep the remote history')
        from models.tracking import StreamAction
        actions=(await db.execute(select(StreamAction).where(StreamAction.user_id==viewer.id,
            StreamAction.connection_id==event.connection_id,StreamAction.media_id==event.media_id,
            StreamAction.action=='reset',StreamAction.state=='conflict'))).scalars().all()
        for action in actions:
            action.last_error=None
            if body.action=='confirm':
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
    elif event.kind=='cloud_conflict':
        entry=(await db.execute(select(TrackedEntry).where(
            TrackedEntry.user_id==viewer.id,TrackedEntry.media_id==event.media_id))).scalar_one_or_none()
        if not entry:raise HTTPException(409,'The tracked entry no longer exists')
        if body.action not in {'confirm','keep','change'}:raise HTTPException(422,'Resolve or keep this imported history')
        status_changed = False
        if body.action!='keep':
            changes=(event.payload or {}).get('changes') or []
            for change in changes:
                field=change.get('field');value=change.get('proposed')
                if field=='status':
                    entry.status=body.status.value if body.action=='change' and body.status else value
                    status_changed = True
                elif field=='start_date':entry.start_date=date.fromisoformat(value) if value else None
                elif field=='finish_date':entry.finish_date=date.fromisoformat(value) if value else None
                elif field=='progress' and value is not None:entry.progress=max(0,int(value))
            if body.action=='change' and body.status:
                entry.status=body.status.value
                status_changed = True
            if status_changed:mark_status_change(entry,'local')
            from core.activity import record_daily_activity, series_activity_details
            position, finished = await series_activity_details(db, await db.get(Media, entry.media_id), entry.progress)
            await record_daily_activity(db,user_id=viewer.id,media_id=entry.media_id,status=entry.status,
                score=effective_score(entry.rating_mode,entry.manual_score,entry.season_scores),
                progress=entry.progress,position=position,finished_seasons=finished,status_changed=status_changed)
    else:
        entry=(await db.execute(select(TrackedEntry).where(TrackedEntry.user_id==viewer.id,TrackedEntry.media_id==event.media_id))).scalar_one_or_none()
        if not entry:raise HTTPException(409,'The tracked entry no longer exists')
        status=body.status.value if body.action=='change' and body.status else event.previous_status if body.action=='keep' else event.proposed_status
        if not status:raise HTTPException(422,'Choose a status')
        entry.status=status
        mark_status_change(entry,'local')
        if status=='watching':
            from core.stream_actions import queue_restorations
            await queue_restorations(db,viewer.id,await db.get(Media,entry.media_id))
        from core.activity import record_daily_activity
        await record_daily_activity(db,user_id=viewer.id,media_id=entry.media_id,status=status,
            score=effective_score(entry.rating_mode,entry.manual_score,entry.season_scores),status_changed=True)
    event.state='confirmed' if body.action=='confirm' else 'corrected'
    await db.commit()
    return {'state':event.state}


@router.get("/people/{username}")
async def person(username: str, db: AsyncSession = Depends(get_db), viewer: User | None = Depends(get_optional_user)):
    user, owner = await profile_access(db, username, viewer)
    entries = (await db.execute(select(TrackedEntry, Media).join(Media, Media.id == TrackedEntry.media_id).where(TrackedEntry.user_id == user.id))).all()
    if not await anime_is_visible(db):entries=[row for row in entries if not is_anime(row[1])]
    following_ids = (await db.execute(select(Follow.following_id).where(Follow.follower_id == user.id))).scalars().all()
    followers_ids = (await db.execute(select(Follow.follower_id).where(Follow.following_id == user.id))).scalars().all()
    people = (await db.execute(select(User, UserProfileData).join(UserProfileData, UserProfileData.user_id == User.id).where(User.id.in_(set(following_ids + followers_ids)), UserProfileData.privacy_level == PrivacyLevel.public))).all()
    visible = [{"username":u.username, "display_name":p.display_name or u.username, "following":u.id in following_ids, "follower":u.id in followers_ids} for u,p in people]
    scores = [score for entry, _ in entries if (score := effective_score(entry.rating_mode, entry.manual_score, entry.season_scores)) is not None]
    activity_rows = (await db.execute(select(TrackingActivity, Media).join(Media, Media.id == TrackingActivity.media_id)
        .where(TrackingActivity.user_id == user.id).order_by(TrackingActivity.created_at.desc()).limit(60))).all()
    if not await anime_is_visible(db):
        activity_rows = [row for row in activity_rows if not is_anime(row[1])]
    prefs=await db.get(TrackingPreferences,user.id)
    return {"id":user.id,"username":user.username,"display_name":user.display_name,"bio":user.profile.bio if user.profile else None,
        "owner":owner,"following":bool(viewer and viewer.id in followers_ids),"has_avatar":bool(user.profile and user.profile.avatar_path),
        "combine_lists":True if prefs is None else prefs.combine_lists,
        "counts":{"movies":sum(m.media_type==MediaType.movie for e,m in entries),"series":sum(m.media_type==MediaType.series for e,m in entries),
                  "completed":sum(e.status=='completed' for e,m in entries),"following":len(following_ids),"followers":len(followers_ids),
                  "favorites":sum(e.favorite for e,m in entries),"rated":len(scores)},
        "average_score":round(sum(scores)/len(scores),1) if scores else None,
        "favorites":[media_data(m) for e,m in entries if e.favorite],"people":visible,
        "recent_activity":activity_data(activity_rows,limit=12)}


@router.get('/people-search')
async def people_search(q:str=Query('',max_length=100),db:AsyncSession=Depends(get_db),viewer:User=Depends(get_current_user)):
    term=q.strip()
    if not term:return {'results':[]}
    rows=(await db.execute(select(User,UserProfileData).join(UserProfileData,UserProfileData.user_id==User.id).where(
        UserProfileData.privacy_level==PrivacyLevel.public,
        or_(User.username.ilike(f'%{term}%'),UserProfileData.display_name.ilike(f'%{term}%'))
    ).order_by(User.username).limit(24))).all()
    followed=set((await db.execute(select(Follow.following_id).where(Follow.follower_id==viewer.id,
        Follow.following_id.in_([u.id for u,_ in rows])))).scalars()) if rows else set()
    return {'results':[{'id':u.id,'username':u.username,'display_name':p.display_name or u.username,
        'bio':p.bio,'has_avatar':bool(p.avatar_path),'following':u.id in followed,'owner':u.id==viewer.id} for u,p in rows]}


@router.post('/people/{username}/follow')
async def follow_person(username:str,db:AsyncSession=Depends(get_db),viewer:User=Depends(get_current_user)):
    target=(await db.execute(select(User).join(UserProfileData,UserProfileData.user_id==User.id).where(
        User.username==username,UserProfileData.privacy_level==PrivacyLevel.public))).scalar_one_or_none()
    if not target:raise HTTPException(404,'Public profile not found')
    if target.id==viewer.id:raise HTTPException(422,'You cannot follow yourself')
    exists=(await db.execute(select(Follow.id).where(Follow.follower_id==viewer.id,Follow.following_id==target.id))).scalar_one_or_none()
    if not exists:db.add(Follow(follower_id=viewer.id,following_id=target.id));await db.commit()
    return {'following':True}


@router.delete('/people/{username}/follow')
async def unfollow_person(username:str,db:AsyncSession=Depends(get_db),viewer:User=Depends(get_current_user)):
    target=(await db.execute(select(User).where(User.username==username))).scalar_one_or_none()
    if not target:raise HTTPException(404,'Profile not found')
    await db.execute(delete(Follow).where(Follow.follower_id==viewer.id,Follow.following_id==target.id));await db.commit()
    return {'following':False}


@router.get("/library")
async def library(db: AsyncSession = Depends(get_db), viewer: User = Depends(get_current_user)):
    from models.streaming_library import StreamingLibraryIntent

    observed = select(Collection.media_id).join(CollectionFile,
        CollectionFile.collection_id == Collection.id).where(
        Collection.user_id == viewer.id,
        CollectionFile.source.in_(['stremio', 'nuvio']))
    selected = select(StreamingLibraryIntent.media_id).where(
        StreamingLibraryIntent.user_id == viewer.id,
        StreamingLibraryIntent.desired.is_(True))
    excluded = select(StreamingLibraryIntent.media_id).where(
        StreamingLibraryIntent.user_id == viewer.id,
        StreamingLibraryIntent.desired.is_(False))
    rows = (await db.execute(select(Media).where(
        Media.media_type.in_([MediaType.movie, MediaType.series]),
        or_(Media.id.in_(selected), (Media.id.in_(observed)) & ~Media.id.in_(excluded)),
    ).order_by(Media.title))).scalars().all()
    return {"results":[media_data(m) for m in rows]}


class LibraryIntentPatch(BaseModel):
    in_library: bool


@router.get("/library/{media_id}")
async def get_library_state(media_id: int, db: AsyncSession = Depends(get_db), viewer: User = Depends(get_current_user)):
    media = await db.get(Media, media_id)
    if not media or media.media_type not in (MediaType.movie, MediaType.series):
        raise HTTPException(404, "Title not found")
    from core.streaming_library import library_state
    return await library_state(db, viewer.id, media_id)


@router.put("/library/{media_id}")
async def update_library_state(media_id: int, body: LibraryIntentPatch, background_tasks: BackgroundTasks,
                               db: AsyncSession = Depends(get_db), viewer: User = Depends(get_current_user)):
    from core.streaming_library import deliver_library_intent, set_library_intent
    state = await set_library_intent(db, viewer.id, media_id, body.in_library)
    if state['pending']:
        background_tasks.add_task(deliver_library_intent, viewer.id, media_id)
    return state


@router.post("/library/{media_id}/retry")
async def retry_library_delivery(media_id: int, db: AsyncSession = Depends(get_db), viewer: User = Depends(get_current_user)):
    from core.streaming_library import dispatch_library_deliveries, library_state
    await dispatch_library_deliveries(db, viewer.id, media_id)
    return await library_state(db, viewer.id, media_id)


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
        return normalize_score(value, 0.1)

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


def is_anime(media: Media) -> bool:
    data=media.tmdb_data or {}
    genres={str(g if isinstance(g,str) else g.get('name','')).lower() for g in data.get('genres',[]) if isinstance(g,(str,dict))}
    countries={str(value).upper() for value in data.get('origin_country',[]) if value}
    countries.update(str(value.get('iso_3166_1','')).upper() for value in data.get('production_countries',[]) if isinstance(value,dict))
    return 'animation' in genres and (data.get('original_language')=='ja' or 'JP' in countries)


async def anime_is_visible(db: AsyncSession) -> bool:
    settings=await db.get(GlobalSettings,1)
    return bool(settings and settings.show_anime)


def media_data(media):
    data = media.tmdb_data or {}
    regular_seasons = [
        season for season in data.get("seasons", [])
        if isinstance(season, dict) and (season.get("season_number") or 0) > 0
    ]
    return {
        "id": media.id, "title": media.title, "type": media.media_type.value,
        "poster": media.poster_path, "backdrop": media.backdrop_path,
        "overview": media.overview, "year": (media.release_date or "")[:4],
        "release_date": media.release_date, "original_title": media.original_title,
        "runtime": media.runtime or data.get("runtime"), "tmdb_score": media.tmdb_rating,
        "imdb_score": media.imdb_rating,
        "rt_critic_score": media.rt_critic_score,
        "rt_audience_score": media.rt_audience_score,
        "external_scores_updated_at": media.external_scores_updated_at,
        "tagline": media.tagline or data.get("tagline"), "adult": media.adult,
        "original_language": data.get("original_language"),
        "networks": [n for n in data.get("networks", []) if isinstance(n, dict) and n.get("name")],
        "studios": [c for c in data.get("production_companies", []) if isinstance(c, dict) and c.get("name")],
        "creators": [c for c in data.get("created_by", []) if isinstance(c, dict) and c.get("name")],
        "episode_runtime": next((value for value in data.get("episode_run_time", []) if value), None),
        "season_count": data.get("number_of_seasons") or len(regular_seasons) or None,
        "episode_count": data.get("number_of_episodes"),
        "last_air_date": data.get("last_air_date"),
        "release_status": media.status or data.get('status'), "genres": [g if isinstance(g, str) else g["name"] for g in data.get("genres", []) if isinstance(g, str) or (isinstance(g, dict) and g.get("name"))],
        "tmdb_id": media.tmdb_id, "tvdb_id": media.tvdb_id, "imdb_id": media.imdb_id,
        "is_anime": is_anime(media),
    }


def activity_data(rows, *, include_user=False, limit=12):
    """Collapse legacy and current rows into one newest-first UTC-day card."""
    from core.activity import merge_activity_payload
    grouped = {}
    for row in rows:
        activity, media = row[0], row[1]
        user = row[2] if include_user else None
        key = (user.id if user else activity.user_id, media.id, activity.created_at.date())
        if key not in grouped:
            grouped[key] = {
                **({"username": user.username} if user else {}),
                "status": activity.status,
                "score": activity.score,
                "payload": dict(activity.payload or {}),
                "created_at": activity.created_at,
                "media": media_data(media),
            }
        else:
            grouped[key]["payload"] = merge_activity_payload(grouped[key]["payload"], activity.payload)
    for activity in grouped.values():
        if activity["score"] is None:
            # Legacy rows can claim a rating change without retaining a score.
            # Do not publish a false "Rated" event when the value is unknowable.
            activity["payload"]["rating_changed"] = False
    return list(grouped.values())[:limit]


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
async def profile_list(username: str, media_type: Literal["movie", "series", "all"], db: AsyncSession = Depends(get_db), viewer: User | None = Depends(get_optional_user)):
    user, owner = await profile_access(db, username, viewer)
    query=select(TrackedEntry,Media).join(Media,Media.id==TrackedEntry.media_id).where(TrackedEntry.user_id==user.id)
    if media_type=='all':query=query.where(Media.media_type.in_([MediaType.movie,MediaType.series]))
    else:query=query.where(Media.media_type==MediaType(media_type))
    rows=(await db.execute(query.order_by(Media.title))).all()
    if not await anime_is_visible(db):rows=[row for row in rows if not is_anime(row[1])]
    following = False
    if viewer and not owner:
        following = (await db.execute(select(Follow.id).where(Follow.follower_id == viewer.id, Follow.following_id == user.id))).scalar_one_or_none() is not None
    entries = [entry_data(e, m, owner) for e, m in rows]
    movie_ids = [m.id for _, m in rows if m.media_type == MediaType.movie]
    watched_movies = set((await db.execute(select(WatchEvent.media_id).where(
        WatchEvent.user_id == user.id, WatchEvent.media_id.in_(movie_ids),
        WatchEvent.completed.is_(True)))).scalars()) if movie_ids else set()
    for result, (_, media) in zip(entries, rows):
        if media.media_type == MediaType.movie:
            result['progress'] = int(bool(result['progress'] or result['status'] == 'completed'
                                          or media.id in watched_movies))
    if media_type in ('series','all'):
        # One batched query for all catalogue episode IDs; don't confuse status
        # Completed with history covering newly released episodes.
        tmdb_ids = {episode_id for _, m in rows if (m.tmdb_data or {}).get('tracking_catalogue_provider') != 'tvdb'
                    for episode_id in (m.tmdb_data or {}).get('tracking_episode_ids', [])}
        tvdb_ids = {episode_id for _, m in rows if (m.tmdb_data or {}).get('tracking_catalogue_provider') == 'tvdb'
                    for episode_id in (m.tmdb_data or {}).get('tracking_episode_ids', [])}
        identity_filters = []
        if tmdb_ids:
            identity_filters.append(Media.tmdb_id.in_(tmdb_ids))
        if tvdb_ids:
            identity_filters.append(Media.tvdb_id.in_(tvdb_ids))
        released = (await db.execute(select(Media.id, Media.tmdb_id, Media.tvdb_id, Media.season_number, Media.episode_number).where(
            Media.media_type == MediaType.episode, or_(*identity_filters), Media.season_number > 0,
            Media.release_date.is_not(None), Media.release_date <= date.today().isoformat()))).all() if identity_filters else []
        watched = set((await db.execute(select(WatchEvent.media_id).where(WatchEvent.user_id == user.id,
            WatchEvent.media_id.in_([r.id for r in released]), WatchEvent.completed.is_(True)))).scalars()) if released else set()
        for result, (_, media) in zip(entries, rows):
            if media.media_type != MediaType.series:
                continue
            ids = set((media.tmdb_data or {}).get('tracking_episode_ids', []))
            if (media.tmdb_data or {}).get('tracking_catalogue_refreshed_at'):
                provider = (media.tmdb_data or {}).get('tracking_catalogue_provider', 'tmdb')
                title_episodes = [r for r in released if (r.tvdb_id if provider == 'tvdb' else r.tmdb_id) in ids]
                result['released_episodes'] = len(title_episodes)
                unwatched = [r for r in title_episodes if r.id not in watched]
                result['progress'] = len(title_episodes) - len(unwatched)
                watched_episodes = [r for r in title_episodes if r.id in watched]
                latest = max(watched_episodes, key=lambda r: (r.season_number or 0, r.episode_number or 0), default=None)
                result['season_position'] = f'S{latest.season_number}E{latest.episode_number}' if latest else None
                result['new_seasons'] = len({r.season_number for r in unwatched}) if result['status'] == 'completed' else 0
    prefs=await db.get(TrackingPreferences,user.id)
    return {
        "profile": {"id": user.id, "username": user.username, "display_name": user.display_name,
                    "bio": user.profile.bio if user.profile else None, "has_avatar": bool(user.profile and user.profile.avatar_path)},
        "owner": owner, "following": following,"combine_lists":True if prefs is None else prefs.combine_lists,
        "entries": entries,
    }


@router.get("/profile/{username}/stats/summary")
async def profile_stats(username: str, media_type: Literal["movie", "series", "all"] = "all",
                        year: int | None = Query(None, ge=1900, le=2200), db: AsyncSession = Depends(get_db),
                        viewer: User | None = Depends(get_optional_user)):
    """Current list totals plus documented viewing statistics for a profile."""
    user, owner = await profile_access(db, username, viewer)
    query = select(TrackedEntry, Media).join(Media, Media.id == TrackedEntry.media_id).where(
        TrackedEntry.user_id == user.id, Media.media_type.in_([MediaType.movie, MediaType.series]))
    if media_type != "all": query = query.where(Media.media_type == MediaType(media_type))
    entries = (await db.execute(query)).all()
    if not await anime_is_visible(db): entries = [row for row in entries if not is_anime(row[1])]
    statuses = {status.value: 0 for status in TrackingStatus}
    scores, genres = [], {}
    for entry, media in entries:
        statuses[entry.status] = statuses.get(entry.status, 0) + 1
        score = effective_score(entry.rating_mode, entry.manual_score, entry.season_scores)
        if score: scores.append(float(score))
        for genre in media_data(media)["genres"]: genres[genre] = genres.get(genre, 0) + 1

    events = (await db.execute(select(WatchEvent, Media).join(Media, Media.id == WatchEvent.media_id).where(
        WatchEvent.user_id == user.id, WatchEvent.completed.is_(True),
        Media.media_type.in_([MediaType.movie, MediaType.episode])))).all()
    if media_type == "movie": events = [row for row in events if row[1].media_type == MediaType.movie]
    elif media_type == "series": events = [row for row in events if row[1].media_type == MediaType.episode]
    if year is not None: events = [row for row in events if row[0].watched_at and row[0].watched_at.year == year]
    movies = {media.id for _, media in events if media.media_type == MediaType.movie}
    episodes = {media.id for _, media in events if media.media_type == MediaType.episode}
    shows = {media.show_id for _, media in events if media.media_type == MediaType.episode and media.show_id}
    seasons = {(media.show_id, media.season_number) for _, media in events if media.media_type == MediaType.episode and media.show_id and (media.season_number or 0) > 0}
    documented_plays = sum(max(event.play_count or 1, 1) for event, _ in events)
    dated = [(event, media) for event, media in events if event.watched_at]
    estimated_minutes = sum((media.runtime or 0) * max(event.play_count or 1, 1) for event, media in dated)
    activity = {}
    for event, media in dated:
        bucket = activity.setdefault(event.watched_at.strftime("%Y-%m"), {"movies": 0, "episodes": 0})
        bucket["movies" if media.media_type == MediaType.movie else "episodes"] += max(event.play_count or 1, 1)
    distribution = {step / 2: 0 for step in range(1, 21)}
    for score in scores: distribution[score] = distribution.get(score, 0) + 1
    prefs = await db.get(TrackingPreferences, user.id)
    return {"profile":{"id":user.id,"username":user.username,"display_name":user.display_name,
                       "bio":user.profile.bio if user.profile else None,"has_avatar":bool(user.profile and user.profile.avatar_path)},
            "owner":owner,"combine_lists":True if prefs is None else prefs.combine_lists,"media_type":media_type,"year":year,
            "current":{"total":len(entries),"statuses":statuses},
            "viewing":{"unique_titles":len(movies)+len(shows),"unique_movies":len(movies),"unique_episodes":len(episodes),
                       "unique_seasons":len(seasons),"repeat_views":max(0,documented_plays-len(movies)-len(episodes)),
                       "estimated_watch_minutes":estimated_minutes},
            "scores":{"average":round(sum(scores)/len(scores),1) if scores else None,"rated":len(scores),
                      "distribution":[{"score":score,"count":count} for score,count in distribution.items()]},
            "genres":[{"genre":genre,"count":count} for genre,count in sorted(genres.items(),key=lambda item:(-item[1],item[0]))],
            "activity":[{"month":month,**counts} for month,counts in sorted(activity.items())]}


@router.get("/catalog")
async def catalog(q: str = "", media_type: Literal["movie", "series"] = "movie", db: AsyncSession = Depends(get_db), viewer: User | None = Depends(get_optional_user)):
    await catalog_access(db, viewer)
    show_anime=await anime_is_visible(db)
    term = q.strip()[:200]
    query = select(Media).where(Media.media_type == MediaType(media_type))
    if term:
        similarity = func.similarity(Media.title, term)
        query = query.where(or_(Media.title.ilike(f"%{term}%"), similarity >= 0.2)).order_by(similarity.desc(), Media.title)
    else:
        query = query.order_by(Media.title)
    rows = (await db.execute(query.limit(80))).scalars().all()
    if not show_anime:rows=[m for m in rows if not is_anime(m)]
    results = [media_data(m) for m in rows]
    notice = None
    if term:
        from routers.media import get_user_tmdb_key
        from core import tmdb
        key = await get_user_tmdb_key(db, viewer.id if viewer else -1)
        if key:
            try:
                search = tmdb.search_movies if media_type == 'movie' else tmdb.search_shows
                remote = await search(term, api_key=key)
                remote_results = remote.get('results', [])
                # Search an unimported title with a few safe corrections only
                # when the literal request came back empty. This preserves
                # normal TMDB ranking and keeps the fallback within its API
                # budget.
                if not remote_results:
                    corrected = await asyncio.gather(
                        *(search(candidate, api_key=key) for candidate in fuzzy_remote_terms(term)),
                        return_exceptions=True,
                    )
                    remote_results = [
                        item
                        for response in corrected if isinstance(response, dict)
                        for item in response.get('results', [])
                        if is_close_title_match(term, item.get('title') or item.get('name') or '')
                    ]
                known = {m.tmdb_id for m in rows if m.tmdb_id}
                for item in remote_results:
                    candidate_data={'genres':[{'name':'Animation'}] if 16 in item.get('genre_ids',[]) else [],'original_language':item.get('original_language'),'origin_country':item.get('origin_country',[])}
                    candidate=type('Candidate',(),{'tmdb_data':candidate_data})()
                    if item['id'] in known or item.get('adult') or (not show_anime and is_anime(candidate)):
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
    if is_anime(media) and not await anime_is_visible(db):
        raise HTTPException(404,"Title not found")
    # Logged-out visitors only read the shared cache. Signed-in visits may
    # refresh stale scores using the user's key, then the administrator key.
    if viewer:
        from core.external_scores import refresh_external_scores
        await refresh_external_scores(db, media, viewer.id)
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
    from routers.shows import get_user_tvdb_key
    from core.tracking_metadata import hydrate_tracking_episodes
    media = await db.get(Media, media_id)
    if not media or media.media_type != MediaType.series:
        raise HTTPException(404, 'Series not found')
    key = await get_user_tmdb_key(db, viewer.id)
    tvdb_key = await get_user_tvdb_key(db, viewer.id)
    if not key and not tvdb_key:
        raise HTTPException(409, 'Add a metadata provider key in Settings, or ask your administrator, to load episodes')
    try:
        count = await hydrate_tracking_episodes(db, media, key, tvdb_key)
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
        return [media] if not media.release_date or media.release_date[:10] <= date.today().isoformat() else []
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
        provider = (media.tmdb_data or {}).get('tracking_catalogue_provider', 'tmdb')
        identity = Media.tvdb_id if provider == 'tvdb' else Media.tmdb_id
        query = query.where(identity.in_(catalogue_ids))
    return (await db.execute(query.order_by(Media.season_number, Media.episode_number))).scalars().all()


class SeasonProgressPatch(BaseModel):
    watched: bool
    confirm_rollback: bool = False


@router.patch('/entry/{media_id}/season/{season_number}')
async def save_season_progress(media_id: int, season_number: int, body: SeasonProgressPatch,
                               background_tasks: BackgroundTasks,
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
    return await save_entry(media_id, EntryPatch(progress=target, confirm_rollback=True), background_tasks, db, viewer)


@router.patch("/entry/{media_id}")
async def save_entry(media_id: int, body: EntryPatch, background_tasks: BackgroundTasks,
                     db: AsyncSession = Depends(get_db), viewer: User = Depends(get_current_user)):
    media = await db.get(Media, media_id)
    if not media or media.media_type not in (MediaType.movie, MediaType.series):
        raise HTTPException(404, "Title not found")
    # Serialize edits for this user, including first insertion of a title.
    await db.execute(select(User.id).where(User.id == viewer.id).with_for_update())
    entry = (await db.execute(select(TrackedEntry).where(TrackedEntry.user_id == viewer.id, TrackedEntry.media_id == media_id))).scalar_one_or_none()
    previous = entry.status if entry else None
    old_progress = entry.progress if entry else 0
    old_score = effective_score(entry.rating_mode, entry.manual_score, entry.season_scores) if entry else None
    old_season_scores = dict(entry.season_scores or {}) if entry else {}
    added_watched_ids: set[int] = set()
    removed_watched_ids: set[int] = set()
    if entry is None:
        # An explicit user edit re-adds a previously deleted entry. Imports never
        # do this; they honor the marker until the user makes this choice.
        await db.execute(delete(TrackingDeletion).where(TrackingDeletion.user_id==viewer.id,TrackingDeletion.media_id==media_id))
        await db.execute(delete(SyncReview).where(SyncReview.user_id==viewer.id,SyncReview.media_id==media_id,SyncReview.kind=='outbound_pending'))
        entry = TrackedEntry(user_id=viewer.id, media_id=media_id, status="planning", rating_mode="manual", season_scores={}, progress=0, favorite=False, rewatch_count=0)
        db.add(entry)
    fields = body.model_fields_set
    local_status_decision = 'status' in fields
    if body.season_scores is not None and media.media_type != MediaType.series:
        raise HTTPException(422, "Movies do not have season ratings")
    if body.rating_mode == 'average' and media.media_type != MediaType.series:
        raise HTTPException(422, "Only shows can average season ratings")
    if body.season_scores and any(body.season_scores.values()) and not any(entry.season_scores.values()) and body.rating_mode is None:
        raise HTTPException(409, "Choose average seasons or a separate show score")
    status = body.status.value if body.status else entry.status
    today = datetime.now(timezone.utc).date()
    entry.start_date, entry.finish_date = default_dates(previous, status, entry.start_date, entry.finish_date, today)
    entry.status = status
    for name in ("manual_score", "rating_mode", "favorite", "notes", "start_date", "finish_date", "rewatch_count"):
        if name in fields:
            value = getattr(body, name)
            if value is None and name in ("rating_mode", "favorite", "rewatch_count"):
                raise HTTPException(422, f"{name} cannot be null")
            setattr(entry, name, value)
    if body.season_scores is not None:
        entry.season_scores = {**entry.season_scores, **body.season_scores}
    episodes = []
    completing_series = media.media_type == MediaType.series and status == 'completed' and previous != 'completed'
    if body.progress is not None or body.mark_released_watched or completing_series:
        if media.media_type == MediaType.series and not (media.tmdb_data or {}).get('tracking_catalogue_refreshed_at'):
            raise HTTPException(409, 'Refresh episode metadata on the title page before changing progress')
        episodes = await released_episodes(db, media)
        target = len(episodes) if body.mark_released_watched or completing_series else body.progress
        if not episodes or target > len(episodes):
            raise HTTPException(409, "Released episode metadata is needed before changing progress")
        ids = [m.id for m in episodes]
        watched = set((await db.execute(select(WatchEvent.media_id).where(WatchEvent.user_id == viewer.id, WatchEvent.media_id.in_(ids), WatchEvent.completed.is_(True)))).scalars())
        # Watch events are canonical. A cached aggregate can lag after a
        # catalogue refresh, so only later watched events make this a rollback.
        rollback = bool(watched.intersection(ids[target:]))
        if rollback and not body.confirm_rollback:
            raise HTTPException(409, "Confirm marking later episodes unwatched")
        for episode in episodes[:target]:
            if episode.id not in watched:
                db.add(WatchEvent(user_id=viewer.id, media_id=episode.id, completed=True, watched_at=None, provisional=True))
                added_watched_ids.add(episode.id)
        if rollback:
            removed_watched_ids = watched.intersection(ids[target:])
            await db.execute(delete(WatchEvent).where(WatchEvent.user_id == viewer.id, WatchEvent.media_id.in_(ids[target:])))
        entry.progress = target
        if 'status' not in fields and target > 0:
            # Watching later episodes resumes paused/dropped titles, while a
            # completed title remains completed even when correcting history.
            from core.tracking_rules import observed_status
            entry.status = observed_status(previous, target == len(episodes), True)
            entry.start_date, entry.finish_date = default_dates(previous, entry.status, entry.start_date, entry.finish_date, today)
            local_status_decision = entry.status != previous
    if local_status_decision:
        mark_status_change(entry, 'local')
    if previous == 'watching' and entry.status != 'watching':
        from core.stream_actions import queue_local_dismissals
        await queue_local_dismissals(db, viewer.id, media)
    # Mirror the effective value into the existing provider-facing rating rows.
    if entry.status=='watching' and previous!='watching':
        from core.stream_actions import queue_restorations
        await queue_restorations(db,viewer.id,media)
    score = effective_score(entry.rating_mode, entry.manual_score, entry.season_scores)
    if score is not None:
        from core.web_push import resolve_rating_prompts
        await resolve_rating_prompts(db, user_id=viewer.id, media_id=media_id)
    scores = {None: score, **{int(k): v for k, v in entry.season_scores.items()}}
    for season, value in scores.items():
        row = (await db.execute(select(Rating).where(Rating.user_id == viewer.id, Rating.media_id == media_id, Rating.season_number == season, Rating.episode_order.is_(None)))).scalar_one_or_none()
        if value is None:
            if row:
                await db.delete(row)
        elif row:
            if row.rating != value:
                row.rating, row.rated_at = value, datetime.now(timezone.utc).replace(tzinfo=None)
        else:
            db.add(Rating(user_id=viewer.id, media_id=media_id, season_number=season, rating=value))
    progress_changed = entry.progress != old_progress
    if previous != entry.status or old_score != score or progress_changed:
        from core.activity import record_daily_activity, series_activity_details
        position, finished = await series_activity_details(db, media, entry.progress)
        await record_daily_activity(db, user_id=viewer.id, media_id=media_id, status=entry.status, score=score,
            episodes_watched=max(0, entry.progress-old_progress), progress=entry.progress if media.media_type == MediaType.series else None,
            position=position, finished_seasons=finished, status_changed=previous != entry.status, rating_changed=old_score != score)
    await db.commit()
    await db.refresh(entry)
    changed_ratings = {}
    removed_ratings = set()
    before_scores = {None: old_score, **{int(k): v for k, v in old_season_scores.items()}}
    for season in set(before_scores) | set(scores):
        before, after = before_scores.get(season) or None, scores.get(season) or None
        if before == after:
            continue
        key = (media_id, season)
        if after is None:
            removed_ratings.add(key)
        else:
            changed_ratings[key] = after
    if added_watched_ids or changed_ratings or removed_ratings:
        from core.local_outbound import dispatch_local_tracking_delta
        background_tasks.add_task(
            dispatch_local_tracking_delta, viewer.id,
            added_watched_ids, changed_ratings, removed_ratings,
        )
    if removed_watched_ids:
        from core.local_outbound import dispatch_local_watch_rollback
        background_tasks.add_task(dispatch_local_watch_rollback, viewer.id, removed_watched_ids)
    return entry_data(entry, media, True)


@router.get("/activity")
async def activity(db: AsyncSession = Depends(get_db), viewer: User = Depends(get_current_user)):
    followed = select(Follow.following_id).where(Follow.follower_id == viewer.id)
    rows = (await db.execute(select(TrackingActivity, Media, User).join(Media, Media.id == TrackingActivity.media_id)
        .join(User, User.id == TrackingActivity.user_id).outerjoin(UserProfileData, UserProfileData.user_id == User.id)
        .where((User.id.in_(followed)) & (UserProfileData.privacy_level == PrivacyLevel.public))
        .order_by(TrackingActivity.created_at.desc()).limit(60))).all()
    if not await anime_is_visible(db):rows=[row for row in rows if not is_anime(row[1])]
    return {"results": activity_data(rows,include_user=True,limit=60)}
