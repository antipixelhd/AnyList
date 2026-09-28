"""Capture successful streaming pulls and create private review events.

This layer never treats a first/partial snapshot as a destructive removal and
does not perform network writes. Provider dispatch remains a separate step.
"""
from datetime import date, datetime, timezone
from sqlalchemy import delete, or_, select
from models import Media, User, Show, WatchEvent
from models.base import MediaType
from models.tracking import StreamAction, StreamBaseline, SyncReview, TrackedEntry, TrackingPreferences, TrackingDeletion
from core.tracking_rules import effective_score, observed_status, default_dates
from core.tracking_import import import_tracking_history
from core.status_provenance import mark_status_change, provider_changed_at, status_changed_at
from core.web_push import queue_sync_completion_rating


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


async def changed_watch_rows_from_source(db, conn, rows):
    """Import only a provider's new watches or changed play dates after approval."""
    baseline = await db.get(StreamBaseline, conn.id)
    if not baseline:
        return rows
    if not baseline.approved:
        return await current_watch_rows(db, conn, rows)
    previous_rows = (baseline.snapshot or {}).get('records', {}).get('watched', [])
    previous = {(watch_key(row), str(row.get('watched_at'))) for row in previous_rows}
    changed = [row for row in rows
               if (watch_key(row), str(row.get('watched_at'))) not in previous]
    return await current_watch_rows(db, conn, changed)


async def current_watch_rows(db, conn, rows):
    """Late history must not undo a newer tracking/progress correction."""
    entries = (await db.execute(select(TrackedEntry, Media).join(
        Media, Media.id == TrackedEntry.media_id,
    ).where(TrackedEntry.user_id == conn.user_id))).all()
    clocks = {}
    for entry, media in entries:
        if media.tmdb_id is not None:
            clocks[(media.tmdb_id, media.media_type.value)] = status_changed_at(entry)
    baseline = await db.get(StreamBaseline, conn.id)
    mappings = dict((baseline.snapshot or {}).get('mappings', {})) if baseline else {}
    # The caller may have just resolved a new identifier. Fall back to media
    # external IDs rather than treating an unmapped historical row as current.
    for entry, media in entries:
        imdb = (media.tmdb_data or {}).get('external_ids', {}).get('imdb_id')
        if imdb and media.tmdb_id:
            mappings.setdefault(imdb, media.tmdb_id)
    result = []
    for row in rows:
        key = str(row.get('content_id'))
        tmdb_id = mappings.get(key)
        if tmdb_id is None and key.startswith('tmdb:'):
            try:
                tmdb_id = int(key[5:])
            except ValueError:
                pass
        clock = clocks.get((tmdb_id, row.get('content_type')))
        observed = provider_changed_at(row)
        if clock and observed and observed < clock:
            continue
        result.append(row)
    return result


async def media_ids_for_watch_rows(db, mappings, rows):
    """Resolve provider watched-state rows to the corresponding local media IDs."""
    media_rows = [row for row in rows if row.get('content_id') is not None]
    movie_ids = {
        mappings.get(str(row.get('content_id')))
        for row in media_rows if row.get('content_type') == 'movie'
    }
    series_ids = {
        mappings.get(str(row.get('content_id')))
        for row in media_rows if row.get('content_type') == 'series'
    }
    movie_ids.discard(None)
    series_ids.discard(None)
    movies = (await db.execute(select(Media).where(
        Media.tmdb_id.in_(movie_ids), Media.media_type == MediaType.movie,
    ))).scalars().all() if movie_ids else []
    shows = (await db.execute(select(Show).where(Show.tmdb_id.in_(series_ids)))).scalars().all() if series_ids else []
    movie_by_tmdb = {media.tmdb_id: media.id for media in movies}
    show_by_tmdb = {show.tmdb_id: show.id for show in shows}
    show_ids = set(show_by_tmdb.values())
    episodes = (await db.execute(select(Media).where(
        Media.show_id.in_(show_ids), Media.media_type == MediaType.episode,
    ))).scalars().all() if show_ids else []
    episode_by_position = {
        (media.show_id, media.season_number, media.episode_number): media.id
        for media in episodes
    }
    result = {}
    for row in media_rows:
        content_id = str(row.get('content_id'))
        tmdb_id = mappings.get(content_id)
        media_id = None
        if row.get('content_type') == 'movie':
            media_id = movie_by_tmdb.get(tmdb_id)
        elif row.get('content_type') == 'series' and row.get('season') is not None and row.get('episode') is not None:
            try:
                position = (show_by_tmdb.get(tmdb_id), int(row['season']), int(row['episode']))
            except (TypeError, ValueError):
                continue
            media_id = episode_by_position.get(position)
        if media_id is not None:
            result[watch_key(row)] = media_id
    return result


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


