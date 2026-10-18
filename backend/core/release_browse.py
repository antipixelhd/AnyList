"""Merge ordered provider and stored event streams before presenting a page."""
from datetime import date

from sqlalchemy import select

from core import chronological_discovery, release_catalogue
from core.browse_cursor import encode_search_cursor
from core.catalog_search import known_result_visible
from core.release_events import window
from models import Media
from models.base import MediaType


def remote_projection(item, kind):
    from core.browse import GENRES
    value = item['release_date']
    return {'id': None, 'tmdb_id': item['id'], 'type': kind,
            'title': item.get('title') or item.get('name'), 'release_date': value, 'year': value[:4],
            'release_event': item['release_event'], 'release_season': item.get('release_season'),
            'release_country': item.get('release_country'), 'poster': item.get('poster_path'),
            'tmdb_score': item.get('vote_average'), 'imdb_score': item.get('imdb_score'),
            'genres': [GENRES.get(native, '') for native in item.get('genre_ids', [])]
                or [genre.get('name', '') for genre in item.get('genres', []) if isinstance(genre, dict)]}


async def page(db, viewer, *, media_type, filters, key, show_anime, page_number, cursor=None,
               source='', prefetched=None, size=24, term='', event_view=False):
    from core.tracking_editor import attach_editor_context
    upcoming = filters['sort'] == 'upcoming' or filters.get('status') == 'upcoming'
    if source == 'remote' and not key:
        raise ValueError('The metadata key is no longer available')
    feed_offset = cursor[2] if cursor and cursor[2] >= 0 else 0
    local_offset = max(cursor[1], 0) if cursor else (page_number - 1) * size
    remote, provider = None, []
    notice = None
    if key and source != 'local':
        try:
            if isinstance(prefetched, BaseException):
                raise prefetched
            remote, provider = prefetched or await chronological_discovery.page(
                media_type, key, filters, offset=feed_offset, show_anime=show_anime, size=size, term=term)
        except Exception:
            if source == 'remote' or page_number > 1:
                raise
            notice = 'Discovery is unavailable. Showing the local catalogue.'
    lower = remote['window_lower'] if remote else filters.get('start')
    upper = remote['window_upper'] if remote else filters.get('end')
    if not upcoming and not upper:
        upper = date.today().isoformat()
    local, consumed, local_more = await release_catalogue.page(db, media_type, filters,
        lower=lower, upper=upper, offset=local_offset, size=size, show_anime=show_anime, term=term)
    known = list(await db.scalars(select(Media).where(Media.media_type == MediaType(media_type),
        Media.tmdb_id.in_([item['id'] for item in provider])))) if provider else []
    by_tmdb = {media.tmdb_id: media for media in known}
    visible = []
    for item in provider:
        cached = by_tmdb.get(item['id'])
        if cached and not known_result_visible(item, cached, media_type, show_anime=show_anime,
                min_runtime=filters.get('min_runtime', 0), excluded_genres=filters.get('excluded_genres', ())):
            visible.append(None)  # Keep the provider position consumed.
        else:
            row = remote_projection(item, media_type)
            if cached:
                row['id'] = cached.id
            visible.append(row)
    local_rows = [release_catalogue.project(media, event) for media, event, _ in local]
    provider_keys = {row['release_event'] for row in visible if row}
    li = ri = 0
    rows, identities = [], set()
    while len(rows) < size and (li < len(local_rows) or ri < len(visible)):
        if ri < len(visible) and visible[ri] is None:
            ri += 1
            continue
        if li < len(local_rows) and local_rows[li]['release_event'] in provider_keys:
            li += 1  # Fresh provider event takes precedence over a cached date.
            continue
        use_local = ri >= len(visible)
        if li < len(local_rows) and ri < len(visible):
            left = (window(local_rows[li]['release_date'])[0], local_rows[li]['release_event'])
            right = (window(visible[ri]['release_date'])[0], visible[ri]['release_event'])
            use_local = left <= right if upcoming else left >= right
        if use_local:
            row = local_rows[li]
            li += 1
        else:
            row = visible[ri]
            ri += 1
        if row['release_event'] not in identities:
            identities.add(row['release_event'])
            rows.append(row)
    # Advance only consumed prefixes; never skip a cached result because a
    # provider stream fetched a different number of candidates.
    local_next = local[li - 1][2] if li else local_offset
    if li == len(local):
        local_next = consumed
    local_remaining = li < len(local) or local_more
    provider_remaining = ri < len(provider) or bool(remote and remote['window_has_more'])
    has_more = local_remaining or provider_remaining
    if remote:
        stride = chronological_discovery.CURSOR_STRIDE
        window_index, position = divmod(feed_offset, stride)
        feed_next = window_index * stride + position + ri
        if not has_more and remote['more_windows']:
            has_more = True
            feed_next, local_next = (window_index + 1) * stride, 0
    else:
        feed_next = -1
    for row in rows:
        row.update(list_status=None, score=None, rating_mode='manual')
    media_rows = {media.id: media for media in [*known, *(entry[0] for entry in local)]}
    await attach_editor_context(db, viewer, rows, media_rows=list(media_rows.values()))
    if term and not event_view:
        from core.catalog_search import media_search_rank
        provider_metadata = {item['id']: item for item in provider}
        local_metadata = {media.tmdb_id: media.tmdb_data or {} for media in media_rows.values()}
        for row in rows:
            row['search_rank'] = media_search_rank(term, {**local_metadata.get(row['tmdb_id'], {}),
                **provider_metadata.get(row['tmdb_id'], {}), 'title':row['title']}, media_type)
        rows.sort(key=lambda row: row['search_rank'], reverse=True)
    return {'results': rows, 'has_more': has_more, 'page': page_number,
            'cursor': encode_search_cursor(1, local_next, feed_next) if has_more else None,
            'source': 'remote' if remote else 'local', 'partial': bool(remote and remote['partial']),
            'notice': remote.get('notice') if remote else notice}
