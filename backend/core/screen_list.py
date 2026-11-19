"""Bounded profile rows and compact membership for movie and series lists."""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from sqlalchemy import Integer, String, and_, any_, case, column, func, literal, or_, select, values
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import aliased

from core import list_categories, season_completion, season_releases, tracking_projection
from core.episode_release import EpisodeRelease
from core.tracking_access import anime_is_visible
from core.tracking_rules import effective_score
from models import Media, Rating, Show, ShowEpisodePosition, WatchEvent
from models.base import MediaType
from models.seasons import SeasonTracking, SeriesSeason
from models.tracking import MediaFavorite, TrackedEntry

ScreenScope = Literal['movie', 'series', 'all']
SCREEN_STATUSES = tuple(category[0] for category in list_categories.DEFINITIONS['screen'])
STATUS_ORDER = {value: index for index, value in enumerate(SCREEN_STATUSES)}
ROW_FIELDS = frozenset(('id', 'type', 'title', 'original_title', 'tagline', 'poster', 'backdrop', 'backdrop_fallbacks', 'background_status', 'year', 'genres', 'release_date',
    'status', 'rating_mode', 'score', 'progress', 'favorite', 'rewatch_count', 'updated_at', 'notes',
    'released_episodes', 'season_position', 'progress_positions', 'new_seasons', 'new_episodes',
    'availability_dot', 'availability_reason', 'entry', 'editor_library', 'has_season_ratings',
    'list_id', 'series_title', 'series_poster', 'list_season', 'completed_seasons_only', 'detached_season'))


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


def released_episodes(episodes, release):
    return [row for row in episodes if release.is_released(row.release_date, row.imported)]


async def completed_movies(db, user_id, ids):
    return set(await db.scalars(select(WatchEvent.media_id).where(WatchEvent.user_id == user_id,
        WatchEvent.media_id == any_(literal(ids, type_=ARRAY(Integer))), WatchEvent.completed.is_(True)).distinct())) if ids else set()


async def rows_for_profile(db, user_id, viewer, owner, scope: ScreenScope, ids=None, placements=None):
    query = membership(user_id, scope, (TrackedEntry, Media, MediaFavorite.media_id))
    if not owner:
        rated = select(Rating.id).where(Rating.user_id == user_id, Rating.media_id == Media.id,
            Rating.season_number > 0, Rating.rating > 0).exists()
        query = query.add_columns(rated.label('has_season_ratings'))
    if ids is not None:
        if not ids:
            return []
        query = query.where(Media.id.in_({media_id_for_row(identifier) for identifier in ids}))
    loaded = (await db.execute(query.order_by(Media.title, Media.id))).all()
    if not await anime_is_visible(db):
        loaded = [row for row in loaded if not tracking_projection.is_anime(row[1])]
    entries = []
    for row in loaded:
        tracked, media, favorite = row[:3]
        entry = tracking_projection.entry_data(tracked, media, owner, favorite=bool(tracked.favorite or favorite))
        if not owner and media.media_type == MediaType.series:
            entry['has_season_ratings'] = bool(row.has_season_ratings or any(
                int(number) > 0 and score and score > 0 for number, score in (tracked.season_scores or {}).items()))
        entries.append(entry)
    watched_movies = await completed_movies(db, user_id, [row[1].id for row in loaded if row[1].media_type == MediaType.movie])
    history = await episode_history(db, user_id, [row[1].tmdb_data or {} for row in loaded if row[1].media_type == MediaType.series])
    today = date.today()
    for entry, row in zip(entries, loaded):
        media = row[1]
        entry['availability_dot'], entry['availability_reason'] = False, None
        if media.media_type == MediaType.movie:
            entry['progress'] = int(bool(entry['progress'] or entry['status'] == 'completed' or media.id in watched_movies))
        else:
            project_series(entry, media, history(media.tmdb_data or {}), today, owner)
    if owner:
        from core.tracking_editor import attach_editor_context
        await attach_editor_context(db, viewer, entries, media_rows=[row[1] for row in loaded])
        for entry in entries:
            if entry['type'] == 'series' and entry['entry']:
                entry['entry'].update(progress=entry['progress'], season_position=entry.get('season_position'))
    if ids is None:
        return entries
    if placements is None:
        candidates = [(media.id, tracked, media.tmdb_data or {}, media.tvdb_air_schedule) for tracked, media, *_ in loaded
                      if media.media_type == MediaType.series and can_detach(tracked)]
        placements = await series_placements(db, user_id, candidates, history=history)
    detached_ids = {identifier for identifier in ids if identifier < 0}
    season_totals = await displayed_season_totals(db, [
        (media, season) for _, media, *_ in loaded if (placement := placements.get(media.id))
        for season, _ in placement.active if season_row_id(media.id, season.number) in detached_ids
    ])
    last_unwatched_season = {media.id: max((episode.season_number for episode in
        released_episodes(history(media.tmdb_data or {}), EpisodeRelease(media.tvdb_air_schedule))
        if not episode.watched), default=0) for _, media, *_ in loaded if media.id in placements}
    projected = (row for entry in entries for row in
                 list_rows(entry, placements.get(entry['id']), season_totals, last_unwatched_season.get(entry['id'])))
    by_id = {entry.get('list_id', entry['id']): {key: value for key, value in entry.items() if key in ROW_FIELDS}
             for entry in projected}
    return [by_id[identifier] for identifier in dict.fromkeys(ids) if identifier in by_id]


