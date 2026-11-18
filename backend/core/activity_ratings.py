"""Current rating breakdowns for the visible cards in an activity response."""
from collections import defaultdict

from sqlalchemy import func, or_, select, tuple_

from core.tracking_rules import effective_score
from models.ratings import Rating
from models.tracking import TrackedEntry


async def attach_series_ratings(db, cards, *, entries=None, owner_id=None):
    aired = 'tmdb:aired'
    grouped = defaultdict(list)
    for card in cards:
        details = card['payload']
        if (card['media']['type'] == 'series' and (card['score'] or 0) > 0
                and (details.get('rating_changed') or details.get('rating_snapshot'))):
            key = (owner_id if owner_id is not None else card['user_id'], card['media']['id'])
            grouped[(*key, details.get('episode_order') or aired)].append(card)
    if not grouped:
        return

    keys = {(user, media) for user, media, _ in grouped}
    if entries is None:
        entries = (await db.execute(select(TrackedEntry.user_id, TrackedEntry.media_id,
            TrackedEntry.rating_mode, TrackedEntry.manual_score, TrackedEntry.season_scores)
            .where(tuple_(TrackedEntry.user_id, TrackedEntry.media_id).in_(keys)))).all()
    tracked = {(entry.user_id, entry.media_id): entry for entry in entries
               if (entry.user_id, entry.media_id) in keys}

    scope = (Rating.season_number > 0) & tuple_(
        Rating.user_id, Rating.media_id, func.coalesce(Rating.episode_order, aired)).in_(grouped)
    if untracked := keys - tracked.keys():
        scope = or_(scope, Rating.season_number.is_(None) & Rating.episode_order.is_(None)
                    & tuple_(Rating.user_id, Rating.media_id).in_(untracked))
    rows = await db.execute(select(Rating.user_id, Rating.media_id, Rating.episode_order,
        Rating.season_number, Rating.rating).where(scope))

    seasons = defaultdict(dict)
    standalone = {}
    for user, media, order, number, score in rows:
        if number is None:
            standalone[user, media] = score
        else:
            seasons[user, media, order or aired][str(number)] = score

    for (user, media, order), activities in grouped.items():
        entry = tracked.get((user, media))
        aired_scores = dict(entry.season_scores or {}) if entry is not None else {}
        series_score = (effective_score(entry.rating_mode, entry.manual_score, aired_scores) if entry is not None
                        else standalone.get((user, media)))
        aired_scores.update(seasons.get((user, media, aired), {}))
        scores = aired_scores if order == aired else seasons.get((user, media, order), {})
        breakdown = {'series_score': series_score, 'season_scores': {
            number: score for number, score in scores.items() if number.isdecimal() and int(number) > 0 and score and score > 0
        }}
        for card in activities:
            card['rating_breakdown'] = breakdown
