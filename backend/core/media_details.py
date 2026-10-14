"""Read existing catalogue metadata for the shared media detail composition."""
import re
from urllib.parse import urlsplit
from sqlalchemy import or_, select
from sqlalchemy.orm import load_only

from core.catalogue_normalize import image
from core.media_images import provider_images, unique_images
from core.screen_characters import character_images, role_image_candidates
from core.studio_names import studio_name
from core.principal_credits import principal_clause, select_credits, evidence, positive, label
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


async def work_details(db, work, *, extras=True):
    if work is None:
        return {'people': [], 'organizations': [], 'posters': [], 'artwork': [], 'relations': [], 'similar': [], 'sources': []}
    credits = (await db.execute(select(CatalogueCredit, CatalogueEntity).join(CatalogueEntity,
        CatalogueEntity.id == CatalogueCredit.contributor_id).where(CatalogueCredit.work_id == work.id,
            or_(CatalogueEntity.kind == 'organization', principal_clause(CatalogueCredit, work.kind))
            if work.kind in ('movie', 'series') else True)
        .options(load_only(CatalogueCredit.id, CatalogueCredit.work_id, CatalogueCredit.contributor_id,
            CatalogueCredit.provider, CatalogueCredit.role, CatalogueCredit.role_label, CatalogueCredit.position,
            CatalogueCredit.character_label, CatalogueCredit.character_image_url, CatalogueCredit.screen_character_id,
            CatalogueCredit.attributes),
            load_only(CatalogueEntity.id, CatalogueEntity.kind, CatalogueEntity.name, CatalogueEntity.image_url,
                CatalogueEntity.canonical_person_id, CatalogueEntity.canonical_company_id))
        .order_by(CatalogueCredit.position.is_(None), CatalogueCredit.position, CatalogueCredit.id))).all()
    people = {}
    organizations = {}
    billing = {}
    local_images = {}
    alternative_images = {}
    from core.person_identities import canonical_people
    canonical = await canonical_people(db, [person for _, person in credits])
    studio_ids = {canonical[person.id].id for c, person in credits if c.role == 'producer'}
    from core.screen_cast import complete_cast_works, preferred_cast
    if work.kind in ('movie', 'series'):
        credits = preferred_cast([(credit, canonical[person.id]) for credit, person in credits],
                                 await complete_cast_works(db, {work.id}))
        credits = select_credits(credits, work.kind)
    images = await character_images(db, {credit.screen_character_id for credit, _ in credits
        if credit.role == 'actor' and credit.screen_character_id})
    for credit, contributor in credits:
        contributor = canonical.get(contributor.id, contributor)
        actor = credit.role == 'actor'
        kind = 'studio' if contributor.kind == 'organization' else 'actor' if actor else 'staff'
        group = organizations if kind == 'studio' else people
        key = (contributor.id, kind)
        if key not in group:
            path = 'studio' if kind == 'studio' else 'actors' if actor else 'person'
            group[key] = {'id': contributor.id, 'kind': kind, 'name': studio_name(contributor.name) if kind == 'studio' else contributor.name,
                'image': image(contributor.image_url), 'url': f'/{path}/catalogue:{contributor.id}'
                if kind != 'studio' or (work.kind in ('movie', 'series') and contributor.id in studio_ids) else None,
                'roles': [], 'role_codes': [], 'position': credit.position,
                'credit_order': len(group), 'characters': []}
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
        # Each job may overlap the same episodes. Do not sum role counts or
        # infer episode numbers/season ranges from an aggregate count.
        count = positive(evidence(credit).get('episode_count'))
        if count:
            row['episode_count'] = max(row.get('episode_count', 0), count)
            if label(credit) in ('producer', 'executive producer'):
                row['producer_episode_count'] = max(row.get('producer_episode_count', 0), count)
        if credit.character_label and credit.character_label not in row['characters']:
            row['characters'].append(credit.character_label)
        if actor:
            local_images.setdefault(key, []).extend([url] if (url := image(credit.character_image_url)) else [])
            alternative_images.setdefault(key, []).extend(role_image_candidates(credit, images.get(credit.screen_character_id, [])))
    for key, row in people.items():
        if row['kind'] == 'actor':
            row['character_images'] = list(dict.fromkeys(local_images.get(key, []) + alternative_images.get(key, [])))[:4]
            row['character_image'] = next(iter(row['character_images']), None)
        elif work.kind == 'series' and row.get('episode_count'):
            total = positive((work.attributes or {}).get('episode_count'))
            if total:
                row['episode_total'] = total
    if not extras:
        return {'catalogue_id':work.id, 'people':list(people.values()), 'organizations':list(organizations.values())}
    attrs = work.attributes or {}
    artwork = [url for value in attrs.get('artwork') or [] if (url := image(value))]
    posters = [work.image_url, *(attrs.get('posters') or [])]
    if work.kind in ('movie', 'series'):
        # Untagged legacy URLs cannot establish the requested image language.
        artwork, posters = [], []
    # Details need image variants and public source links, not the provider's
    # complete credits/episode response. Keep large snapshot payloads in SQL.
    snapshots = (await db.execute(select(MetadataSnapshot.provider,
        MetadataSnapshot.payload['images'].label('images'),
        MetadataSnapshot.payload['homepage'].astext.label('homepage'),
        MetadataSnapshot.payload['url'].astext.label('url')).where(MetadataSnapshot.entity_id == work.id)
        .order_by(MetadataSnapshot.fetched_at.desc().nullslast()).limit(6))).all()
    sources = []
    for snapshot in snapshots:
        images = snapshot.images or {}
        variants = provider_images(images, snapshot.provider) if snapshot.provider == 'tmdb' or work.kind not in ('movie', 'series') else {'posters': [], 'artwork': []}
        posters.extend(variants['posters'])
        artwork.extend(variants['artwork'])
        homepage = public_url(snapshot.homepage)
        if url := homepage or public_url(snapshot.url):
            sources.append({'name': 'Official website' if homepage else snapshot.provider.upper(), 'url': url})
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
