"""Compact public detail presentation; no episode layout, artwork or viewer state."""
from datetime import datetime, timezone
from sqlalchemy import select
from core.countries import screen_countries
from core.movie_financials import movie_financials
from core.tracking_projection import media_data
from models.catalogue import MetadataSnapshot

FIELDS = ('id', 'type', 'title', 'original_title', 'overview', 'tagline', 'release_date', 'year',
          'release_status', 'runtime', 'episode_runtime', 'season_count', 'episode_count',
          'original_language', 'genres', 'creators', 'networks', 'countries', 'budget', 'revenue')
SNAPSHOT_FIELDS = ('original_title', 'original_name', 'tagline', 'status', 'number_of_seasons',
                   'number_of_episodes', 'episode_run_time', 'created_by', 'networks', 'averageRuntime', 'budget', 'revenue')


async def screen_primary(db, media, work):
    """Reuse normalized metadata and focused snapshot fields without full credits."""
    base = media_data(media)
    data = {field: base.get(field) for field in FIELDS}
    data['countries'] = screen_countries(media.tmdb_data or {})['countries']
    status = {'state': 'pending', 'pending': True}
    if work is None:
        return data, status
    attrs = work.attributes or {}
    provider = ('tvdb' if (media.tmdb_data or {}).get('tracking_catalogue_provider') == 'tvdb'
                or (media.tmdb_data or {}).get('source') == 'tvdb' else
                (work.field_sources or {}).get('name', 'tmdb'))
    if provider not in ('tmdb', 'tvdb'):
        provider = 'tmdb' if media.tmdb_id else 'tvdb'
    row = (await db.execute(select(MetadataSnapshot.coverage, MetadataSnapshot.expires_at,
        *[MetadataSnapshot.payload[field].label(field) for field in SNAPSHOT_FIELDS]).where(
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
    data['year'] = (data.get('release_date') or '')[:4]
    return data, status
