"""Adapt a connection's first watch snapshot to the shared streaming importer.

Only provider identities resolve titles. Drafts never write personal progress;
the same import transaction and review UI used by file imports do that later.
"""
import asyncio
from datetime import date, timedelta
from math import isfinite
from contextvars import ContextVar
import re
import uuid

from sqlalchemy import select

from core import netflix_sessions, tmdb
from core.streaming_evidence import prepare_evidence
from core.streaming_history import WatchObservation, MIN_WATCH_SECONDS
from models import Media, MediaServerConnection, NetflixImportSession, Show, User, WatchEvent
from models.tracking import StreamBaseline, TrackedEntry


def observations(watched, progress):
    """A watched flag is explicit evidence; an offset alone must meet the ratio."""
    result = []
    for complete, rows in ((True, watched), (False, progress)):
        for row in rows:
            if row.get('content_type') not in ('movie', 'series'):
                continue
            try:
                position = max(0., float(row.get('position') or 0) / 1000)
                duration = max(0., float(row.get('duration') or 0) / 1000)
                season = int(row['season']) if row.get('season') is not None else None
                episode = int(row['episode']) if row.get('episode') is not None else None
            except (ValueError, TypeError, OverflowError):
                continue
            if not isfinite(position) or not isfinite(duration):
                continue
            if row['content_type'] == 'series' and (not season or season < 1 or not episode or episode < 1):
                continue
            if not complete and (duration <= 0 or position < MIN_WATCH_SECONDS):
                continue
            from core.watch_dates import normalize_watch_datetime
            stamp = normalize_watch_datetime(row.get('watched_at') if complete else row.get('last_watched'))
            # Stremio's bitfield dates belong to the current video only.
            day = stamp.date().isoformat() if stamp and not row.get('date_shared') else ''
            result.append((row, WatchObservation(
                row=len(result) + 1, source_title=str(row.get('title') or row['content_id']),
                watched_at=day, season_number=season, episode_number=episode,
                watched_seconds=None if complete else position,
                duration_seconds=duration or None, completed_evidence=complete,
            )))
    return result


async def prepare_items(watched, progress, mappings, api_key):
    grouped = {}
    for row, observation in observations(watched, progress):
        identity = (row['content_type'], mappings.get(str(row['content_id'])))
        if identity[1] is not None:
            grouped.setdefault(identity, []).append(observation)
    semaphore = asyncio.Semaphore(4)

    async def prepare(identity, rows):
        kind, tmdb_id = identity
        async with semaphore:
            details = await (tmdb.get_movie_light(tmdb_id, api_key=api_key) if kind == 'movie'
                             else tmdb.get_show_light(tmdb_id, api_key=api_key))
        if details.get('id') != tmdb_id:
            raise ValueError('The provider identity could not be verified with TMDB.')
        title = details.get('title') or details.get('name') or rows[0].source_title
        catalog, seasons = [], []
        if kind == 'series':
            async def season_data(number):
                async with semaphore:
                    return await tmdb.get_season(tmdb_id, number, api_key=api_key)
            declared = [s for s in details.get('seasons', []) if s.get('season_number', 0) > 0]
            fetched = await asyncio.gather(*(season_data(s['season_number']) for s in declared))
            today = date.today().isoformat()
            for season, response in zip(declared, fetched):
                episodes = response.get('episodes') or []
                released = [ep for ep in episodes if ep.get('air_date') and ep['air_date'] <= today]
                catalog.extend({**ep, 'season_number': season['season_number'], 'release_date': ep.get('air_date'),
                                'tmdb_episode_id': ep.get('id')} for ep in released)
                seasons.append({'season_number': season['season_number'], 'represented': 0,
                                'total_released': len(released), 'catalogue_complete': bool(episodes)
                                and len(episodes) == season.get('episode_count')
                                and all(ep.get('air_date') for ep in episodes)})
        by_position = {(ep['season_number'], ep['episode_number']): ep for ep in catalog}
        source_episodes = {}
        for row in rows:
            row.source_title = title if kind == 'movie' else f'{title} S{row.season_number}E{row.episode_number}'
            if kind == 'series':
                row.show_title = title
                position = row.season_number, row.episode_number
                if position not in by_position:
                    continue  # Never reinterpret an unverified episode position by title.
                source_episodes.setdefault(position, {**by_position[position], 'source_title': row.source_title,
                    'matched': True, 'resolution': 'exact', 'dates': []})
        from core.netflix_import import is_anime_candidate
        candidate = {**details, 'details': details, 'tmdb_id': tmdb_id, 'title': title,
                     'media_type': 'movie' if kind == 'movie' else 'show',
                     'tvdb_id': (details.get('external_ids') or {}).get('tvdb_id'),
                     'year': int((details.get('release_date') or details.get('first_air_date') or '0')[:4])}
        item = netflix_sessions._normalise_groups({
            'movies' if kind == 'movie' else 'shows': [{
                'source_title': title, 'candidate': candidate, 'status': 'matched', 'confidence': 'high',
                'is_anime': is_anime_candidate(details), 'episodes': list(source_episodes.values()),
                'catalog_episodes': catalog, 'seasons': seasons,
            }],
        })[0]
        item['id'] = f'connection_{kind}_{tmdb_id}'
        item['identity_verified'] = True
        prepare_evidence([item], rows)
        if kind == 'movie' and not item.get('progress_reviewed'):
            item['requires_watch_confirmation'] = True
            item['outcome']['status'] = 'dropped'
            item['outcome']['tracking_status'] = 'dropped'
        return item if not item['is_anime'] and (kind == 'movie' or source_episodes) else None

    return [item for item in await asyncio.gather(*(prepare(identity, rows) for identity, rows in grouped.items())) if item]


