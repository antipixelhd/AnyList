"""Season-local activity windows and immutable historical rating snapshots.

This runs on writes, using the canonical catalogue/watches, never on feed reads.
Episode progress keeps its own window; status and rating actions belong to the
selected episode order and season instead of leaking into the next season.
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func

from models import Rating
from models.tracking import TrackingActivity, TrackedEntry


def season_target(context, *, rating=False, status=None):
    released = {ep.id for ep in context.released}
    groups = [(number, [ep for ep in members if ep.id in released], expected)
              for number, members, expected in context.groups]
    groups = [(number, members, expected) for number, members, expected in groups if members]
    if not groups:
        return None, False
    watched = [(number, sum(ep.id in context.watched for ep in members), expected or len(members))
               for number, members, expected in groups]
    partial = [number for number, count, total in watched if 0 < count < total]
    if partial:
        return partial[-1], True
    if rating:
        seen = [number for number, count, _ in watched if count]
        if seen:
            return seen[-1], False
    return next((number for number, count, total in watched if count < total), watched[-1][0]), False


def in_window(row, now, hours=24):
    payload = row.payload or {}
    start = datetime.fromisoformat(payload.get('window_started_at') or row.created_at.isoformat())
    return start <= now < start + timedelta(hours=hours)


def rating_values(row):
    details = row.payload or {}
    if details.get('season_ratings'):
        return dict(details['season_ratings'])
    number = details.get('season_number')
    return {str(number): row.score} if number and row.score is not None else {}


def put_rating(row, number, score, *, source, previous=None, now=None, event=True):
    """Update this card only; no join to today's mutable series/season rating."""
    details = dict(row.payload or {})
    values = rating_values(row)
    key = str(number)
    if score is None:
        values.pop(key, None)
    else:
        values[key] = score
    sources = dict(details.get('season_rating_sources') or {})
    sources[key] = source
    details.update(season_ratings=values, season_rating_sources=sources,
                   rating_changed=bool(values) and (event or bool(details.get('rating_changed'))),
                   rating_snapshot=bool(values))
    if previous is not None and len(values) == 1:
        details.update(previous_score=previous, rating_first=False)
    else:
        details.pop('previous_score', None)
        details['rating_first'] = True
    if now and not details.get('first_rated_at') and score is not None:
        details['first_rated_at'] = now.isoformat()
    row.score = sum(values.values()) / len(values) if values else None
    row.payload = details


async def absorb_season_actions(db, cards, milestones):
    """Completion wins inside its season's window without losing other seasons."""
    from core.activity import has_activity_event
    removed = set()
    for card in cards:
        details = card.payload or {}
        if details.get('season_completion_id'):
            continue
        for target in milestones:
            payload = target.payload
            number = payload['season_number']
            at = datetime.fromisoformat(payload['window_started_at'])
            if details.get('episode_order') != payload['episode_order'] or not in_window(card, at):
                continue
            values = rating_values(card)
            if str(number) in values:
                put_rating(target, number, values[str(number)],
                    source=details.get('season_rating_sources',{}).get(str(number),'season'),
                    previous=details.get('previous_score'),
                    now=datetime.fromisoformat(details.get('first_rated_at') or at.isoformat()),
                    event=bool(details.get('rating_changed')))
            if details.get('season_rating_group') and str(number) in values:
                put_rating(card,number,None,source='season')
            elif details.get('season_status') and details.get('season_number') == number:
                removed.add(card)
            if not has_activity_event(card.status, card.payload):
                removed.add(card)
    for card in removed:
        await db.delete(card)
    return removed


