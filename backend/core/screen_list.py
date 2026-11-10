"""Bounded profile rows and compact membership for movie and series lists."""

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from sqlalchemy import Integer, any_, func, literal, or_, select
from sqlalchemy.dialects.postgresql import ARRAY

from core import season_releases, tracking_projection
from core.tracking_access import anime_is_visible
from core.tracking_rules import effective_score
from models import Media, WatchEvent
from models.base import MediaType
from models.tracking import MediaFavorite, TrackedEntry

ScreenScope = Literal['movie', 'series', 'all']
SCREEN_STATUSES = ('watching', 'completed', 'paused', 'dropped', 'planning')
STATUS_ORDER = {value: index for index, value in enumerate(SCREEN_STATUSES)}
ROW_FIELDS = frozenset(('id', 'type', 'title', 'poster', 'backdrop', 'backdrop_fallbacks', 'background_status', 'year', 'genres', 'release_date',
    'status', 'rating_mode', 'score', 'progress', 'favorite', 'rewatch_count', 'updated_at', 'notes',
    'released_episodes', 'season_position', 'progress_positions', 'new_seasons', 'new_episodes',
    'availability_dot', 'availability_reason', 'entry', 'editor_library'))


def membership(user_id, scope: ScreenScope, columns):
    return select(*columns).select_from(TrackedEntry).join(Media, Media.id == TrackedEntry.media_id).outerjoin(
        MediaFavorite, (MediaFavorite.user_id == TrackedEntry.user_id) & (MediaFavorite.media_id == Media.id)
    ).where(TrackedEntry.user_id == user_id, Media.media_type.in_(
        [MediaType.movie, MediaType.series] if scope == 'all' else [MediaType(scope)]
    ))


async def episode_history(db, user_id, metadata):
    """Read just episode identity, position, release and watch evidence once per batch."""
    tmdb_ids, tvdb_ids, imported_ids = set(), set(), set()
    for data in metadata:
        ids = data.get('tracking_episode_ids') or []
        (tvdb_ids if data.get('tracking_catalogue_provider') == 'tvdb' else tmdb_ids).update(ids)
        imported_ids.update(data.get('tracking_import_episode_media_ids') or [])
    filters = [column == any_(literal(list(ids), type_=ARRAY(Integer))) for column, ids in (
        (Media.tmdb_id, tmdb_ids), (Media.tvdb_id, tvdb_ids), (Media.id, imported_ids)) if ids]
    imported = Media.tmdb_data['tracking_import_released'].as_boolean()
    watched = select(WatchEvent.id).where(WatchEvent.user_id == user_id, WatchEvent.media_id == Media.id,
        WatchEvent.completed.is_(True)).exists()
    rows = (await db.execute(select(Media.id, Media.tmdb_id, Media.tvdb_id, Media.season_number,
        Media.episode_number, Media.release_date, imported.label('imported'), watched.label('watched'))
        .where(Media.media_type == MediaType.episode, Media.season_number > 0, or_(*filters),
               or_(Media.release_date.is_not(None), imported.is_(True))))).all() if filters else []
    by_id, by_tmdb, by_tvdb = {}, {}, {}
    for row in rows:
        by_id[row.id] = row
        if row.tmdb_id is not None:
            by_tmdb.setdefault(row.tmdb_id, []).append(row)
        if row.tvdb_id is not None:
            by_tvdb.setdefault(row.tvdb_id, []).append(row)

    def episodes(data):
        provider = by_tvdb if data.get('tracking_catalogue_provider') == 'tvdb' else by_tmdb
        matched = {row.id: row for identifier in data.get('tracking_episode_ids') or []
            for row in provider.get(identifier, ())}
        matched.update({identifier: by_id[identifier] for identifier in data.get('tracking_import_episode_media_ids') or []
            if identifier in by_id})
        return matched.values()

    return episodes


def released_episodes(episodes, today):
    return [row for row in episodes if row.imported or row.release_date and row.release_date[:10] <= today]