def project_series(entry, media, episodes, today, owner):
    data = media.tmdb_data or {}
    release = EpisodeRelease(media.tvdb_air_schedule, today=today)
    pointer = data.get('last_episode_to_air') or {}
    entry['latest_released_position'] = (
        f"S{pointer['season_number']}E{pointer['episode_number']}"
        if pointer.get('season_number') is not None and pointer.get('episode_number') is not None else None
    )
    upcoming = data.get('next_episode_to_air') or {}
    entry['next_episode_to_air'] = {key: upcoming.get(key) for key in ('season_number', 'episode_number', 'air_date')} if upcoming else None
    episode_dates = [(row.season_number, row.release_date, row.id, bool(row.imported)) for row in episodes]
    entry['currently_airing'], entry['next_release_date'] = season_releases.current_airing_details(
        {**data, 'status': data.get('status') or media.status}, episode_dates, release=release)
    entry['next_release_at'] = release.air_at(entry['next_release_date'])
    released = sorted(released_episodes(episodes, release), key=lambda row: (row.season_number or 0, row.episode_number or 0))
    watched = {row.id for row in released if row.watched}
    entry['availability_dot'], entry['availability_reason'] = season_releases.availability_dot(
        data, episode_dates, watched, entry['status'], release=release)
    if not data.get('tracking_catalogue_refreshed_at'):
        return
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


def visible_seasons(groups, metadata, watched_ids, scores, today, catalogue_complete, *, release=None,
                    media=None, order='tmdb:aired', manual_completions=None):
    from core.series_editor import season_progress_total

    release = release or EpisodeRelease(today=today)
    by_number = {number: (episodes, expected) for number, episodes, expected in groups}
    previous_completed = False
    seasons = []
    numbers = sorted(by_number.keys() | metadata.keys())
    for number in numbers:
        info = metadata.get(number, {})
        episodes, expected = by_number.get(number, ([], info.get('episode_count') or 0))
        released = [episode for episode in episodes if season_completion.is_released(episode, today, release=release)]
        watched = sum(episode.id in watched_ids for episode in released)
        total = max(expected, len(episodes))
        manual = str(number) in (manual_completions or {})
        completed = manual or bool(total and (expected or catalogue_complete) and watched == total)
        if watched or previous_completed or manual:
            progress_total = season_progress_total(media, order, number, episodes, expected,
                {episode.id for episode in released}, release, latest=number == numbers[-1]) if media else None
            dates = [day for episode in episodes if (day := season_completion.release_day(episode))]
            air_date = info.get('air_date') or (min(dates).isoformat() if dates else None)
            seasons.append(dict(number=number, name=info.get('name'), poster=info.get('poster_path'),
                year=air_date[:4] if air_date else None,
                total=(total or None) if progress_total is None else progress_total, watched=watched,
                score=scores.get(number) or None, completed=completed,
                upcoming=not released and bool(air_date and air_date > today.isoformat())))
        previous_completed = completed
    return seasons