async def record_season_activity(db, *, user_id, media, status, score,
                                 status_changed=False, explicit_status=False, previous_status=None,
                                 rating_changed=False, previous_score=None, season_changes=None,
                                 context=None, new_entry=False, episode_order=None, now=None):
    from core.activity import has_activity_event
    from core.series_editor import editor_context
    if not season_changes and not rating_changed and (not status_changed or status == 'completed'
            or (status == 'watching' and not explicit_status)):
        return True
    # The same lock order as completion reconciliation, including first rating
    # writes that have no existing activity row to lock.
    await db.execute(select(TrackedEntry.id).where(TrackedEntry.user_id==user_id,
        TrackedEntry.media_id==media.id).with_for_update())
    await db.execute(select(func.pg_advisory_xact_lock(user_id,media.id)))
    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    context = context or await editor_context(db, media, user_id)
    order = episode_order or context.order
    number, partial = season_target(context, status=status)
    if rating_changed and not context.watched and not status_changed and not season_changes:
        return False  # A standalone overall rating still describes the series.
    if number is None and season_changes:
        number = min(season_changes)
    if number is None:
        return False  # Unknown catalogue: the ordinary title card remains usable.
    rows = list(await db.scalars(select(TrackingActivity).where(
        TrackingActivity.user_id == user_id, TrackingActivity.media_id == media.id
    ).order_by(TrackingActivity.created_at.desc(), TrackingActivity.id.desc())))
    scoped = [row for row in rows if (row.payload or {}).get('episode_order') == order
              and ((row.payload or {}).get('season_number') or (row.payload or {}).get('season_ratings'))]
    changed_status = status_changed and status != 'completed' and (status != 'watching' or explicit_status)
    status_card = None
    if changed_status:
        status_card = next((row for row in scoped if row.payload.get('season_number') == number
                            and row.payload.get('season_status') and in_window(row, now)), None)
        if status_card is None:
            details = {'window_key': f'season-status:{order}:{number}:{now.isoformat()}',
                       'window_started_at': now.isoformat(), 'episode_order': order,
                       'season_number': number, 'season_status': True, 'status_start': previous_status}
            status_card = TrackingActivity(user_id=user_id, media_id=media.id, created_at=now, payload=details)
            db.add(status_card)
            scoped.insert(0, status_card)
        details = dict(status_card.payload)
        details.update(status_changed=details.get('status_start') != status,
                       started_watching=status == 'watching',
                       continued_watching=status == 'watching' and previous_status in ('paused', 'dropped') and partial)
        status_card.status, status_card.payload = status, details
        if not details.get('season_start_id'):
            status_card.created_at = now
        # A more significant status supersedes this season's episode window.
        for row in rows:
            payload = dict(row.payload or {})
            positions = payload.get('episode_positions') or []
            if in_window(row, now) and payload.get('episodes_watched') and positions and all(
                    position.startswith(f'S{number}E') for position in positions):
                payload.update(episodes_watched=0, finished_seasons=[])
                row.payload = payload
                if not has_activity_event(row.status, payload):
                    await db.delete(row)
    changes = dict(season_changes or {})
    if rating_changed:
        target, _ = season_target(context, rating=not (explicit_status and status == 'watching'), status=status)
        # Explicit season scores always take precedence over the series fallback.
        explicit = await db.scalar(select(Rating.rating).where(Rating.user_id == user_id,
            Rating.media_id == media.id, Rating.season_number == target,
            Rating.episode_order == (None if order == 'tmdb:aired' else order)))
        if target not in changes and explicit is None:
            changes[target] = (previous_score, score, 'series')
        elif target not in changes:
            from core.activity import record_daily_activity
            await record_daily_activity(db, user_id=user_id, media_id=media.id, status=status, score=score,
                rating_changed=True, previous_score=previous_score, now=now)
    for sn, change in sorted(changes.items()):
        before, after, *source = change
        source = source[0] if source else 'season'
        originals = [row for row in scoped if str(sn) in rating_values(row)]
        original = originals[0] if originals else None
        # Corrections keep the original timestamp and fixed 24/72 hour window.
        correction = False
        if original and original.payload.get('first_rated_at'):
            start = datetime.fromisoformat(original.payload['first_rated_at'])
            correction = start <= now < start + timedelta(hours=72 if status in ('completed', 'dropped') else 24)
        matching = next((row for row in scoped if row.payload.get('season_number') == sn
                         and (row.payload.get('season_completion_id') or row.payload.get('season_status'))
                         and in_window(row, now)), None)
        if correction:
            target_card = original
        elif matching:
            target_card = matching
        elif new_entry:
            continue  # Bulk additions must not invent a separate rating history.
        else:
            target_card = next((row for row in scoped if row.payload.get('season_rating_group')
                                and in_window(row, now)), None)
            if target_card is None and after is not None:
                target_card = TrackingActivity(user_id=user_id, media_id=media.id, status=status,
                    created_at=now, payload={'window_key': f'season-rating:{order}:{now.isoformat()}',
                        'window_started_at': now.isoformat(), 'episode_order': order,
                        'season_rating_group': True})
                db.add(target_card)
                scoped.insert(0, target_card)
        if target_card is None:
            continue
        previous = None if correction or original is None or before is None else rating_values(original).get(str(sn), before)
        put_rating(target_card, sn, after, source=source, previous=previous, now=now)
        if not has_activity_event(target_card.status, target_card.payload):
            await db.delete(target_card)
    # A status can display a snapshot even when the user didn't change a score.
    if status_card is not None and status_card.score is None:
        explicit = await db.scalar(select(Rating.rating).where(Rating.user_id == user_id,
            Rating.media_id == media.id, Rating.season_number == number,
            Rating.episode_order == (None if order == 'tmdb:aired' else order)))
        fallback = explicit if explicit is not None else score
        if fallback is not None:
            put_rating(status_card, number, fallback, source='season' if explicit is not None else 'series', now=now, event=False)
    if status_card is not None and not has_activity_event(status_card.status, status_card.payload):
        await db.delete(status_card)
    await db.flush()
    return True


def present_series_activity(activity, media, *, today=None):
    """Cheap legacy/release compatibility using metadata already joined to feeds."""
    details = dict(activity.payload or {})
    if media.media_type.value != 'series':
        return details
    from datetime import date
    today = (today or date.today()).isoformat()
    premieres = {int(s['season_number']): str(s['air_date'])[:10]
                 for s in (media.tmdb_data or {}).get('seasons', []) if isinstance(s, dict)
                 and (s.get('season_number') or 0) > 0 and s.get('air_date')
                 and str(s['air_date'])[:10] <= today}
    recorded = details.get('season_number') or max(details.get('finished_seasons') or [0])
    import re
    position = re.match(r'S(\d+)E\d+', str(details.get('position') or ''))
    recorded = recorded or (int(position[1]) if position else 0)
    if details.get('series_completed') and any(number > recorded for number in premieres):
        details['series_completed'] = False
        details['status_changed'] = False
    if activity.status == 'completed' and details.get('status_changed') and not details.get('season_completion_id'):
        inferred = [number for number, day in premieres.items() if day <= activity.created_at.date().isoformat()]
        number = recorded or max(inferred or [1])
        details.update(status_changed=False, season_number=number, finished_seasons=[number])
    elif details.get('status_changed') and not details.get('season_status') and (
            activity.status in ('planning','paused','dropped') or
            (activity.status == 'watching' and details.get('started_watching'))):
        details.update(season_status=True, season_number=recorded or 1)
    return details
