"""Load and refresh complete regular-episode catalogues for tracked series."""
import asyncio
from datetime import datetime, timedelta, timezone
from sqlalchemy import and_, or_, select
from core import tmdb, tvdb
from core.enrichment import create_media_safely
from models import Media, Show
from models.base import MediaType
from models.tracking import TrackedEntry


FINAL_SHOW_STATUSES = {"Ended", "Canceled"}


def tracking_catalogue_is_fresh(media, show_status: str | None, now: datetime | None = None) -> bool:
    value = (media.tmdb_data or {}).get("tracking_catalogue_refreshed_at")
    if not value:
        return False
    try:
        refreshed_at = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return False
    if refreshed_at.tzinfo is None:
        refreshed_at = refreshed_at.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    max_age = timedelta(days=30) if show_status in FINAL_SHOW_STATUSES else timedelta(hours=20)
    return now - refreshed_at < max_age


async def hydrate_tracking_episodes(db, media, api_key=None, tvdb_api_key=None):
    if media.media_type != MediaType.series or not (media.tmdb_id or media.tvdb_id):
        raise ValueError('A series provider identity is required')
    # Serialize catalogue refreshes independently of any user's tracking state.
    await db.execute(select(Media.id).where(Media.id == media.id).with_for_update())
    identities = []
    if media.tmdb_id:
        identities.append(Show.tmdb_id == media.tmdb_id)
    if media.tvdb_id:
        identities.append(Show.tvdb_id == media.tvdb_id)
    show = (await db.execute(select(Show).where(or_(*identities)))).scalars().first()
    if show and show.canonical_source == 'tvdb':
        if not show.tvdb_id or not tvdb_api_key:
            raise ValueError('A TVDB key is required to refresh this show in its original episode order')
        raw = await tvdb.get_series(show.tvdb_id, tvdb_api_key, cache_ttl=None)
        details = tvdb.format_series(raw)
        raw_episodes = await tvdb.get_series_episodes(
            show.tvdb_id, None, tvdb_api_key, season_type='official', cache_ttl=None,
        )
        fetched = [tvdb.format_episode(item) for item in raw_episodes]
        fetched = [item for item in fetched if (item.get('season_number') or 0) > 0]
        if any(not item.get('tvdb_id') or not item.get('episode_number') for item in fetched):
            raise ValueError('Episode identities are incomplete')
        positions = [(item['season_number'], item['episode_number']) for item in fetched]
        if len(set(positions)) != len(positions):
            raise ValueError('The provider returned duplicate episode positions')
        expected = {
            season['season_number']: season.get('episode_count')
            for season in details.get('seasons', [])
            if (season.get('season_number') or 0) > 0 and season.get('episode_count')
        }
        actual = {number: sum(item['season_number'] == number for item in fetched) for number in expected}
        if any(actual[number] != count for number, count in expected.items()):
            raise ValueError('Episode metadata is incomplete; try refreshing again later')
        existing = (await db.execute(select(Media).where(
            Media.show_id == show.id, Media.media_type == MediaType.episode,
        ))).scalars().all()
        by_tvdb = {episode.tvdb_id: episode for episode in existing if episode.tvdb_id}
        by_position = {(episode.season_number, episode.episode_number): episode for episode in existing}
        for data in fetched:
            position = (data['season_number'], data['episode_number'])
            episode = by_tvdb.get(data['tvdb_id']) or by_position.get(position)
            if episode and (episode.season_number, episode.episode_number) != position:
                raise ValueError('Episode numbering changed; existing watch history was preserved')
            if episode and episode.tvdb_id not in (None, data['tvdb_id']):
                raise ValueError('Episode identity changed; existing watch history was preserved')
            if episode is None:
                episode = Media(show_id=show.id, media_type=MediaType.episode,
                    tvdb_id=data['tvdb_id'], season_number=position[0], episode_number=position[1],
                    title=data.get('name') or f'Episode {position[1]}')
                db.add(episode)
            else:
                episode.tvdb_id = episode.tvdb_id or data['tvdb_id']
            episode.release_date = data.get('air_date')
            episode.title = data.get('name') or episode.title
            episode.runtime = data.get('runtime')
        air_metadata = {key: details[key] for key in ('status', 'last_episode_to_air', 'next_episode_to_air') if key in details}
        media.status = details.get('status') or media.status
        media.tmdb_data = {**(media.tmdb_data or {}), **air_metadata, 'seasons': details.get('seasons', []),
            'tracking_episode_ids': [item['tvdb_id'] for item in fetched],
            'tracking_catalogue_provider': 'tvdb',
            'tracking_catalogue_refreshed_at': datetime.now(timezone.utc).isoformat()}
        await db.flush()
        return len(fetched)
    if not media.tmdb_id or not api_key:
        raise ValueError('A TMDB key is required to refresh this show')
    details = await tmdb.get_show(media.tmdb_id, api_key=api_key)
    seasons = details.get('seasons')
    if not isinstance(seasons, list):
        raise ValueError('The provider did not return a complete season catalogue')
    # Fetch everything before persisting: one failed season must not make a
    # partial catalogue look like a complete show.
    fetched = []
    for season in seasons:
        number = season.get('season_number')
        if not isinstance(number, int) or number <= 0:
            continue
        data = await tmdb.get_season(media.tmdb_id, number, api_key=api_key)
        episodes = data.get('episodes')
        if not isinstance(episodes, list) or len(episodes) != season.get('episode_count'):
            raise ValueError('Episode metadata is incomplete; try refreshing again later')
        if any(not e.get('id') or not e.get('episode_number') for e in episodes):
            raise ValueError('Episode identities are incomplete')
        fetched.extend((number, e) for e in episodes)
    if show is None:
        show = Show(tmdb_id=media.tmdb_id, title=media.title, canonical_source='tmdb')
        db.add(show)
        await db.flush()
    for number, data in fetched:
        episode, created = await create_media_safely(db, data['id'], MediaType.episode,
            title=data.get('name') or f'Episode {data["episode_number"]}', show_id=show.id,
            season_number=number, episode_number=data['episode_number'])
        if (episode.show_id, episode.season_number, episode.episode_number) != (show.id, number, data['episode_number']):
            raise ValueError('Episode numbering changed; existing watch history was preserved')
        episode.release_date = data.get('air_date')
        episode.title = data.get('name') or episode.title
        episode.runtime = data.get('runtime')
    air_metadata = {key: details[key] for key in ('status', 'last_episode_to_air', 'next_episode_to_air') if key in details}
    media.status = details.get('status') or media.status
    media.tmdb_data = {**(media.tmdb_data or {}), **air_metadata, 'seasons': seasons,
        'tracking_episode_ids': [data['id'] for _, data in fetched],
        'tracking_catalogue_refreshed_at': datetime.now(timezone.utc).isoformat()}
    await db.flush()
    return len(fetched)