async def series_seasons(db, media, user_id, aired_scores):
    order, groups, metadata = await season_completion.season_groups(db, media, user_id, include_placeholders=True)
    watched = await season_completion.watched_episode_ids(db, user_id,
        [episode.id for _, episodes, _ in groups for episode in episodes])
    scores = {int(number): score for number, score in (aired_scores or {}).items()} if order == 'tmdb:aired' else {}
    scores.update((await db.execute(select(Rating.season_number, Rating.rating).where(
        Rating.user_id == user_id, Rating.media_id == media.id, Rating.season_number.is_not(None),
        Rating.episode_order == (None if order == 'tmdb:aired' else order)))).all())
    release = EpisodeRelease(media.tvdb_air_schedule)
    manual = await db.scalar(select(TrackedEntry.manual_season_completions).where(
        TrackedEntry.user_id == user_id, TrackedEntry.media_id == media.id))
    return {'order': order, 'seasons': visible_seasons(groups, metadata, watched, scores, release.today,
        bool((media.tmdb_data or {}).get('tracking_catalogue_refreshed_at')), release=release,
        media=media, order=order, manual_completions=(manual or {}).get(order))}


@dataclass(slots=True)
class ListSeason:
    number: int
    name: str
    total: int
    watched: int = 0
    completed: bool = False
    poster: str | None = None
    year: str | None = None
    score: float | None = None
    order: str = 'tmdb:aired'


@dataclass(slots=True)
class SeriesPlacement:
    parent_status: str | None
    completed: list[ListSeason]
    active: list[tuple[ListSeason, str]]


# Leave room for the season number while keeping PostgreSQL integer media IDs
# within JavaScript's safe integer range.
SEASON_ROW_STRIDE = 1 << 21


def season_row_id(media_id, season_number):
    return -(media_id * SEASON_ROW_STRIDE + season_number)


def media_id_for_row(identifier):
    if identifier < 0:
        return -identifier // SEASON_ROW_STRIDE
    return identifier


def can_detach(entry):
    return bool(entry.season_list_statuses or (entry.status != 'completed' and (entry.progress or entry.status == 'watching')))


def list_season(number, info, *, total=0, watched=0, completed=False, score=None, order='tmdb:aired'):
    return ListSeason(number, (info.get('name') or '').strip() or f'Season {number}',
        max(total, info.get('episode_count') or 0), watched, completed, info.get('poster_path'),
        (info.get('air_date') or '')[:4] or None, score or None, order)