async def remove_covered_items(db, user_id, items):
    """Compare actual watches/progress, including undated provider flags."""
    from core.streaming_duplicates import remove_covered_items as remove_dated_evidence
    items, _ = await remove_dated_evidence(db, user_id, items)
    if not items:
        return []
    identities = {(item['kind'], item['match']['candidate']['tmdb_id']) for item in items}
    roots = (await db.execute(select(Media, TrackedEntry, Show.id).join(
        TrackedEntry, (TrackedEntry.media_id == Media.id) & (TrackedEntry.user_id == user_id),
    ).outerjoin(Show, (Show.tmdb_id == Media.tmdb_id) & (Media.media_type == 'series')).where(
        Media.tmdb_id.in_({identity[1] for identity in identities}), Media.media_type.in_(['movie', 'series']),
    ))).all() if identities else []
    lookup = {('movie' if media.media_type == 'movie' else 'show', media.tmdb_id): (media, entry, show_id)
              for media, entry, show_id in roots}
    ids = [media.id for media, _, _ in roots]
    shows = [show_id for _, _, show_id in roots if show_id]
    alternate_shows = {show.id: show.tmdb_id for show in (await db.scalars(select(Show).where(
        Show.id.in_(shows), Show.canonical_source == 'tvdb',
    ))).all()} if shows else {}
    from models.episode_order import EpisodeOrderMapping
    order = {(mapping.series_tmdb_id, mapping.tmdb_season_number, mapping.tmdb_episode_number):
             (mapping.tvdb_season_number, mapping.tvdb_episode_number)
             for mapping in (await db.scalars(select(EpisodeOrderMapping).where(
                 EpisodeOrderMapping.series_tmdb_id.in_(alternate_shows.values()),
             ))).all()} if alternate_shows else {}
    watches = (await db.execute(select(Media, WatchEvent).join(WatchEvent, WatchEvent.media_id == Media.id).where(
        WatchEvent.user_id == user_id, WatchEvent.completed.is_(True),
        (Media.id.in_(ids) | Media.show_id.in_(shows)),
    ))).all() if roots else []
    retained = []
    for item in items:
        root = lookup.get((item['kind'], item['match']['candidate']['tmdb_id']))
        if not root:
            retained.append(item)
            continue
        media, entry, show_id = root
        item['existing'] = netflix_sessions._existing_state(media, entry)
        incoming = ([{'dates': item.get('source_dates', [])}] if item['kind'] == 'movie' else item.get('episodes', []))
        covered = True
        for episode in incoming:
            position = episode.get('season_number'), episode.get('episode_number')
            local_position = order.get((alternate_shows[show_id], *position)) if show_id in alternate_shows else position
            rows = [watch for child, watch in watches if (child.id == media.id if item['kind'] == 'movie' else
                    child.show_id == show_id and local_position == (child.season_number, child.episode_number))]
            days = episode.get('dates') or []
            # A real earlier date is new information even when progress exists.
            if days:
                if not any(not watch.date_inferred and not watch.date_shared and not watch.provisional
                           and watch.watched_at and watch.watched_at.date().isoformat() <= min(days) for watch in rows):
                    covered = False
            elif not rows:
                catalog = sorted((ep['season_number'], ep['episode_number']) for ep in item.get('catalog_episodes', []))
                progressed = (entry.progress or 0) >= (1 if item['kind'] == 'movie' else
                    catalog.index(position) + 1 if position in catalog else float('inf'))
                if not progressed or show_id in alternate_shows:
                    covered = False
        if not covered:
            retained.append(item)
    return retained


