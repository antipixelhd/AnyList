"""Retryable playback actions; never mutates streaming library membership."""
import logging
from datetime import datetime, timezone
from types import SimpleNamespace
from sqlalchemy import select, update
from fastapi import HTTPException
from models import MediaServerConnection, Media, MediaType, Show
from models.events import WatchEvent
from models.playback_progress import PlaybackProgress
from models.tracking import StreamAction, StreamBaseline, SyncReview, TrackedEntry, TrackingDeletion
from core import stremio, nuvio
from core.status_provenance import provider_changed_at, status_changed_at
from core.deletion_markers import settle_marker_target

logger = logging.getLogger(__name__)


class RemotePlaybackChanged(Exception):
    pass


def same_progress(left, right):
    return all(left.get(key) == right.get(key) for key in ('position', 'duration', 'season', 'episode'))


def _iso_utc(value):
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def _stream_action_enabled(conn, action):
    if action.action == 'reset':
        return conn.push_watched or conn.push_playback
    payload = action.payload or {}
    visibility_only = conn.type == 'nuvio' and (
        action.action == 'dismiss' and (
            payload.get('visibility_only') is True or not conn.push_playback
        )
        or action.action == 'upsert' and payload.get('next_up_only') is True
    )
    if visibility_only:
        return conn.push_watched or conn.push_playback
    return conn.push_playback


