"""Compact public detail presentation; no episode layout, artwork or viewer state."""
from datetime import datetime, timezone
from sqlalchemy import select
from core.countries import screen_countries
from core.episode_release import EpisodeRelease
from core.movie_financials import movie_financials
from core.season_releases import current_airing_episode
from core.tracking_metadata import released_episodes
from core.tracking_projection import media_data
from models.catalogue import MetadataSnapshot

FIELDS = ('id', 'type', 'title', 'original_title', 'overview', 'tagline', 'release_date', 'year',
          'release_status', 'runtime', 'episode_runtime', 'season_count', 'episode_count',
          'original_language', 'genres', 'creators', 'networks', 'countries', 'budget', 'revenue')
SNAPSHOT_FIELDS = ('original_title', 'original_name', 'tagline', 'status', 'number_of_seasons',
                   'number_of_episodes', 'episode_run_time', 'created_by', 'networks', 'averageRuntime', 'budget', 'revenue')
EPISODE_POINTERS = ('last_episode_to_air', 'next_episode_to_air')

async def series_airing(db, media, status, schedule_fields=None) -> dict | None:
    metadata = {**(media.tmdb_data or {}), **(schedule_fields or {})}
    status = str(metadata.get('status') or status or '').strip().casefold()
    if status not in ('returning series', 'airing', 'continuing'):
        return None
    release = EpisodeRelease(media.tvdb_air_schedule)
    episodes = await released_episodes(db, media, include_unreleased=True, release_details_only=True)
    airing, episode = current_airing_episode({**metadata, 'status': 'Returning Series'}, episodes, release=release)
    if episode:
        return {**episode, 'air_at': release.air_at(episode['air_date'])}
    return {} if airing or status == 'airing' else None


async def screen_primary(db, media, work):
    """Reuse normalized metadata and focused snapshot fields without full credits."""
    base = media_data(media)
    data = {field: base.get(field) for field in FIELDS}
    data['countries'] = screen_countries(media.tmdb_data or {})['countries']
    status = {'state': 'pending', 'pending': True}
    if work is None:
        if data['type'] == 'series':
            data['series_airing'] = await series_airing(db, media, data['release_status'])
        return data, status
    attrs = work.attributes or {}
    provider = ('tvdb' if (media.tmdb_data or {}).get('tracking_catalogue_provider') == 'tvdb'
                or (media.tmdb_data or {}).get('source') == 'tvdb' else
                (work.field_sources or {}).get('name', 'tmdb'))
    if provider not in ('tmdb', 'tvdb'):
        provider = 'tmdb' if media.tmdb_id else 'tvdb'
    # Credits enrichment also caches provider episode pointers for series that
    # have not loaded their tracking catalogue yet.
    schedule_fields = ()
    if data['type'] == 'series' and provider == 'tmdb':
        metadata = media.tmdb_data or {}
        schedule_fields = tuple(field for field in EPISODE_POINTERS if field not in metadata)
        if not metadata.get('seasons'):
            schedule_fields += ('seasons',)
    row = (await db.execute(select(MetadataSnapshot.coverage, MetadataSnapshot.expires_at,
        *[MetadataSnapshot.payload[field].label(field) for field in (*SNAPSHOT_FIELDS, *schedule_fields)]).where(
        MetadataSnapshot.entity_id == work.id, MetadataSnapshot.kind == media.media_type.value,
        MetadataSnapshot.provider == provider).order_by(MetadataSnapshot.fetched_at.desc().nullslast()).limit(1))).first()
    raw = dict(row._mapping) if row else {}
    from core.media_credits import VERSION
    if row and row.expires_at and row.expires_at > datetime.now(timezone.utc).replace(tzinfo=None) and (
            row.coverage or {}).get('detail_credits_version', 0) >= VERSION:
        status = {'state': 'ready', 'pending': False}
    runtime = attrs.get('episode_run_time') or raw.get('episode_run_time') or []
    release_status = attrs.get('status') or raw.get('status')
    if isinstance(release_status, dict):
        release_status = release_status.get('name')
    fallback = dict(title=work.name, overview=work.description,
        release_date=attrs.get('release_date'), original_title=raw.get('original_title') or raw.get('original_name'),
        tagline=attrs.get('tagline') or raw.get('tagline'), release_status=release_status,
        runtime=attrs.get('runtime'), episode_runtime=next((value for value in runtime if value), None) or raw.get('averageRuntime'),
        season_count=attrs.get('season_count') or raw.get('number_of_seasons'),
        episode_count=attrs.get('episode_count') or raw.get('number_of_episodes'),
        original_language=attrs.get('original_language'), genres=[value if isinstance(value, str) else value.get('name')
            for value in attrs.get('genres') or [] if isinstance(value, str) or isinstance(value, dict) and value.get('name')],
        creators=raw.get('created_by') or [], networks=raw.get('networks') or [], countries=attrs.get('countries') or [])
    if media.media_type.value == 'movie':
        data.update(movie_financials(attrs, raw, data))
    data['countries'] = attrs.get('countries') or data['countries']
    for field, value in fallback.items():
        if not data.get(field) and value:
            data[field] = value
    if data['type'] == 'series':
        schedule = {field: raw[field] for field in schedule_fields if raw.get(field) is not None}
        data['series_airing'] = await series_airing(db, media, data['release_status'], schedule)
    data['year'] = (data.get('release_date') or '')[:4]
    return data, status
