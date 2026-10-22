"""Read existing catalogue metadata for the shared media detail composition."""
import re
from fastapi import HTTPException
from urllib.parse import urlsplit
from sqlalchemy import or_, select
from sqlalchemy.orm import load_only

from core.catalogue_normalize import image
from core.media_images import provider_images, unique_images
from core.screen_characters import character_images, role_image_candidates
from core.studio_names import studio_name
from core.movie_financials import movie_financials
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
    if work.kind=='game':
        from core.game_credit_projection import displayed_credits
        credits=await displayed_credits(db,work.id)
    else:
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
    game_performances,game_characters={},[]
    if work.kind=='game':
        from core.game_credit_projection import character_projection
        game_performances,game_characters=await character_projection(db,work.id,[(credit,canonical[person.id]) for credit,person in credits])
    studio_ids = {canonical[person.id].id for c, person in credits if c.role == 'producer'}
    from core.screen_cast import complete_cast_works, preferred_cast
    if work.kind in ('movie', 'series'):
        credits = preferred_cast([(credit, canonical[person.id]) for credit, person in credits],
                                 await complete_cast_works(db, {work.id}))
        credits = select_credits(credits, work.kind)
    image_roles, art_counts = [], {}
    for credit, person in credits:
        if work.kind in ('movie','series') and credit.role == 'actor' and art_counts.get(person.id, 0) < 8:
            image_roles.append(credit)
            art_counts[person.id] = art_counts.get(person.id, 0) + 1
    images = await character_images(db, {credit.screen_character_id for credit in image_roles
        if credit.screen_character_id})
    image_role_ids = {credit.id for credit in image_roles}
    for credit, contributor in credits:
        contributor = canonical.get(contributor.id, contributor)
        actor = credit.role == 'actor' or work.kind=='game' and credit.role in ('voice_actor','narrator')
        kind = 'studio' if contributor.kind == 'organization' else 'actor' if actor else 'staff'
        group = organizations if kind == 'studio' else people
        key = (contributor.id, kind)
        if key not in group:
            path = 'studio' if kind == 'studio' else 'actors' if actor else 'person'
            group[key] = {'id': contributor.id, 'kind': kind, 'name': studio_name(contributor.name) if kind == 'studio' else contributor.name,
                'image': image(contributor.image_url), 'url': f'/{path}/catalogue:{contributor.id}' + ('?media_type=game' if work.kind=='game' else '')
                if kind != 'studio' or work.kind=='game' and credit.role in ('developer','publisher','supporting','porting') or (work.kind in ('movie', 'series') and contributor.id in studio_ids) else None,
                'roles': [], 'role_codes': [], 'position': credit.position,
                'credit_order': len(group), 'characters': []}
        row = group[key]
        if credit.position is not None:
            candidate = (0 if credit.provider == 'tmdb' else 1, credit.position)
            if key not in billing or candidate < billing[key]:
                billing[key] = candidate
                row['position'] = credit.position
        if len(row['role_codes']) < 8 and credit.role not in row['role_codes']:
            row['role_codes'].append(credit.role)
        role = credit.role_label or credit.role.replace('_', ' ').title()
        if len(row['roles']) < 8 and role not in row['roles']:
            row['roles'].append(role)
        # Each job may overlap the same episodes. Do not sum role counts or
        # infer episode numbers/season ranges from an aggregate count.
        count = positive(evidence(credit).get('episode_count'))
        if count:
            row['episode_count'] = max(row.get('episode_count', 0), count)
            if label(credit) in ('producer', 'executive producer'):
                row['producer_episode_count'] = max(row.get('producer_episode_count', 0), count)
        if work.kind=='game' and actor:
            performances=game_performances.get(credit.id,[])
            role=(credit.attributes or {}).get('game_role') or {}
            names=[value['name'] for value in performances]+role.get('text_characters',[]) if performances else role.get('characters') or ([credit.character_label] if credit.character_label else [])
            for name in names:
                if name and name not in row['characters']:row['characters'].append(name)
            row.setdefault('performances',[]).extend(performances)
            for lang in (credit.attributes or {}).get('languages') or ['']:
                if lang not in row.setdefault('languages',[]):row['languages'].append(lang)
            if credit.provider=='imdb':
                row.setdefault('credit_performances',[]).append({'source':'imdb','position':credit.position,
                    'characters':names,'languages':(credit.attributes or {}).get('languages') or [],
                    'attributes':role.get('performance_attributes'),'voice':role.get('voice'),
                    'credited_as':role.get('credited_as'),'uncredited':role.get('uncredited')})
            local_images.setdefault(key,[]).extend(value['image'] for value in performances if value['image'])
        elif work.kind=='game':
            jobs=((credit.attributes or {}).get('game_role') or {}).get('jobs') or []
            row.setdefault('jobs',[]).extend(job for job in jobs if job not in row.get('jobs',[]))
        elif credit.character_label and len(row['characters']) < 8 and credit.character_label not in row['characters']:
            row['characters'].append(credit.character_label)
        if actor and credit.id in image_role_ids:
            local_images.setdefault(key, []).extend([url] if (url := image(credit.character_image_url)) else [])
            alternative_images.setdefault(key, []).extend(role_image_candidates(credit, images.get(credit.screen_character_id, [])))
    for key, row in people.items():
        if row['kind'] == 'actor':
            row['character_images'] = list(dict.fromkeys(local_images.get(key, []) + alternative_images.get(key, [])))[:4]
            row['character_image'] = next(iter(row['character_images']), None)
        elif row['kind'] == 'staff' and work.kind == 'series' and row.get('episode_count'):
            total = positive((work.attributes or {}).get('episode_count'))
            if total:
                row['episode_total'] = total
    if not extras:
        return {'catalogue_id':work.id, 'people':list(people.values()), 'organizations':list(organizations.values()),
            **({'game_characters':game_characters} if work.kind=='game' else {})}
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
        MetadataSnapshot.payload['budget'].label('budget'),
        MetadataSnapshot.payload['revenue'].label('revenue'),
        MetadataSnapshot.payload['url'].astext.label('url')).where(MetadataSnapshot.entity_id == work.id,
            MetadataSnapshot.kind.not_like('game_credit%'),MetadataSnapshot.kind!='game_binding_review',
            # Auxiliary metadata/job components must not consume the six
            # image/source slots and evict usable provider artwork or links.
            or_(*(MetadataSnapshot.payload[field].is_not(None) for field in ('images','homepage','url','budget','revenue'))))
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
        **({'game_characters':game_characters} if work.kind=='game' else {}),
        'posters': unique_images(posters), 'artwork': unique_images(artwork), **related,
        **(movie_financials(attrs, *({'budget': row.budget, 'revenue': row.revenue}
            for row in snapshots if row.provider == 'tmdb')) if work.kind == 'movie' else {}),
        'sources': list({source['url']: source for source in sources}.values()), 'countries': attrs.get('countries') or []}


