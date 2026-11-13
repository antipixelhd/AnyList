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
    selected = context.selected_position()
    if selected:
        number,count = selected['season_number'],selected['episode_count']
        total = next(expected or len(members) for sn,members,expected in context.groups if sn == number)
        return number, 0 < count < total
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
    baselines = dict(details.get('season_rating_baselines') or {})
    if details.get('rating_changed'):
        for sn in values:
            baselines.setdefault(sn,details.get('previous_score') if len(values) == 1 else None)
    if score is None:
        values.pop(key, None)
        baselines.pop(key, None)
    else:
        values[key] = score
        if event:
            baselines.setdefault(key,previous)
    sources = dict(details.get('season_rating_sources') or {})
    sources[key] = source
    details.update(season_ratings=values, season_rating_sources=sources,season_rating_baselines=baselines,
                   rating_changed=any(sn in baselines and baselines[sn] != value for sn,value in values.items()),
                   rating_snapshot=bool(values))
    previous = baselines.get(next(iter(values))) if len(values) == 1 else None
    if previous is not None and len(values) == 1 and previous != next(iter(values.values())):
        details.update(previous_score=previous, rating_first=False)
    else:
        details.pop('previous_score', None)
        details['rating_first'] = True
    if now and not details.get('first_rated_at') and score is not None:
        details['first_rated_at'] = now.isoformat()
    row.score = sum(values.values()) / len(values) if values else None
    row.payload = details


async def remove_season_milestone(db, card, *, target=None):
    """Remove only the dated action; an actual rating remains independent."""
    from core.activity import has_activity_event
    details = dict(card.payload or {})
    if target is not None:
        if details.get('rating_changed') and not (target.payload or {}).get('rating_changed'):
            for number,score in rating_values(card).items():
                put_rating(target,int(number),score,
                    source=details.get('season_rating_sources',{}).get(number,'season'),
                    previous=details.get('season_rating_baselines',{}).get(number,details.get('previous_score')),
                    now=datetime.fromisoformat(details.get('first_rated_at') or card.created_at.isoformat()))
            if details.get('first_rated_at'):
                target.payload = {**target.payload,'first_rated_at':details['first_rated_at']}
        await db.delete(card)
        return
    for key in ('season_start_id','season_completion_id','series_completed','finished_seasons','completion_date_source',
                'started_watching','continued_watching'):
        details.pop(key,None)
    if card.status == 'watching':
        details['status_changed'] = False
        details.pop('season_status',None)
    if details.get('rating_changed') and rating_values(card):
        if not details.get('status_changed'):
            details['season_rating_group'] = True
            card.created_at = datetime.fromisoformat(details.get('first_rated_at') or card.created_at.isoformat())
            details['window_started_at'] = card.created_at.isoformat()
            details['window_key'] = f'season-rating:detached:{card.id}'
    card.payload = details
    if not has_activity_event(card.status,details):
        await db.delete(card)


async def absorb_season_actions(db, cards, milestones):
    """Completion wins inside its season's window without losing other seasons."""
    from core.activity import activity_window_start, has_activity_event
    removed = set()
    for card in cards:
        details = card.payload or {}
        if details.get('season_completion_id'):
            continue
        for target in milestones:
            if card is target:
                continue
            payload = target.payload
            number = payload['season_number']
            at = datetime.fromisoformat(payload['window_started_at'])
            start_target = bool(payload.get('season_start_id'))
            if start_target and not details.get('season_rating_group'):
                continue
            if details.get('episode_order') != payload['episode_order'] or not (
                    in_window(card, at) or in_window(target, activity_window_start(card))):
                continue
            values = rating_values(card)
            if str(number) in values:
                had_rating = target.payload.get('rating_changed')
                put_rating(target, number, values[str(number)],
                    source=details.get('season_rating_sources',{}).get(str(number),'season'),
                    previous=details.get('season_rating_baselines',{}).get(str(number),details.get('previous_score')),
                    now=datetime.fromisoformat(details.get('first_rated_at') or at.isoformat()),
                    event=bool(details.get('rating_changed')))
                if details.get('rating_changed') and not had_rating and details.get('first_rated_at'):
                    target.payload = {**target.payload,'first_rated_at':details['first_rated_at']}
            if details.get('season_rating_group') and str(number) in values:
                put_rating(card,number,None,source='season')
            elif details.get('season_status') and details.get('season_number') == number:
                removed.add(card)
            if not has_activity_event(card.status, card.payload):
                removed.add(card)
    for card in removed:
        await db.delete(card)
    return removed


