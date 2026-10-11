"""Read existing catalogue metadata for the shared media detail composition."""
import re
from urllib.parse import urlsplit
from sqlalchemy import or_, select

from core.catalogue_normalize import image
from core.media_images import provider_images, unique_images
from models.catalogue import (
    BookEdition, CatalogueCredit, CatalogueEntity, CatalogueIdentity,
    CatalogueLegacyLink, GameRelease, MetadataSnapshot, SteamPriceSnapshot,
)


def public_url(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = urlsplit(value)
        return value if parsed.scheme == 'https' and parsed.hostname and not parsed.username else None
    except ValueError:
        return None


async def screen_work(db, media):
    work = await db.scalar(select(CatalogueEntity).join(CatalogueLegacyLink,
        CatalogueLegacyLink.entity_id == CatalogueEntity.id).where(CatalogueLegacyLink.media_id == media.id))
    if work:
        return work
    namespaces = []
    for provider in ('tmdb', 'tvdb'):
        native = getattr(media, provider + '_id', None)
        if native:
            namespaces.append((CatalogueIdentity.namespace == f'{provider}.{media.media_type.value}') &
                (CatalogueIdentity.external_id == str(native)))
    if not namespaces:
        return None
    return await db.scalar(select(CatalogueEntity).join(CatalogueIdentity).where(
        CatalogueEntity.kind == media.media_type.value, or_(*namespaces)).order_by(CatalogueEntity.id).limit(1))


async def work_details(db, work):
    if work is None:
        return {'people': [], 'organizations': [], 'posters': [], 'artwork': [], 'relations': [], 'similar': [], 'sources': []}
    credits = (await db.execute(select(CatalogueCredit, CatalogueEntity).join(CatalogueEntity,
        CatalogueEntity.id == CatalogueCredit.contributor_id).where(CatalogueCredit.work_id == work.id)
        .order_by(CatalogueCredit.position.is_(None), CatalogueCredit.position, CatalogueCredit.id).limit(500))).all()
    people = {}
    organizations = {}
    billing = {}
    studio_ids = {contributor.id for credit, contributor in credits if credit.role == 'producer'}
    for credit, contributor in credits:
        actor = credit.role == 'actor'
        kind = 'studio' if contributor.kind == 'organization' else 'actor' if actor else 'staff'
        group = organizations if kind == 'studio' else people
        key = (contributor.id, kind)
        if key not in group:
            path = 'studio' if kind == 'studio' else 'actors' if actor else 'person'
            group[key] = {'id': contributor.id, 'kind': kind, 'name': contributor.name,
                'image': image(contributor.image_url), 'url': f'/{path}/catalogue:{contributor.id}'
                if kind != 'studio' or (work.kind in ('movie', 'series') and contributor.id in studio_ids) else None,
                'roles': [], 'role_codes': [], 'position': credit.position, 'characters': []}
        row = group[key]
        if credit.position is not None:
            candidate = (0 if credit.provider == 'tmdb' else 1, credit.position)
            if key not in billing or candidate < billing[key]:
                billing[key] = candidate
                row['position'] = credit.position
        if credit.role not in row['role_codes']:
            row['role_codes'].append(credit.role)
        role = credit.role_label or credit.role.replace('_', ' ').title()
        if role not in row['roles']:
            row['roles'].append(role)
        if credit.character_label and credit.character_label not in row['characters']:
            row['characters'].append(credit.character_label)
    attrs = work.attributes or {}
    artwork = [url for value in attrs.get('artwork') or [] if (url := image(value))]
    posters = [work.image_url, *(attrs.get('posters') or [])]
    snapshots = list(await db.scalars(select(MetadataSnapshot).where(MetadataSnapshot.entity_id == work.id)
        .order_by(MetadataSnapshot.fetched_at.desc().nullslast()).limit(6)))
    sources = []
    for snapshot in snapshots:
        raw = snapshot.payload or {}
        images = raw.get('images') or {}
        variants = provider_images(images, snapshot.provider)
        posters.extend(variants['posters'])
        artwork.extend(variants['artwork'])
        if url := public_url(raw.get('homepage') or raw.get('url')):
            sources.append({'name': snapshot.provider.upper(), 'url': url})
    from core.media_related import catalogue_related
    related = await catalogue_related(db, work)
    return {'catalogue_id': work.id, 'people': list(people.values()), 'organizations': list(organizations.values()),
        'posters': unique_images(posters), 'artwork': unique_images(artwork), **related,
        'sources': list({source['url']: source for source in sources}.values()), 'countries': attrs.get('countries') or []}


async def catalogue_title(db, work):
    details = await work_details(db, work)
    attrs = work.attributes or {}
    editions = []
    releases = []
    prices = []
    if work.kind == 'book':
        rows = (await db.execute(select(BookEdition, CatalogueEntity).join(CatalogueEntity,
            CatalogueEntity.id == BookEdition.entity_id).where(BookEdition.work_id == work.id)
            .order_by(BookEdition.release_date.desc().nullslast(), BookEdition.entity_id).limit(60))).all()
        editions = [{'id': edition.entity_id, 'name': entity.name, 'image': image(entity.image_url),
            'release_date': edition.release_date, 'language': edition.language, 'format': edition.format,
            'pages': edition.pages} for edition, entity in rows]
        details['posters'] = unique_images([*details['posters'], *(row['image'] for row in editions)])
    if work.kind == 'game':
        releases = [{'platform': release.platform_name, 'region': release.region, 'release_date': release.release_date}
            for release in await db.scalars(select(GameRelease).where(GameRelease.work_id == work.id)
                .order_by(GameRelease.release_date.desc().nullslast(), GameRelease.entity_id).limit(100))]
        prices = [{'steam_app_id': row.steam_app_id, 'country': row.country, 'current': row.current,
            'historical_low': row.historical_low, 'historical_low_at': row.historical_low_at,
            'url': public_url(row.url), 'fetched_at': row.fetched_at}
            for row in await db.scalars(select(SteamPriceSnapshot).where(SteamPriceSnapshot.work_id == work.id)
                .order_by(SteamPriceSnapshot.fetched_at.desc()).limit(8))]
    identities = list(await db.scalars(select(CatalogueIdentity).where(CatalogueIdentity.entity_id == work.id)))
    for identity in identities:
        native = identity.external_id
        if identity.namespace == 'openlibrary.book' and re.fullmatch(r'OL[1-9][0-9]*W', native):
            details['sources'].append({'name': 'Open Library', 'url': f'https://openlibrary.org/works/{native}'})
        elif identity.namespace == 'steam.app' and native.isdecimal():
            details['sources'].append({'name': 'Steam', 'url': f'https://store.steampowered.com/app/{native}/'})
    return {**details, 'id': work.id, 'type': work.kind, 'title': work.name, 'overview': work.description,
        'poster': image(work.image_url), 'backdrop': next(iter(details['artwork']), None),
        'release_date': attrs.get('release_date'), 'year': str(attrs.get('release_date') or '')[:4],
        'genres': [value if isinstance(value, str) else value.get('name') for value in attrs.get('genres') or []
            if isinstance(value, str) or (isinstance(value, dict) and value.get('name'))],
        'countries': attrs.get('countries') or [], 'pages': attrs.get('pages'),
        'editions': editions, 'releases': releases, 'prices': prices, 'entry': None, 'tracking_available': False,
        'friends': [], 'friends_average': None, 'seasons': []}
