"""Suppress review only when the viewer already owns all incoming evidence."""
from sqlalchemy import or_, select

from models import Media, Show, WatchEvent
from models.tracking import TrackedEntry, TrackingActivity


def evidence_covered(item, watches, events=()):
    """Unknown/inferred dates cannot prove that real source evidence was imported."""
    def covered(position, dates):
        incoming = [str(day)[:10] for day in dates if day]
        actual = watches.get(position)
        return bool(incoming and actual and actual <= min(incoming))

    if item['kind'] == 'movie':
        if covered(None, item.get('source_dates', [])):
            return True
        # Partial movies deliberately have no completed watch. Their historical
        # import status event is evidence of the already accepted viewing.
        if item.get('suggested_movie_status') == 'dropped':
            incoming = {str(day)[:10] for day in item.get('source_dates', [])}
            return any(event.get('key') == 'status' and event.get('status') == 'dropped'
                       and str(event.get('date', ''))[:10] in incoming for event in events)
        return False
    episodes = item.get('episodes', [])
    return bool(episodes) and all(
        episode.get('matched') and covered(
            (episode.get('season_number'), episode.get('episode_number')),
            episode.get('dates', []),
        ) for episode in episodes
    )


async def remove_covered_items(db, user_id, items):
    """Use three batched, viewer-scoped reads; never discard by title or status."""
    candidate_ids = {(item.get('match', {}).get('candidate') or {}).get('tmdb_id') for item in items}
    candidate_ids.discard(None)
    if not candidate_ids:
        return items, 0
    roots = (await db.execute(select(Media, Show.canonical_source).join(
        TrackedEntry, (TrackedEntry.media_id == Media.id) & (TrackedEntry.user_id == user_id),
    ).outerjoin(Show, Show.id == Media.show_id).where(
        Media.tmdb_id.in_(candidate_ids), Media.media_type.in_(['movie', 'series']),
    ))).all()
    by_identity = {(media.media_type.value, media.tmdb_id): (media, order) for media, order in roots}
    root_ids = [media.id for media, _ in roots]
    show_ids = [media.show_id for media, order in roots if media.show_id and order != 'tvdb']
    if not root_ids:
        return items, 0
    rows = (await db.execute(select(Media.id, Media.show_id, Media.season_number,
                                   Media.episode_number, WatchEvent.watched_at).join(
        WatchEvent, WatchEvent.media_id == Media.id,
    ).where(WatchEvent.user_id == user_id, WatchEvent.completed.is_(True),
            WatchEvent.date_inferred.is_(False), WatchEvent.provisional.is_(False),
            WatchEvent.date_shared.is_(False),
            or_(Media.id.in_(root_ids), Media.show_id.in_(show_ids))))).all()
    watches = {}
    show_watches = {}
    for media_id, show_id, season, episode, watched_at in rows:
        if watched_at is None:
            continue
        key = ('show', show_id, season, episode) if season and episode else ('movie', media_id)
        day = watched_at.date().isoformat()
        watches[key] = min(watches.get(key, day), day)
        if season and episode:
            show_watches.setdefault(show_id, {})[(season, episode)] = watches[key]
    events = {}
    for media_id, payload in (await db.execute(select(TrackingActivity.media_id, TrackingActivity.payload).where(
        TrackingActivity.user_id == user_id, TrackingActivity.media_id.in_(root_ids),
        TrackingActivity.payload.has_key('streaming_events'),
    ))).all():
        events.setdefault(media_id, []).extend((payload.get('streaming_events') or {}).values())
    retained = []
    for item in items:
        candidate = item.get('match', {}).get('candidate') or {}
        identity = ('movie' if item['kind'] == 'movie' else 'series', candidate.get('tmdb_id'))
        root = by_identity.get(identity)
        # Uncertain matches and alternate episode order still require a decision.
        match_state = item.get('title_match_state', item.get('match', {}).get('state'))
        if not root or match_state != 'matched' or root[1] == 'tvdb':
            retained.append(item)
            continue
        media = root[0]
        positions = ({None: watches.get(('movie', media.id))} if item['kind'] == 'movie'
                     else show_watches.get(media.show_id, {}))
        if not evidence_covered(item, positions, events.get(media.id, [])):
            retained.append(item)
    return retained, len(items) - len(retained)
