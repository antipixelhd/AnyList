"""Capture successful streaming pulls and create private review events.

This layer never treats a first/partial snapshot as a destructive removal and
does not perform network writes. Provider dispatch remains a separate step.
"""
from datetime import date, datetime, timezone
from sqlalchemy import select
from models import Media, User, Show, WatchEvent
from models.base import MediaType
from models.tracking import StreamBaseline, SyncReview, TrackedEntry, TrackingPreferences, TrackingActivity, TrackingDeletion
from core.tracking_rules import effective_score, observed_status, default_dates
from core.tracking_import import import_tracking_history


def _active(rows):
    result={}
    for row in rows:
        try:
            duration=float(row.get('duration') or 0)
            position=float(row.get('position') or 0)
        except (ValueError,TypeError):continue
        if duration>0 and 0<position/duration<0.9:
            result[str(row.get('content_id'))]=dict(row)
    return result


def completed_progress(record, watched):
    return any(str(row.get('content_id'))==str(record.get('content_id'))
        and row.get('season')==record.get('season') and row.get('episode')==record.get('episode') for row in watched)


def watch_key(row):
    return f"{row.get('content_id')}:{row.get('season')}:{row.get('episode')}"


def completed_rows(progress):
    result = []
    for row in progress:
        try:
            duration, position = float(row.get('duration') or 0), float(row.get('position') or 0)
            if duration > 0 and position / duration >= 0.9:
                result.append(row)
        except (ValueError, TypeError):
            continue
    return result


async def require_stream_reconciliation(db, conn):
    """Shared by HTTP and background entry points; no first-write bypass."""
    if conn.type not in ('stremio', 'nuvio'):
        return
    baseline = await db.get(StreamBaseline, conn.id)
    if not baseline or not baseline.approved:
        from fastapi import HTTPException
        raise HTTPException(409, 'Run a full import and confirm its summary in Recent events before pushing to this connection')
    unresolved = (await db.execute(select(SyncReview.id).where(SyncReview.user_id == conn.user_id,
        SyncReview.connection_id == conn.id, SyncReview.state == 'pending',
        SyncReview.kind.in_(['conflict','uncertain_removal','deletion_conflict'])))).first()
    if unresolved:
        from fastapi import HTTPException
        raise HTTPException(409, 'Resolve this connection’s conflicts in Recent events before pushing')


async def apply_series_observation(db, user_id, media, entry, row, finished):
    """Advance cumulative regular episodes only with a verified matching order."""
    show=(await db.execute(select(Show).where(Show.tmdb_id==media.tmdb_id))).scalar_one_or_none() if media.tmdb_id else None
    if not show or show.canonical_source!='tmdb':return False
    episodes=(await db.execute(select(Media).where(Media.show_id==show.id,Media.media_type==MediaType.episode,
        Media.season_number>0,Media.release_date.is_not(None),Media.release_date<=date.today().isoformat())
        .order_by(Media.season_number,Media.episode_number))).scalars().all()
    index=next((i for i,e in enumerate(episodes) if (e.season_number,e.episode_number)==(row.get('season'),row.get('episode'))),None)
    if index is None:return False
    through=index+1 if finished else index
    watched=set((await db.execute(select(WatchEvent.media_id).where(WatchEvent.user_id==user_id,
        WatchEvent.media_id.in_([e.id for e in episodes]),WatchEvent.completed.is_(True)))).scalars())
    for episode in episodes[:through]:
        if episode.id not in watched:
            db.add(WatchEvent(user_id=user_id,media_id=episode.id,completed=True,provisional=True,
                watched_at=datetime.now(timezone.utc).replace(tzinfo=None) if finished and episode.id==episodes[index].id else None))
            watched.add(episode.id)
    entry.progress=len(watched)
    return bool((media.tmdb_data or {}).get('tracking_catalogue_refreshed_at') and episodes and len(watched)==len(episodes))


