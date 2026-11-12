"""One projection of canonical watches into the viewer's selected seasons.

No provider I/O, synthetic episode dates, or rewatch milestones. Reconciliation
is repeatable and its receipt is deleted only in the publishing transaction.
"""
import asyncio
import hashlib
import logging
from collections import defaultdict
from datetime import date, datetime, time

from sqlalchemy import or_, select, update, func, tuple_
from sqlalchemy.dialects.postgresql import insert

from models import Media, Show, WatchEvent, UserShowEpisodeOrder, ShowEpisodePosition
from models.base import MediaType
from models.seasons import SeriesSeason, SeasonTracking, SeasonReconcileJob
from models.tracking import TrackedEntry, TrackingActivity
from core.episode_order import normalize_order_key

log = logging.getLogger(__name__)


def completion_evidence(episodes, events, expected):
    """Coverage proves completion; trustworthy season-local dates date it."""
    by_episode = defaultdict(list)
    for event in events:
        if event.completed:
            by_episode[event.media_id].append(event)
    watched = sum(bool(by_episode[ep.id]) for ep in episodes)
    complete = bool(expected and len(episodes) == expected and watched == expected
                    and all(ep.release_date and ep.release_date[:10] <= date.today().isoformat()
                            or (ep.tmdb_data or {}).get('tracking_import_released') for ep in episodes))
    reliable = {}
    for ep in episodes:
        history = by_episode[ep.id]
        earliest = min((e.watched_at for e in history if e.watched_at), default=None)
        values = [e.watched_at for e in history if e.watched_at
                  and not (e.date_inferred or e.date_shared or e.provisional)]
        # A separate later play cannot date the original undated/estimated play.
        # Authoritative corrections replace the original event via watch_dates.
        if values and not any(e.watched_at is None for e in history) and min(values) == earliest:
            reliable[ep.id] = min(values)
    if not complete:
        return False, watched, None, None, {}
    if len(reliable) == expected:
        day = max(reliable.values()).date()
        return True, watched, day, 'episode', {'episode_ids': sorted(reliable), 'rule': 'first_complete_coverage'}
    # X, X-1, X-2 estimate a date only after independent complete coverage.
    tail = [ep.id for ep in episodes[-3:] if ep.id in reliable]
    day = max((reliable[key] for key in tail), default=None)
    return True, watched, day.date() if day else None, 'inferred' if day else None, {
        'episode_ids': tail, 'rule': 'season_tail' if day else 'unknown'}


async def season_groups(db, media, user_id):
    identities = []
    if media.tmdb_id:
        identities.append(Show.tmdb_id == media.tmdb_id)
    if media.tvdb_id:
        identities.append(Show.tvdb_id == media.tvdb_id)
    show = await db.scalar(select(Show).where(or_(*identities))) if identities else None
    if show is None:
        return 'tmdb:aired', [], {}
    episodes = list(await db.scalars(select(Media).where(
        Media.show_id == show.id, Media.media_type == MediaType.episode, Media.season_number > 0)))
    data = media.tmdb_data or {}
    catalogue = data.get('tracking_episode_ids')
    if catalogue is not None:
        provider = data.get('tracking_catalogue_provider', 'tmdb')
        episodes = [ep for ep in episodes if getattr(ep, f'{provider}_id', None) in catalogue
                    or (ep.tmdb_data or {}).get('tracking_import_released')]
    order = await db.scalar(select(UserShowEpisodeOrder.episode_order).where(
        UserShowEpisodeOrder.user_id == user_id, UserShowEpisodeOrder.series_tmdb_id == media.tmdb_id)) if media.tmdb_id else None
    order = normalize_order_key(order)
    metadata = {int(s['season_number']): s for s in data.get('seasons', [])
                if isinstance(s, dict) and s.get('season_number', 0) > 0}
    groups = defaultdict(list)
    expected = {number: int(s.get('episode_count') or 0) for number, s in metadata.items()}
    if order == 'tmdb:aired':
        for ep in episodes:
            groups[ep.season_number].append((ep.episode_number or 0, ep))
    else:
        positions = list(await db.scalars(select(ShowEpisodePosition).where(
            ShowEpisodePosition.series_tmdb_id == media.tmdb_id, ShowEpisodePosition.order_key == order,
            ShowEpisodePosition.display_season > 0)))
        by_native = {ep.tmdb_id: ep for ep in episodes if ep.tmdb_id}
        expected = defaultdict(int)
        for pos in positions:
            expected[pos.display_season] += 1
            if pos.tmdb_episode_id in by_native:
                groups[pos.display_season].append((pos.display_episode, by_native[pos.tmdb_episode_id]))
        metadata = {}  # Aired season names/art must not label a different grouping.
    return order, [(number, [ep for _, ep in sorted(values, key=lambda item: item[0])], expected.get(number, 0))
                   for number, values in sorted(groups.items())], metadata


