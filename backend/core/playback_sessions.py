"""Race tolerant commits for concurrent playback session updates."""

from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError


async def _commit_playback_session_update(db: AsyncSession, *keep) -> bool:
    """Commits a pending PlaybackSession update, tolerating a concurrent
    PlaybackStop having already deleted that same row. Jellyfin/Emby send no
    dedup protection on webhook deliveries (unlike Plex), so an
    overlapping/duplicate progress tick can race a stop event for the same
    session_key and try to UPDATE a row that's already gone - SQLAlchemy
    surfaces that as a StaleDataError (0 rows matched) instead of a silent
    no-op, which otherwise crashes the whole request with a 500. Returns
    False (after rolling back) if that happened, True on a normal commit.

    A rollback expires every ORM object in the session, and the callers go
    on to read `settings`/`media` in the scrobble forwarders - a lazy-load
    outside a greenlet, i.e. MissingGreenlet (#410). Pass those objects as
    `keep` and they are reloaded here, while we can still await."""
    try:
        await db.commit()
        return True
    except StaleDataError:
        await db.rollback()
        for obj in keep:
            if obj is None:
                continue
            try:
                await db.refresh(obj)
            except InvalidRequestError:
                pass  # row is gone too; nothing to reload
        return False
