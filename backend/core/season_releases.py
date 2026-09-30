"""Season-level release discovery and private inbox notifications."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from sqlalchemy import or_, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from models import Media, Show, WatchEvent
from core import tracking_projection
from models.base import MediaType
from models.tracking import (
    SyncReview,
    TrackedEntry,
    TrackingPreferences,
    TrackingSeasonReleaseObservation,
)


SEASON_RELEASE_DATE_KIND = "new_season_release_date"
SEASON_RELEASE_KIND = "new_season_release"
SEASON_NOTIFICATION_KINDS = {SEASON_RELEASE_DATE_KIND, SEASON_RELEASE_KIND}
ELIGIBLE_STATUSES = {"watching", "completed", "planning"}


def parse_release_day(value: object) -> date | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def availability_dot(tmdb_data, episodes, watched_episode_ids, status, today=None):
    """Return the availability indicator and reason from cached season/episode data.

    ``episodes`` is an iterable of ``(season_number, release_date, media_id)``
    rows. Incomplete or malformed catalogue data never produces a dot.
    """
    today = today or date.today()
    data = tmdb_data if isinstance(tmdb_data, dict) else {}
    seasons = data.get('seasons')
    if not isinstance(seasons, list):
        return False, None


    regular = {}
    for season in seasons:
        if not isinstance(season, dict):
            continue
        number = season.get('season_number')
        count = season.get('episode_count')
        premiered = parse_release_day(season.get('air_date'))
        if isinstance(number, int) and number > 0 and isinstance(count, int) and count > 0 and premiered:
            regular[number] = (count, premiered)
    if not regular:
        return False, None

    released_by_season, future_by_season = {}, {}
    watched = set(watched_episode_ids or ())
    for episode in episodes or ():
        season, released_at, media_id = episode[:3]
        import_confirmed_release = len(episode) > 3 and episode[3]
        if season not in regular:
            continue
        released_day = parse_release_day(released_at)
        if released_day is None and not import_confirmed_release:
            continue
        if import_confirmed_release or released_day <= today:
            released_by_season.setdefault(season, []).append(media_id)
        else:
            future_by_season.setdefault(season, []).append(media_id)

    # Airing takes priority and applies regardless of the user's tracking status.
    next_episode = data.get('next_episode_to_air')
    if isinstance(next_episode, dict):
        next_season = next_episode.get('season_number')
        next_day = parse_release_day(next_episode.get('air_date'))
        if next_season in regular and next_day and next_day >= today:
            expected, premiered = regular[next_season]
            if premiered <= today and len(released_by_season.get(next_season, ())) < expected:
                return True, 'airing'
    for number, (expected, premiered) in regular.items():
        if premiered <= today and len(released_by_season.get(number, ())) < expected and future_by_season.get(number):
            return True, 'airing'

    # Only the newest season that has premiered can be a recent new season.
    started = [(number, info) for number, info in regular.items() if info[1] <= today]
    if not started or status == 'watching':
        return False, None
    number, (_, premiered) = max(started, key=lambda item: item[0])
    if premiered < today - timedelta(days=30):
        return False, None
    if any(media_id not in watched for media_id in released_by_season.get(number, ())):
        return True, 'new_season'
    return False, None


def current_airing_details(tmdb_data, episodes, today=None):
    """Return active-release state and its next known date for a series.

    TMDB's broad status can stay at ``Returning Series`` between seasons, so
    require both that active status and the episode-level signal used by the
    availability indicator: a started season with a future scheduled episode.
    """
    today = today or date.today()
    data = tmdb_data if isinstance(tmdb_data, dict) else {}
    status = str(data.get('status') or '').strip().casefold()
    if status != 'returning series':
        return False, None
    episode_rows = list(episodes or ())
    next_episode = data.get('next_episode_to_air')
    last_episode = data.get('last_episode_to_air')
    if isinstance(next_episode, dict) and isinstance(last_episode, dict):
        next_position = (next_episode.get('season_number'), next_episode.get('episode_number'))
        last_position = (last_episode.get('season_number'), last_episode.get('episode_number'))
        if all(isinstance(part, int) for part in next_position + last_position) and next_position <= last_position:
            # TMDB occasionally retains a stale next pointer after a batch
            # drop. Episode release dates can still prove active releases.
            data = {**data}
            data.pop('next_episode_to_air', None)
            next_episode = None
    _, reason = availability_dot(data, episode_rows, set(), 'watching', today)
    if reason != 'airing':
        return False, None


    seasons = data.get('seasons')
    regular = {
        season['season_number']: season for season in seasons if isinstance(season, dict)
        and isinstance(season.get('season_number'), int) and season['season_number'] > 0
        and isinstance(season.get('episode_count'), int) and season['episode_count'] > 0
        and parse_release_day(season.get('air_date')) and parse_release_day(season.get('air_date')) <= today
    } if isinstance(seasons, list) else {}
    next_episode = data.get('next_episode_to_air')
    next_day = parse_release_day(next_episode.get('air_date')) if isinstance(next_episode, dict) else None
    next_season = next_episode.get('season_number') if isinstance(next_episode, dict) else None
    if next_day and next_day >= today and next_season in regular:
        return True, next_day.isoformat()

    last_episode = data.get('last_episode_to_air')
    active_season = last_episode.get('season_number') if isinstance(last_episode, dict) else None
    if active_season not in regular:
        candidates = [number for number in regular if any(
            len(row) > 1 and row[0] == number and parse_release_day(row[1]) and parse_release_day(row[1]) > today
            for row in episode_rows
        )]
        active_season = max(candidates, default=None)
    if active_season not in regular:
        return True, None
    future_days = [parse_release_day(row[1]) for row in episode_rows
                   if len(row) > 1 and row[0] == active_season
                   and parse_release_day(row[1]) and parse_release_day(row[1]) > today]
    return True, min(future_days).isoformat() if future_days else None


async def media_availability_dot(db, user_id, media, status, today=None):
    data = media.tmdb_data or {}
    ids = data.get('tracking_episode_ids') or []
    imported_ids = data.get('tracking_import_episode_media_ids') or []
    provider = data.get('tracking_catalogue_provider', 'tmdb')
    identity = Media.tvdb_id if provider == 'tvdb' else Media.tmdb_id
    if not ids and not imported_ids:
        return availability_dot(data, [], set(), status, today)
    identity_filters = []
    if ids:
        identity_filters.append(identity.in_(ids))
    if imported_ids:
        identity_filters.append(Media.id.in_(imported_ids))
    rows = (await db.execute(select(Media.id, Media.season_number, Media.release_date, Media.tmdb_data).where(
        Media.media_type == MediaType.episode,
        or_(*identity_filters),
        Media.season_number > 0
    ))).all()
    watched = set((await db.execute(select(WatchEvent.media_id).where(
        WatchEvent.user_id == user_id, WatchEvent.completed.is_(True),
        WatchEvent.media_id.in_([row.id for row in rows])
    ))).scalars()) if rows else set()
    return availability_dot(data, [
        (row.season_number, row.release_date, row.id,
         bool((row.tmdb_data or {}).get('tracking_import_released')))
        for row in rows
    ], watched, status, today)


def _season_rows(metadata: dict | None) -> list[dict]:
    seasons = metadata.get("seasons") if isinstance(metadata, dict) else None
    if not isinstance(seasons, list):
        return []
    results: list[dict] = []
    for season in seasons:
        if not isinstance(season, dict):
            continue
        number = season.get("season_number")
        released = parse_release_day(season.get("air_date"))
        if isinstance(number, int) and not isinstance(number, bool) and number > 0 and released:
            results.append({**season, "season_number": number, "air_date": released.isoformat()})
    return results


def upcoming_season(metadata: dict | None, today: date | None = None) -> dict | None:
    """Return the earliest confirmed regular season that has not premiered.

    TMDB and TVDB expose season premiere dates independently of episode rows,
    so an announced season with zero catalogue episodes is still eligible.
    """
    today = today or date.today()
    seasons = _season_rows(metadata)
    prior_numbers = [
        season["season_number"]
        for season in seasons
        if (parse_release_day(season.get("air_date")) or date.max) < today
    ]
    latest_past = max(prior_numbers, default=0)
    candidates = [
        season for season in seasons
        if season["season_number"] > max(latest_past, 1)
        and (parse_release_day(season.get("air_date")) or date.min) >= today
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda season: (season["season_number"], season["air_date"]))


def observation_transition(
    release_day: date,
    previous_release_date: str | None,
    seen_upcoming: bool,
    release_processed: bool,
    today: date,
) -> dict[str, bool]:
    """Decide which one-time notices a scan may create for one season."""
    date_changed = parse_release_day(previous_release_date) != release_day
    if date_changed and release_day > today:
        # A corrected future date starts a new observation window. If a prior
        # stale date had already been processed as past, it must not suppress
        # the eventual release event for this newly confirmed schedule.
        release_processed = False
    return {
        "date_changed": date_changed,
        # A date first discovered on its premiere day is no longer upcoming.
        "send_date_notice": date_changed and release_day > today,
        "send_release_notice": not release_processed and (
            release_day == today or (release_day < today and seen_upcoming)
        ),
        "seen_upcoming": seen_upcoming or release_day >= today,
        "release_processed": release_processed or release_day <= today,
    }


def season_event_dedupe_key(kind: str, media_id: int, season_number: int, release_date: str) -> str:
    suffix = f":{release_date}" if kind == SEASON_RELEASE_DATE_KIND else ""
    return f"{kind}:{media_id}:{season_number}{suffix}"


def season_date_notice_is_current(
    media_id: int,
    season_number: int,
    season: dict | None,
    event_key: str | None,
) -> bool:
    """Whether a visible date notice still matches confirmed provider data."""
    release_day = parse_release_day(season.get("air_date")) if season else None
    return bool(
        release_day
        and event_key == season_event_dedupe_key(
            SEASON_RELEASE_DATE_KIND, media_id, season_number, release_day.isoformat(),
        )
    )


def _metadata_for(media: Media, show: Show | None) -> dict:
    media_data = media.tmdb_data if isinstance(media.tmdb_data, dict) else {}
    show_data = show.tmdb_data if show and isinstance(show.tmdb_data, dict) else {}
    # TVDB-native shows keep the latest provider season art/date summary on the
    # Show row. TMDB-backed shows are refreshed daily on that same row. The
    # series Media snapshot remains a useful fallback for imported catalogues.
    if show and show.canonical_source == "tvdb":
        # TVDB catalogue hydration refreshes a series Media snapshot; the
        # lighter scheduled summary refresh updates Show.tmdb_data. Use the
        # freshest timestamp so a stale copy on either row cannot win.
        media_fresh = _metadata_timestamp(media_data)
        show_fresh = _metadata_timestamp(show_data)
        if media_data.get("seasons") and (media_fresh or datetime.min) > (show_fresh or datetime.min):
            return media_data
    if show_data.get("seasons"):
        return show_data
    if media_data.get("seasons"):
        return media_data
    return show_data or media_data


def _metadata_timestamp(data: dict) -> datetime | None:
    timestamps = []
    for key in ("refreshed_at", "tracking_catalogue_refreshed_at", "season_metadata_refreshed_at"):
        value = data.get(key)
        if not isinstance(value, str):
            continue
        try:
            parsed = datetime.fromisoformat(value)
            timestamps.append(parsed.replace(tzinfo=None) if parsed.tzinfo else parsed)
        except ValueError:
            continue
    return max(timestamps) if timestamps else None


def _season_art(media: Media, season: dict) -> dict:
    own_poster = season.get("poster_path") or None
    fallback = not own_poster
    return {
        "season_number": season["season_number"],
        "season_title": season.get("name") or f"Season {season['season_number']}",
        "release_date": season.get("air_date"),
        "season_poster_path": own_poster,
        "season_artwork_fallback": fallback,
        "artwork_retry": fallback,
        "poster": media.poster_path,
    }


def _event_payload(media: Media, season: dict) -> dict:
    return _season_art(media, season)


async def _refresh_visible_notice_art(
    db: AsyncSession,
    *,
    user_id: int,
    media: Media,
    season: dict,
) -> None:
    notices = (await db.execute(select(SyncReview).where(
        SyncReview.user_id == user_id,
        SyncReview.media_id == media.id,
        SyncReview.kind.in_(SEASON_NOTIFICATION_KINDS),
        SyncReview.season_number == season["season_number"],
        SyncReview.state == "pending",
        SyncReview.dismissed_at.is_(None),
    ))).scalars().all()
    payload = _event_payload(media, season)
    for notice in notices:
        # A release-date card describes the confirmed date in the metadata
        # currently shown to the user, so also keep it current after date edits.
        notice.payload = payload
        notice.message = (
            f"{media.title} — Season {season['season_number']} release date: {season['air_date']}"
            if notice.kind == SEASON_RELEASE_DATE_KIND
            else f"{media.title} — Season {season['season_number']} is releasing"
        )


async def _ensure_observations(
    db: AsyncSession,
    candidates: list[tuple[int, int, int, str | None]],
) -> dict[tuple[int, int, int], TrackingSeasonReleaseObservation]:
    """Create missing observation rows idempotently, then lock them for update."""
    if not candidates:
        return {}
    unique = {(user_id, media_id, season_number): release_date for user_id, media_id, season_number, release_date in candidates}
    values = [
        {
            "user_id": user_id,
            "media_id": media_id,
            "season_number": season_number,
            # Leave the initial date empty so the first observation is treated
            # as newly confirmed by the notification state machine below.
            "release_date": None,
            "seen_upcoming": False,
            "release_processed": False,
        }
        for (user_id, media_id, season_number), release_date in unique.items()
    ]
    # The scheduler can run in multiple application workers. Conflict-safe
    # inserts avoid aborting the scan when two workers first observe one title.
    for offset in range(0, len(values), 500):
        statement = pg_insert(TrackingSeasonReleaseObservation).values(values[offset:offset + 500])
        await db.execute(statement.on_conflict_do_nothing(
            index_elements=[
                TrackingSeasonReleaseObservation.user_id,
                TrackingSeasonReleaseObservation.media_id,
                TrackingSeasonReleaseObservation.season_number,
            ],
        ))
    keys = list(unique)
    rows = (await db.execute(
        select(TrackingSeasonReleaseObservation)
        .where(tuple_(
            TrackingSeasonReleaseObservation.user_id,
            TrackingSeasonReleaseObservation.media_id,
            TrackingSeasonReleaseObservation.season_number,
        ).in_(keys))
        .with_for_update()
    )).scalars().all()
    return {(row.user_id, row.media_id, row.season_number): row for row in rows}


async def _upsert_season_notice(
    db: AsyncSession,
    *,
    user_id: int,
    media: Media,
    season: dict,
    kind: str,
) -> None:
    release_date = season["air_date"]
    season_number = season["season_number"]
    event_key = season_event_dedupe_key(kind, media.id, season_number, release_date)
    message = (
        f"{media.title} — Season {season_number} release date: {release_date}"
        if kind == SEASON_RELEASE_DATE_KIND
        else f"{media.title} — Season {season_number} is releasing"
    )
    payload = _event_payload(media, season)
    statement = pg_insert(SyncReview).values(
        user_id=user_id,
        media_id=media.id,
        kind=kind,
        state="pending",
        season_number=season_number,
        season_event_key=event_key,
        message=message,
        priority="medium",
        payload=payload,
    ).on_conflict_do_nothing(
        index_elements=[SyncReview.user_id, SyncReview.season_event_key],
    )
    await db.execute(statement)
    # Update visible, undismissed cards with newly available season art. A
    # dismissed event remains dismissed and the event key is never re-used.
    existing = (await db.execute(select(SyncReview).where(
        SyncReview.user_id == user_id,
        SyncReview.season_event_key == event_key,
    ))).scalar_one_or_none()
    if existing and existing.dismissed_at is None and existing.state == "pending":
        existing.payload = payload
        existing.message = message


async def refresh_season_release_notifications(db: AsyncSession, today: date | None = None) -> dict[str, int]:
    """Observe newly confirmed season dates and create per-user inbox cards.

    A future date can notify on the first scan. Past seasons are quiet unless
    the user had already been observed tracking that season before its date.
    An exact premiere-day first scan still creates the release notice.
    """
    today = today or date.today()
    rows = (await db.execute(
        select(TrackedEntry, Media, Show)
        .join(Media, Media.id == TrackedEntry.media_id)
        .outerjoin(Show, or_(
            (Media.tmdb_id.is_not(None) & (Show.tmdb_id == Media.tmdb_id)),
            (Media.tvdb_id.is_not(None) & (Show.tvdb_id == Media.tvdb_id)),
        ))
        .where(Media.media_type == MediaType.series)
        .order_by(TrackedEntry.user_id, Media.id)
    )).unique().all()
    if not rows:
        return {"date_notices": 0, "release_notices": 0}

    user_ids = {entry.user_id for entry, _, _ in rows}
    preference_rows = (await db.execute(select(TrackingPreferences).where(
        TrackingPreferences.user_id.in_(user_ids),
    ))).scalars().all()
    preferences = {row.user_id: row for row in preference_rows}

    # Prepare at most the next season per show plus any still-unprocessed
    # premiere that was previously seen in the future.
    metadata_by_pair: dict[tuple[int, int], tuple[TrackedEntry, Media, Show | None, list[dict], dict[int, dict]]] = {}
    observation_keys: list[tuple[int, int, int, str | None]] = []
    for entry, media, show in rows:
        metadata = _metadata_for(media, show)
        seasons = _season_rows(metadata)
        prior_numbers = [
            season["season_number"] for season in seasons
            if (parse_release_day(season.get("air_date")) or date.max) < today
        ]
        latest_past = max(prior_numbers, default=0)
        candidates = [
            season for season in seasons
            if season["season_number"] > max(latest_past, 1)
            and (parse_release_day(season.get("air_date")) or date.min) >= today
        ]
        next_season = min(candidates, key=lambda season: (season["season_number"], season["air_date"])) if candidates else None
        seasons_by_number = {season["season_number"]: season for season in seasons}
        metadata_by_pair[(entry.user_id, media.id)] = (entry, media, show, seasons, seasons_by_number)
        if next_season:
            observation_keys.append((entry.user_id, media.id, next_season["season_number"], next_season["air_date"]))

    # A provider can retract a previously confirmed date or remove the season.
    # Retire visible cards so the inbox never presents stale information.
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    pending_date_notices = (await db.execute(select(SyncReview).where(
        SyncReview.user_id.in_(user_ids),
        SyncReview.media_id.is_not(None),
        SyncReview.kind == SEASON_RELEASE_DATE_KIND,
        SyncReview.dismissed_at.is_(None),
    ))).scalars().all()
    for notice in pending_date_notices:
        pair_data = metadata_by_pair.get((notice.user_id, notice.media_id))
        current_season = pair_data[4].get(notice.season_number) if pair_data else None
        if not season_date_notice_is_current(
            notice.media_id, notice.season_number, current_season, notice.season_event_key,
        ):
            notice.dismissed_at = now
            notice.state = "corrected"

    # Existing upcoming observations stay eligible for their release event
    # after the date passes, including when a metadata refresh was delayed.
    pairs = {(entry.user_id, media.id) for entry, media, _ in rows}
    pair_keys = list(pairs)
    if pair_keys:
        stored = (await db.execute(select(TrackingSeasonReleaseObservation).where(
            tuple_(TrackingSeasonReleaseObservation.user_id, TrackingSeasonReleaseObservation.media_id).in_(pair_keys)
        ))).scalars().all()
        for observation in stored:
            current_season = metadata_by_pair.get((observation.user_id, observation.media_id), (None, None, None, [], {}))[4].get(observation.season_number)
            if current_season:
                release_day = parse_release_day(current_season.get("air_date"))
                observation_keys.append((observation.user_id, observation.media_id, observation.season_number,
                                         release_day.isoformat() if release_day else None))

    observations = await _ensure_observations(db, observation_keys)
    date_notices = 0
    release_notices = 0

    for pair, (entry, media, _show, _seasons, seasons_by_number) in metadata_by_pair.items():
        prefs = preferences.get(entry.user_id)
        date_enabled = True if prefs is None else bool(prefs.new_season_release_dates)
        release_enabled = True if prefs is None else bool(prefs.new_season_releases)
        eligible = entry.status in ELIGIBLE_STATUSES
        candidates = {
            season_number: season
            for (user_id, media_id, season_number), observation in observations.items()
            if (user_id, media_id) == pair
            and (season := seasons_by_number.get(season_number)) is not None
            and (parse_release_day(season.get("air_date")) is not None)
        }

        for season_number, season in candidates.items():
            release_day = parse_release_day(season.get("air_date"))
            if release_day is None:
                continue
            observation = observations[(entry.user_id, media.id, season_number)]
            transition = observation_transition(
                release_day,
                observation.release_date,
                observation.seen_upcoming,
                observation.release_processed,
                today,
            )
            observation.release_date = release_day.isoformat()
            observation.seen_upcoming = transition["seen_upcoming"]

            if transition["send_date_notice"]:
                if eligible and date_enabled:
                    await _upsert_season_notice(
                        db, user_id=entry.user_id, media=media, season=season,
                        kind=SEASON_RELEASE_DATE_KIND,
                    )
                    date_notices += 1

            if transition["send_release_notice"]:
                # An exact premiere-day first scan or a previously seen future
                # date that passed during a delayed sweep can trigger release.
                if eligible and release_enabled:
                    await _upsert_season_notice(
                        db, user_id=entry.user_id, media=media, season=season,
                        kind=SEASON_RELEASE_KIND,
                    )
                    release_notices += 1
            observation.release_processed = transition["release_processed"]
            observation.updated_at = now
            await _refresh_visible_notice_art(
                db, user_id=entry.user_id, media=media, season=season,
            )

    await db.commit()
    return {"date_notices": date_notices, "release_notices": release_notices}


async def upcoming_seasons_for_user(db: AsyncSession, user_id: int, today: date | None = None) -> list[dict]:
    """Project one next confirmed regular-season release per tracked series."""
    today = today or date.today()
    rows = (await db.execute(
        select(TrackedEntry, Media, Show)
        .join(Media, Media.id == TrackedEntry.media_id)
        .outerjoin(Show, or_(
            (Media.tmdb_id.is_not(None) & (Show.tmdb_id == Media.tmdb_id)),
            (Media.tvdb_id.is_not(None) & (Show.tvdb_id == Media.tvdb_id)),
        ))
        .where(TrackedEntry.user_id == user_id, Media.media_type == MediaType.series)
        .order_by(Media.title)
    )).unique().all()

    results = []
    for _entry, media, show in rows:
        season = upcoming_season(_metadata_for(media, show), today)
        if not season:
            continue
        results.append({**tracking_projection.media_data(media), **_season_art(media, season)})
    return results
