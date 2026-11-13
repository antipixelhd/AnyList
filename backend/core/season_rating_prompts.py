"""Season-scoped, repeat-safe prompts from accepted connection completions."""
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert

from core.season_completion import completion_evidence, season_groups
from models import Rating, WatchEvent
from models.tracking import SyncReview


async def queue_season_rating_prompts(db, *, user_id, media, entry, source, new_episode_ids=(), positions=()):
    if not new_episode_ids and not positions:
        return
    order, groups, metadata = await season_groups(db, media, user_id)
    native = set(positions)
    candidates = [(number, members, expected) for number, members, expected in groups
        if any(ep.id in new_episode_ids or (ep.season_number, ep.episode_number) in native for ep in members)]
    ids = {ep.id for _, members, _ in candidates for ep in members}
    if not ids:
        return
    events = list(await db.scalars(select(WatchEvent).where(WatchEvent.user_id == user_id,
        WatchEvent.completed.is_(True), WatchEvent.media_id.in_(ids))))
    rated = set(await db.scalars(select(Rating.season_number).where(Rating.user_id == user_id,
        Rating.media_id == media.id, Rating.rating > 0,
        Rating.episode_order == (None if order == 'tmdb:aired' else order))))
    if order == 'tmdb:aired':
        rated.update(int(number) for number, score in (entry.season_scores or {}).items() if score and score > 0)
    for number, members, expected in candidates:
        if number in rated or not completion_evidence(members, [event for event in events if event.media_id in {ep.id for ep in members}], expected)[0]:
            continue
        detail = metadata.get(number, {})
        key = f'rating:{media.id}:{order}:{number}'
        enrichment = {'rating_metadata': {'episode_count': expected,
            'year': str(detail.get('air_date') or members[0].release_date or '')[:4]}}
        if detail.get('poster_path'):
            enrichment['season_poster_path'] = detail['poster_path']
        await db.execute(insert(SyncReview).values(user_id=user_id, media_id=media.id, provider=source,
            season_number=number, season_event_key=key, kind='rating_needed', state='confirmed',
            proposed_status='completed', priority='low',
            message=f'{media.title} · Season {number} completed. Rate this season.',
            payload={'push_state': 'pending', 'delivered_subscription_ids': [], 'episode_order': order,
                'season_poster_path': detail.get('poster_path') or media.poster_path,
                'rating_metadata': {'episode_count': expected, 'year': str(detail.get('air_date') or members[0].release_date or '')[:4]}})
            .on_conflict_do_update(index_elements=[SyncReview.user_id, SyncReview.season_event_key],
                set_={'payload': SyncReview.payload.op('||')(enrichment)}, where=SyncReview.dismissed_at.is_(None)))
