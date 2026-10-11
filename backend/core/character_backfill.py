"""Bounded role projection and optional key-free series artwork enrichment."""
import re
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse
from sqlalchemy import select, or_, String
from models.catalogue import CatalogueCredit, CatalogueEntity, CatalogueIdentity, MetadataSnapshot
from models.tracking import TrackedEntry
from models.media import Media
from core import catalogue
from core.catalogue_normalize import image
from core.catalogue_providers import ProviderError
from core.screen_characters import normalized_role, role_name, project_characters

VERSION = 1


async def backfill_screen_characters(db, *, limit=25, force=False):
    query = select(CatalogueEntity).where(CatalogueEntity.kind.in_(['movie', 'series']),
        select(CatalogueCredit.id).where(CatalogueCredit.work_id == CatalogueEntity.id, CatalogueCredit.role == 'actor').exists())
    if not force:
        query = query.where(or_(CatalogueEntity.attributes['screen_character_version'].astext.is_(None),
            CatalogueEntity.attributes['screen_character_version'].astext != str(VERSION)))
    works = list(await db.scalars(query.order_by(CatalogueEntity.id).limit(limit)))
    await catalogue.write_lock(db)
    count = 0
    for work in works:
        count += await project_characters(db, work.id)
        work.attributes = {**(work.attributes or {}), 'screen_character_version': VERSION}
    await db.commit()
    return {'examined': len(works), 'credits_mapped': count}


async def tvmaze_show(http, ids):
    for lookup_namespace, param in (('imdb.title', 'imdb'), ('tvdb.series', 'thetvdb')):
        if lookup_namespace not in ids:
            continue
        try:
            raw = await http.request('tvmaze', 'GET', 'https://api.tvmaze.com/lookup/shows', params={param: ids[lookup_namespace]})
        except ProviderError as error:
            if error.code == 'not_found':
                continue
            raise
        if not isinstance(raw, dict):
            raise ProviderError('invalid_json')
        if '_redirect' in raw:
            parsed = urlparse(raw['_redirect'])
            if parsed.scheme != 'https' or parsed.netloc != 'api.tvmaze.com' or not re.fullmatch(r'/shows/[1-9][0-9]*', parsed.path) or parsed.query:
                raise ProviderError('identity_conflict')
            raw = await http.request('tvmaze', 'GET', raw['_redirect'])
        if not isinstance(raw, dict) or type(raw.get('id')) is not int or raw['id'] <= 0:
            raise ProviderError('invalid_json')
        external = raw.get('externals') or {}
        if not isinstance(external, dict):
            raise ProviderError('invalid_json')
        for namespace, field in (('imdb.title', 'imdb'), ('tvdb.series', 'thetvdb')):
            if namespace in ids and external.get(field) is not None and str(external[field]) != ids[namespace]:
                raise ProviderError('identity_conflict')
        if str(external.get(param)) != ids[lookup_namespace]:
            raise ProviderError('identity_conflict')
        return raw
    return None


def match_cast(credits, cast):
    """Match a performance in an exactly identified show, not a person identity."""
    candidates = {}
    primary = [(c, p) for c, p in credits if c.provider == 'tmdb']
    for credit, person in primary or credits:
        key = (normalized_role(person.name), normalized_role(credit.character_label))
        if key[0] and key[1]:
            candidates.setdefault(key, {})[person.id] = credit
    matches = []
    for member in cast:
        if not isinstance(member, dict):
            continue
        person = member.get('person') or {}; character = member.get('character') or {}
        if not isinstance(person, dict) or not isinstance(character, dict):
            continue
        artwork = character.get('image')
        url = image(artwork) if isinstance(artwork, str) else (image(artwork.get('original')) or image(artwork.get('medium'))) if isinstance(artwork, dict) else None
        key = (normalized_role(person.get('name')), normalized_role(character.get('name')))
        choices = candidates.get(key, {})
        if not url or len(choices) != 1 or type(person.get('id')) is not int or type(character.get('id')) is not int or min(person['id'], character['id']) <= 0:
            continue
        credit = next(iter(choices.values()))
        matches.append({'work_id': credit.work_id, 'contributor_id': credit.contributor_id,
            'role': 'actor', 'role_label': 'Actor', 'character_label': role_name(character.get('name'))[:500],
            'character_image_url': url, 'provider': 'tvmaze', 'source_key': f"{person['id']}:{character['id']}"})
    return matches