async def catalogue_title(db, work, viewer_id=None):
    details = await work_details(db, work)
    attrs = work.attributes or {}
    editions = []
    releases = []
    prices = []
    personal = {}
    if work.kind == 'game':
        from core.game_types import capabilities
        from core import game_tracking
        caps = capabilities(work.attributes)
        state = (await game_tracking.states(db, viewer_id, [work.id]))[0] if viewer_id else None
        base = None
        if caps.playable_child:
            try:
                base_id = await game_tracking.base_work_id(db, work)
                base = await db.get(CatalogueEntity, base_id)
                if state:
                    state.base_work_id = base_id
            except HTTPException:
                pass  # Preserve metadata pages while an uncertain base is resolved.
        personal = {'subject': {'kind': 'game', 'catalogue_id': work.id}, 'game_class': caps.game_class,
            'base_work_id': base.id if base else None,
            'base_game': {'id': base.id, 'title': base.name, 'image_url': image(base.image_url)} if base else None,
            'game_state': state.model_dump(mode='json') if state else None,
            'completion_estimates':attrs.get('completion_estimates'),
            'entry': state.model_dump(mode='json') if state and state.listed else None,
            'favorite': bool(state and state.favorite),
            'tracking_available': caps.independent or (caps.expansion_page and base is not None)}
    if work.kind == 'book':
        rows = (await db.execute(select(BookEdition, CatalogueEntity).join(CatalogueEntity,
            CatalogueEntity.id == BookEdition.entity_id).where(BookEdition.work_id == work.id)
            .order_by(BookEdition.release_date.desc().nullslast(), BookEdition.entity_id).limit(60))).all()
        editions = [{'id': edition.entity_id, 'name': entity.name, 'image': image(entity.image_url),
            'release_date': edition.release_date, 'language': edition.language, 'format': edition.format,
            'pages': edition.pages} for edition, entity in rows]
        details['posters'] = unique_images([*details['posters'], *(row['image'] for row in editions)])
    if work.kind == 'game':
        release_rows = await db.scalars(select(GameRelease).where(GameRelease.work_id == work.id,
            GameRelease.platform_namespace == 'igdb.platform')
            .order_by(GameRelease.release_date.asc().nullslast(), GameRelease.entity_id))
        seen_platforms = set()
        for release in release_rows:
            platform = 'PC' if release.platform_id in {'3','6','14'} else release.platform_name
            if not platform or platform in seen_platforms:
                continue
            seen_platforms.add(platform)
            releases.append({'platform': platform, 'region': release.region, 'release_date': release.release_date})
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
        'platforms': list(dict.fromkeys('PC' if row.get('group')=='PC' or str(row.get('id')) in {'3','6','14'}
            else row.get('name') for row in attrs.get('platforms') or [] if isinstance(row,dict) and row.get('name'))) if work.kind=='game' else [],
        'editions': editions, 'releases': releases, 'prices': prices, 'entry': None, 'tracking_available': False,
        'friends': [], 'friends_average': None, 'seasons': [], **personal}