async def stage_first_snapshot(db, conn, library, watched, progress, mappings, api_key, *, full_resync=False,
                               source_started_at=None):
    """Return True when the watch snapshot belongs to onboarding, not live sync."""
    if full_resync:
        return False  # Existing explicit repair semantics are separate from connection onboarding.
    baseline = await db.get(StreamBaseline, conn.id)
    if baseline:
        session_id = (baseline.snapshot or {}).get('import_session_id')
        if not session_id:
            return False
        # Abandoned/expired reviews never silently approve a connection.
        return True
    identity_version = conn.identity_version
    unresolved = {str(row.get('content_id')) for row, _ in observations(watched, progress)
                  if re.fullmatch(r'tt\d+|tmdb:\d+', str(row.get('content_id') or ''), re.IGNORECASE)
                  and str(row['content_id']) not in mappings}
    if unresolved:
        # A failed lookup is unknown evidence, not a reviewed skip. Do not
        # establish a baseline that would suppress these rows on a retry.
        raise ValueError(f'{len(unresolved)} watched title(s) could not be verified. Sync again to retry their identities.')
    items = await prepare_items(watched, progress, mappings, api_key)
    # Serialize competing first pulls after network work, before creating a draft.
    await db.execute(select(User.id).where(User.id == conn.user_id).with_for_update())
    await db.refresh(conn)
    if conn.identity_version != identity_version:
        raise ValueError('Connection account changed while preparing history; sync again.')
    if await db.get(StreamBaseline, conn.id, populate_existing=True):
        return True
    items = await remove_covered_items(db, conn.user_id, items)
    await netflix_sessions.hydrate_existing_items(db, conn.user_id, items)
    from core.tracking_snapshot import _active, completed_rows, watch_key
    snapshot = {'library': sorted({str(row.get('content_id')) for row in library}), 'mappings': mappings,
                'progress': _active(progress), 'resume': {}, 'outbound': {},
                'watched': sorted({watch_key(row) for row in watched}),
                'progress_completed': sorted({watch_key(row) for row in completed_rows(progress)}),
                'records': {'library': library, 'watched': watched, 'progress': progress}}
    now = netflix_sessions._now()
    if items:
        session = NetflixImportSession(id=str(uuid.uuid4()), user_id=conn.user_id, status='review', phase='review',
            progress={'current': len(items), 'total': len(items), 'message': 'History ready'}, revision=0,
            payload={'source': conn.type, 'connection_id': conn.id, 'identity_version': conn.identity_version,
                     'items': items, 'counts': {}, 'errors': []},
            created_at=now, updated_at=now, expires_at=now + timedelta(days=7))
        db.add(session)
        snapshot['import_session_id'] = session.id
    db.add(StreamBaseline(connection_id=conn.id, user_id=conn.user_id, approved=not items,
                          snapshot=snapshot, observed_at=source_started_at or now))
    await db.commit()
    return True


async def apply_resume(db, session, item, root, entry, show, episode_mappings):
    """Restore only accepted, unwatched resume offsets inside the import transaction."""
    connection_id = (session.payload or {}).get('connection_id')
    if connection_id is None or item['outcome'].get('status') != 'partial' or entry.status != 'watching':
        return
    from core.streaming_evidence import completed_viewing
    from core.watch_dates import normalize_watch_datetime
    from models import PlaybackProgress
    baseline = await db.get(StreamBaseline, connection_id)
    snapshot = (baseline.snapshot or {}) if baseline else {}
    mappings = snapshot.get('mappings') or {}
    rows = [row for row in snapshot.get('records', {}).get('progress', [])
            if row.get('content_type') == ('series' if show else 'movie')
            and mappings.get(str(row.get('content_id'))) == item['match']['candidate']['tmdb_id']]
    observed_at = (baseline.observed_at or netflix_sessions._now()) if baseline else netflix_sessions._now()
    rows.sort(key=lambda row: normalize_watch_datetime(row.get('last_watched')) or observed_at, reverse=True)
    for row in rows:
        try:
            seconds, duration = float(row.get('position') or 0) / 1000, float(row.get('duration') or 0) / 1000
            position = int(row.get('season') or 0), int(row.get('episode') or 0)
        except (ValueError, TypeError, OverflowError):
            continue
        if not isfinite(seconds) or not isfinite(duration) or seconds < MIN_WATCH_SECONDS or duration <= 0:
            continue
        if completed_viewing({'watched_seconds': seconds, 'duration_seconds': duration}):
            continue
        media = root
        if show:
            episode = netflix_sessions._all_catalog_episodes(item).get(position)
            if episode is None:
                continue
            media = await netflix_sessions._get_or_create_episode(db, show, item['match']['candidate'], episode, episode_mappings)
        if media is None:
            continue
        watched = await db.scalar(select(WatchEvent.id).where(WatchEvent.user_id == session.user_id,
            WatchEvent.media_id == media.id, WatchEvent.completed.is_(True)).limit(1))
        current = await db.scalar(select(PlaybackProgress.id).where(PlaybackProgress.user_id == session.user_id,
            PlaybackProgress.media_id == media.id))
        if watched is not None or current is not None:
            continue
        db.add(PlaybackProgress(user_id=session.user_id, media_id=media.id, progress_seconds=int(seconds),
            progress_percent=seconds / duration,
            updated_at=normalize_watch_datetime(row.get('last_watched')) or baseline.observed_at or netflix_sessions._now()))
        await db.flush()