async def completed_movies(db, user_id, ids):
    return set(await db.scalars(select(WatchEvent.media_id).where(WatchEvent.user_id == user_id,
        WatchEvent.media_id == any_(literal(ids, type_=ARRAY(Integer))), WatchEvent.completed.is_(True)).distinct())) if ids else set()


async def rows_for_profile(db, user_id, viewer, owner, scope: ScreenScope, ids=None):
    query = membership(user_id, scope, (TrackedEntry, Media, MediaFavorite.media_id))
    if ids is not None:
        if not ids:
            return []
        query = query.where(Media.id.in_(ids))
    loaded = (await db.execute(query.order_by(Media.title, Media.id))).all()
    if not await anime_is_visible(db):
        loaded = [row for row in loaded if not tracking_projection.is_anime(row[1])]
    entries = [tracking_projection.entry_data(entry, media, owner, favorite=bool(entry.favorite or favorite))
        for entry, media, favorite in loaded]
    watched_movies = await completed_movies(db, user_id, [media.id for _, media, _ in loaded if media.media_type == MediaType.movie])
    history = await episode_history(db, user_id, [media.tmdb_data or {} for _, media, _ in loaded if media.media_type == MediaType.series])
    today = date.today().isoformat()
    for entry, (_, media, _) in zip(entries, loaded):
        entry['availability_dot'], entry['availability_reason'] = False, None
        if media.media_type == MediaType.movie:
            entry['progress'] = int(bool(entry['progress'] or entry['status'] == 'completed' or media.id in watched_movies))
        else:
            project_series(entry, media, history(media.tmdb_data or {}), today, owner)
    if owner:
        from core.tracking_editor import attach_editor_context
        await attach_editor_context(db, viewer, entries, media_rows=[media for _, media, _ in loaded])
    if ids is None:
        return entries
    by_id = {entry['id']: {key: value for key, value in entry.items() if key in ROW_FIELDS} for entry in entries}
    return [by_id[identifier] for identifier in dict.fromkeys(ids) if identifier in by_id]


def project_series(entry, media, episodes, today, owner):
    data = media.tmdb_data or {}
    pointer = data.get('last_episode_to_air') or {}
    entry['latest_released_position'] = (
        f"S{pointer['season_number']}E{pointer['episode_number']}"
        if pointer.get('season_number') is not None and pointer.get('episode_number') is not None else None
    )
    upcoming = data.get('next_episode_to_air') or {}
    entry['next_episode_to_air'] = {key: upcoming.get(key) for key in ('season_number', 'episode_number', 'air_date')} if upcoming else None
    episode_dates = [(row.season_number, row.release_date, row.id, bool(row.imported)) for row in episodes]
    entry['currently_airing'], entry['next_release_date'] = season_releases.current_airing_details(
        {**data, 'status': data.get('status') or media.status}, episode_dates)
    watched = {row.id for row in episodes if row.watched and (row.imported or row.release_date and row.release_date[:10] <= today)}
    entry['availability_dot'], entry['availability_reason'] = season_releases.availability_dot(
        data, episode_dates, watched, entry['status'])
    if not data.get('tracking_catalogue_refreshed_at'):
        return
    released = sorted(released_episodes(episodes, today), key=lambda row: (row.season_number or 0, row.episode_number or 0))
    entry['released_episodes'] = len(released)
    if owner:
        entry['progress_positions'] = [f'S{row.season_number}E{row.episode_number}' for row in released]
    if released:
        last = released[-1]
        entry['latest_released_position'] = f'S{last.season_number}E{last.episode_number}'
    entry['progress'] = sum(row.watched for row in released)
    last_watched = next((row for row in reversed(released) if row.watched), None)
    entry['season_position'] = f'S{last_watched.season_number}E{last_watched.episode_number}' if last_watched else None
    entry.update(tracking_projection.release_annotations(released, watched, entry['status']))


