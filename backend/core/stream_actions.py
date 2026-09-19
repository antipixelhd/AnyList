"""Retryable playback actions; never mutates streaming library membership."""
from datetime import datetime, timezone
from sqlalchemy import select, update
from fastapi import HTTPException
from models import MediaServerConnection
from models.tracking import StreamAction, StreamBaseline, SyncReview, TrackedEntry, TrackingDeletion
from core import stremio, nuvio


class RemotePlaybackChanged(Exception):
    pass


def same_progress(left, right):
    return all(left.get(key) == right.get(key) for key in ('position', 'duration', 'season', 'episode'))


async def dismiss_stremio(token, record, *, restore=False, reset=False):
    rows = await stremio.datastore_get(token, ids=[record['content_id']])
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
        # A temporary non-library entry can participate in Continue Watching.
        if candidate.get('removed'):
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


async def dismiss_nuvio(db, conn, record, *, restore=False, reset=False):
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
            rows = await nuvio._pull_watch_progress(client, conn.url, session.access_token, profile)
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
                return
            matches = [r for r in rows if r.get('content_id') == record['content_id']
                and r.get('season') == record.get('season') and r.get('episode') == record.get('episode')]
            if restore:
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
            if not matches:
                if len(rows) >= 200:
                    raise RemotePlaybackChanged()
                return
            if len(matches) != 1 or not same_progress(matches[0], record):
                raise RemotePlaybackChanged()
            key = matches[0].get('progress_key')
            if not key:
                raise RemotePlaybackChanged()
            await nuvio._rpc(client, conn.url, session.access_token, 'sync_delete_watch_progress',
                {'p_profile_id': profile, 'p_keys': [key]})


async def _queue_dismissals(db, user_id, media, *, exclude_connection_id=None):
    filters = [
        MediaServerConnection.user_id == user_id,
        MediaServerConnection.type.in_(['stremio', 'nuvio']),
        MediaServerConnection.push_playback.is_(True),
    ]
    if exclude_connection_id is not None:
        filters.append(MediaServerConnection.id != exclude_connection_id)
    targets = (await db.execute(select(MediaServerConnection).where(*filters))).scalars().all()
    for conn in targets:
        baseline = await db.get(StreamBaseline, conn.id)
        if not baseline:
            continue  # a newly attached empty account has no playback to dismiss
        for key, record in baseline.snapshot.get('progress', {}).items():
            if baseline.snapshot.get('mappings', {}).get(key) != media.tmdb_id or record.get('content_type') != media.media_type.value:
                continue
            pending = (await db.execute(select(StreamAction.id).where(StreamAction.connection_id == conn.id,
                StreamAction.media_id == media.id, StreamAction.state == 'pending', StreamAction.action == 'dismiss'))).first()
            if not pending:
                db.add(StreamAction(user_id=user_id,connection_id=conn.id,media_id=media.id,
                    action='dismiss',payload=dict(record)))


async def queue_dismissals(db, source, media):
    """Mirror an inferred provider removal to the user's other stream accounts."""
    await _queue_dismissals(db, source.user_id, media, exclude_connection_id=source.id)


async def queue_local_dismissals(db, user_id, media):
    """An explicit local status change wins over every connected stream account."""
    await _queue_dismissals(db, user_id, media)


async def queue_restorations(db, user_id, media):
    targets=(await db.execute(select(MediaServerConnection).where(MediaServerConnection.user_id==user_id,
        MediaServerConnection.type.in_(['stremio','nuvio']),MediaServerConnection.push_playback.is_(True)))).scalars().all()
    for conn in targets:
        baseline=await db.get(StreamBaseline,conn.id)
        if not baseline:continue
        for key,record in baseline.snapshot.get('resume',{}).items():
            if baseline.snapshot.get('mappings',{}).get(key)!=media.tmdb_id or record.get('content_type')!=media.media_type.value:continue
            pending=(await db.execute(select(StreamAction.id).where(StreamAction.connection_id==conn.id,
                StreamAction.media_id==media.id,StreamAction.state=='pending',StreamAction.action=='restore'))).first()
            if not pending:db.add(StreamAction(user_id=user_id,connection_id=conn.id,media_id=media.id,action='restore',payload=dict(record)))


