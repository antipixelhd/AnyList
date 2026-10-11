"""Fill missing contributor profiles through verified IDs; never change credits."""
import asyncio
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import exists, or_, select

from core import catalogue, tmdb, tvdb
from core.contributor_details import positive_id, public_url
from core.statistics_genres import poster
from models.catalogue import CatalogueCredit, CatalogueEntity, CatalogueIdentity, CatalogueLegacyLink
from models.tracking import TrackedEntry

VERSION = 1
FIELDS = ('birthday', 'deathday', 'place_of_birth', 'known_for_department', 'also_known_as',
          'imdb_id', 'origin_country', 'headquarters', 'homepage')


def missing_values(row, raw):
    attrs = row.attributes or {}
    values = {'attributes': {key: raw[key] for key in FIELDS if raw.get(key) and not attrs.get(key)}}
    if 'homepage' in values['attributes'] and not public_url(values['attributes']['homepage']):
        del values['attributes']['homepage']
    if not row.description:
        values['description'] = raw.get('biography') or raw.get('description')
    if not row.image_url:
        values['image_url'] = poster(raw.get('profile_path') or raw.get('logo_path'))
    return values


def tvdb_profile(raw):
    biographies = [b for b in raw.get('biographies') or [] if isinstance(b, dict) and b.get('biography')]
    preferred = next((b for b in biographies if b.get('language') in ('eng','en')), None)
    image = raw.get('image')
    if isinstance(image, str) and image and not image.startswith(('https://','http://')):
        image = 'https://artworks.thetvdb.com/' + image.lstrip('/')
    result = {'id':raw.get('id'), 'biography':(preferred or (biographies[0] if biographies else {})).get('biography'),
              'profile_path':image, 'place_of_birth':raw.get('birthPlace'),
              'also_known_as':[alias['name'] for alias in raw.get('aliases') or [] if isinstance(alias,dict) and alias.get('name')]}
    for source,target in [('birth','birthday'),('death','deathday')]:
        try:
            value = str(raw.get(source) or '')[:10]
            result[target] = date.fromisoformat(value).isoformat()
        except ValueError:
            pass
    return result


async def backfill_contributors(db, api_key, *, limit=100, provider='tmdb'):
    if provider not in ('tmdb','tvdb'):
        raise ValueError('Unsupported contributor provider')
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    attrs = CatalogueEntity.attributes
    version_key = 'contributor_profile_version' if provider == 'tmdb' else 'contributor_tvdb_profile_version'
    attempted_key = 'contributor_profile_attempted_at' if provider == 'tmdb' else 'contributor_tvdb_profile_attempted_at'
    namespaces = ((CatalogueEntity.kind == 'person') & (CatalogueIdentity.namespace == 'tvdb.person')) if provider == 'tvdb' else or_(
        (CatalogueEntity.kind == 'person') & (CatalogueIdentity.namespace == 'tmdb.person'),
        (CatalogueEntity.kind == 'organization') & (CatalogueIdentity.namespace == 'tmdb.company'))
    query = select(CatalogueEntity, CatalogueIdentity).join(CatalogueIdentity,
        CatalogueIdentity.entity_id == CatalogueEntity.id).where(
        namespaces,
        exists(select(CatalogueCredit.id).join(CatalogueLegacyLink, CatalogueLegacyLink.entity_id == CatalogueCredit.work_id)
            .join(TrackedEntry, TrackedEntry.media_id == CatalogueLegacyLink.media_id)
            .where(CatalogueCredit.contributor_id == CatalogueEntity.id)),
        or_(attrs[version_key].astext.is_(None), attrs[version_key].astext != str(VERSION)),
        or_(attrs[attempted_key].astext.is_(None), attrs[attempted_key].astext < (now-timedelta(days=7)).isoformat()),
    ).order_by(CatalogueEntity.id).limit(limit)
    rows = list((await db.execute(query)).all()) if api_key else []
    result = {'examined':len(rows),'updated':0,'failed':0,'unavailable_fields':0}
    semaphore = asyncio.Semaphore(6)

    async def fetch(row, identity):
        async with semaphore:
            try:
                native = positive_id(identity.external_id)
                if not native:
                    raise ValueError('Invalid identity')
                async with asyncio.timeout(20):
                    raw = await tvdb.get_person(native, api_key) if provider == 'tvdb' else await (
                        tmdb.get_person_profile(native, api_key=api_key) if row.kind == 'person' else tmdb.get_company(native, api_key=api_key))
                if positive_id(raw.get('id')) != native:
                    raise ValueError('Provider identity mismatch')
                return tvdb_profile(raw) if provider == 'tvdb' else raw
            except Exception:
                return None

    replies = await asyncio.gather(*(fetch(row, identity) for row, identity in rows))
    await catalogue.write_lock(db)
    for (row, identity), raw in zip(rows, replies):
        await db.refresh(row, with_for_update=True)
        await db.refresh(identity)
        if identity.entity_id != row.id or (raw and positive_id(raw.get('id')) != positive_id(identity.external_id)):
            result['failed'] += 1
            continue
        row.attributes = {**(row.attributes or {}), attempted_key:now.isoformat()}
        if raw is None:
            result['failed'] += 1
            continue
        before = (row.description, row.image_url, dict(row.attributes))
        values = missing_values(row, raw)
        row.attributes = {key:value for key,value in row.attributes.items()
            if catalogue.has_value(value) or key not in values['attributes'] or 'attributes.'+key in (row.protected_fields or [])}
        catalogue.merge_fields(row, values, provider)
        if before != (row.description, row.image_url, row.attributes):
            result['updated'] += 1
        if not raw.get('biography') and not raw.get('description'):
            result['unavailable_fields'] += 1
        row.attributes = {**row.attributes, version_key:VERSION}
    await db.commit()
    return result