async def refresh_tracked_catalogues(db, api_key, now: datetime | None = None, tvdb_api_key=None):
    """Refresh stale episode catalogues shared by any tracked series.

    Active shows refresh daily. Ended/canceled shows refresh monthly, but the
    daily show-metadata sweep changes a revived show's status first, making its
    catalogue immediately eligible in the same pass.
    """
    rows = (await db.execute(
        select(Media, Show)
        .join(TrackedEntry, TrackedEntry.media_id == Media.id)
        .outerjoin(Show, or_(
            and_(Media.tmdb_id.isnot(None), Show.tmdb_id == Media.tmdb_id),
            and_(Media.tvdb_id.isnot(None), Show.tvdb_id == Media.tvdb_id),
        ))
        .where(Media.media_type == MediaType.series, or_(Media.tmdb_id.isnot(None), Media.tvdb_id.isnot(None)))
        .order_by(Media.id)
    )).unique().all()
    stats = {"refreshed": 0, "episodes": 0, "skipped": 0, "failed": 0}
    for media, show in rows:
        if show is not None and show.canonical_source == "tvdb" and not tvdb_api_key:
            stats["skipped"] += 1
            continue
        if (show is None or show.canonical_source == "tmdb") and not api_key:
            stats["skipped"] += 1
            continue
        if tracking_catalogue_is_fresh(media, show.status if show else None, now):
            stats["skipped"] += 1
            continue
        try:
            async with db.begin_nested():
                if show is not None and show.canonical_source == "tvdb":
                    stats["episodes"] += await hydrate_tracking_episodes(
                        db, media, api_key, tvdb_api_key,
                    )
                else:
                    stats["episodes"] += await hydrate_tracking_episodes(db, media, api_key)
            stats["refreshed"] += 1
        except Exception:
            # A provider/season failure rolls back this title only; the existing
            # complete catalogue and watch history remain intact.
            stats["failed"] += 1
    await db.commit()
    return stats


async def refresh_tracked_tvdb_show_summaries(db, api_key, now: datetime | None = None):
    """Refresh TVDB-native season dates and artwork daily without episode fetches.

    The main show sweep is TMDB-backed. TVDB-only tracked shows need the same
    daily season-date/artwork refresh cadence so renewals and newly supplied
    season posters do not wait for the monthly final-show episode catalogue.
    """
    from core.season_releases import _metadata_timestamp

    now = now or datetime.now(timezone.utc)
    if not api_key:
        return {"refreshed": 0, "skipped": 0, "failed": 0}
    rows = (await db.execute(
        select(Show)
        .join(Media, or_(
            and_(Media.tmdb_id.is_not(None), Show.tmdb_id == Media.tmdb_id),
            and_(Media.tvdb_id.is_not(None), Show.tvdb_id == Media.tvdb_id),
        ))
        .join(TrackedEntry, TrackedEntry.media_id == Media.id)
        .where(Show.canonical_source == "tvdb", Show.tvdb_id.is_not(None))
    )).scalars().all()
    shows = {show.id: show for show in rows}.values()
    stats = {"refreshed": 0, "skipped": 0, "failed": 0}
    stale_after = timedelta(hours=20)
    semaphore = asyncio.Semaphore(10)

    async def refresh(show):
        metadata = show.tmdb_data if isinstance(show.tmdb_data, dict) else {}
        refreshed_at = _metadata_timestamp(metadata)
        if refreshed_at and now.replace(tzinfo=None) - refreshed_at < stale_after:
            stats["skipped"] += 1
            return
        async with semaphore:
            try:
                raw = await tvdb.get_series(show.tvdb_id, api_key, cache_ttl=None)
                details = tvdb.format_series(raw)
            except Exception:
                stats["failed"] += 1
                return
        show.title = details.get("title") or show.title
        show.original_title = details.get("original_title")
        show.overview = details.get("overview")
        show.poster_path = details.get("poster_path")
        show.backdrop_path = details.get("backdrop_path")
        show.status = details.get("status") or show.status
        show.first_air_date = details.get("first_air_date")
        show.last_air_date = details.get("last_air_date")
        show.tmdb_data = {
            **metadata,
            "seasons": details.get("seasons", []),
            "genres": details.get("genres", []),
            "source": "tvdb",
            "refreshed_at": now.isoformat(),
        }
        stats["refreshed"] += 1

    await asyncio.gather(*(refresh(show) for show in shows))
    await db.commit()
    return stats