async def backfill_tvmaze_images(db, providers, *, limit=10, force=False):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    cutoff = (now - timedelta(days=7)).isoformat()
    query = select(CatalogueEntity).where(CatalogueEntity.kind == 'series',
        select(CatalogueIdentity.id).join(Media, or_(
            (CatalogueIdentity.namespace == 'tmdb.series') & (CatalogueIdentity.external_id == Media.tmdb_id.cast(String)),
            (CatalogueIdentity.namespace == 'tvdb.series') & (CatalogueIdentity.external_id == Media.tvdb_id.cast(String))))
        .join(TrackedEntry, TrackedEntry.media_id == Media.id).where(CatalogueIdentity.entity_id == CatalogueEntity.id).exists())
    if not force:
        query = query.where(or_(CatalogueEntity.attributes['tvmaze_character_attempted_at'].astext.is_(None),
            CatalogueEntity.attributes['tvmaze_character_attempted_at'].astext < cutoff))
    works = list(await db.scalars(query.order_by(CatalogueEntity.id).limit(limit)))
    result = {'examined': len(works), 'images': 0, 'failed': 0, 'unmatched': 0}
    for work in works:
        error_code = None
        try:
            ids = {i.namespace: i.external_id for i in await db.scalars(select(CatalogueIdentity).where(CatalogueIdentity.entity_id == work.id))}
            show = await tvmaze_show(providers.http, ids)
            if not show:
                result['unmatched'] += 1
            else:
                cast = await providers.http.request('tvmaze', 'GET', f"https://api.tvmaze.com/shows/{show['id']}/cast")
                if not isinstance(cast, list):
                    raise ProviderError('invalid_json')
                async with db.begin_nested():
                    await catalogue.write_lock(db)
                    credits = (await db.execute(select(CatalogueCredit, CatalogueEntity).join(CatalogueEntity,
                        CatalogueEntity.id == CatalogueCredit.contributor_id).where(CatalogueCredit.work_id == work.id,
                        CatalogueCredit.role == 'actor', CatalogueEntity.kind == 'person')
                        .execution_options(populate_existing=True))).all()
                    matches = match_cast(credits, cast)
                    for values in matches:
                        await catalogue.upsert(db, CatalogueCredit, values, ['work_id', 'provider', 'source_key'], preserve_missing=True)
                    await project_characters(db, work.id)
                    snapshot = await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.provider == 'tvmaze',
                        MetadataSnapshot.kind == 'series', MetadataSnapshot.external_id == str(show['id'])))
                    if not snapshot:
                        snapshot = MetadataSnapshot(provider='tvmaze', kind='series', external_id=str(show['id']))
                        db.add(snapshot)
                    if snapshot.entity_id and snapshot.entity_id != work.id:
                        raise ProviderError('identity_conflict')
                    snapshot.entity_id = work.id; snapshot.payload = {'show': show, 'cast': cast}
                    snapshot.fetched_at = now; snapshot.expires_at = now + timedelta(days=7)
                    snapshot.error_code = None; snapshot.attempts = 0
                    snapshot.coverage = {'matched_character_images': len(matches), 'match_basis': 'verified_show_unique_actor_role'}
                result['images'] += len(matches)
        except Exception as error:
            # Keep last-good credits and never log provider responses/credentials.
            result['failed'] += 1
            error_code = error.code if isinstance(error, ProviderError) else 'unavailable'
        # Successful/absent artwork is checked weekly; transient failures retry
        # the next daily pass without repeatedly hammering a failed provider.
        attempted = now - timedelta(days=6) if error_code else now
        work.attributes = {**(work.attributes or {}), 'tvmaze_character_attempted_at': attempted.isoformat(),
                           'tvmaze_character_error': error_code}
        await db.commit()
    return result