async def release_rewatched_deletions(db, conn, watched, progress, mappings):
    """Accept a new provider watch only after every deletion reset was acknowledged."""
    markers = (await db.execute(select(TrackingDeletion).where(
        TrackingDeletion.user_id == conn.user_id))).scalars().all()
    eligible = {marker.media_id: marker for marker in markers if not marker.pending_connections}
    if not eligible:
        return
    ids = {mappings.get(str(row.get('content_id'))) for row in [*watched, *progress]}
    ids.discard(None)
    media = (await db.execute(select(Media).where(Media.tmdb_id.in_(ids),
        Media.media_type.in_([MediaType.movie, MediaType.series])))).scalars().all()
    lookup = {(item.tmdb_id, item.media_type.value): item for item in media}
    released = set()
    for row in [*watched, *progress]:
        item = lookup.get((mappings.get(str(row.get('content_id'))), row.get('content_type')))
        marker = eligible.get(item.id) if item else None
        if not marker:
            continue
        # A remote metadata update cannot turn old watched history into a new
        # watch. Require viewing time, even when the provider also has _mtime.
        seen_at = provider_changed_at({'watched_at': row.get('watched_at'),
            'last_watched': row.get('last_watched')})
        if seen_at and seen_at > marker.deleted_at:
            released.add(item.id)
    if released:
        await db.execute(delete(TrackingDeletion).where(
            TrackingDeletion.user_id == conn.user_id, TrackingDeletion.media_id.in_(released)))
        await db.execute(delete(SyncReview).where(
            SyncReview.user_id == conn.user_id, SyncReview.media_id.in_(released),
            SyncReview.kind == 'outbound_pending'))


def same_playback(left, right):
    return all(left.get(key) == right.get(key) for key in ('position','duration','season','episode'))


def playback_rank(row):
    try:
        position = float(row.get('position') or 0)
        duration = float(row.get('duration') or 0)
        fraction = position / duration if duration > 0 else 0
        return (int(row.get('season') or 0), int(row.get('episode') or 0), fraction, position)
    except (TypeError, ValueError):
        return (0, 0, 0, 0)


async def previous_source_playback(db, entry, media):
    source = entry.status_source or ''
    if ':' not in source:
        return None
    kind, raw_id = source.split(':', 1)
    if kind not in ('stremio','nuvio'):
        return None
    try:
        connection_id = int(raw_id)
    except ValueError:
        return None
    baseline = await db.get(StreamBaseline, connection_id)
    if not baseline:
        return None
    mappings = baseline.snapshot.get('mappings', {})
    rows = [row for key, row in baseline.snapshot.get('progress', {}).items()
        if mappings.get(key) == media.tmdb_id]
    return max(rows, key=playback_rank) if rows else None


async def require_stream_reconciliation(db, conn):
    """Shared by HTTP and background entry points; no first-write bypass."""
    if conn.type not in ('stremio', 'nuvio', 'jellyfin', 'emby', 'plex'):
        return
    baseline = await db.get(StreamBaseline, conn.id)
    if not baseline or not baseline.approved:
        from fastapi import HTTPException
        raise HTTPException(409, 'Run a full import and confirm its summary in Notifications before pushing to this connection')
    unresolved = (await db.execute(select(SyncReview.id).where(SyncReview.user_id == conn.user_id,
        SyncReview.connection_id == conn.id, SyncReview.state == 'pending',
        SyncReview.kind.in_(['conflict','uncertain_removal','deletion_conflict','rating_conflict'])))).first()
    if unresolved:
        from fastapi import HTTPException
        raise HTTPException(409, 'Resolve this connection’s conflicts in Notifications before pushing')