async def series_placements(db, user_id, candidates, *, history=None):
    if not candidates:
        return {}
    # Current reconciliation already knows the selected episode order and
    # coverage. Select counts and names, without transferring episode IDs.
    season = SeriesSeason
    ratings_order = case((season.episode_order == 'tmdb:aired', None), else_=season.episode_order)
    rows = (await db.execute(select(season.media_id, season.season_number, season.episode_order,
        season.metadata_payload, func.jsonb_array_length(season.episode_ids).label('size'),
        SeasonTracking.watched_episodes, SeasonTracking.completed, Rating.rating)
        .select_from(season).join(SeasonTracking, SeasonTracking.season_id == season.id)
        .outerjoin(Rating, and_(Rating.media_id == season.media_id, Rating.user_id == SeasonTracking.user_id,
            Rating.season_number == season.season_number, Rating.episode_order.is_not_distinct_from(ratings_order)))
        .where(season.media_id.in_([identifier for identifier, _, _, _ in candidates]),
            SeasonTracking.user_id == user_id, SeasonTracking.current.is_(True)))).all()
    summaries = {}
    for row in rows:
        summaries.setdefault(row.media_id, []).append(row)
    if history is None:
        history = await episode_history(db, user_id, [data for identifier, entry, data, _ in candidates
            if entry.progress and identifier not in summaries])
    return {identifier: place_series(entry, data, summaries.get(identifier, []),
        history(data) if identifier not in summaries and entry.progress else (), EpisodeRelease(schedule))
        for identifier, entry, data, schedule in candidates}


def place_series(entry, data, reconciled, episodes, release):
    order = reconciled[0].episode_order if reconciled else 'tmdb:aired'
    scores = entry.season_scores or {}
    metadata = {info['season_number']: info for info in data.get('seasons') or []
                if isinstance(info, dict) and (info.get('season_number') or 0) > 0} if order == 'tmdb:aired' else {}
    seasons = {number: list_season(number, info, score=scores.get(str(number))) for number, info in metadata.items()}
    for row in reconciled:
        info = row.metadata_payload or metadata.get(row.season_number, {})
        score = row.rating if row.rating is not None else scores.get(str(row.season_number)) if order == 'tmdb:aired' else None
        seasons[row.season_number] = list_season(row.season_number, info, total=row.size,
            watched=row.watched_episodes, completed=row.completed, score=score, order=order)
    if not reconciled and episodes:
        counts = {}
        for episode in episodes:
            total, watched = counts.get(episode.season_number, (0, 0))
            released = release.is_released(episode.release_date, episode.imported)
            counts[episode.season_number] = total + 1, watched + int(bool(episode.watched and released))
        for number, (total, watched) in counts.items():
            info = metadata.get(number, {})
            expected = info.get('episode_count') or 0
            seasons[number] = list_season(number, info, total=total, watched=watched,
                completed=bool((expected or data.get('tracking_catalogue_refreshed_at')) and watched == max(total, expected)),
                score=scores.get(str(number)))
    ordered = sorted(seasons.values(), key=lambda season: season.number)
    completed = [season for season in ordered if season.completed]
    cursor = entry.season_cursor if isinstance(entry.season_cursor, dict) else {}
    selected = seasons.get(cursor.get('season_number')) if cursor.get('order') == order else None
    # A later playback/import can supersede a previously saved cursor.
    latest = next((season for season in reversed(ordered) if season.watched), None)
    if (not selected or cursor.get('progress_season') or selected.watched != cursor.get('episode_count')
            or latest and latest.number > selected.number):
        selected = latest or next(iter(ordered), None)
    statuses = (entry.season_list_statuses or {}).get(order, {})
    active = [(season, statuses[str(season.number)]) for season in ordered if str(season.number) in statuses]
    if selected and not active and entry.status != 'completed':
        if selected.completed and entry.status in ('watching', 'planning'):
            selected = next((season for season in ordered if season.number > selected.number and not season.completed), None)
        if selected and not selected.completed and (entry.status == 'watching' or completed):
            active.append((selected, entry.status))
    elif selected and active and selected.watched and not selected.completed and str(selected.number) not in statuses:
        if entry.status != 'completed':
            active.append((selected, entry.status))
    return SeriesPlacement('completed' if completed else None if active else entry.status, completed, active)