async def queue_resets(db,user_id,media,deleted_at):
    targets=(await db.execute(select(MediaServerConnection).where(MediaServerConnection.user_id==user_id,
        MediaServerConnection.type.in_(['stremio','nuvio'])))).scalars().all()
    for conn in targets:
        baseline=await db.get(StreamBaseline,conn.id)
        keys={key for key,tmdb_id in (baseline.snapshot.get('mappings',{}) if baseline else {}).items() if tmdb_id==media.tmdb_id}
        if not keys and media.imdb_id:keys={media.imdb_id}
        for key in keys:
            db.add(StreamAction(user_id=user_id,connection_id=conn.id,media_id=media.id,action='reset',
                payload={'content_id':key,'content_type':media.media_type.value,'deleted_at':deleted_at.replace(tzinfo=timezone.utc).isoformat()}))


async def dispatch_stream_actions(db, user_id):
    from core.tracking_snapshot import require_stream_reconciliation
    from models import User
    await db.execute(select(User.id).where(User.id == user_id).with_for_update())
    actions = (await db.execute(select(StreamAction).where(StreamAction.user_id == user_id,
        StreamAction.state == 'pending').order_by(StreamAction.id).limit(100).with_for_update(skip_locked=True))).scalars().all()
    for action in actions:
        conn = await db.get(MediaServerConnection, action.connection_id)
        if not conn or action.action not in ('dismiss','restore','reset'):
            continue
        # An explicit confirmed deletion is authoritative and is not an
        # ordinary optional mirroring preference. It still waits for the
        # connection's reviewed first-import baseline below.
        if action.action!='reset' and not conn.push_playback:continue
        entry = (await db.execute(select(TrackedEntry).where(TrackedEntry.user_id == user_id, TrackedEntry.media_id == action.media_id))).scalar_one_or_none()
        deleted = (await db.execute(select(TrackingDeletion).where(TrackingDeletion.user_id == user_id, TrackingDeletion.media_id == action.media_id))).scalar_one_or_none()
        valid_statuses = ('watching',) if action.action == 'restore' else ('planning','paused','dropped','completed')
        invalid=(not deleted or entry is not None) if action.action=='reset' else (deleted or not entry or entry.status not in valid_statuses)
        if invalid:
            action.state = 'cancelled'; action.payload = {}
            continue
        try:
            await require_stream_reconciliation(db, conn)
        except HTTPException:
            continue
        action.attempts += 1
        try:
            if conn.type == 'stremio':
                if action.action == 'reset':await dismiss_stremio(conn.token, action.payload, reset=True)
                elif action.action == 'restore':await dismiss_stremio(conn.token, action.payload, restore=True)
                else:await dismiss_stremio(conn.token, action.payload)
            elif conn.type == 'nuvio':
                if action.action == 'reset':await dismiss_nuvio(db, conn, action.payload, reset=True)
                elif action.action == 'restore':await dismiss_nuvio(db, conn, action.payload, restore=True)
                else:await dismiss_nuvio(db, conn, action.payload)
            else:
                continue
            action.state = 'applied'; action.last_error = None
            # Update the baseline to recognize our write on the next pull.
            baseline = await db.get(StreamBaseline, conn.id)
            snapshot = dict(baseline.snapshot)
            key = action.payload['content_id']
            snapshot['progress'] = {k:v for k,v in snapshot.get('progress', {}).items() if k != key}
            snapshot['resume'] = {**snapshot.get('resume',{})}
            if action.action=='dismiss':snapshot['resume'][key]=dict(action.payload)
            if action.action=='reset':snapshot['resume'].pop(key,None)
            if action.action == 'restore':
                snapshot['progress'][key]=dict(action.payload)
                snapshot['resume'].pop(key,None)
            records = dict(snapshot.get('records', {}))
            records['progress'] = [r for r in records.get('progress', []) if r.get('content_id') != key]
            if action.action == 'restore':records['progress'].append(dict(action.payload))
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
    await db.commit()
