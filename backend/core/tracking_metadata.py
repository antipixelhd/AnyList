"""Load and refresh complete regular-episode catalogues for tracked series."""
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from core import tmdb
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


async def hydrate_tracking_episodes(db, media, api_key):
    if media.media_type != MediaType.series or not media.tmdb_id:
        raise ValueError('A TMDB series identity is required')
    # Serialize catalogue refreshes independently of any user's tracking state.
    await db.execute(select(Media.id).where(Media.id == media.id).with_for_update())
    show = (await db.execute(select(Show).where(Show.tmdb_id == media.tmdb_id))).scalar_one_or_none()
    if show and show.canonical_source != 'tmdb':
        raise ValueError('This show uses a different episode order; refresh through its original provider')
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
    media.tmdb_data = {**(media.tmdb_data or {}), 'seasons': seasons,
        'tracking_episode_ids': [data['id'] for _, data in fetched],
        'tracking_catalogue_refreshed_at': datetime.now(timezone.utc).isoformat()}
    await db.flush()
    return len(fetched)


async def refresh_tracked_catalogues(db, api_key, now: datetime | None = None):
    """Refresh stale TMDB episode catalogues shared by any tracked series.

    Active shows refresh daily. Ended/canceled shows refresh monthly, but the
    daily show-metadata sweep changes a revived show's status first, making its
    catalogue immediately eligible in the same pass.
    """
    rows = (await db.execute(
        select(Media, Show)
        .join(TrackedEntry, TrackedEntry.media_id == Media.id)
        .outerjoin(Show, Show.tmdb_id == Media.tmdb_id)
        .where(Media.media_type == MediaType.series, Media.tmdb_id.isnot(None))
        .order_by(Media.id)
    )).unique().all()
    stats = {"refreshed": 0, "episodes": 0, "skipped": 0, "failed": 0}
    for media, show in rows:
        if show is not None and show.canonical_source != "tmdb":
            stats["skipped"] += 1
            continue
        if tracking_catalogue_is_fresh(media, show.status if show else None, now):
            stats["skipped"] += 1
            continue
        try:
            async with db.begin_nested():
                stats["episodes"] += await hydrate_tracking_episodes(db, media, api_key)
            stats["refreshed"] += 1
        except Exception:
            # A provider/season failure rolls back this title only; the existing
            # complete catalogue and watch history remain intact.
            stats["failed"] += 1
    await db.commit()
    return stats
