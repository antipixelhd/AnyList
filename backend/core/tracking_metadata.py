"""Load a complete regular-episode catalogue before cumulative progress edits."""
from datetime import datetime, timezone
from sqlalchemy import select
from core import tmdb
from core.enrichment import create_media_safely
from models import Media, Show
from models.base import MediaType


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