async def absorb_completed_progress(db, cards, milestones, groups, order):
    """A completion replaces overlapping episode/start evidence for its members.

    Resolve by canonical episode identity so alternate orders and windows that
    cross a season boundary retain the other season's progress.
    """
    from core.activity import activity_window_start, has_activity_event
    from core.streaming_activity import _rebuild
    episodes = [ep for _, members, _ in groups for ep in members]
    native = {f'S{ep.season_number}E{ep.episode_number}': ep.id for ep in episodes}
    display = {f'S{sn}E{en}': ep.id for sn, members, _ in groups for en, ep in enumerate(members, 1)}
    first_season = groups[0][0] if groups else 1
    members = {sn: {ep.id for ep in eps} for sn, eps, _ in groups}

    def overlaps(card, target):
        a, b = activity_window_start(card), activity_window_start(target)
        return a < b + timedelta(hours=24) and b < a + timedelta(hours=24)

    def trim(payload, covered):
        payload = dict(payload)
        lookup = display if payload.get('progress_order') == order else native
        labels = payload.get('episode_positions')
        ids = payload.get('progress_episode_ids')
        if ids is None:
            if labels is None:
                ordered = list(lookup)
                start, end = payload.get('position_start'), payload.get('position')
                labels = ordered[ordered.index(start):ordered.index(end)+1] if start in lookup and end in lookup else []
            ids = [lookup.get(label) for label in labels]
        labels = labels or [next((label for label, key in lookup.items() if key == ep_id), None) for ep_id in ids]
        removed = {ep_id for ep_id in ids if ep_id in covered}
        if not removed or not payload.get('episodes_watched'):
            return payload
        remaining = [(ep_id, label) for ep_id, label in zip(ids, labels) if ep_id not in removed]
        payload.update(episodes_watched=max(0, int(payload['episodes_watched'])-len(removed)),
            progress_episode_ids=[ep_id for ep_id, _ in remaining],
            episode_positions=[label for _, label in remaining if label],
            position_start=remaining[0][1] if remaining else None,
            position=remaining[-1][1] if remaining else None)
        # Retain the absorbed episodes in the window baseline, so a subsequent
        # sync/edit cannot recreate them on a surviving cross-season card.
        baseline = payload.get('watched_start_ids')
        if baseline is not None:
            payload['watched_start_ids'] = sorted(set(baseline) | removed)
        payload['progress_start'] = int(payload.get('progress_start') or 0) + len(removed)
        return payload

    def retain(target, card, payload):
        # Keep the encapsulated window, so undoing completion can reveal its
        # still-watched episodes instead of losing the correction baseline.
        lookup = display if payload.get('progress_order') == order else native
        labels = payload.get('episode_positions')
        if labels is None:
            ordered = list(lookup)
            start, end = payload.get('position_start'), payload.get('position')
            labels = ordered[ordered.index(start):ordered.index(end)+1] if start in lookup and end in lookup else []
        ids = payload.get('progress_episode_ids') or [lookup.get(label) for label in labels]
        pairs = [(ep_id, label) for ep_id, label in zip(ids, labels)
                 if ep_id in members.get(target.payload['season_number'], set())]
        if not pairs or not payload.get('episodes_watched'):
            return
        snapshot = {key: payload[key] for key in ('window_key', 'window_started_at', 'progress_order',
            'watched_start_ids', 'progress_start', 'progress') if key in payload}
        snapshot.update(progress_episode_ids=[ep_id for ep_id, _ in pairs],
                        episode_positions=[label for _, label in pairs], created_at=card.created_at.isoformat(),
                        status=card.status, score=card.score)
        snapshot.setdefault('window_started_at', activity_window_start(card).isoformat())
        saved = [dict(item) for item in target.payload.get('absorbed_progress', [])]
        previous = next((item for item in saved if item.get('window_started_at') == snapshot.get('window_started_at')), None)
        if previous:
            previous.update(snapshot)
        else:
            saved.append(snapshot)
        target.payload = {**target.payload, 'absorbed_progress': saved}

    for card in cards:
        if card in db.deleted or (card.payload or {}).get('episode_order'):
            continue
        targets = [target for target in milestones if target.payload.get('season_completion_id') and overlaps(card, target)]
        if not targets:
            continue
        numbers = {target.payload['season_number'] for target in targets}
        covered = set().union(*(members.get(sn, set()) for sn in numbers))
        def covers_start(payload):
            if payload.get('start_position'):
                return native.get(payload['start_position']) in covered
            sn = payload.get('season_number') or first_season
            return sn in numbers if order == 'tmdb:aired' else native.get(f'S{sn}E1') in covered
        details = dict(card.payload or {})
        for target in targets:
            retain(target, card, details.get('streaming_base', details))
        if 'streaming_base' in details:
            details['streaming_base'] = trim(details['streaming_base'], covered)
            events = {}
            for key, event in details.get('streaming_events', {}).items():
                if event.get('episodes_watched') and native.get(event.get('position')) in covered:
                    continue
                if key == 'start' and covers_start(event):
                    continue
                events[key] = event
            details['streaming_events'] = events
            card.payload = details
            _rebuild(card)
        else:
            details = trim(details, covered)
            card.payload = details
        details = dict(card.payload)
        start = details.get('streaming_events', {}).get('start') or details
        if details.get('started_watching') and covers_start(start):
            details['started_watching'] = False
            if card.status == 'watching':
                details['status_changed'] = False
            if 'streaming_base' in details:
                base = dict(details['streaming_base'])
                base['started_watching'] = False
                if details.get('streaming_base_status') == 'watching':
                    base['status_changed'] = False
                details['streaming_base'] = base
        card.payload = details
        if not has_activity_event(card.status, details):
            await db.delete(card)


