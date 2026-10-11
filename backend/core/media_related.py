"""Typed detail relations, collection entries and provider recommendations.

Catalogue reads never create tracked titles. Provider-only screen titles resolve
through the existing discovery route when the user chooses one.
"""
from datetime import datetime, timedelta, timezone
import re

from sqlalchemy import func, literal, or_, select, update
from sqlalchemy.dialects.postgresql import JSONB

from core.catalogue_normalize import image
from models import Media
from models.catalogue import CatalogueCredit, CatalogueEntity, CatalogueIdentity, CatalogueLegacyLink, CatalogueRelationship


def tmdb_image(path):
    if isinstance(path, str) and re.fullmatch(r'/[A-Za-z0-9_.-]+\.(jpg|png|webp)', path):
        return 'https://image.tmdb.org/t/p/w500' + path
    return image(path)


def unique(rows, excluded=()):
    seen = set(excluded)
    result = []
    for row in rows:
        key = (row['type'], row['url'])
        if key not in seen:
            seen.add(key)
            result.append(row)
    return result


async def catalogue_related(db, work):
    if work is None:
        return {'relations': [], 'similar': []}
    edges = list(await db.scalars(select(CatalogueRelationship).where(or_(
        CatalogueRelationship.source_id == work.id, CatalogueRelationship.target_id == work.id))
        .order_by(CatalogueRelationship.id)))
    ids = {edge.target_id if edge.source_id == work.id else edge.source_id for edge in edges}
    targets = {row.id: row for row in await db.scalars(select(CatalogueEntity).where(CatalogueEntity.id.in_(ids)))} if ids else {}
    groups = {row.id: row for row in targets.values() if row.kind in ('book_series', 'game_collection')}
    memberships = list(await db.scalars(select(CatalogueRelationship).where(
        CatalogueRelationship.target_id.in_(groups), CatalogueRelationship.relation == 'series_member',
        CatalogueRelationship.source_id != work.id).order_by(CatalogueRelationship.id))) if groups else []
    member_ids = {edge.source_id for edge in memberships}
    if member_ids:
        targets.update({row.id: row for row in await db.scalars(select(CatalogueEntity).where(CatalogueEntity.id.in_(member_ids)))})
    # Books without provider recommendations can use other cached works by the
    # same author. This never crosses into screen adaptations or games.
    author_books = []
    if work.kind == 'book':
        authors = select(CatalogueCredit.contributor_id).where(CatalogueCredit.work_id == work.id, CatalogueCredit.role == 'author')
        author_books = list(await db.scalars(select(CatalogueEntity).join(CatalogueCredit,
            CatalogueCredit.work_id == CatalogueEntity.id).where(CatalogueEntity.kind == 'book',
            CatalogueEntity.id != work.id, CatalogueCredit.role == 'author', CatalogueCredit.contributor_id.in_(authors))
            .distinct().order_by(CatalogueEntity.id.desc()).limit(40)))
        targets.update({row.id: row for row in author_books})
    links = dict((await db.execute(select(CatalogueLegacyLink.entity_id, CatalogueLegacyLink.media_id)
        .where(CatalogueLegacyLink.entity_id.in_(targets)))).all()) if targets else {}
    identities = list(await db.scalars(select(CatalogueIdentity).where(CatalogueIdentity.entity_id.in_(targets)))) if targets else []
    native = {(row.entity_id, row.namespace): row.external_id for row in identities}
    for kind in ('movie', 'series'):
        by_native = {int(value):entity_id for (entity_id, namespace), value in native.items() if namespace == 'tmdb.' + kind and value.isdecimal()}
        if by_native:
            from models.base import MediaType
            for media in await db.scalars(select(Media).where(Media.media_type == MediaType(kind), Media.tmdb_id.in_(by_native))):
                links[by_native[media.tmdb_id]] = media.id

    def card(target):
        if target.kind not in ('movie', 'series', 'game', 'book') or target.id == work.id or (target.attributes or {}).get('adult'):
            return None
        url = f'/title/{links[target.id]}' if target.id in links else None
        if target.kind in ('game', 'book'):
            url = f'/{"games" if target.kind == "game" else "books"}/{target.id}'
        elif not url:
            provider_id = native.get((target.id, 'tmdb.' + target.kind))
            if provider_id and provider_id.isdecimal():
                url = f'/discover-title?type={target.kind}&id={provider_id}'
            else:
                provider_id = native.get((target.id, 'tvdb.' + target.kind))
                if provider_id and provider_id.isdecimal():
                    url = f'https://thetvdb.com/dereferrer/{target.kind}/{provider_id}'
        if not url:
            return None
        return {'id':target.id, 'title':target.name, 'type':target.kind, 'poster':image(target.image_url),
            'year':str((target.attributes or {}).get('release_date') or '')[:4], 'url':url}

    relations, similar, seen = [], [], set()
    for edge in edges:
        target = targets.get(edge.target_id if edge.source_id == work.id else edge.source_id)
        row = card(target) if target else None
        reverse = edge.target_id == work.id
        if not row or (target.id, edge.relation, reverse) in seen:
            continue
        seen.add((target.id, edge.relation, reverse))
        row.update(relation=edge.relation, reverse=reverse)
        if edge.relation == 'recommendation':
            if not reverse:
                (similar if target.kind == work.kind else relations).append(row)
        else:
            relations.append(row)
    for edge in memberships:
        target = targets.get(edge.source_id)
        row = card(target) if target else None
        if row:
            row.update(relation='series_member', reverse=False, label=groups[edge.target_id].name)
            relations.append(row)
    excluded = {(row['type'], row['url']) for row in relations}
    similar.extend(row for target in author_books if (row := card(target)))
    return {'relations':relations, 'similar':unique(similar, excluded)[:12]}