async def displayed_season_totals(db, candidates):
    """Load compact evidence only for the detached seasons being rendered."""
    from core.series_editor import season_progress_total

    candidates = [(media, season) for media, season in candidates
                  if media.tvdb_air_schedule or (season.order == 'tmdb:aired' and media.tmdb_id
                      and ((media.tmdb_data or {}).get('status') or media.status) == 'Returning Series')]
    if not candidates:
        return {}
    requested = values(column('media_id', Integer), column('number', Integer), column('order_key', String),
        name='requested_seasons').data([(media.id, season.number, season.order) for media, season in candidates])
    episode = aliased(Media)
    position = ShowEpisodePosition
    aired = requested.c.order_key == 'tmdb:aired'
    rows = (await db.execute(select(requested.c.media_id, requested.c.number, episode.id, episode.tmdb_id, episode.tvdb_id,
        episode.season_number, episode.episode_number, episode.release_date,
        episode.tmdb_data['tracking_import_released'].as_boolean().is_(True).label('imported_release'),
        season_completion.episode_placeholder(episode).label('placeholder'))
        .select_from(requested).join(Media, Media.id == requested.c.media_id)
        .join(Show, or_(Show.tmdb_id == Media.tmdb_id, Show.tvdb_id == Media.tvdb_id))
        .join(episode, and_(episode.show_id == Show.id, episode.media_type == MediaType.episode,
                            episode.season_number > 0))
        .outerjoin(position, and_(~aired, position.series_tmdb_id == Media.tmdb_id,
            position.order_key == requested.c.order_key, position.display_season == requested.c.number,
            position.tmdb_episode_id == episode.tmdb_id))
        .where(or_(and_(aired, episode.season_number == requested.c.number), position.id.is_not(None)))
        .order_by(episode.episode_number))).all()
    by_media = defaultdict(dict)
    for row in rows:
        by_media[row.media_id, row.number][row.id] = row

    totals = {}
    for media, season in candidates:
        data = media.tmdb_data or {}
        members = season_completion.catalogue_members(data, by_media[media.id, season.number].values())
        metadata = {item['season_number']: item for item in data.get('seasons') or []
                    if isinstance(item, dict) and (item.get('season_number') or 0) > 0}
        latest = max([ep.season_number for ep in members] + list(metadata), default=season.number)
        if season.order == 'tmdb:aired':
            members = [ep for ep in members if ep.season_number == season.number]
        release = EpisodeRelease(media.tvdb_air_schedule)
        released = {ep.id for ep in members if season_completion.is_released(ep, release.today, release=release)}
        expected = metadata.get(season.number, {}).get('episode_count') or 0
        total = season_progress_total(media, season.order, season.number, members, expected,
                                      released, release, latest=season.number == latest)
        if total is not None:
            totals[media.id, season.number] = total
    return totals


def list_rows(entry, placement, progress_totals, last_unwatched_season):
    if not placement:
        yield {**entry, 'completed_seasons_only': True} if entry['status'] == 'completed' and entry['type'] == 'series' else entry
        return
    if placement.parent_status:
        parent = {**entry, 'status': placement.parent_status,
                  'completed_seasons_only': placement.parent_status == 'completed'}
        if placement.completed:
            finished = placement.completed[-1]
            parent.update(progress=sum(item.watched for item in placement.completed),
                season_position=f'S{finished.number}E{finished.watched}')
        yield parent
    for season, status in placement.active:
        yield {**entry, 'list_id': season_row_id(entry['id'], season.number), 'status': status,
            'series_title': entry['title'], 'series_poster': entry['poster'],
            'title': f"{entry['title']} - {season.name}", 'poster': season.poster or entry['poster'],
            'year': season.year or entry['year'], 'progress': season.watched, 'score': season.score,
            'rewatch_count': 0 if placement.parent_status else entry['rewatch_count'],
            'notes': None if placement.parent_status else entry['notes'],
            'list_season': {'number': season.number, 'order': season.order,
                            'can_increment': last_unwatched_season >= season.number
                                if last_unwatched_season is not None and season.order == 'tmdb:aired' else None,
                            'total': progress_totals.get((entry['id'], season.number), season.total)}}


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


