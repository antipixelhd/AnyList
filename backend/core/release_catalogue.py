"""Page stored release events in PostgreSQL, without loading whole catalogues.

The same pure event rules normalize the small returned page. SQL supplies the
ordered candidate stream, including cached year/month-only season metadata.
"""
from datetime import date

from sqlalchemy import String, case, func, select, text, true

from core import tracking_projection
from core.release_events import events, window
from models import Media


async def page(db, media_type, filters, *, show_anime=True, lower=None, upper=None, offset=0, size=24, term=''):
    from core.browse import local_query
    from core.chronological_discovery import audience_relevant

    upcoming = filters['sort'] == 'upcoming' or filters.get('status') == 'upcoming'
    # Premiere dates cannot prefilter season returns or later country releases.
    eligible = local_query(media_type, term=term, show_anime=show_anime,
        **{**filters, 'start': None, 'end': None, 'status': '' if upcoming else filters.get('status', ''),
           'sort': 'title', 'cinema': False}).order_by(None).cte('release_titles')
    # All SQL is static; provider/country values remain bound parameters.
    candidate = text("""
        SELECT COALESCE(NULLIF(release_titles.tmdb_data->>:date_field, ''), release_titles.release_date) AS value
        UNION
        SELECT COALESCE(NULLIF(season->>'air_date', ''), season->>'year')
        FROM jsonb_array_elements(CASE WHEN :include_seasons AND jsonb_typeof(release_titles.tmdb_data->'seasons')='array'
             THEN release_titles.tmdb_data->'seasons' ELSE '[]'::jsonb END) season
        WHERE season->>'season_number' ~ '^[1-9][0-9]*$' AND season->>'season_number' <> '1'
        UNION
        SELECT release_titles.tmdb_data->'next_episode_to_air'->>'air_date'
        WHERE :include_seasons AND release_titles.tmdb_data->'next_episode_to_air'->>'episode_number' = '1'
        UNION
        SELECT jsonb_array_elements_text(CASE WHEN jsonb_typeof(release_titles.tmdb_data->'theatrical_release_dates_by_country'->:country)='array'
            THEN release_titles.tmdb_data->'theatrical_release_dates_by_country'->:country ELSE '[]'::jsonb END)
        UNION
        SELECT left(release->>'release_date', 10)
        FROM jsonb_array_elements(CASE WHEN jsonb_typeof(release_titles.tmdb_data->'release_dates'->'results')='array'
            THEN release_titles.tmdb_data->'release_dates'->'results' ELSE '[]'::jsonb END) territory,
             jsonb_array_elements(CASE WHEN jsonb_typeof(territory->'release_dates')='array'
            THEN territory->'release_dates' ELSE '[]'::jsonb END) release
        WHERE territory->>'iso_3166_1' = :country
    """).bindparams(date_field='first_air_date' if media_type == 'series' else 'release_date',
        include_seasons=media_type == 'series' and filters.get('include_returning', True),
        country=filters.get('cinema_country') or filters.get('region') or '').columns(value=String).lateral('release_candidates')
    value = candidate.c.value
    valid = value.op('~')(r'^[1-9][0-9]{3}(-[0-9]{2}(-[0-9]{2})?)?$')
    start = case((func.length(value) == 4, value + '-01-01'),
                 (func.length(value) == 7, value + '-01'), else_=value)
    # Lexical bounds preserve uncertain dates. Pure rules below validate days
    # and distinguish an announced window from a known exact release date.
    stop = case((func.length(value) == 4, value + '-12-31'),
                (func.length(value) == 7, value + '-31'), else_=value)
    query = select(eligible.c.id.label('media_id'), value, start.label('event_order')).select_from(
        eligible.join(candidate, true())).where(valid)
    if lower:
        query = query.where(stop >= lower)
    if upper:
        query = query.where(start <= upper)
    if upcoming:
        query = query.where(stop >= date.today().isoformat())
    else:
        query = query.where(start <= date.today().isoformat())
    candidates = query.cte('release_candidates_ordered')
    # Read candidates in chronological batches. A title can contribute multiple
    # distinct events; consumed SQL positions, not a catalogue cap, paginate.
    ordering = candidates.c.event_order.asc() if upcoming else candidates.c.event_order.desc()
    query = select(Media, candidates.c.value).join(candidates, candidates.c.media_id == Media.id).order_by(ordering, Media.id)
    output, seen = [], set()
    consumed = offset
    exhausted = False
    for _ in range(4):
        batch = (await db.execute(query.offset(consumed).limit(size + 1))).all()
        if not batch:
            exhausted = True
            break
        for media, value in batch:
            consumed += 1
            raw = {**(media.tmdb_data or {}), 'id': media.tmdb_id or media.id, 'title': media.title,
                   'status': (media.tmdb_data or {}).get('status') or media.status}
            field = 'first_air_date' if media_type == 'series' else 'release_date'
            raw[field] = raw.get(field) or media.release_date
            if filters.get('audience_curation', True) and not audience_relevant(raw, filters.get('region')):
                continue
            for event in events(raw, media_type, upcoming_only=upcoming,
                    include_returning=filters.get('include_returning', True),
                    country=filters.get('cinema_country') or filters.get('region'), cinema=filters.get('cinema', False)):
                if event['release_date'] != value or event['release_event'] in seen:
                    continue
                bounds = window(value)
                if not bounds or (lower and bounds[1].isoformat() < lower) or (upper and bounds[0].isoformat() > upper):
                    continue
                if not upcoming and bounds[1] > date.today():
                    continue
                seen.add(event['release_event'])
                output.append((media, event, consumed))
            if len(output) >= size:
                break
        if len(output) >= size:
            break
        if len(batch) < size + 1:
            exhausted = True
            break
    return output, consumed, not exhausted


def project(media, event):
    return {**tracking_projection.media_data(media), **{name: event[name]
        for name in ('title', 'release_date', 'release_event', 'release_season', 'release_country')},
        'year': event['release_date'][:4]}