def restore_absorbed_progress(db, cards, snapshots, *, user_id, media_id, watched_ids, completed_ids):
    """Restore a corrected completion's net progress in its original window."""
    for snapshot in snapshots:
        pairs = [(ep_id, label) for ep_id, label in zip(snapshot['progress_episode_ids'], snapshot['episode_positions'])
                 if ep_id in watched_ids and ep_id not in completed_ids]
        if not pairs:
            continue
        row = next((card for card in cards if card not in db.deleted
            and not (card.payload or {}).get('episode_order') and
            (card.payload or {}).get('window_started_at') == snapshot.get('window_started_at')), None)
        if row is None:
            row = TrackingActivity(user_id=user_id, media_id=media_id, status=snapshot['status'], score=snapshot.get('score'),
                created_at=datetime.fromisoformat(snapshot['created_at']), payload={})
            db.add(row)
            cards.append(row)
        details = dict(row.payload or {})
        pairs = dict(zip(details.get('progress_episode_ids', []), details.get('episode_positions', []))) | dict(pairs)
        pairs = {key: label for key, label in pairs.items() if key in watched_ids and key not in completed_ids}
        pairs = sorted(pairs.items(), key=lambda pair: tuple(map(int,pair[1][1:].split('E'))))
        baseline = set(snapshot.get('watched_start_ids') or []) | completed_ids
        if details.get('watched_start_ids') is not None:
            baseline &= set(details['watched_start_ids']) | completed_ids
        details.update({key: snapshot[key] for key in ('window_key', 'window_started_at', 'progress_order') if key in snapshot})
        details.update(progress_episode_ids=[key for key, _ in pairs], episode_positions=[label for _, label in pairs],
            episodes_watched=len(pairs), watched_start_ids=sorted(baseline), progress_start=len(baseline),
            progress=len(baseline | set(dict(pairs))), position_start=pairs[0][1], position=pairs[-1][1])
        if 'streaming_base' in details:
            from core.streaming_activity import _rebuild
            progress_keys = {'progress_episode_ids', 'episode_positions', 'episodes_watched', 'watched_start_ids',
                             'progress_start', 'progress', 'position_start', 'position', 'progress_order'}
            details['streaming_base'] = {**details['streaming_base'],
                **{key: value for key, value in details.items() if key in progress_keys}}
            row.payload = details
            _rebuild(row)
        else:
            row.payload = details


async def record_season_activity(db, *, user_id, media, status, score,
                                 status_changed=False, explicit_status=False, previous_status=None,
                                 rating_changed=False, previous_score=None, season_changes=None,
                                 context=None, new_entry=False, episode_order=None, season_advanced=False, now=None):
    from core.activity import has_activity_event
    from core.series_editor import editor_context
    if not season_advanced and not season_changes and not rating_changed and (not status_changed or status == 'completed'
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
    if status == 'watching':
        changed_status = changed_status or season_advanced
        selected = context.selected_position()
        if selected:
            total = next(expected or len(members) for sn,members,expected in context.groups if sn == number)
            if selected['episode_count'] >= total:
                changed_status = False
    status_card = None
    if changed_status:
        status_card = next((row for row in scoped if row.payload.get('season_number') == number
                            and row.payload.get('season_status') and in_window(row, now)), None)
        if status_card is None:
            details = {'window_key': f'season-status:{order}:{number}:{now.isoformat()}',
                       'window_started_at': now.isoformat(), 'episode_order': order,
                       'season_number': number, 'season_status': True, 'status_start': None if season_advanced else previous_status}
            status_card = TrackingActivity(user_id=user_id, media_id=media.id, created_at=now, payload=details)
            db.add(status_card)
            scoped.insert(0, status_card)
        details = dict(status_card.payload)
        if status != 'watching':
            details.pop('season_start_id',None)
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
        if correction:
            previous = original.payload.get('season_rating_baselines',{}).get(str(sn),original.payload.get('previous_score'))
        else:
            previous = None if original is None or before is None else rating_values(original).get(str(sn), before)
        put_rating(target_card, sn, after, source=source, previous=previous, now=now)
        if not has_activity_event(target_card.status, target_card.payload):
            if target_card in db.new:
                db.expunge(target_card)
            else:
                await db.delete(target_card)
            scoped.remove(target_card)
            if target_card is status_card:
                status_card = None
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