def related_pending(media):
    data = media.tmdb_data or {}
    cache = data.get('_anylist_related') or {}
    collection = data.get('belongs_to_collection') or {}
    return bool(media.tmdb_id and (
        (collection.get('id') and (cache.get('collection') or {}).get('id') != collection.get('id')) or
        ('recommendations' not in data and 'recommendations' not in cache)))


async def screen_related(db, media, details):
    data = media.tmdb_data or {}
    cache = data.get('_anylist_related') or {}
    kind = media.media_type.value
    collection = cache.get('collection') or {}
    parts = collection.get('parts') or []
    recommendations = (cache.get('recommendations') or data.get('recommendations') or {}).get('results') or []
    ids = {row.get('id') for row in parts + recommendations if isinstance(row, dict) and isinstance(row.get('id'), int)}
    locals_ = {row.tmdb_id:row.id for row in await db.scalars(select(Media).where(
        Media.media_type == media.media_type, Media.tmdb_id.in_(ids)))} if ids else {}

    def card(raw):
        native = raw.get('id')
        actual_kind = {'tv':'series', 'movie':'movie', 'series':'series'}.get(raw.get('media_type'), kind)
        if not isinstance(native, int) or native <= 0 or native == media.tmdb_id or raw.get('adult') or actual_kind != kind:
            return None
        return {'id':locals_.get(native), 'tmdb_id':native, 'type':kind, 'title':raw.get('title') or raw.get('name') or '',
            'poster':tmdb_image(raw.get('poster_path')), 'year':str(raw.get('release_date') or raw.get('first_air_date') or '')[:4],
            'url':f'/title/{locals_[native]}' if native in locals_ else f'/discover-title?type={kind}&id={native}'}

    relations = list(details.get('relations') or [])
    if kind == 'movie' and collection.get('id') == (data.get('belongs_to_collection') or {}).get('id'):
        for raw in sorted(parts, key=lambda row: (row.get('release_date') or '9999', row.get('id') or 0)):
            if row := card(raw):
                date = raw.get('release_date')
                row.update(relation='series_member', reverse=False, label='Previous entry' if date and media.release_date and date < media.release_date else 'Next entry' if date and media.release_date and date > media.release_date else collection.get('name') or 'Same collection')
                relations.append(row)
    relations = unique(relations)
    excluded = {(row['type'], row['url']) for row in relations}
    similar = unique([row for raw in recommendations if isinstance(raw, dict) and (row := card(raw))] + list(details.get('similar') or []), excluded)[:12]
    return {**details, 'relations':relations, 'similar':similar, 'related_pending':related_pending(media)}


async def fill_screen_related(db, media, api_key):
    """Cache missing public metadata atomically without replacing other fields."""
    if not api_key or not related_pending(media):
        return
    from core import tmdb
    data = media.tmdb_data or {}
    cache = dict(data.get('_anylist_related') or {})
    # Failed lookups retry after a short cooldown instead of every page view.
    try:
        if datetime.fromisoformat(cache.get('retry_after', '')) > datetime.now(timezone.utc):
            return
    except (ValueError, TypeError):
        pass
    try:
        collection = data.get('belongs_to_collection') or {}
        if collection.get('id') and (cache.get('collection') or {}).get('id') != collection['id']:
            result = await tmdb.get_collection(collection['id'], api_key)
            if result.get('id') != collection['id'] or not isinstance(result.get('parts'), list):
                raise ValueError('Invalid collection response')
            cache['collection'] = result
        if 'recommendations' not in data and 'recommendations' not in cache:
            result = await (tmdb.get_movie_recommendations(media.tmdb_id, api_key) if media.media_type.value == 'movie' else tmdb.get_show_recommendations(media.tmdb_id, api_key))
            if not isinstance(result.get('results'), list):
                raise ValueError('Invalid recommendation response')
            cache['recommendations'] = result
        cache.pop('retry_after', None)
    except Exception:
        cache['retry_after'] = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    await db.execute(update(Media).where(Media.id == media.id).values(tmdb_data=func.coalesce(Media.tmdb_data, literal({}, type_=JSONB)).op('||')({'_anylist_related':cache})))
    await db.commit()
    await db.refresh(media)