async def members(db, user_id, scope: ScreenScope, *, progress=False, cap=None, placements=None):
    show_anime = await anime_is_visible(db)
    metadata = Media.tmdb_data
    anime_keys = () if show_anime else ('origin_country', 'production_countries', 'original_language')
    history_keys = ('tracking_episode_ids', 'tracking_import_episode_media_ids',
        'tracking_catalogue_provider', 'tracking_catalogue_refreshed_at') if scope != 'movie' else ()
    season_keys = ('seasons',) if scope != 'movie' else ()
    keys = ('genres', *anime_keys, *history_keys, *season_keys)
    detach_candidate = or_(TrackedEntry.season_list_statuses.is_not(None),
        and_(TrackedEntry.status != 'completed', or_(TrackedEntry.status == 'watching', TrackedEntry.progress > 0)))
    columns = (Media.id, func.lower(Media.title).label('title'), Media.media_type, Media.release_date, Media.tvdb_air_schedule,
        TrackedEntry.status, TrackedEntry.rating_mode, TrackedEntry.manual_score, TrackedEntry.season_scores,
        TrackedEntry.progress, TrackedEntry.updated_at, TrackedEntry.season_cursor, TrackedEntry.season_list_statuses,
        (TrackedEntry.favorite | MediaFavorite.media_id.is_not(None)).label('favorite'),
        *(metadata[key].label(key) for key in ('genres', *anime_keys)),
        *(case((detach_candidate, metadata[key]), else_=None).label(key) for key in season_keys),
        *((metadata[key] if progress else case((detach_candidate & (TrackedEntry.progress > 0), metadata[key]), else_=None))
            .label(key) for key in history_keys))
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
        and (data.get('tracking_catalogue_refreshed_at') or can_detach(row))]) if progress else None
    projected = await series_placements(db, user_id,
        [(row.id, row, data, row.tvdb_air_schedule) for row, data in inputs if row.media_type == MediaType.series and can_detach(row)],
        history=history)
    if placements is not None:
        placements.update(projected)
    result = []
    for rank, (row, data) in enumerate(inputs):
        count = row.progress
        if row.media_type == MediaType.movie:
            count = int(bool(count or row.status == 'completed' or row.id in movies))
        elif history and data.get('tracking_catalogue_refreshed_at'):
            count = sum(episode.watched for episode in released_episodes(history(data), EpisodeRelease(row.tvdb_air_schedule)))
        year = (row.release_date or '')[:4]
        year = year if len(year) == 4 and year.isascii() and year.isdigit() else ''
        genres = tracking_projection.media_genres(data)
        placement = projected.get(row.id)
        parent_status = placement.parent_status if placement else row.status
        if parent_status:
            if placement and placement.completed:
                count = sum(season.watched for season in placement.completed)
            result.append(Member(row.id, row.title, parent_status, effective_score(row.rating_mode, row.manual_score, row.season_scores),
                count, row.updated_at, year, genres, bool(row.favorite), rank))
        for season, status in placement.active if placement else ():
            result.append(Member(season_row_id(row.id, season.number), f'{row.title} - {season.name.lower()}', status, season.score, season.watched,
                row.updated_at, season.year or year, genres, bool(row.favorite), rank))
    if cap is not None and len(result) > cap:
        return None
    return result


def select_page(entries, *, status, sort, q, genre, year, favorites, offset, limit, status_order=STATUS_ORDER):
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
    matches.sort(key=lambda member: status_order.get(member.status, len(status_order)))
    for member in matches:
        group_counts[member.status] = group_counts.get(member.status, 0) + 1
    return [member.id for member in matches[offset:offset + limit]], dict(
        offset=offset, total=len(matches), status_counts=status_counts, group_counts=group_counts,
        genres=sorted({genre for member in entries for genre in member.genres}),
        years=sorted({member.year for member in entries if member.year}, reverse=True))