async def finish_import(db, session, *, validate=False):
    payload = session.payload or {}
    connection_id = payload.get('connection_id')
    if connection_id is None:
        return
    conn = await db.get(MediaServerConnection, connection_id, populate_existing=True)
    if not conn or conn.user_id != session.user_id or conn.identity_version != payload.get('identity_version'):
        if validate:
            raise ValueError('This connection changed. Cancel this import and sync the connection again.')
        return
    baseline = await db.get(StreamBaseline, connection_id)
    if validate and (not baseline or (baseline.snapshot or {}).get('import_session_id') != session.id):
        raise ValueError('This connection snapshot changed. Cancel this import and sync the connection again.')
    if baseline and (baseline.snapshot or {}).get('import_session_id') == session.id and not validate:
        baseline.approved = True
        baseline.snapshot = {**{key: value for key, value in baseline.snapshot.items() if key != 'import_session_id'},
                             'import_handoff': session.id}


# A committed onboarding import owns its first delivery cycle. Other workers
# must not fold that connection into routine sync before delivery succeeds.
handoff_connection = ContextVar("import_handoff_connection", default=None)


async def onboarding_pending(db, conn, *, require_snapshot=False, require_approval=False):
    if conn.type not in ('stremio', 'nuvio'):
        return False
    baseline = await db.get(StreamBaseline, conn.id, populate_existing=True)
    if require_snapshot and not baseline:
        return True
    if require_approval and (not baseline or not baseline.approved):
        return True
    snapshot = (baseline.snapshot or {}) if baseline else {}
    return bool(snapshot.get('import_session_id') or
                (snapshot.get('import_handoff') and handoff_connection.get() != conn.id))


async def release_handoff(db, connection_id, session_id):
    if connection_id is None:
        return
    baseline = await db.get(StreamBaseline, connection_id, populate_existing=True)
    if baseline and (baseline.snapshot or {}).get('import_handoff') == session_id:
        baseline.snapshot = {key: value for key, value in baseline.snapshot.items() if key != 'import_handoff'}


async def remove_abandoned_connection(db, session):
    """Remove only the provisional identity that owns this abandoned draft."""
    from sqlalchemy import delete
    payload = session.payload or {}
    connection_id = payload.get('connection_id')
    if connection_id is None:
        return
    conn = await db.get(MediaServerConnection, connection_id, populate_existing=True)
    baseline = await db.get(StreamBaseline, connection_id, populate_existing=True)
    if (conn and conn.user_id == session.user_id and conn.identity_version == payload.get('identity_version')
            and baseline and (baseline.snapshot or {}).get('import_session_id') == session.id):
        # Cascades remove queued deliveries and the provisional baseline. No
        # external account data is cleared or changed when a draft expires.
        await db.execute(delete(MediaServerConnection).where(MediaServerConnection.id == connection_id))


async def seed_import_delivery(db, user_id, job_id, cycle):
    """Include accepted imports even when the provider snapshot is unchanged."""
    from models.ratings import Rating
    session = await db.scalar(select(NetflixImportSession).where(
        NetflixImportSession.user_id == user_id, NetflixImportSession.status == 'committed',
        NetflixImportSession.result['pull_job_id'].as_integer() == job_id,
        NetflixImportSession.result['pull_pending'].as_boolean().is_(True)).limit(1))
    if not session:
        return
    result = session.result or {}
    roots = result.get('delivery_media_ids') or []
    cycle.new_watched_ids.update(result.get('delivery_watch_ids') or [])
    for rating in (await db.scalars(select(Rating).where(
            Rating.user_id == user_id, Rating.media_id.in_(roots), Rating.episode_order.is_(None)))).all():
        cycle.new_ratings[(rating.media_id, rating.season_number)] = rating.rating
        cycle.rating_sources[(rating.media_id, rating.season_number)] = set()
    from core.stream_actions import queue_local_dismissals, queue_restorations
    for media, entry in (await db.execute(select(Media, TrackedEntry).join(
            TrackedEntry, TrackedEntry.media_id == Media.id).where(
            Media.id.in_(roots), TrackedEntry.user_id == user_id))).all():
        if entry.status == 'watching':
            await queue_restorations(db, user_id, media)
        elif entry.status in ('completed', 'paused', 'dropped', 'planning'):
            await queue_local_dismissals(db, user_id, media)
