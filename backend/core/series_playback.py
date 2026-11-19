"""Keep the latest playback activity independent of episode coverage."""

from core.status_provenance import naive_utc, provider_changed_at
from sqlalchemy import and_, cast, func, or_
from sqlalchemy.dialects.postgresql import JSONPATH


def has_active_playback(entry):
    statuses = getattr(entry, 'season_list_statuses', None) or {}
    return (bool(getattr(entry, 'latest_episode_activity', None)) or (entry.status == 'watching' and not statuses)
            or any('watching' in season_statuses.values() for season_statuses in statuses.values()))


def active_playback_filter(model):
    return or_(and_(model.status == 'watching', model.season_list_statuses.is_(None)),
               model.latest_episode_activity.is_not(None),
               func.jsonb_path_exists(model.season_list_statuses, cast('$.*.* ? (@ == "watching")', JSONPATH)))


def watching_episodes(entry, released):
    statuses = (getattr(entry, 'season_list_statuses', None) or {}).get('tmdb:aired')
    if not statuses:
        return released
    return [episode for episode in released if statuses.get(str(episode.season_number)) == 'watching']


def save_season_statuses(entry, order, statuses):
    by_order = dict(entry.season_list_statuses or {})
    if statuses:
        by_order[order] = statuses
    else:
        by_order.pop(order, None)
    entry.season_list_statuses = by_order or None


def remember_episode(entry, episode_id, at, *, completed=False):
    at = naive_utc(at)
    activity = entry.latest_episode_activity or {}
    previous = provider_changed_at(activity)
    finished_at_same_time = (completed and activity.get('episode_id') == episode_id and not activity.get('completed'))
    if at is None or (previous is not None and (at < previous or at == previous and not finished_at_same_time)):
        return False
    entry.latest_episode_activity = {
        'episode_id': episode_id, 'completed': completed, 'updated_at': at.isoformat(),
    }
    return True


def activity_resume(entry, released, offsets):
    """Return the selected episode, its offset and the activity timestamp."""
    activity = getattr(entry, 'latest_episode_activity', None)
    if not activity:
        return None, None, None
    index = next((i for i, episode in enumerate(released) if episode.id == activity['episode_id']), None)
    if index is None:
        return None, None, None
    next_index = index + int(activity['completed'])
    target = released[next_index] if next_index < len(released) else None
    offset = offsets.get(target.id) if target else None
    at = provider_changed_at(activity)
    if offset and (offset.progress_seconds <= 0 or not 0 < offset.progress_percent < 0.9):
        offset = None
    return target, offset, at
