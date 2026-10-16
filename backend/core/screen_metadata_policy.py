"""Shared source selection for screen metadata; episode numbering stays fixed."""
from sqlalchemy import or_, select

from core import catalogue
from models.base import MediaType
from models.catalogue import CatalogueCredit, CatalogueEntity
from models.show import Show


def credit_group(credit):
    if credit['contributor']['kind'] == 'organization':
        return 'organizations'
    return 'cast' if credit['role'] == 'actor' else 'crew'


async def screen_sources(db, media):
    data = media.tmdb_data or {}
    primary = 'tvdb' if data.get('tracking_catalogue_provider') == 'tvdb' or data.get('source') == 'tvdb' else 'tmdb'
    tvdb_id = media.tvdb_id
    if media.media_type == MediaType.series:
        terms = []
        if media.tmdb_id:
            terms.append(Show.tmdb_id == media.tmdb_id)
        if tvdb_id:
            terms.append(Show.tvdb_id == tvdb_id)
        if terms:
            rows = list((await db.execute(select(Show.canonical_source, Show.tvdb_id).where(or_(*terms)))).all())
            # Never switch an established TVDB episode catalogue, even after a
            # TMDB identity has been added. Ambiguous IDs do not create bridges.
            if any(row.canonical_source == 'tvdb' for row in rows):
                primary = 'tvdb'
            ids = {row.tvdb_id for row in rows if row.tvdb_id}
            if not tvdb_id and len(ids) == 1:
                tvdb_id = ids.pop()
    ids = {'tmdb': media.tmdb_id, 'tvdb': tvdb_id}
    secondary = 'tvdb' if primary == 'tmdb' else 'tmdb'
    return [(primary, ids[primary]), (secondary, ids[secondary])]


async def metadata_gaps(db, work):
    groups = {'cast', 'crew', 'organizations'}
    if work is None:
        return groups, True
    rows = await db.execute(select(CatalogueCredit.role, CatalogueEntity.kind).join(
        CatalogueEntity, CatalogueEntity.id == CatalogueCredit.contributor_id).where(
        CatalogueCredit.work_id == work.id).distinct())
    for role, kind in rows:
        groups.discard('organizations' if kind == 'organization' else 'cast' if role == 'actor' else 'crew')
    attributes = work.attributes or {}
    missing = any(not catalogue.has_value(getattr(work, field)) for field in ('name', 'description', 'image_url'))
    missing |= any(not catalogue.has_value(attributes.get(field)) for field in ('release_date', 'genres'))
    return groups, missing


def gap_document(document, groups):
    """Project only absent sections; keep the complete provider snapshot intact."""
    doc = {**document, 'work': {**document['work']}}
    doc['credits'] = [credit for credit in document['credits'] if credit_group(credit) in groups]
    # A secondary provider must never supply a competing season/episode layout.
    doc['work']['attributes'] = {key: value for key, value in document['work'].get('attributes', {}).items()
                                 if key not in ('episode_count', 'regular_episode_count', 'episode_run_time')}
    return doc