def _stremio_last_watched(value):
    """Stremio stores lastWatched as an ISO timestamp; peer snapshots may use epoch ms."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return _iso_utc(value)
    try:
        milliseconds = int(value)
    except (TypeError, ValueError):
        try:
            parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        except (TypeError, ValueError):
            return None
        return _iso_utc(parsed)
    try:
        return _iso_utc(datetime.fromtimestamp(milliseconds / 1000, timezone.utc))
    except (OverflowError, OSError, ValueError):
        return None


async def push_stremio_progress(token, record):
    rows = await stremio.datastore_get(token, ids=[record['content_id']], allow_missing=True)
    if rows:
        item = rows[0]
    else:
        from routers.sync import _stremio_new_library_item
        item = _stremio_new_library_item(record, _iso_utc(datetime.now(timezone.utc)), in_library=False)
    if item.get('type') and record.get('content_type') and item['type'] != record['content_type']:
        raise RemotePlaybackChanged()
    state = dict(item.get('state') or {})
    remote = {
        'position': state.get('timeOffset'), 'duration': state.get('duration'),
        'season': record.get('season'), 'episode': record.get('episode'),
    }
    if record.get('season') is not None:
        video_id = str(state.get('video_id') or '')
        if not video_id.endswith(f":{record['season']}:{record['episode']}"):
            remote['season'] = remote['episode'] = None
    matching_video = (str(state.get('video_id') or '') == record['content_id']
        if record.get('season') is None else remote['season'] is not None)
    matching_progress = same_progress(remote, record) and matching_video
    # Continue Watching is derived from visible library records. Stremio's
    # temporary marker keeps this resume out of the permanent collection, but
    # `removed` must be false for the client to include it in that view.
    eligible = not item.get('removed')
    if matching_progress and eligible:
        return
    observed_at = provider_changed_at({'modified_at': record.get('observed_at')})
    remote_at = provider_changed_at({'modified_at': item.get('_mtime')}) if rows else None
    if not matching_progress and (not observed_at or (remote_at and remote_at > observed_at)):
        raise RemotePlaybackChanged()
    state['timeOffset'] = record['position']
    state['duration'] = record['duration']
    if record.get('season') is not None:
        state['video_id'] = record.get('video_id') or f"{record['content_id']}:{record['season']}:{record['episode']}"
    else:
        state['video_id'] = record.get('video_id') or record['content_id']
    if record.get('last_watched') is not None:
        last_watched = _stremio_last_watched(record['last_watched'])
        if last_watched is not None:
            state['lastWatched'] = last_watched
    candidate = {**item, 'state': state, '_mtime': _iso_utc(datetime.now(timezone.utc))}
    if candidate.get('removed'):
        candidate['removed'] = False
        candidate['temp'] = True
    await stremio.datastore_put(token, [candidate])
    confirmed = await stremio.datastore_get(token, ids=[record['content_id']])
    if len(confirmed) != 1:
        raise stremio.StremioAPIError('Stremio progress write was not visible on readback')
    confirmed_state = confirmed[0].get('state') or {}
    if confirmed[0].get('removed'):
        raise stremio.StremioAPIError('Stremio progress write remained hidden from Continue Watching')
    confirmed_season = record.get('season')
    confirmed_episode = record.get('episode')
    if confirmed_season is not None and not str(confirmed_state.get('video_id') or '').endswith(
        f":{confirmed_season}:{confirmed_episode}"
    ):
        raise stremio.StremioAPIError('Stremio progress write did not match the requested episode')
    if confirmed_season is None and confirmed_state.get('video_id') != record['content_id']:
        raise stremio.StremioAPIError('Stremio progress write did not match the requested movie')
    if confirmed_state.get('timeOffset') != record['position'] or confirmed_state.get('duration') != record['duration']:
        raise stremio.StremioAPIError('Stremio progress write did not persist')


async def push_nuvio_progress(db, conn, record):
    async def refreshed(session):
        from db import AsyncSessionLocal
        from sqlalchemy.orm.attributes import set_committed_value
        async with AsyncSessionLocal() as token_db:
            await token_db.execute(update(MediaServerConnection).where(MediaServerConnection.id == conn.id)
                .values(token=session.refresh_token))
            await token_db.commit()
        set_committed_value(conn, 'token', session.refresh_token)
    async with nuvio.connection_lock(conn.id):
        await db.refresh(conn)
        async with nuvio.httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            session = await nuvio.refresh_session(conn.url, conn.token, client=client)
            await refreshed(session)
            profile = nuvio.parse_profile_id(conn.server_user_id)
            rows = await nuvio._pull_watch_progress(client, conn.url, session.access_token, profile)
            matches = [row for row in rows if row.get('content_id') == record['content_id']
                and row.get('season') == record.get('season') and row.get('episode') == record.get('episode')]
            if len(matches) == 1 and same_progress(matches[0], record):
                await nuvio.update_next_up_dismissals(client, conn.url, session.access_token,
                    profile, show=_nuvio_action_content_ids(record),
                    on_written=_nuvio_visibility_echo_writer(db, conn))
                return
            if matches:
                if len(matches) != 1:
                    raise RemotePlaybackChanged()
                # Nuvio last_watched is viewing evidence, not a universal row
                # modification time. Only an explicit row update time can order
                # a competing resume value; otherwise require review.
                observed_at = provider_changed_at({'updated_at': record.get('observed_at')})
                remote_at = provider_changed_at({'updated_at': matches[0].get('updated_at')})
                if not observed_at or not remote_at or remote_at > observed_at:
                    raise RemotePlaybackChanged()
            payload = {key: value for key, value in record.items() if key in (
                'content_id','content_type','video_id','season','episode','position','duration','last_watched','progress_key')}
            payload.setdefault('progress_key', f"{record['content_id']}_s{record['season']}e{record['episode']}"
                if record.get('season') is not None else record['content_id'])
            await nuvio._rpc(client, conn.url, session.access_token, 'sync_push_watch_progress',
                {'p_profile_id': profile, 'p_entries': [payload]})
            confirmed_rows = await nuvio._pull_watch_progress(client, conn.url, session.access_token, profile)
            confirmed = [row for row in confirmed_rows if row.get('progress_key') == payload['progress_key']]
            if len(confirmed) != 1 or not same_progress(confirmed[0], record):
                raise nuvio.NuvioAPIError('Nuvio progress write was not confirmed by readback')
            await nuvio.update_next_up_dismissals(client, conn.url, session.access_token,
                profile, show=_nuvio_action_content_ids(record),
                on_written=_nuvio_visibility_echo_writer(db, conn))


async def queue_progress_update(db, source, media, record):
    """Coalesce a trustworthy inbound resume delta for every eligible peer."""
    evidence = record
    if source.type == 'nuvio':
        evidence = {key: record.get(key) for key in ('updated_at', 'modified_at')}
    changed_at = provider_changed_at(evidence)
    if changed_at is None:
        return
    # Playback accepted from either Nuvio client must clear the other client's
    # presentation state too, without echoing watch progress back to its source.
    if source.type == 'nuvio' and source.push_playback:
        await _queue_nuvio_next_up_show(db, source.user_id, media, [source])
    targets = (await db.execute(select(MediaServerConnection).where(
        MediaServerConnection.user_id == source.user_id,
        MediaServerConnection.id != source.id,
        MediaServerConnection.type.in_(['stremio', 'nuvio']),
        MediaServerConnection.push_playback.is_(True),
    ))).scalars().all()
    for conn in targets:
        baseline = await db.get(StreamBaseline, conn.id)
        if not baseline or not baseline.approved:
            continue
        mappings = baseline.snapshot.get('mappings', {})
        keys = [key for key, tmdb_id in mappings.items() if tmdb_id == media.tmdb_id]
        if not keys and media.imdb_id:
            keys = [media.imdb_id]
        if not keys:
            continue
        key = next((item for item in keys if item in baseline.snapshot.get('progress', {})), keys[0])
        existing = (await db.execute(select(StreamAction).where(
            StreamAction.connection_id == conn.id, StreamAction.media_id == media.id,
            StreamAction.action == 'upsert', StreamAction.state == 'pending').with_for_update())).scalar_one_or_none()
        payload = {
            **record, 'content_id': key, 'content_type': media.media_type.value,
            'observed_at': _iso_utc(changed_at),
            'source_connection_id': source.id,
        }
        payload.pop('modified_at', None)
        payload.pop('updated_at', None)
        if existing:
            previous_at = provider_changed_at({'updated_at': existing.payload.get('observed_at')})
            if previous_at is None or changed_at >= previous_at:
                existing.payload = payload
                existing.attempts = 0
                existing.last_error = None
        else:
            db.add(StreamAction(user_id=source.user_id, connection_id=conn.id,
                media_id=media.id, action='upsert', payload=payload))


async def dismiss_stremio(token, record, *, restore=False, reset=False):
    rows = await stremio.datastore_get(
        token,
        ids=[record['content_id']],
        allow_missing=reset,
    )
    # A deleted title that is already absent from the provider has no remote
    # playback state left to clear. Treat it as a completed reset rather than
    # failing the queued deletion action on datastore_get's missing-item check.
    if not rows and reset:
        return
    item = rows[0]
    if item.get('type') and record.get('content_type') and item['type']!=record['content_type']:
        raise RemotePlaybackChanged()
    state = dict(item.get('state') or {})
    if reset:
        cleared={**state,'timeOffset':0,'duration':0,'timesWatched':0,'flaggedWatched':0,
            'lastWatched':None,'video_id':None,'watched':None,'timeWatched':0,'overallTimeWatched':0}
        if cleared==state:return
        modified=item.get('_mtime')
        if modified and record.get('deleted_at'):
            try:
                if datetime.fromisoformat(str(modified).replace('Z','+00:00')) > datetime.fromisoformat(record['deleted_at']):
                    raise RemotePlaybackChanged()
            except ValueError:
                raise RemotePlaybackChanged()
        await stremio.datastore_put(token,[{**item,'state':cleared,'_mtime':datetime.now(timezone.utc).isoformat().replace('+00:00','Z')}])
        return
    if restore:
        if state.get('timeOffset'):
            same_episode = record.get('season') is None or str(state.get('video_id') or '').endswith(f":{record['season']}:{record['episode']}")
            if same_episode and state.get('timeOffset') == record.get('position') and state.get('duration') == record.get('duration'):
                return
            raise RemotePlaybackChanged()
        state['timeOffset'] = record['position']
        state['duration'] = record['duration']
        if record.get('season') is not None:
            state['video_id'] = record.get('video_id') or f"{record['content_id']}:{record['season']}:{record['episode']}"
        candidate = {**item, 'state':state, '_mtime':datetime.now(timezone.utc).isoformat().replace('+00:00','Z')}
        # Make a restored resume visible in Continue Watching without turning
        # a temporary progress record into permanent library membership.
        if candidate.get('removed'):
            candidate['removed'] = False
            candidate['temp'] = True
        await stremio.datastore_put(token, [candidate])
        return
    if not state.get('timeOffset'):
        return
    if state.get('timeOffset') != record.get('position') or state.get('duration') != record.get('duration'):
        raise RemotePlaybackChanged()
    if record.get('season') is not None:
        if not str(state.get('video_id') or '').endswith(f":{record['season']}:{record['episode']}"):
            raise RemotePlaybackChanged()
    state['timeOffset'] = 0
    candidate = {**item, 'state': state, '_mtime': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')}
    await stremio.datastore_put(token, [candidate])


async def dismiss_nuvio(db, conn, record, *, restore=False, reset=False, visibility_only=False):
    async def refreshed(session):
        # Refresh tokens rotate. Persist independently of the action's
        # transaction so a later API failure cannot strand the connection.
        from db import AsyncSessionLocal
        from sqlalchemy.orm.attributes import set_committed_value
        async with AsyncSessionLocal() as token_db:
            await token_db.execute(update(MediaServerConnection).where(MediaServerConnection.id == conn.id)
                .values(token=session.refresh_token))
            await token_db.commit()
        set_committed_value(conn, 'token', session.refresh_token)
    async with nuvio.connection_lock(conn.id):
        await db.refresh(conn)
        async with nuvio.httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            session = await nuvio.refresh_session(conn.url, conn.token, client=client)
            await refreshed(session)
            profile = nuvio.parse_profile_id(conn.server_user_id)
            content_ids = _nuvio_action_content_ids(record)
            if visibility_only:
                if not content_ids:
                    raise ValueError('Nuvio visibility action has no content IDs')
                # A removal inferred from this connection already happened on
                # the provider. Persist only the Next Up decision; do not echo
                # the removed resume back through watch-progress RPCs.
                watched = await nuvio._pull_watched_items(client, conn.url, session.access_token, profile)
                content_ids = await _nuvio_visibility_aliases(db, conn, record, watched)
                await nuvio.update_next_up_dismissals(client, conn.url, session.access_token,
                    profile, hide=content_ids, seeds=nuvio.next_up_seeds(watched, aliases=content_ids),
                    on_written=_nuvio_visibility_echo_writer(db, conn))
                return
            rows = await nuvio._pull_watch_progress(client, conn.url, session.access_token, profile)
            watched_rows = []
            if reset:
                if len(rows)>=200:raise RemotePlaybackChanged()
                watched=await nuvio._pull_watched_items(client,conn.url,session.access_token,profile)
                matching_progress=[r for r in rows if r.get('content_id')==record['content_id']]
                matching_watched=[r for r in watched if r.get('content_id')==record['content_id']]
                if any(r.get('content_type')!=record.get('content_type') for r in [*matching_progress,*matching_watched]):raise RemotePlaybackChanged()
                cutoff=datetime.fromisoformat(record['deleted_at']).timestamp()*1000
                for row in [*matching_progress,*matching_watched]:
                    timestamp=row.get('last_watched') or row.get('watched_at')
                    if timestamp:
                        try:
                            stamp=float(timestamp)
                        except (ValueError,TypeError):
                            try:stamp=datetime.fromisoformat(str(timestamp).replace('Z','+00:00')).timestamp()*1000
                            except ValueError:raise RemotePlaybackChanged()
                        if stamp>cutoff:raise RemotePlaybackChanged()
                progress_keys=[r.get('progress_key') for r in matching_progress]
                if any(not key for key in progress_keys):raise RemotePlaybackChanged()
                if progress_keys:await nuvio._rpc(client,conn.url,session.access_token,'sync_delete_watch_progress',{'p_profile_id':profile,'p_keys':progress_keys})
                watched_keys=[{k:r[k] for k in ('content_id','season','episode') if k in r} for r in matching_watched]
                if watched_keys:await nuvio._rpc(client,conn.url,session.access_token,'sync_delete_watched_items',{'p_profile_id':profile,'p_keys':watched_keys})
                await nuvio.update_next_up_dismissals(client, conn.url, session.access_token,
                    profile, hide=[record['content_id']], seeds={},
                    on_written=_nuvio_visibility_echo_writer(db, conn))
                return
            content_ids = _nuvio_action_content_ids(record)
            matches = [r for r in rows if r.get('content_id') == record['content_id']
                and r.get('season') == record.get('season') and r.get('episode') == record.get('episode')]
            if restore:
                await nuvio.update_next_up_dismissals(client, conn.url, session.access_token,
                    profile, show=content_ids,
                    on_written=_nuvio_visibility_echo_writer(db, conn))
                if matches:
                    if len(matches) == 1 and same_progress(matches[0],record):
                        return
                    raise RemotePlaybackChanged()
                if len(rows) >= 200:
                    raise RemotePlaybackChanged()
                payload = {k:v for k,v in record.items() if k in ('content_id','content_type','video_id','season','episode','position','duration','last_watched','progress_key')}
                payload.setdefault('progress_key', f"{record['content_id']}_s{record['season']}e{record['episode']}" if record.get('season') is not None else record['content_id'])
                await nuvio._rpc(client,conn.url,session.access_token,'sync_push_watch_progress',
                    {'p_profile_id':profile,'p_entries':[payload]})
                return

            watched_rows = await nuvio._pull_watched_items(
                client, conn.url, session.access_token, profile,
            )
            if record.get('content_ids') and record.get('tmdb_id') is not None:
                # A remote watched/progress row can expose an identity that
                # was absent from the original snapshot. Resolve those rows
                # against the title and baseline before selecting deletions.
                baseline = await db.get(StreamBaseline, conn.id)
                media = SimpleNamespace(
                    tmdb_id=record.get('tmdb_id'),
                    imdb_id=record.get('imdb_id'),
                    tmdb_data={'external_ids': {'imdb_id': record.get('imdb_id')}}
                        if record.get('imdb_id') else {},
                    media_type=record.get('content_type'),
                )
                from core.nuvio_visibility import provider_content_ids
                resolved = provider_content_ids(media, baseline,
                    records=[*rows, *watched_rows])
                content_ids = list(dict.fromkeys([*content_ids, *resolved]))
            if not content_ids:
                raise RemotePlaybackChanged()
            if len(rows) >= 200:
                # A full progress page can hide a second alias for this title.
                # Do not mark visibility dismissed until the resume snapshot is
                # known to be complete.
                raise RemotePlaybackChanged()

            # A canonical Paused/Dropped decision removes current cloud resume
            # rows, even when the local baseline is missing or stale. Baseline
            # position mismatches alone are not conflicts: only a provider
            # timestamp newer than the local status action proves a real race.
            action_at = provider_changed_at({
                'updated_at': record.get('observed_at') or record.get('status_changed_at'),
            })
            progress_matches = [row for row in rows
                if str(row.get('content_id') or '') in content_ids]
            for row in progress_matches:
                remote_at = provider_changed_at({
                    'updated_at': row.get('updated_at') or row.get('modified_at'),
                    'last_watched': row.get('last_watched') or row.get('watched_at'),
                })
                if action_at and remote_at and remote_at > action_at:
                    raise RemotePlaybackChanged()
            watched_matches = [row for row in watched_rows
                if str(row.get('content_id') or '') in content_ids]
            for row in watched_matches:
                remote_at = provider_changed_at({
                    'updated_at': row.get('updated_at') or row.get('modified_at'),
                    'last_watched': row.get('last_watched') or row.get('watched_at'),
                })
                if action_at and remote_at and remote_at > action_at:
                    raise RemotePlaybackChanged()

            progress_keys = [row.get('progress_key') for row in progress_matches]
            if any(not key for key in progress_keys):
                raise RemotePlaybackChanged()
            if progress_keys:
                await nuvio._rpc(client, conn.url, session.access_token, 'sync_delete_watch_progress',
                    {'p_profile_id': profile, 'p_keys': progress_keys})
                confirmed_rows = await nuvio._pull_watch_progress(
                    client, conn.url, session.access_token, profile,
                )
                if any(str(row.get('content_id') or '') in content_ids for row in confirmed_rows):
                    raise nuvio.NuvioAPIError('Nuvio progress deletion was not confirmed by readback')
            await _clear_nuvio_baseline_progress(db, conn.id,
                [{'content_id': content_id} for content_id in content_ids], all_for_ids=True)

            # Keep watched_items intact: only generated Next Up visibility and
            # active Resume/In Progress rows are removed for each known alias.
            await nuvio.update_next_up_dismissals(client, conn.url, session.access_token,
                profile, hide=content_ids, seeds=nuvio.next_up_seeds(watched_rows, aliases=content_ids),
                on_written=_nuvio_visibility_echo_writer(db, conn))


def _nuvio_action_content_ids(record):
    ids = []
    for value in [*(record.get('content_ids') or []), record.get('content_id')]:
        key = str(value or '').strip()
        if key and key not in ids:
            ids.append(key)
    return ids


def _nuvio_visibility_echo_writer(db, conn):
    async def record(written):
        from core.nuvio_visibility import record_visibility_echo
        await record_visibility_echo(db, conn, written)
    return record


async def _nuvio_visibility_aliases(db, conn, record, records):
    from core.nuvio_visibility import provider_content_ids
    baseline = await db.get(StreamBaseline, conn.id)
    media = SimpleNamespace(tmdb_id=record.get('tmdb_id'), imdb_id=record.get('imdb_id'),
        tmdb_data={}, media_type=record.get('content_type'))
    return list(dict.fromkeys([*_nuvio_action_content_ids(record),
        *provider_content_ids(media, baseline, records=records)]))


async def show_nuvio_next_up(db, conn, record):
    """Clear Next Up dismissals for a Watching show without inventing a resume."""
    async def refreshed(session):
        from db import AsyncSessionLocal
        from sqlalchemy.orm.attributes import set_committed_value
        async with AsyncSessionLocal() as token_db:
            await token_db.execute(update(MediaServerConnection).where(
                MediaServerConnection.id == conn.id).values(token=session.refresh_token))
            await token_db.commit()
        set_committed_value(conn, 'token', session.refresh_token)

    content_ids = _nuvio_action_content_ids(record)
    if not content_ids:
        raise ValueError('Nuvio visibility action has no content IDs')
    async with nuvio.connection_lock(conn.id):
        await db.refresh(conn)
        async with nuvio.httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            session = await nuvio.refresh_session(conn.url, conn.token, client=client)
            await refreshed(session)
            rows = await nuvio._pull_watch_progress(client, conn.url, session.access_token,
                nuvio.parse_profile_id(conn.server_user_id))
            content_ids = await _nuvio_visibility_aliases(db, conn, record, rows)
            matching = [row for row in rows if str(row.get('content_id') or '') in content_ids]
            from routers.sync import _nuvio_progress_keys_to_clear
            clear_keys = await _nuvio_progress_keys_to_clear(db, conn.user_id, conn.id, matching)
            cleared_rows = [row for row in matching
                if str(row.get('progress_key') or '') in set(clear_keys)]
            if clear_keys:
                profile = nuvio.parse_profile_id(conn.server_user_id)
                await nuvio._rpc(client, conn.url, session.access_token,
                    'sync_delete_watch_progress', {'p_profile_id': profile, 'p_keys': clear_keys})
                confirmed_rows = await nuvio._pull_watch_progress(client, conn.url,
                    session.access_token, profile)
                if any(str(row.get('progress_key') or '') in set(clear_keys) for row in confirmed_rows):
                    raise nuvio.NuvioAPIError('Nuvio synthetic resume removal was not confirmed by readback')
                await _clear_nuvio_baseline_progress(db, conn.id, cleared_rows)
            await nuvio.update_next_up_dismissals(client, conn.url, session.access_token,
                nuvio.parse_profile_id(conn.server_user_id), show=content_ids,
                on_written=_nuvio_visibility_echo_writer(db, conn))
            return cleared_rows


async def _clear_nuvio_baseline_progress(db, connection_id, removed_rows, *, all_for_ids=False):
    """Forget only Nuvio resumes confirmed absent from the remote profile."""
    removed_ids = {str(row.get('content_id') or '') for row in removed_rows
        if row.get('content_id')}
    removed_keys = {str(row.get('progress_key') or '') for row in removed_rows
        if row.get('progress_key')}
    if not removed_ids and not removed_keys:
        return
    baseline = await db.get(StreamBaseline, connection_id)
    if not baseline:
        return
    snapshot = dict(baseline.snapshot or {})
    progress = dict(snapshot.get('progress', {}))
    for key, value in list(progress.items()):
        content_id = str(value.get('content_id') or key)
        if ((all_for_ids and content_id in removed_ids)
                or str(value.get('progress_key') or '') in removed_keys):
            progress.pop(key, None)
    snapshot['progress'] = progress
    resume = dict(snapshot.get('resume', {}))
    if all_for_ids:
        for key in removed_ids:
            resume.pop(key, None)
    else:
        for key, value in list(resume.items()):
            if str(value.get('progress_key') or '') in removed_keys:
                resume.pop(key, None)
    snapshot['resume'] = resume
    outbound = dict(snapshot.get('outbound', {}))
    for key in removed_ids:
        if all_for_ids or str(outbound.get(key, {}).get('progress_key') or '') in removed_keys:
            outbound.pop(key, None)
    snapshot['outbound'] = outbound
    records = dict(snapshot.get('records', {}))
    records['progress'] = [row for row in records.get('progress', [])
        if not ((all_for_ids and str(row.get('content_id') or '') in removed_ids)
            or str(row.get('progress_key') or '') in removed_keys)]
    snapshot['records'] = records
    baseline.snapshot = snapshot


async def _queue_dismissals(db, user_id, media, *, exclude_connection_id=None,
        source_visibility_connection_id=None):
    filters = [
        MediaServerConnection.user_id == user_id,
        MediaServerConnection.type.in_(['stremio', 'nuvio']),
    ]
    if exclude_connection_id is not None:
        filters.append(MediaServerConnection.id != exclude_connection_id)
    targets = (await db.execute(select(MediaServerConnection).where(*filters))).scalars().all()
    for conn in targets:
        if conn.type == 'stremio' and not conn.push_playback:
            continue
        if conn.type == 'nuvio' and not (conn.push_watched or conn.push_playback):
            continue
        baseline = await db.get(StreamBaseline, conn.id)
        if not baseline:
            continue  # a newly attached empty account has no playback to dismiss
        if conn.type == 'nuvio':
            from core.nuvio_visibility import provider_content_ids
            from routers.sync import _nuvio_imdb_id
            snapshot = baseline.snapshot or {}
            cached_records = [
                *snapshot.get('records', {}).get('progress', []),
                *snapshot.get('records', {}).get('watched', []),
            ]
            content_ids = provider_content_ids(media, baseline, records=cached_records)
            if not content_ids:
                continue
            from core.watch_intents import _content_id_for_connection
            primary = _content_id_for_connection(conn, baseline, media, None)
            if primary not in content_ids:
                primary = content_ids[0]
            entry = (await db.execute(select(TrackedEntry).where(
                TrackedEntry.user_id == user_id, TrackedEntry.media_id == media.id,
            ))).scalar_one_or_none()
            changed_at = status_changed_at(entry) if entry else None
            payload = {
                'content_id': primary,
                'content_ids': content_ids,
                'content_type': media.media_type.value,
                'tmdb_id': media.tmdb_id,
                'imdb_id': _nuvio_imdb_id(media),
                'observed_at': _iso_utc(changed_at),
            }
            if conn.id == source_visibility_connection_id or not conn.push_playback:
                payload['visibility_only'] = True
            pending = (await db.execute(select(StreamAction).where(
                StreamAction.connection_id == conn.id, StreamAction.media_id == media.id,
                StreamAction.state == 'pending', StreamAction.action == 'dismiss',
            ))).scalar_one_or_none()
            if pending:
                pending.payload = payload
                pending.attempts = 0
                pending.last_error = None
            else:
                db.add(StreamAction(user_id=user_id, connection_id=conn.id, media_id=media.id,
                    action='dismiss', payload=payload))
            continue
        has_resume = any(baseline.snapshot.get('mappings', {}).get(key) == media.tmdb_id
            and row.get('content_type') == media.media_type.value
            for key, row in baseline.snapshot.get('progress', {}).items())
        for key, record in baseline.snapshot.get('progress', {}).items():
            if baseline.snapshot.get('mappings', {}).get(key) != media.tmdb_id or record.get('content_type') != media.media_type.value:
                continue
            pending = (await db.execute(select(StreamAction.id).where(StreamAction.connection_id == conn.id,
                StreamAction.media_id == media.id, StreamAction.state == 'pending', StreamAction.action == 'dismiss'))).first()
            if not pending:
                db.add(StreamAction(user_id=user_id,connection_id=conn.id,media_id=media.id,
                    action='dismiss',payload=dict(record)))


async def queue_dismissals(db, source, media):
    """Mirror an inferred removal, projecting source Nuvio visibility only."""
    if source.type == 'nuvio':
        await _queue_dismissals(db, source.user_id, media,
            source_visibility_connection_id=source.id)
    else:
        await _queue_dismissals(db, source.user_id, media, exclude_connection_id=source.id)


async def queue_local_dismissals(db, user_id, media):
    """An explicit local status change wins over every connected stream account."""
    await _queue_dismissals(db, user_id, media)


async def _queue_nuvio_next_up_show(db, user_id, media, connections):
    """Queue a settings-only Next Up show when no playback resume is available."""
    if media.media_type != MediaType.series:
        return
    from core.nuvio_visibility import provider_content_ids
    from core.watch_intents import _content_id_for_connection
    from routers.sync import _nuvio_imdb_id

    for conn in connections:
        if conn.type != 'nuvio':
            continue
        baseline = await db.get(StreamBaseline, conn.id)
        if not baseline:
            continue
        snapshot = baseline.snapshot or {}
        cached_records = [
            *snapshot.get('records', {}).get('progress', []),
            *snapshot.get('records', {}).get('watched', []),
        ]
        content_ids = provider_content_ids(media, baseline, records=cached_records)
        if not content_ids:
            continue
        primary = _content_id_for_connection(conn, baseline, media, None)
        if primary not in content_ids:
            primary = content_ids[0]
        payload = {
            'content_id': primary,
            'content_ids': content_ids,
            'content_type': 'series',
            'tmdb_id': media.tmdb_id,
            'imdb_id': _nuvio_imdb_id(media),
            'next_up_only': True,
        }
        existing = (await db.execute(select(StreamAction).where(
            StreamAction.connection_id == conn.id, StreamAction.media_id == media.id,
            StreamAction.action == 'upsert', StreamAction.state == 'pending',
        ).with_for_update())).scalar_one_or_none()
        if existing:
            existing.payload = payload
            existing.attempts = 0
            existing.last_error = None
        else:
            db.add(StreamAction(user_id=user_id, connection_id=conn.id,
                media_id=media.id, action='upsert', payload=payload))


async def queue_restorations(db, user_id, media):
    """Queue a fresh resume for eligible providers when a title enters Watching."""
    pending=(await db.execute(select(StreamAction).where(StreamAction.user_id==user_id,
        StreamAction.media_id==media.id,StreamAction.state=='pending',StreamAction.action=='restore'))).scalars().all()
    for action in pending:
        action.state='cancelled'
        action.payload={}
        action.last_error=None
    entry=(await db.execute(select(TrackedEntry).where(
        TrackedEntry.user_id==user_id,TrackedEntry.media_id==media.id))).scalar_one_or_none()
    if not entry or entry.status!='watching':
        return

    targets=(await db.execute(select(MediaServerConnection).where(MediaServerConnection.user_id==user_id,
        MediaServerConnection.type.in_(['stremio','nuvio'])))).scalars().all()
    targets = [conn for conn in targets if conn.push_playback
        or conn.type == 'nuvio' and conn.push_watched]
    # A local return to Watching supersedes stale hidden-state work even if the
    # catalogue cannot currently supply a safe resume position.
    stale_dismissals=(await db.execute(select(StreamAction).where(
        StreamAction.user_id==user_id, StreamAction.media_id==media.id,
        StreamAction.action=='dismiss', StreamAction.state=='pending'))).scalars().all()
    target_ids={conn.id for conn in targets}
    for action in stale_dismissals:
        if action.connection_id in target_ids:
            action.state='cancelled';action.payload={};action.last_error=None

    observed_at=datetime.now(timezone.utc)
    record=await _local_resume_record(db,user_id,media,entry,observed_at)
    if record is None:
        await _queue_nuvio_next_up_show(db, user_id, media, targets)
        return
    from models.users import UserSettings
    from routers.sync import _ensure_nuvio_imdb_ids, _get_effective_tmdb_key, _nuvio_imdb_id
    show = await db.get(Show, record['episode_media'].show_id) if media.media_type == MediaType.series else None
    settings = (await db.execute(select(UserSettings).where(UserSettings.user_id == user_id))).scalar_one_or_none()
    api_key = await _get_effective_tmdb_key(db, settings)
    await _ensure_nuvio_imdb_ids([record['episode_media']], {show.id: show} if show else {}, api_key)
    resolved_imdb_id = _nuvio_imdb_id(show or media)
    for conn in targets:
        if conn.type == 'nuvio' and not conn.push_playback:
            await _queue_nuvio_next_up_show(db, user_id, media, [conn])
            continue
        baseline=await db.get(StreamBaseline,conn.id)
        if not baseline or not baseline.approved:
            continue
        snapshot=dict(baseline.snapshot or {})
        mappings=snapshot.get('mappings',{})
        keys=[key for key,tmdb_id in mappings.items() if media.tmdb_id is not None and tmdb_id==media.tmdb_id]
        if not keys and resolved_imdb_id:
            keys=[resolved_imdb_id]
        if not keys:
            continue
        content_id=next((key for key in keys if key in snapshot.get('progress',{})),keys[0])
        if media.media_type==MediaType.series:
            episode=record['episode_media']
            season=episode.season_number
            episode_number=episode.episode_number
            progress_key=f"{content_id}_s{season}e{episode_number}"
            video_id=f"{content_id}:{season}:{episode_number}"
            content_type='series'
        else:
            progress_key=content_id
            video_id=content_id
            season=episode_number=None
            content_type='movie'
        provider_time=observed_at
        action_record={
            'content_id':content_id,'content_type':content_type,'video_id':video_id,
            'title':media.title,
            'season':season,'episode':episode_number,
            'position':record['position'],'duration':record['duration'],
            'observed_at':_iso_utc(observed_at),
            'last_watched':(_iso_utc(provider_time) if conn.type=='stremio'
                else int(provider_time.timestamp()*1000)),
            'progress_key':progress_key,
        }
        if conn.type == 'nuvio' and record.get('synthetic_resume'):
            action_record['synthetic_resume'] = True
        if (conn.type == 'nuvio' and media.media_type == MediaType.series
                and record.get('has_watched_history') and record.get('synthetic_resume')):
            # Nuvio can surface a watched series through Next Up without a
            # fabricated episode position. Keep this as a durable upsert
            # action so failed visibility writes are retried by the worker.
            action_record = {
                'content_id': content_id,
                'content_type': 'series',
                'next_up_only': True,
            }
        existing=(await db.execute(select(StreamAction).where(
            StreamAction.connection_id==conn.id,StreamAction.media_id==media.id,
            StreamAction.action=='upsert',StreamAction.state=='pending').with_for_update())).scalar_one_or_none()
        if existing:
            existing.payload=action_record
            existing.attempts=0
            existing.last_error=None
        else:
            db.add(StreamAction(user_id=user_id,connection_id=conn.id,media_id=media.id,
                action='upsert',payload=action_record))
        resume=dict(snapshot.get('resume',{}))
        stale_keys=[key for key in resume if mappings.get(key)==media.tmdb_id]
        if stale_keys:
            for key in stale_keys:
                resume.pop(key,None)
            snapshot['resume']=resume
            baseline.snapshot=snapshot


async def _local_resume_record(db, user_id, media, entry, observed_at):
    """Build local playback in provider units, marking a synthetic fallback."""
    target=media
    has_watched_history=False
    if media.media_type==MediaType.series:
        show_ids=(await db.execute(select(Show.id).where(Show.tmdb_id==media.tmdb_id))).scalars().all()
        if not show_ids:
            return None
        episodes=(await db.execute(select(Media).where(
            Media.show_id.in_(show_ids),Media.media_type==MediaType.episode,
        ).order_by(Media.season_number,Media.episode_number,Media.id))).scalars().all()
        today=datetime.now(timezone.utc).date().isoformat()
        released=[episode for episode in episodes if not episode.release_date or episode.release_date<=today]
        if not released:
            return None
        watched=(await db.execute(select(WatchEvent.media_id).where(
            WatchEvent.user_id==user_id,WatchEvent.completed.is_(True),
            WatchEvent.media_id.in_([episode.id for episode in released]),
        ))).scalars().all()
        watched_ids=set(watched)
        has_watched_history=bool(watched_ids)
        target=next((episode for episode in released if episode.id not in watched_ids),released[0])

    progress=(await db.execute(select(PlaybackProgress).where(
        PlaybackProgress.user_id==user_id,PlaybackProgress.media_id==target.id,
    ))).scalar_one_or_none()
    if progress and progress.updated_at:
        updated_at=progress.updated_at
        if updated_at.tzinfo is None:
            updated_at=updated_at.replace(tzinfo=timezone.utc)
        cutoff=entry.status_changed_at or observed_at
        if cutoff.tzinfo is None:
            cutoff=cutoff.replace(tzinfo=timezone.utc)
        fresh=updated_at>=cutoff
    else:
        fresh=False

    if fresh and progress:
        try:
            position_ms=round(max(0.0,float(progress.progress_seconds))*1000)
            percent=float(progress.progress_percent)
        except (TypeError,ValueError):
            position_ms=0;percent=0
    else:
        position_ms=1000
        percent=0.01
    synthetic_resume = not (fresh and progress and position_ms > 0 and percent > 0)
    if position_ms<=0 or percent<=0:
        position_ms=1000
        percent=0.01
        synthetic_resume=True
    if target.runtime and target.runtime>0:
        duration_ms=int(target.runtime)*60_000
    else:
        duration_ms=round(position_ms/max(min(percent,1.0),0.01))
    duration_ms=max(position_ms,duration_ms)
    return {'position':position_ms,'duration':duration_ms,'episode_media':target,
        'synthetic_resume':synthetic_resume,'has_watched_history':has_watched_history}


async def queue_resets(db,user_id,media,deleted_at):
    targets=(await db.execute(select(MediaServerConnection).where(MediaServerConnection.user_id==user_id,
        MediaServerConnection.type.in_(['stremio','nuvio']),
        (MediaServerConnection.push_watched.is_(True) | MediaServerConnection.push_playback.is_(True))))).scalars().all()
    queued = []
    for conn in targets:
        baseline=await db.get(StreamBaseline,conn.id)
        keys={key for key,tmdb_id in (baseline.snapshot.get('mappings',{}) if baseline else {}).items() if tmdb_id==media.tmdb_id}
        if not keys and media.imdb_id:keys={media.imdb_id}
        if not keys:
            continue
        queued.append(f'connection:{conn.id}')
        for key in keys:
            db.add(StreamAction(user_id=user_id,connection_id=conn.id,media_id=media.id,action='reset',
                payload={'content_id':key,'content_type':media.media_type.value,'deleted_at':deleted_at.replace(tzinfo=timezone.utc).isoformat()}))
    return queued


async def dispatch_stream_actions(db, user_id):
    from core.pull_cycle import is_active
    if is_active(user_id):
        return
    from core.tracking_snapshot import require_stream_reconciliation
    from models import User
    await db.execute(select(User.id).where(User.id == user_id).with_for_update())
    await cleanup_disabled_stream_actions(db, user_id)
    actions = (await db.execute(select(StreamAction).where(StreamAction.user_id == user_id,
        StreamAction.state == 'pending').order_by(StreamAction.id).limit(100).with_for_update(skip_locked=True))).scalars().all()
    for action in actions:
        if action.action == 'restore':
            # Legacy queued restores contain the old provider position. A
            # transition back to Watching now uses fresh local playback or a
            # synthetic one-second resume from the full-push projection.
            action.state = 'cancelled'
            action.payload = {}
            action.last_error = None
            continue
        conn = await db.get(MediaServerConnection, action.connection_id)
        if not conn or action.action not in ('dismiss','restore','reset','upsert'):
            continue
        enabled = _stream_action_enabled(conn, action)
        if not enabled:
            action.state = 'cancelled'
            action.payload = {}
            action.last_error = None
            if action.action == 'reset':
                await settle_marker_target(db, user_id, action.media_id, f'connection:{conn.id}')
            continue
        entry = (await db.execute(select(TrackedEntry).where(TrackedEntry.user_id == user_id, TrackedEntry.media_id == action.media_id))).scalar_one_or_none()
        deleted = (await db.execute(select(TrackingDeletion).where(TrackingDeletion.user_id == user_id, TrackingDeletion.media_id == action.media_id))).scalar_one_or_none()
        valid_statuses = ('watching',) if action.action in ('restore','upsert') else ('planning','paused','dropped','completed')
        invalid=(not deleted or entry is not None) if action.action=='reset' else (deleted or not entry or entry.status not in valid_statuses)
        if invalid:
            action.state = 'cancelled'; action.payload = {}
            continue
        media = await db.get(Media, action.media_id)
        if not media and action.action != 'reset':
            action.state = 'cancelled'; action.payload = {}
            action.last_error = None
            continue
        try:
            await require_stream_reconciliation(db, conn)
        except HTTPException:
            continue
        action.attempts += 1
        try:
            payload = dict(action.payload or {})
            visibility_only = (conn.type == 'nuvio' and (
                action.action == 'dismiss' and (
                    payload.get('visibility_only') is True or not conn.push_playback
                )
                or action.action == 'upsert' and payload.get('next_up_only') is True
            ))
            if conn.type == 'nuvio' and action.action != 'reset':
                if not media:
                    raise RemotePlaybackChanged()
                baseline_for_aliases = await db.get(StreamBaseline, conn.id)
                from core.nuvio_visibility import provider_content_ids
                from routers.sync import _nuvio_imdb_id
                aliases = provider_content_ids(media, baseline_for_aliases,
                    records=(baseline_for_aliases.snapshot or {}).get('records', {}).get('progress', [])
                        + (baseline_for_aliases.snapshot or {}).get('records', {}).get('watched', [])
                        if baseline_for_aliases else [])
                payload['content_ids'] = list(dict.fromkeys([
                    *(payload.get('content_ids') or []), *aliases, payload.get('content_id'),
                ]))
                payload['tmdb_id'] = media.tmdb_id
                payload['imdb_id'] = _nuvio_imdb_id(media)
            if conn.type == 'stremio':
                # Share one per-connection lock with library pushes, provider
                # pulls, and clear operations so datastore read/modify/write
                # actions cannot race those snapshots.
                async with stremio.connection_lock(conn.id):
                    if action.action == 'reset':await dismiss_stremio(conn.token, payload, reset=True)
                    elif action.action == 'restore':await dismiss_stremio(conn.token, payload, restore=True)
                    elif action.action == 'upsert':await push_stremio_progress(conn.token, payload)
                    else:await dismiss_stremio(conn.token, payload)
            elif conn.type == 'nuvio':
                if action.action == 'reset':await dismiss_nuvio(db, conn, payload, reset=True)
                elif action.action == 'restore':await dismiss_nuvio(db, conn, payload, restore=True)
                elif action.action == 'upsert' and visibility_only:
                    await show_nuvio_next_up(db, conn, payload)
                elif action.action == 'upsert':await push_nuvio_progress(db, conn, payload)
                elif action.action == 'dismiss':
                    await dismiss_nuvio(db, conn, payload, visibility_only=visibility_only)
                else:continue
            else:
                continue
            action.state = 'applied'; action.last_error = None
            # Visibility-only Watching actions do not represent watch progress.
            # show_nuvio_next_up updates only the exact synthetic row it proves
            # absent; leave every genuine current resume in the baseline.
            if not visibility_only:
                baseline = await db.get(StreamBaseline, conn.id)
                snapshot = dict(baseline.snapshot)
                content_ids = (_nuvio_action_content_ids(payload)
                    if conn.type == 'nuvio' and action.action in ('dismiss', 'reset')
                    else [payload['content_id']])
                key = payload['content_id']
                snapshot['progress'] = {k:v for k,v in snapshot.get('progress', {}).items()
                    if str(k) not in content_ids and str(v.get('content_id') or '') not in content_ids}
                snapshot['resume'] = {**snapshot.get('resume',{})}
                if action.action in ('dismiss','reset'):
                    for content_id in content_ids:
                        snapshot['resume'].pop(content_id,None)
                if action.action in ('restore','upsert'):
                    snapshot['progress'][key]=dict(action.payload)
                    snapshot['resume'].pop(key,None)
                snapshot['outbound'] = {**snapshot.get('outbound', {})}
                if action.action in ('restore', 'upsert'):
                    snapshot['outbound'][key] = {field: payload.get(field)
                        for field in ('position','duration','season','episode','progress_key',
                            'observed_at','last_watched')}
                    snapshot['outbound'][key]['action'] = action.action
                    if payload.get('synthetic_resume') is True:
                        snapshot['outbound'][key]['synthetic_resume'] = True
                elif action.action in ('dismiss','reset'):
                    for content_id in content_ids:
                        snapshot['outbound'].pop(content_id, None)
                records = dict(snapshot.get('records', {}))
                records['progress'] = [r for r in records.get('progress', [])
                    if str(r.get('content_id') or '') not in content_ids]
                if action.action in ('restore','upsert'):
                    records['progress'].append(dict(payload))
                snapshot['records'] = records
                baseline.snapshot = snapshot
            action.payload = {}
            if action.action=='reset' and deleted:
                await db.flush()
                remaining=(await db.execute(select(StreamAction.id).where(StreamAction.connection_id==conn.id,
                    StreamAction.media_id==action.media_id,StreamAction.action=='reset',StreamAction.state!='applied'))).first()
                if not remaining:deleted.pending_connections=[p for p in deleted.pending_connections if p!=f'connection:{conn.id}']
                if not deleted.pending_connections:
                    reviews=(await db.execute(select(SyncReview).where(SyncReview.user_id==user_id,SyncReview.media_id==action.media_id,SyncReview.kind=='outbound_pending'))).scalars()
                    for review in reviews:review.state='confirmed'
        except RemotePlaybackChanged:
            action.state = 'conflict'; action.last_error = 'Playback changed since the last import'
            db.add(SyncReview(user_id=user_id,connection_id=conn.id,media_id=action.media_id,kind='deletion_conflict' if action.action=='reset' else 'conflict',
                previous_status=entry.status if entry else None,proposed_status='watching',message=(
                    'Newer remote viewing prevented deletion. Retry deletion explicitly, or keep the remote history. Your local entry remains deleted.'
                    if action.action=='reset' else 'Playback changed on a connected account. The queued action was not applied; review the newer activity.')))
        except Exception as error:
            action.last_error = type(error).__name__  # never persist tokens/remote bodies
            logger.warning(
                'Stream action delivery failed connection=%s media=%s action=%s error=%s attempt=%s',
                conn.id, action.media_id, action.action, action.last_error, action.attempts,
            )
    await db.commit()


async def cleanup_disabled_stream_actions(db, user_id):
    """Cancel pending stream work that is no longer enabled, including legacy rows.

    Safe to run repeatedly: it only changes pending/conflict actions whose
    action-specific push flags are disabled, and only removes their matching
    reset target from a deletion marker. Empty deletion markers remain as
    tombstones so an old provider snapshot cannot resurrect local tracking.
    """
    connections = (await db.execute(select(MediaServerConnection).where(
        MediaServerConnection.user_id == user_id,
        MediaServerConnection.type.in_(('stremio', 'nuvio')),
    ))).scalars().all()
    by_id = {conn.id: conn for conn in connections}
    actions = (await db.execute(select(StreamAction).where(
        StreamAction.user_id == user_id,
        StreamAction.connection_id.in_(by_id) if by_id else False,
        StreamAction.state.in_(('pending', 'conflict')),
    ).order_by(StreamAction.id))).scalars().all()
    for action in actions:
        conn = by_id.get(action.connection_id)
        if not conn:
            continue
        enabled = _stream_action_enabled(conn, action)
        if enabled:
            continue
        action.state = 'cancelled'
        action.payload = {}
        action.last_error = None
        if action.action == 'reset':
            await settle_marker_target(db, user_id, action.media_id, f'connection:{conn.id}')
        await _correct_disabled_action_review(db, action)

    # Older deletions recorded every push-enabled connection even when no
    # content key existed, leaving a marker key with no StreamAction to cancel.
    markers = (await db.execute(select(TrackingDeletion).where(
        TrackingDeletion.user_id == user_id,
    ))).scalars().all()
    for marker in markers:
        for target in marker.pending_connections or []:
            if not target.startswith('connection:'):
                continue
            try:
                connection_id = int(target.split(':', 1)[1])
            except ValueError:
                continue
            conn = by_id.get(connection_id)
            if not conn or not (conn.push_watched or conn.push_playback):
                await settle_marker_target(db, user_id, marker.media_id, target)


async def _correct_disabled_action_review(db, action):
    """Retire a conflict review after its disabled outbound action is cancelled."""
    await db.flush()
    live = (await db.execute(select(StreamAction.id).where(
        StreamAction.user_id == action.user_id,
        StreamAction.connection_id == action.connection_id,
        StreamAction.media_id == action.media_id,
        StreamAction.action == action.action,
        StreamAction.state.in_(('pending', 'conflict')),
    ).limit(1))).first()
    if live:
        return
    kind = 'deletion_conflict' if action.action == 'reset' else 'conflict'
    reviews = (await db.execute(select(SyncReview).where(
        SyncReview.user_id == action.user_id,
        SyncReview.connection_id == action.connection_id,
        SyncReview.media_id == action.media_id,
        SyncReview.kind == kind,
        SyncReview.state == 'pending',
    ))).scalars()
    for review in reviews:
        review.state = 'corrected'
