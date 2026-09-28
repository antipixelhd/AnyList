"""Project list status onto Nuvio's independently synced Next Up visibility."""
from sqlalchemy import select
from models.media import Media
from models.base import MediaType
from models.tracking import TrackedEntry, StreamBaseline


async def next_up_visibility(db, user_id, connection_id, records):
    from routers.sync import _nuvio_imdb_id
    rows = (await db.execute(select(Media, TrackedEntry.status).join(
        TrackedEntry, TrackedEntry.media_id == Media.id,
    ).where(TrackedEntry.user_id == user_id, Media.media_type == MediaType.series))).all()
    by_tmdb = {media.tmdb_id: status for media, status in rows if media.tmdb_id}
    by_external = {_nuvio_imdb_id(media): status for media, status in rows if _nuvio_imdb_id(media)}
    baseline = await db.get(StreamBaseline, connection_id)
    mappings = (baseline.snapshot or {}).get('mappings', {}) if baseline else {}
    ids = {str(row['content_id']) for row in records
           if row.get('content_type') == 'series' and row.get('content_id')}
    hidden, visible = set(), set()
    for key in ids:
        tmdb_id = mappings.get(key)
        if tmdb_id is None and key.startswith('tmdb:'):
            try:
                tmdb_id = int(key[5:])
            except ValueError:
                pass
        status = by_external.get(key, by_tmdb.get(tmdb_id))
        (visible if status == 'watching' else hidden).add(key)
    return hidden, visible


async def sync_next_up_visibility(db, conn, records):
    from core import nuvio
    from db import AsyncSessionLocal
    from sqlalchemy import update
    from sqlalchemy.orm.attributes import set_committed_value
    from models.connections import MediaServerConnection
    hidden, visible = await next_up_visibility(db, conn.user_id, conn.id, records)
    if not hidden and not visible:
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
            await nuvio.update_next_up_dismissals(client, conn.url, session.access_token,
                nuvio.parse_profile_id(conn.server_user_id), hide=hidden, show=visible)