async def observe_stream_snapshot(db,conn,library,watched,progress,tmdb_ids,*,complete=True,touched=None,removed_library=None):
    # Partial/failed pulls cannot establish or advance a trusted baseline.
    # A provider's verified incremental feed must first be materialized into
    # a complete snapshot by its adapter; touched rows alone prove no removal.
    if not complete:
        baseline = await db.get(StreamBaseline, conn.id)
        records = baseline.snapshot.get('records') if baseline else None
        if records is None or touched is None:
            return
        touched = {str(key) for key in touched}
        def merge(previous, current):
            return [row for row in previous if str(row.get('content_id')) not in touched] + current
        library = merge(records.get('library', []), library)
        watched = merge(records.get('watched', []), watched)
        progress = merge(records.get('progress', []), progress)
        tmdb_ids = {**baseline.snapshot.get('mappings', {}), **tmdb_ids}
    # Import is additive and skips tombstones. It never creates tracked entries
    # merely because a title appears in a streaming library.
    existing_ids = set((await db.execute(select(TrackedEntry.media_id).where(TrackedEntry.user_id == conn.user_id))).scalars())
    imported = await import_tracking_history(db,conn.user_id)
    await db.execute(select(User.id).where(User.id==conn.user_id).with_for_update())
    baseline=await db.get(StreamBaseline,conn.id)
    first=baseline is None
    previous=baseline.snapshot if baseline else {}
    active=_active(progress)
    completed = [*watched, *completed_rows(progress)]
    library_ids={str(row.get('content_id')) for row in library}
    if first:
        baseline=StreamBaseline(connection_id=conn.id,user_id=conn.user_id,approved=False)
        db.add(baseline)
        db.add(SyncReview(user_id=conn.user_id,connection_id=conn.id,kind='initial_import',message=f'{conn.name}: imported {imported} tracked entries, observed {len(library)} library items and {len(watched)} watch records. Review your lists and any conflicts before approving outbound synchronization. An empty first snapshot removes nothing.'))
    old_active=previous.get('progress',{})
    removed=set(old_active)-set(active) if not first else set()
    known=(await db.execute(select(Media).where(Media.tmdb_id.in_(set(tmdb_ids.values())),Media.media_type.in_([MediaType.movie,MediaType.series])))).scalars().all()
    lookup={(m.tmdb_id,m.media_type.value):m for m in known}
    # Preserve prior mappings for a title absent from this pull.
    mappings={**previous.get('mappings',{}),**{k:int(v) for k,v in tmdb_ids.items() if v is not None}}
    missing={mappings[key] for key in removed if key in mappings} - {m.tmdb_id for m in known}
    if missing:
        rows=(await db.execute(select(Media).where(Media.tmdb_id.in_(missing),Media.media_type.in_([MediaType.movie,MediaType.series])))).scalars()
        lookup.update({(m.tmdb_id,m.media_type.value):m for m in rows})
    preferences = await db.get(TrackingPreferences, conn.user_id)
    if first:
        for row in [*active.values(), *completed]:
            media = lookup.get((mappings.get(str(row.get('content_id'))), row.get('content_type')))
            if not media or media.id not in existing_ids:
                continue
            entry = (await db.execute(select(TrackedEntry).where(TrackedEntry.user_id == conn.user_id, TrackedEntry.media_id == media.id))).scalar_one()
            proposed = observed_status(entry.status, media.media_type == MediaType.movie and row in completed, True)
            if proposed == entry.status:
                continue
            pending = (await db.execute(select(SyncReview.id).where(SyncReview.user_id == conn.user_id,
                SyncReview.connection_id == conn.id, SyncReview.media_id == media.id, SyncReview.kind == 'conflict', SyncReview.state == 'pending'))).first()
            if not pending:
                db.add(SyncReview(user_id=conn.user_id,connection_id=conn.id,media_id=media.id,kind='conflict',
                    previous_status=entry.status,proposed_status=proposed,
                    message=f'{conn.name}: imported playback disagrees with your existing status. Your local value was kept; choose the correct status before exporting.'))
    for key in removed:
        old=old_active[key]
        if completed_progress(old,completed):continue
        media=lookup.get((mappings.get(key),old.get('content_type')))
        if not media:continue
        entry=(await db.execute(select(TrackedEntry).where(TrackedEntry.user_id==conn.user_id,TrackedEntry.media_id==media.id))).scalar_one_or_none()
        if not entry or entry.status=='completed':continue
        pending=(await db.execute(select(SyncReview.id).where(SyncReview.user_id==conn.user_id,SyncReview.media_id==media.id,SyncReview.state=='pending',SyncReview.kind.in_(['playback_removed','conflict'])))).first()
        if pending:continue
        # A local edit since the prior source observation has uncertain ordering.
        conflict=bool(entry.updated_at and baseline.observed_at and entry.updated_at>baseline.observed_at)
        proposed='dropped' if media.media_type==MediaType.movie else 'paused'
        auto_confirm = bool(preferences and preferences.auto_confirm and not conflict and baseline.approved)
        db.add(SyncReview(user_id=conn.user_id,connection_id=conn.id,media_id=media.id,kind='conflict' if conflict else 'playback_removed',state='confirmed' if auto_confirm else 'pending',
            previous_status=entry.status,proposed_status=proposed,
            message=f'{conn.name}: playback disappeared without a matching completion. '+('A newer local edit was preserved; choose the correct status.' if conflict else f'Marked {proposed}. Review the change or add a rating.')))
        if not conflict:
            entry.status=proposed
            from core.stream_actions import queue_dismissals
            await queue_dismissals(db, conn, media)
            if auto_confirm:
                db.add(TrackingActivity(user_id=conn.user_id,media_id=media.id,status=proposed,
                    score=effective_score(entry.rating_mode,entry.manual_score,entry.season_scores)))
    # Only changed observations advance an existing tracked entry. Repeated
    # polling must not restore cleared dates or reinterpret the same playback.
    if not first:
        old_watched = set(previous.get('watched', []))
        new_completed = [row for row in completed if watch_key(row) not in old_watched]
        changed_active = [row for key, row in active.items() if old_active.get(key) != row]
        deleted = set((await db.execute(select(TrackingDeletion.media_id).where(TrackingDeletion.user_id == conn.user_id))).scalars())
        for row in [*changed_active, *new_completed]:
            media = lookup.get((mappings.get(str(row.get('content_id'))), row.get('content_type')))
            if not media or media.id in deleted:
                continue
            entry = (await db.execute(select(TrackedEntry).where(TrackedEntry.user_id == conn.user_id, TrackedEntry.media_id == media.id))).scalar_one_or_none()
            if not entry:
                continue
            pending = (await db.execute(select(SyncReview.id).where(SyncReview.user_id == conn.user_id,
                SyncReview.media_id == media.id, SyncReview.state == 'pending', SyncReview.kind.in_(['conflict','playback_removed'])))).first()
            if pending:
                continue
            previous_status = entry.status
            is_complete = media.media_type == MediaType.movie and row in new_completed
            # A newer local correction takes precedence over inferred history too.
            local_change=media.id in existing_ids and entry.updated_at and baseline.observed_at and entry.updated_at>baseline.observed_at
            if media.media_type==MediaType.series and not local_change:
                is_complete=await apply_series_observation(db,conn.user_id,media,entry,row,row in new_completed)
            proposed = observed_status(previous_status, is_complete, True)
            if local_change and (proposed != previous_status or media.media_type==MediaType.series):
                db.add(SyncReview(user_id=conn.user_id,connection_id=conn.id,media_id=media.id,kind='conflict',
                    previous_status=previous_status,proposed_status=proposed,
                    message=f'{conn.name}: new playback overlaps a newer local edit. Your local status was preserved.'))
                continue
            entry.status = proposed
            entry.start_date, entry.finish_date = default_dates(previous_status, entry.status, entry.start_date, entry.finish_date, date.today())
            if media.id not in existing_ids:
                if entry.status=='watching' and entry.start_date is None:entry.start_date=date.today()
                if entry.status=='completed' and entry.finish_date is None:entry.finish_date=date.today()
            if entry.status != previous_status:
                db.add(TrackingActivity(user_id=conn.user_id,media_id=media.id,status=entry.status,
                    score=effective_score(entry.rating_mode,entry.manual_score,entry.season_scores)))
    resume={**previous.get('resume',{})}
    for key in removed:
        if not completed_progress(old_active[key],completed):resume[key]=old_active[key]
    for key in active:resume.pop(key,None)
    # Keep only identity mappings for tombstones, never deleted viewing data.
    deleted_ids=set((await db.execute(select(TrackingDeletion.media_id).where(TrackingDeletion.user_id==conn.user_id))).scalars())
    deleted_keys={key for key,tmdb_id in mappings.items() for kind in ('movie','series')
        if (lookup.get((tmdb_id,kind)) and lookup[(tmdb_id,kind)].id in deleted_ids)}
    active={key:row for key,row in active.items() if key not in deleted_keys}
    resume={key:row for key,row in resume.items() if key not in deleted_keys}
    watched=[row for row in watched if str(row.get('content_id')) not in deleted_keys]
    progress=[row for row in progress if str(row.get('content_id')) not in deleted_keys]
    completed=[row for row in completed if str(row.get('content_id')) not in deleted_keys]
    baseline.snapshot={'library':sorted(library_ids),'progress':active,'mappings':mappings,'resume':resume,
        'watched': sorted({watch_key(row) for row in completed}),
        'records': {'library':library, 'watched':watched, 'progress':progress}}
    baseline.observed_at=datetime.now(timezone.utc).replace(tzinfo=None)
    await db.commit()