@dataclass(slots=True)
class Member:
    id: int
    title: str
    status: str
    score: float | None
    progress: int
    updated: datetime
    year: str
    genres: list[str]
    favorite: bool
    rank: int

    def index_row(self):
        return (self.id, self.title, self.status, self.score, self.progress,
            self.updated.isoformat() if self.updated else None, self.year or None,
            self.genres, self.favorite, None, self.rank)


async def members(db, user_id, scope: ScreenScope, *, progress=False, cap=None):
    show_anime = await anime_is_visible(db)
    metadata = Media.tmdb_data
    anime_keys = () if show_anime else ('origin_country', 'production_countries', 'original_language')
    history_keys = ('tracking_episode_ids', 'tracking_import_episode_media_ids',
        'tracking_catalogue_provider', 'tracking_catalogue_refreshed_at') if progress else ()
    keys = ('genres', *anime_keys, *history_keys)
    columns = (Media.id, func.lower(Media.title).label('title'), Media.media_type, Media.release_date,
        TrackedEntry.status, TrackedEntry.rating_mode, TrackedEntry.manual_score, TrackedEntry.season_scores,
        TrackedEntry.progress, TrackedEntry.updated_at, (TrackedEntry.favorite | MediaFavorite.media_id.is_not(None)).label('favorite'),
        *(metadata[key].label(key) for key in keys))
    query = membership(user_id, scope, columns).order_by(func.lower(Media.title), Media.id)
    if cap is not None:
        query = query.limit(cap + 1)
    rows = (await db.execute(query)).all()
    # A capped result is never represented as a complete local index.
    if cap is not None and len(rows) > cap:
        return None
    inputs = [(row, {key: row._mapping[key] for key in keys if row._mapping[key] is not None}) for row in rows]
    if not show_anime:
        inputs = [(row, data) for row, data in inputs if not tracking_projection.is_anime_data(data)]
    movies = await completed_movies(db, user_id, [row.id for row, _ in inputs if row.media_type == MediaType.movie]) if progress else set()
    history = await episode_history(db, user_id, [data for row, data in inputs if row.media_type == MediaType.series
        and data.get('tracking_catalogue_refreshed_at')]) if progress else None
    today = date.today().isoformat()
    result = []
    for rank, (row, data) in enumerate(inputs):
        count = row.progress
        if row.media_type == MediaType.movie:
            count = int(bool(count or row.status == 'completed' or row.id in movies))
        elif history and data.get('tracking_catalogue_refreshed_at'):
            count = sum(episode.watched for episode in released_episodes(history(data), today))
        year = (row.release_date or '')[:4]
        result.append(Member(row.id, row.title, row.status, effective_score(row.rating_mode, row.manual_score, row.season_scores),
            count, row.updated_at, year if len(year) == 4 and year.isascii() and year.isdigit() else '',
            tracking_projection.media_genres(data), bool(row.favorite), rank))
    return result


def select_page(entries, *, status, sort, q, genre, year, favorites, offset, limit):
    status_counts, group_counts = {}, {}
    for member in entries:
        status_counts[member.status] = status_counts.get(member.status, 0) + 1
    needle = q.strip().lower()
    matches = [member for member in entries if (not status or member.status == status) and needle in member.title
        and (not genre or genre in member.genres) and (not year or year == member.year) and (not favorites or member.favorite)]
    def order(member):
        if sort == 'score':
            return member.score is None, -(member.score or 0), member.id
        if sort == 'progress':
            return -member.progress, member.id
        if sort == 'updated':
            return member.updated or datetime.min, -member.id
        return member.rank,
    matches.sort(key=order, reverse=sort == 'updated')
    matches.sort(key=lambda member: STATUS_ORDER.get(member.status, len(SCREEN_STATUSES)))
    for member in matches:
        group_counts[member.status] = group_counts.get(member.status, 0) + 1
    return [member.id for member in matches[offset:offset + limit]], dict(
        offset=offset, total=len(matches), status_counts=status_counts, group_counts=group_counts,
        genres=sorted({genre for member in entries for genre in member.genres}),
        years=sorted({member.year for member in entries if member.year}, reverse=True))
