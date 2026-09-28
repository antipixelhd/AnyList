"""Project list status onto Nuvio's independently synced Next Up visibility."""
from sqlalchemy import select
from models.media import Media
from models.base import MediaType
from models.tracking import TrackedEntry, StreamBaseline
from core.nuvio_settings import next_up_seeds


def _tmdb_key(value):
    """Normalize TMDB identifiers that may be stored as integers or strings."""
    if value is None:
        return None
    key = str(value).strip()
    if key.startswith('tmdb:'):
        key = key[5:]
    return key or None


def _content_ids_for_record(record, mappings, local_media):
    content_id = str(record.get('content_id') or '').strip()
    if not content_id:
        return set()

    tmdb_id = _tmdb_key(mappings.get(content_id))
    if tmdb_id is None and content_id.startswith('tmdb:'):
        tmdb_id = _tmdb_key(content_id)
    if tmdb_id is None:
        tmdb_id = _tmdb_key(record.get('tmdb_id') or record.get('tmdb'))
    if tmdb_id is None:
        tmdb_id = next((_tmdb_key(media.tmdb_id) for media in local_media
            if _nuvio_imdb_id(media) == content_id), None)

    ids = {content_id}
    if tmdb_id is None:
        return ids

    # Nuvio profiles can retain several provider identities for one title.
    # Apply visibility to the full alias set so stale watched history cannot
    # seed another Next Up card under an older ID.
    ids.update(str(key) for key, value in mappings.items()
        if _tmdb_key(value) == tmdb_id and str(key).strip())
    ids.update(_nuvio_imdb_id(media) for media in local_media
        if _tmdb_key(media.tmdb_id) == tmdb_id and _nuvio_imdb_id(media))
    ids.add(f'tmdb:{tmdb_id}')
    return ids


def _nuvio_imdb_id(media):
    data = media.tmdb_data or {}
    external = data.get('external_ids') if isinstance(data, dict) else None
    value = (data.get('imdb_id') if isinstance(data, dict) else None) or (
        external.get('imdb_id') if isinstance(external, dict) else None
    ) or getattr(media, 'imdb_id', None)
    value = str(value or '').strip()
    return value if value.startswith('tt') and value[2:].isdigit() else None


def provider_content_ids(media, baseline=None, *, records=()):
    """Return every known Nuvio identity for one local title.

    Baseline mappings may hold TMDB IDs as either JSON strings or integers.
    Current remote rows can add aliases when they expose a TMDB/IMDb field.
    """
    tmdb_id = _tmdb_key(getattr(media, 'tmdb_id', None))
    mappings = (baseline.snapshot or {}).get('mappings', {}) if baseline else {}
    ids = {str(key).strip() for key, value in mappings.items()
        if tmdb_id is not None and _tmdb_key(value) == tmdb_id and str(key).strip()}
    imdb_id = _nuvio_imdb_id(media)
    if imdb_id:
        ids.add(imdb_id)
    if tmdb_id:
        ids.add(f'tmdb:{tmdb_id}')

    expected_type = str(getattr(getattr(media, 'media_type', None), 'value',
        getattr(media, 'media_type', '')) or '').lower()
    for row in records:
        row_type = str(row.get('content_type') or '').lower()
        if expected_type in ('series', 'tv') and row_type not in ('', 'series', 'tv'):
            continue
        if expected_type == 'movie' and row_type not in ('', 'movie'):
            continue
        content_id = str(row.get('content_id') or '').strip()
        if not content_id:
            continue
        row_tmdb = _tmdb_key(row.get('tmdb_id') or row.get('tmdb'))
        row_imdb = str(row.get('imdb_id') or row.get('imdb') or '').strip()
        if (content_id in ids or (tmdb_id and row_tmdb == tmdb_id)
                or (imdb_id and row_imdb == imdb_id)):
            ids.add(content_id)
    return sorted(ids)


async def next_up_visibility(db, user_id, connection_id, records, *, seed_records=None):
    rows = (await db.execute(select(Media, TrackedEntry.status).join(
        TrackedEntry, TrackedEntry.media_id == Media.id,
    ).where(TrackedEntry.user_id == user_id, Media.media_type == MediaType.series))).all()
    baseline = await db.get(StreamBaseline, connection_id)
    mappings = dict((baseline.snapshot or {}).get('mappings', {})) if baseline else {}
    # Fresh history may expose aliases not present in the approved baseline.
    for row in [*records, *(seed_records or [])]:
        key = str(row.get('content_id') or '').strip()
        tmdb_id = _tmdb_key(row.get('tmdb_id') or row.get('tmdb'))
        if key and tmdb_id:
            mappings[key] = tmdb_id

    by_tmdb = {_tmdb_key(media.tmdb_id): status for media, status in rows if media.tmdb_id}
    by_external = {_nuvio_imdb_id(media): status for media, status in rows if _nuvio_imdb_id(media)}
    local_media = [media for media, _status in rows]

    candidates = {}
    for row in records:
        content_type = str(row.get('content_type') or '').lower()
        if content_type not in ('series', 'tv') or not row.get('content_id'):
            continue
        tmdb_id = _tmdb_key(mappings.get(str(row['content_id'])))
        if tmdb_id is None:
            tmdb_id = _tmdb_key(row['content_id']) if str(row['content_id']).startswith('tmdb:') else None
        if tmdb_id is None:
            tmdb_id = _tmdb_key(row.get('tmdb_id') or row.get('tmdb'))
        aliases = _content_ids_for_record(row, mappings, local_media)
        if tmdb_id is None:
            tmdb_id = next((_tmdb_key(key) for key in aliases if key.startswith('tmdb:')), None)
        for key in aliases:
            status = by_external.get(key)
            if status is None and tmdb_id is not None:
                status = by_tmdb.get(tmdb_id)
            if candidates.get(key) != 'watching':
                candidates[key] = status

    hidden, visible = set(), set()
    for key, status in candidates.items():
        (visible if status == 'watching' else hidden).add(key)
    seeds = {}
    for row in records if seed_records is None else seed_records:
        if str(row.get('content_type') or '').lower() not in ('series', 'tv'):
            continue
        aliases = _content_ids_for_record(row, mappings, local_media)
        for coordinates in next_up_seeds([row]).values():
            for alias in aliases:
                seeds.setdefault(alias, set()).update(coordinates)
    return hidden, visible, seeds


async def sync_next_up_visibility(db, conn, records):
    from core import nuvio
    from db import AsyncSessionLocal
    from sqlalchemy import update
    from sqlalchemy.orm.attributes import set_committed_value
    from models.connections import MediaServerConnection
    if not records:
        return
    async with nuvio.connection_lock(conn.id):
        await db.refresh(conn)
        async with nuvio.httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            session = await nuvio.refresh_session(conn.url, conn.token, client=client)
            async with AsyncSessionLocal() as token_db:
                await token_db.execute(update(MediaServerConnection).where(
                    MediaServerConnection.id == conn.id).values(token=session.refresh_token))
                await token_db.commit()
            set_committed_value(conn, 'token', session.refresh_token)
            profile = nuvio.parse_profile_id(conn.server_user_id)
            watched = await nuvio._pull_watched_items(client, conn.url, session.access_token, profile)
            hidden, visible, seeds = await next_up_visibility(db, conn.user_id, conn.id,
                records, seed_records=watched)
            await nuvio.update_next_up_dismissals(client, conn.url, session.access_token,
                profile, hide=hidden, show=visible, seeds=seeds)