async def reconcile(db, user_id, media_id):
    await db.execute(select(func.pg_advisory_xact_lock(user_id, media_id)))
    entry = await db.scalar(select(TrackedEntry).where(TrackedEntry.user_id == user_id, TrackedEntry.media_id == media_id))
    media = await db.get(Media, media_id)
    if entry is None or media is None or media.media_type != MediaType.series:
        return []
    previous = (await db.execute(select(SeriesSeason, SeasonTracking).join(SeasonTracking,
        SeasonTracking.season_id == SeriesSeason.id).where(SeriesSeason.media_id == media_id,
        SeasonTracking.user_id == user_id, SeasonTracking.current.is_(True)))).all()
    await db.execute(update(SeasonTracking).where(SeasonTracking.user_id == user_id,
        SeasonTracking.season_id.in_(select(SeriesSeason.id).where(SeriesSeason.media_id == media_id))).values(current=False))
    order, groups, metadata = await season_groups(db, media, user_id)
    ids = {ep.id for _, episodes, _ in groups for ep in episodes}
    events = list(await db.scalars(select(WatchEvent).where(WatchEvent.user_id == user_id,
        WatchEvent.media_id.in_(ids), WatchEvent.completed.is_(True)))) if ids else []
    events_by_id = defaultdict(list)
    for event in events:
        events_by_id[event.media_id].append(event)
    cards = list(await db.scalars(select(TrackingActivity).where(
        TrackingActivity.user_id == user_id, TrackingActivity.media_id == media_id)))
    previous_orders = {s.episode_order for s, t in previous}
    order_changed = bool(previous_orders and order not in previous_orders)
    result = []
    for number, episodes, expected in groups:
        member_ids = sorted(ep.id for ep in episodes)
        fingerprint = hashlib.sha256(','.join(map(str, member_ids)).encode()).hexdigest()
        statement = insert(SeriesSeason).values(media_id=media_id, episode_order=order, season_number=number,
            membership_key=fingerprint, episode_ids=member_ids, metadata_payload=metadata.get(number, {}))
        await db.execute(statement.on_conflict_do_update(
            index_elements=['media_id', 'episode_order', 'season_number', 'membership_key'],
            set_={'metadata_payload': statement.excluded.metadata_payload}))
        season = await db.scalar(select(SeriesSeason).where(SeriesSeason.media_id == media_id,
            SeriesSeason.episode_order == order, SeriesSeason.season_number == number, SeriesSeason.membership_key == fingerprint))
        await db.execute(insert(SeasonTracking).values(user_id=user_id, season_id=season.id, completed=False,
            date_manual=False, watched_episodes=0, evidence={}, current=True,
            activity_suppressed=order_changed or any(s.season_number == number and t.completed
                and s.membership_key != fingerprint for s, t in previous)).on_conflict_do_nothing())
        tracking = await db.get(SeasonTracking, (user_id, season.id))
        tracking.current = True
        complete, watched, day, source, evidence = completion_evidence(episodes,
            [event for ep in episodes for event in events_by_id[ep.id]], expected)
        if order_changed:
            # Suppress only history already complete under the newly selected
            # order. Future first completions still deserve their own cards.
            tracking.activity_suppressed = complete
        if not complete and tracking.completed and watched == len(episodes) and episodes:
            # Metadata corrections cannot undo a supported historical milestone.
            # Removing a watch still invalidates it because coverage then shrinks.
            complete = True
            day, source, evidence = tracking.completion_date, tracking.date_source, tracking.evidence
        if complete and day is None and order == 'tmdb:aired':
            retained = [event.get('date') for card in cards
                        for key, event in (card.payload or {}).get('streaming_events', {}).items()
                        if key == f'season:{number}' and event.get('date')]
            dates = []
            for value in retained:
                try:
                    dates.append(date.fromisoformat(str(value)[:10]))
                except ValueError:
                    continue
            if dates:
                day, source, evidence = min(dates), 'inferred', {'rule': 'retained_season_activity'}
        tracking.completed, tracking.watched_episodes = complete, watched
        if not tracking.date_manual:
            tracking.completion_date, tracking.date_source, tracking.evidence = day, source, evidence
        result.append((season, tracking))
        card = next((c for c in cards if (c.payload or {}).get('season_completion_id') == season.id), None)
        if not complete or not tracking.completion_date:
            if card:
                await db.delete(card)
            continue
        if card is None and tracking.activity_suppressed:
            continue  # Switching order is a projection change, not a new activity.
        if card is None:
            card = TrackingActivity(user_id=user_id, media_id=media_id, status='watching')
            db.add(card)
        card.created_at = datetime.combine(tracking.completion_date, time.min)
        card.payload = {'window_key': f'season:{season.id}', 'season_completion_id': season.id,
                        'episode_order': order, 'finished_seasons': [number],
                        'completion_date_source': tracking.date_source}
    # Remove old bundled milestone actions. Preserve episodes, ratings and status
    # actions; absorb a title completion into its final season's card.
    completed = [(s, t) for s, t in result if t.completed and t.completion_date]
    for card in cards:
        details = dict(card.payload or {})
        if details.get('season_completion_id'):
            if details.get('episode_order') != order:
                await db.delete(card)
            continue
        details.pop('finished_seasons', None)
        for key in ('streaming_base',):
            if key in details:
                details[key] = {k: v for k, v in details[key].items() if k != 'finished_seasons'}
        if 'streaming_events' in details:
            details['streaming_events'] = {k: v for k, v in details['streaming_events'].items() if not k.startswith('season:')}
        if (entry.status == 'completed' and completed and completed[-1][0].id == result[-1][0].id
                and card.status == 'completed' and details.get('status_changed')):
            last, track = completed[-1]
            if card.created_at.date() == track.completion_date:
                details['status_changed'] = False
                details['streaming_events'] = {k: v for k, v in details.get('streaming_events', {}).items() if k != 'finish'}
        card.payload = details
        from core.activity import has_activity_event
        if not has_activity_event(card.status, details):
            await db.delete(card)
    await db.flush()
    return result


async def run_batch(factory, limit=20):
    count = 0
    failed = []
    # Each receipt commits separately, bounding locks and preserving retries.
    for _ in range(limit):
        async with factory() as db:
            job = await db.scalar(select(SeasonReconcileJob).where(
                tuple_(SeasonReconcileJob.user_id, SeasonReconcileJob.media_id).not_in(failed)).order_by(SeasonReconcileJob.user_id,
                SeasonReconcileJob.media_id).with_for_update(skip_locked=True).limit(1))
            if job is None:
                break
            pair = job.user_id, job.media_id
            try:
                await reconcile(db, *pair)
                await db.delete(job)
                await db.commit()
            except Exception:
                await db.rollback()
                failed.append(pair)
                log.exception('Season reconciliation receipt retained for retry')
                continue
            count += 1
    return count


async def scheduler():
    from db import AsyncSessionLocal
    while True:
        try:
            count = await run_batch(AsyncSessionLocal)
        except Exception:
            log.exception('Season completion reconciliation failed; receipts retained')
            count = 0
        await asyncio.sleep(1 if count else 10)