async def apply_series_observation(db, user_id, media, entry, row, finished, newly_watched_ids=None):
    """Advance cumulative regular episodes only with a verified matching order."""
    identities=[]
    if media.tmdb_id:
        identities.append(Show.tmdb_id==media.tmdb_id)
    if media.tvdb_id:
        identities.append(Show.tvdb_id==media.tvdb_id)
    show=(await db.execute(select(Show).where(or_(*identities)))).scalars().first() if identities else None
    if not show:return False
    episodes=(await db.execute(select(Media).where(Media.show_id==show.id,Media.media_type==MediaType.episode,
        Media.season_number>0,Media.release_date.is_not(None),Media.release_date<=date.today().isoformat())
        .order_by(Media.season_number,Media.episode_number))).scalars().all()
    index=next((i for i,e in enumerate(episodes) if (e.season_number,e.episode_number)==(row.get('season'),row.get('episode'))),None)
    if index is None:return False
    through=index+1 if finished else index
    watched=set((await db.execute(select(WatchEvent.media_id).where(WatchEvent.user_id==user_id,
        WatchEvent.media_id.in_([e.id for e in episodes]),WatchEvent.completed.is_(True)))).scalars())
    from core.watch_dates import inferred_watch_datetime, normalize_watch_datetime
    source_watch_at = normalize_watch_datetime(row.get('watched_at') or row.get('last_watched'))
    evidence_watch_at = source_watch_at or inferred_watch_datetime(entry.finish_date)
    for episode in episodes[:through]:
        if episode.id not in watched:
            db.add(WatchEvent(user_id=user_id,media_id=episode.id,completed=True,
                watched_at=evidence_watch_at,
                date_inferred=(source_watch_at is None or not (finished and episode.id==episodes[index].id))))
            watched.add(episode.id)
            if newly_watched_ids is not None:
                newly_watched_ids.add(episode.id)
    entry.progress=len(watched)
    return bool((media.tmdb_data or {}).get('tracking_catalogue_refreshed_at') and episodes and len(watched)==len(episodes))


