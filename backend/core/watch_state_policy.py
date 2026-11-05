"""Account policy for turning playback evidence into automatic list state.

Resume storage and delivery are independent of Watching admission. Explicitly
reviewed imports and manual edits remain user decisions.
"""
from sqlalchemy import select
from models.users import UserSettings


async def conservative_watch_state(db, user_id):
    preferences = await db.scalar(select(UserSettings.preferences).where(UserSettings.user_id == user_id))
    return (preferences or {}).get('watch_state_sync', 'conservative') != 'immediate'


async def has_completed_episode(db, user_id, media):
    from sqlalchemy import or_
    from models import Media, Show, WatchEvent
    from models.base import MediaType
    identities = []
    if media.tmdb_id:
        identities.append(Show.tmdb_id == media.tmdb_id)
    if media.tvdb_id:
        identities.append(Show.tvdb_id == media.tvdb_id)
    if not identities:
        return False
    return bool(await db.scalar(select(WatchEvent.id).join(Media, Media.id == WatchEvent.media_id)
        .join(Show, Show.id == Media.show_id).where(WatchEvent.user_id == user_id,
            WatchEvent.completed.is_(True), Media.media_type == MediaType.episode,
            or_(*identities)).limit(1)))
