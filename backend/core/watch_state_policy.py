"""Account policy for turning playback evidence into automatic list state.

Resume storage and delivery are independent of Watching admission. Duration-
based connection and Prime imports use the same account policy; manual list
edits remain user decisions.
"""
from sqlalchemy import select
from models.users import UserSettings


async def conservative_watch_state(db, user_id):
    preferences = await db.scalar(select(UserSettings.preferences).where(UserSettings.user_id == user_id))
    return (preferences or {}).get('watch_state_sync', 'conservative') != 'immediate'


def import_watch_evidence(item):
    """Return confirmed, below-threshold, or unknown import watch evidence.

    Unknown runtime/identity must remain reviewable, rather than becoming a
    verified negative. Outcome overrides never manufacture viewing evidence.
    """
    from core.streaming_evidence import completed_viewing

    def qualified(rows, runtime):
        if not rows:
            return None
        unknown = False
        for row in rows:
            if row.get('completed_evidence') or row.get('watched_seconds') is None:
                return True
            if not (row.get('duration_seconds') or runtime):
                unknown = True
            elif completed_viewing(row, runtime):
                return True
        return None if unknown else False

    if item.get('kind') == 'movie':
        candidate = (item.get('match') or {}).get('candidate') or {}
        evidence = item.get('source_evidence') or {}
        return qualified(evidence.get('viewings') or evidence.get('observations') or [],
            (candidate.get('details') or {}).get('runtime') or candidate.get('runtime'))
    catalog = {(ep.get('season_number'), ep.get('episode_number')): ep
        for ep in item.get('catalog_episodes', [])}
    unknown = False
    for episode in item.get('episodes', []):
        if (episode.get('decision') or {}).get('action') == 'skip':
            continue
        position = episode.get('season_number'), episode.get('episode_number')
        if not all(position):
            unknown = True
            continue
        enough = qualified(episode.get('viewings') or [], (catalog.get(position) or {}).get('runtime'))
        resolution = episode.get('resolution')
        matched = resolution in ('exact', 'guessed') if resolution else episode.get('matched')
        if enough and matched:
            return True
        if enough is None or enough and not matched:
            unknown = True
    return None if unknown else False


async def filter_import_items(db, user_id, items, source, *, for_commit=False):
    if source not in ('stremio', 'nuvio', 'amazon') or not items or not await conservative_watch_state(db, user_id):
        return items
    result = []
    for item in items:
        evidence = import_watch_evidence(item)
        if evidence is True or not for_commit and evidence is None:
            result.append(item)
    return result


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