async def observe_stream_snapshot(
    db, conn, library, watched, progress, tmdb_ids, *, complete=True, touched=None,
    removed_library=None, sync_playback=True, sync_watched=True,
    removed_watched_ids=None,
):
    # Partial/failed pulls cannot establish or advance a trusted baseline.
    # A provider's verified incremental feed must first be materialized into
    # a complete snapshot by its adapter; touched rows alone prove no removal.
    if not complete:
        baseline = await db.get(StreamBaseline, conn.id)
        records = baseline.snapshot.get('records') if baseline else None
        if records is None or touched is None:
            return set()
        touched = {str(key) for key in touched}
        def merge(previous, current):
            return [row for row in previous if str(row.get('content_id')) not in touched] + current
        library = merge(records.get('library', []), library)
        watched = merge(records.get('watched', []), watched)
        progress = merge(records.get('progress', []), progress)
        tmdb_ids = {**baseline.snapshot.get('mappings', {}), **tmdb_ids}
    # Import is additive. A tombstone is released only by a fresh viewing
    # observation after all outbound deletion acknowledgments have arrived.
    await db.execute(select(User.id).where(User.id==conn.user_id).with_for_update())
    prior = await db.get(StreamBaseline,conn.id)
    mappings = {**(prior.snapshot.get('mappings', {}) if prior else {}), **tmdb_ids}
    await release_rewatched_deletions(db, conn, watched, progress, mappings)
    # Streaming-library membership alone never creates a tracked entry.
    existing_ids = set((await db.execute(select(TrackedEntry.media_id).where(TrackedEntry.user_id == conn.user_id))).scalars())
    baseline=await db.get(StreamBaseline,conn.id)
    first=baseline is None
    imported = await import_tracking_history(db,conn.user_id,initial_import=first)
    previous=baseline.snapshot if baseline else {}
    prior_observed_at = baseline.observed_at if baseline else None
    active = _active(progress) if sync_playback else previous.get('progress', {})
    watched_rows = watched if sync_watched else []
    progress_completed_rows = completed_rows(progress) if sync_playback else []
    completed = [
        *watched_rows,
        *progress_completed_rows,
    ]
    library_ids={str(row.get('content_id')) for row in library}
    if first:
        baseline=StreamBaseline(connection_id=conn.id,user_id=conn.user_id,approved=False)
        db.add(baseline)
        db.add(SyncReview(user_id=conn.user_id,connection_id=conn.id,kind='initial_import',message=f'{conn.name}: imported {imported} tracked entries, observed {len(library)} library items and {len(watched)} watch records. Review your lists and any conflicts before approving outbound synchronization. An empty first snapshot removes nothing.'))
    can_propagate_watches = bool(not first and baseline.approved)
    accepted_watch_rows = []
    inferred_watch_ids = set()
    old_active=previous.get('progress',{})
    outbound = dict(previous.get('outbound', {}))
    # A pull-only connection cannot be kept current by AnyList writes, so its
    # missing playback is never authority to drop a local Watching entry.
    removed=set(old_active)-set(active) if not first and sync_playback and conn.push_playback else set()
    pending_writes = (await db.execute(select(StreamAction.payload).where(
        StreamAction.connection_id == conn.id,
        StreamAction.state == 'pending',
        StreamAction.action.in_(('restore', 'upsert')),
    ))).scalars().all() if removed else []
    pending_write_keys = {str(payload.get('content_id')) for payload in pending_writes if payload and payload.get('content_id')}
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
        # Watched-history rows describe past plays, not current Continue
        # Watching membership. Only active resume progress can disagree with
        # an existing Watching status on a newly attached connection.
        for row in active.values():
            media = lookup.get((mappings.get(str(row.get('content_id'))), row.get('content_type')))
            if not media or media.id not in existing_ids:
                continue
            entry = (await db.execute(select(TrackedEntry).where(TrackedEntry.user_id == conn.user_id, TrackedEntry.media_id == media.id))).scalar_one()
            proposed = observed_status(entry.status, False, True)
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
        if completed_progress(old,completed):
            outbound.pop(key, None)
            continue
        # A restore/upsert RPC can succeed while its playback row is not yet
        # visible in a provider snapshot. Until the provider shows the written
        # row, absence cannot distinguish a rejected write from a user removal.
        # Keep the marker through empty snapshots; a later matching/different
        # active row clears it, after which a new removal is actionable.
        if key in outbound or key in pending_write_keys:
            continue
        media=lookup.get((mappings.get(key),old.get('content_type')))
        if not media:continue
        entry=(await db.execute(select(TrackedEntry).where(TrackedEntry.user_id==conn.user_id,TrackedEntry.media_id==media.id))).scalar_one_or_none()
        if not entry or entry.status=='completed':continue
        pending=(await db.execute(select(SyncReview.id).where(SyncReview.user_id==conn.user_id,SyncReview.media_id==media.id,SyncReview.state=='pending',SyncReview.kind.in_(['playback_removed','conflict'])))).first()
        if pending:continue
        # A local edit since the prior source observation has uncertain ordering.
        changed_at=status_changed_at(entry)
        conflict=bool(changed_at and baseline.observed_at and changed_at>baseline.observed_at)
        proposed='dropped' if media.media_type==MediaType.movie else 'paused'
        auto_confirm = bool(preferences and preferences.auto_confirm and not conflict and baseline.approved)
        db.add(SyncReview(user_id=conn.user_id,connection_id=conn.id,media_id=media.id,kind='conflict' if conflict else 'playback_removed',state='confirmed' if auto_confirm else 'pending',
            previous_status=entry.status,proposed_status=proposed,
            message=f'{conn.name}: playback disappeared without a matching completion. '+('A newer local edit was preserved; choose the correct status.' if conflict else f'Marked {proposed}. Review the change or add a rating.')))
        if not conflict:
            prior_status = entry.status
            entry.status=proposed
            if media.media_type==MediaType.movie:
                entry.progress=0
            mark_status_change(entry,f'{conn.type}:{conn.id}')
            from core.stream_actions import queue_dismissals
            await queue_dismissals(db, conn, media)
            if auto_confirm:
                from core.activity import record_daily_activity
                await record_daily_activity(db,user_id=conn.user_id,media_id=media.id,status=proposed,
                    score=effective_score(entry.rating_mode,entry.manual_score,entry.season_scores),
                    status_changed=True,previous_status=prior_status)
    # Only changed observations advance an existing tracked entry. Repeated
    # polling must not restore cleared dates or reinterpret the same playback.
    if not first:
        old_watched = set(previous.get('watched', []))
        old_progress_completed = set(previous.get('progress_completed', []))
        new_watched_rows = [row for row in watched_rows if watch_key(row) not in old_watched]
        new_watched_rows = await current_watch_rows(db, conn, new_watched_rows)
        new_progress_completed_rows = [row for row in progress_completed_rows
            if watch_key(row) not in old_progress_completed]
        new_completed_by_key = {watch_key(row): row for row in new_progress_completed_rows}
        new_completed_by_key.update({watch_key(row): row for row in new_watched_rows})
        new_completed = list(new_completed_by_key.values())
        acknowledged = set()
        if sync_playback:
            for key, row in active.items():
                expected = outbound.get(key)
                if expected is None:
                    continue
                if same_playback(row, expected):
                    acknowledged.add(key)
                # Any provider row is newer evidence than the pending echo. If it
                # differs, reconcile it as an external change rather than keeping
                # an obsolete marker around to suppress a later removal.
                outbound.pop(key, None)
        for key in acknowledged:
            outbound.pop(key, None)
        changed_active = [row for key, row in active.items()
            if sync_playback and key not in acknowledged and not (old_active.get(key) and same_playback(old_active[key], row))]
        deleted = set((await db.execute(select(TrackingDeletion.media_id).where(TrackingDeletion.user_id == conn.user_id))).scalars())
        for row in [*changed_active, *new_completed]:
            media = lookup.get((mappings.get(str(row.get('content_id'))), row.get('content_type')))
            if not media or media.id in deleted:
                continue
            entry = (await db.execute(select(TrackedEntry).where(TrackedEntry.user_id == conn.user_id, TrackedEntry.media_id == media.id))).scalar_one_or_none()
            if not entry:
                continue
            newly_tracked = media.id not in existing_ids
            pending = (await db.execute(select(SyncReview.id).where(SyncReview.user_id == conn.user_id,
                SyncReview.media_id == media.id, SyncReview.state == 'pending', SyncReview.kind.in_(['conflict','playback_removed'])))).first()
            if pending:
                continue
            previous_status = entry.status
            previous_start_date = entry.start_date
            previous_progress = entry.progress
            is_complete = media.media_type == MediaType.movie and row in new_completed
            # A newer local correction takes precedence over inferred history too.
            changed_at=status_changed_at(entry)
            provider_at=provider_changed_at(row)
            competing=bool(media.id in existing_ids and changed_at and baseline.observed_at and changed_at>baseline.observed_at)
            ordering='apply'
            if provider_at and changed_at and provider_at < changed_at:
                ordering='stale'
            elif competing:
                if provider_at and provider_at > changed_at:
                    ordering='apply'
                elif provider_at and provider_at < changed_at:
                    ordering='stale'
                elif provider_at and provider_at == changed_at and entry.status_source != 'local':
                    previous_row=await previous_source_playback(db,entry,media)
                    ordering='apply' if previous_row and playback_rank(row)>playback_rank(previous_row) else 'stale' if previous_row else 'conflict'
                else:
                    ordering='conflict'
            if ordering=='stale':
                continue
            if media.media_type==MediaType.series and ordering=='apply':
                is_complete=await apply_series_observation(
                    db,conn.user_id,media,entry,row,row in new_completed,
                    newly_watched_ids=inferred_watch_ids if sync_watched else None,
                )
            proposed = observed_status(previous_status, is_complete, True)
            if ordering=='conflict':
                db.add(SyncReview(user_id=conn.user_id,connection_id=conn.id,media_id=media.id,kind='conflict',
                    previous_status=previous_status,proposed_status=proposed,
                    message=f'{conn.name}: playback timing cannot be ordered against another recent change. Your current value was preserved.'))
                continue
            if sync_watched and row in new_watched_rows:
                accepted_watch_rows.append(row)
            entry.status = proposed
            if media.media_type == MediaType.movie:
                entry.progress = 1 if proposed == 'completed' else 0
            mark_status_change(entry,f'{conn.type}:{conn.id}',provider_changed_at(row))
            entry.start_date, entry.finish_date = default_dates(previous_status, entry.status, entry.start_date, entry.finish_date, date.today())
            if newly_tracked:
                if entry.status=='watching' and entry.start_date is None:entry.start_date=date.today()
                if entry.status=='completed' and entry.finish_date is None:entry.finish_date=date.today()
            progress_changed = entry.progress != previous_progress
            if entry.status != previous_status or progress_changed or newly_tracked:
                from core.activity import record_daily_activity, record_progress_activity
                activity_score = effective_score(entry.rating_mode,entry.manual_score,entry.season_scores)
                if media.media_type == MediaType.series:
                    await record_progress_activity(db,user_id=conn.user_id,media=media,
                        previous_progress=0 if newly_tracked else previous_progress,progress=entry.progress,
                        status=entry.status,score=activity_score,
                        status_changed=(entry.status != previous_status or newly_tracked) and not (progress_changed or newly_tracked and entry.progress > 0),
                        previous_status=None if newly_tracked else previous_status,
                        first_watching=newly_tracked or (previous_start_date is None and previous_status != 'watching'))
                else:
                    await record_daily_activity(db,user_id=conn.user_id,media_id=media.id,status=entry.status,
                        score=activity_score,status_changed=entry.status != previous_status or newly_tracked,
                        previous_status=None if newly_tracked else previous_status,
                        first_watching=newly_tracked or (previous_start_date is None and previous_status != 'watching'))
            if row in changed_active and baseline.approved:
                from core.stream_actions import queue_progress_update
                await queue_progress_update(db, conn, media, row)
            if entry.status == 'completed' and (entry.status != previous_status or newly_tracked):
                await queue_sync_completion_rating(
                    db, user_id=conn.user_id, media=media, entry=entry, source=conn.type,
                )
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
        'outbound':outbound if not first else {},
        'watched': (sorted({watch_key(row) for row in watched_rows}) if sync_watched
                    else previous.get('watched', [])),
        'progress_completed': (sorted({watch_key(row) for row in progress_completed_rows}) if sync_playback
                               else previous.get('progress_completed', [])),
        'records': {'library':library,
                    'watched':watched if sync_watched else previous.get('records', {}).get('watched', []),
                    'progress':progress if sync_playback else previous.get('records', {}).get('progress', [])}}
    baseline.observed_at=datetime.now(timezone.utc).replace(tzinfo=None)
    propagated_watch_ids = set()
    if can_propagate_watches:
        if sync_watched and conn.push_watched and removed_watched_ids is not None:
            from models.watch_intent import WatchIntent
            old_rows = previous.get('records', {}).get('watched', [])
            now_keys = {watch_key(row) for row in watched_rows}
            missing_rows = [row for row in old_rows if watch_key(row) not in now_keys]
            missing_media = await media_ids_for_watch_rows(db, mappings, missing_rows)
            pending_local = set((await db.execute(select(WatchIntent.media_id).where(
                WatchIntent.connection_id == conn.id,
                WatchIntent.media_id.in_(set(missing_media.values())),
                WatchIntent.state == 'pending',
                WatchIntent.desired_watched.is_(True),
            ))).scalars()) if missing_media else set()
            for media_id in set(missing_media.values()) - pending_local:
                events = (await db.execute(select(WatchEvent).where(
                    WatchEvent.user_id == conn.user_id,
                    WatchEvent.media_id == media_id,
                ))).scalars().all()
                # An edit made after the last source observation is newer than
                # this undated provider absence. Let its queued push win.
                if not prior_observed_at or any(
                    event.created_at is None or event.created_at > prior_observed_at
                    for event in events
                ):
                    continue
                if not any(event.completed for event in events):
                    continue
                for event in events:
                    await db.delete(event)
                removed_watched_ids.add(media_id)
            if removed_watched_ids:
                await db.flush()
                affected_shows = set()
                for media_id in removed_watched_ids:
                    episode = await db.get(Media, media_id)
                    if episode and episode.media_type == MediaType.episode and episode.show_id:
                        affected_shows.add(episode.show_id)
                for show_id in affected_shows:
                    show = await db.get(Show, show_id)
                    series = lookup.get((show.tmdb_id, 'series')) if show else None
                    if not series:
                        continue
                    entry = (await db.execute(select(TrackedEntry).where(
                        TrackedEntry.user_id == conn.user_id,
                        TrackedEntry.media_id == series.id,
                    ))).scalar_one_or_none()
                    if entry:
                        entry.progress = len(set((await db.execute(select(WatchEvent.media_id).join(
                            Media, Media.id == WatchEvent.media_id,
                        ).where(
                            WatchEvent.user_id == conn.user_id,
                            WatchEvent.completed.is_(True),
                            Media.show_id == show_id,
                            Media.season_number > 0,
                        ))).scalars()))
        source_watch_ids = await media_ids_for_watch_rows(db, mappings, accepted_watch_rows)
        propagated_watch_ids = set(source_watch_ids.values()) | inferred_watch_ids
        if propagated_watch_ids:
            # Provider rows must pass ordering/review checks above and still
            # exist in canonical watched state before they reach destinations.
            await db.flush()
            propagated_watch_ids = set((await db.execute(select(WatchEvent.media_id).where(
                WatchEvent.user_id == conn.user_id,
                WatchEvent.media_id.in_(propagated_watch_ids),
                WatchEvent.completed.is_(True),
            ))).scalars())
    await db.commit()
    return propagated_watch_ids
