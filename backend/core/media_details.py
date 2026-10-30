"""Read existing catalogue metadata for the shared media detail composition."""
from core.game_release_dates import VERSION as GAME_RELEASE_VERSION
from core.game_credit_roles import ACTING as GAME_ACTING
import re
from fastapi import HTTPException
from urllib.parse import urlsplit
from sqlalchemy import or_, select
from sqlalchemy.orm import load_only

from core.catalogue_normalize import image,GAME_ARTWORK_VERSION
from core.media_images import provider_images, unique_images
from core.screen_characters import character_images, role_image_candidates
from core.studio_names import studio_name
from core.movie_financials import movie_financials
from core.principal_credits import principal_clause, select_credits, evidence, positive, label
from models.catalogue import (
    BookEdition, CatalogueCredit, CatalogueEntity, CatalogueIdentity,
    CatalogueLegacyLink, GameRelease, MetadataSnapshot,
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
    if work.kind=='game':
        from core.game_principal_credits import select_credits as select_game_credits
        selected=select_game_credits([{'id':canonical[person.id].id,'kind':person.kind,'role':credit.role,
            'role_label':credit.role_label,'position':credit.position,'character_label':credit.character_label,
            'attributes':credit.attributes,'source':(credit,person)} for credit,person in credits])
        credits=[row['source'] for row in selected]
    game_performances,game_characters={},[]
    game_character_status=None
    if work.kind=='game':
        from core.game_credit_projection import character_projection
        game_performances,game_characters=await character_projection(db,work.id,[(credit,canonical[person.id]) for credit,person in credits])
        character_header=(await db.execute(select(MetadataSnapshot.coverage['characters_complete'].as_boolean(),
            MetadataSnapshot.fetched_at,MetadataSnapshot.lease_until,MetadataSnapshot.next_attempt_at,MetadataSnapshot.error_code).where(MetadataSnapshot.entity_id==work.id,
                MetadataSnapshot.provider=='igdb',MetadataSnapshot.kind=='game')
            .order_by(MetadataSnapshot.fetched_at.desc().nullslast(),MetadataSnapshot.id.desc()).limit(1))).one_or_none()
        game_character_status={'complete':bool(character_header and character_header[0]),'source':'igdb',
            'coverage':'retrieved_connection','fetched_at':character_header[1] if character_header else None}
        if character_header:
            from core.game_imdb_enrichment import now
            active=bool(character_header[2] and character_header[2]>now())
            game_character_status.update(pending=bool(active or character_header[3]),
                complete=game_character_status['complete'] and not active and not character_header[3] and not character_header[4],
                retry_after=max(2,min(3600,int((character_header[3]-now()).total_seconds())+1)) if character_header[3] else 2,
                error=character_header[4])
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
        actor = credit.role == 'actor' or work.kind=='game' and credit.role in GAME_ACTING
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
        if (work.kind=='game' or len(row['role_codes']) < 8) and credit.role not in row['role_codes']:
            row['role_codes'].append(credit.role)
        role = credit.role_label or credit.role.replace('_', ' ').title()
        if (work.kind=='game' or len(row['roles']) < 8) and role not in row['roles']:
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
            # Keep language/qualifiers attached to their own performance. An
            # actor's aggregate language union cannot assign a character to a
            # particular version, especially across several source providers.
            row.setdefault('credit_performances',[]).append({'source':credit.provider,'position':credit.position,
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
            **({'game_characters':game_characters,'game_character_status':game_character_status} if work.kind=='game' else {})}
    attrs = work.attributes or {}
    artwork = [url for value in [*(attrs.get('artwork') or []),
        *((attrs.get('scene_artwork') or []) if work.kind=='game' else [])] if (url := image(value))]
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
        **({'game_characters':game_characters,'game_character_status':game_character_status} if work.kind=='game' else {}),
        'posters': unique_images(posters), 'artwork': unique_images(artwork,limit=None if work.kind=='game' else 10), **related,
        **(movie_financials(attrs, *({'budget': row.budget, 'revenue': row.revenue}
            for row in snapshots if row.provider == 'tmdb')) if work.kind == 'movie' else {}),
        'sources': list({source['url']: source for source in sources}.values()), 'countries': attrs.get('countries') or []}


def primary_details(work,artwork=None):
    """Canonical primary presentation only: no credits, jobs or viewer state."""
    attrs=work.attributes or {}
    gallery=artwork if artwork is not None else unique_images(
        [url for value in attrs.get('artwork') or [] if (url:=image(value))],limit=None if work.kind=='game' else 10)
    scenes=[url for value in attrs.get('scene_artwork') or [] if (url:=image(value))]
    selected=image(attrs.get('backdrop_path')) or (next(iter(gallery),None) if 'attributes.artwork' in (work.protected_fields or []) else None)
    backdrop=selected or next(iter(scenes if work.kind=='game' and attrs.get('artwork_policy_version') in (1,2,GAME_ARTWORK_VERSION) else gallery),None)
    preview=next((url for url in scenes if url!=backdrop),backdrop) if work.kind=='game' else None
    released=attrs.get('release_date')
    if (work.kind=='game' and attrs.get('release_date_projection')!=GAME_RELEASE_VERSION
        and (work.field_sources or {}).get('attributes.release_date')=='igdb'
        and 'attributes.release_date' not in (work.protected_fields or [])):
        released=released[:4] if isinstance(released,str) else None
    poster=image(work.image_url)
    return {'id':work.id,'type':work.kind,'title':work.name,'overview':work.description,
        'poster':poster,'posters':[poster] if poster else [],'artwork':gallery,'backdrop':backdrop,
        **({'artwork_preview':{'image':preview},'backdrop_fallbacks':scenes} if work.kind=='game' else {}),
        'release_date':released,'year':str(released or '')[:4],
        'genres':[value if isinstance(value,str) else value.get('name') for value in attrs.get('genres') or []
            if isinstance(value,str) or isinstance(value,dict) and value.get('name')],
        'platforms':list(dict.fromkeys('PC' if row.get('group')=='PC' or str(row.get('id')) in {'3','6','14'}
            else row.get('name') for row in attrs.get('platforms') or [] if isinstance(row,dict) and row.get('name'))) if work.kind=='game' else []}


async def catalogue_title(db, work, viewer_id=None, *, store_country=None, enqueue_offers=False,enqueue_ratings=False,enqueue_info=False,identities=None):
    details = await work_details(db, work)
    attrs = work.attributes or {}
    legacy_game_date = (work.kind=='game' and attrs.get('release_date_projection')!=GAME_RELEASE_VERSION
        and (work.field_sources or {}).get('attributes.release_date')=='igdb')
    if legacy_game_date and 'attributes.release_date' not in (work.protected_fields or []):
        # Old IGDB timestamps did not retain precision. Keep their known year
        # visible while the on-demand metadata upgrade verifies month/day.
        value=attrs.get('release_date')
        attrs={**attrs,'release_date':value[:4] if isinstance(value,str) else None}
    editions = []
    releases = []
    prices = []
    personal = {}
    if identities is None:identities = list(await db.scalars(select(CatalogueIdentity).where(CatalogueIdentity.entity_id == work.id)))
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
        from core.game_ratings import view as ratings_view
        ratings=await ratings_view(db,work,identities=identities,enqueue=enqueue_ratings)
        personal['game_ratings']=ratings['ratings'];personal['game_rating_status']=ratings['status']
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
            value=release.release_date
            releases.append({'platform': platform, 'region': release.region,
                'release_date':value[:4] if legacy_game_date and isinstance(value,str) else value})
        from core.game_offers import view as offers_view
        offers=await offers_view(db,work,viewer_id,hint=store_country,identities=identities,enqueue=enqueue_offers)
        prices=offers['offers'];personal['game_offer_context']={key:value for key,value in offers.items() if key!='offers'}
        from core.game_info import view as info_view
        info=await info_view(db,work,identities=identities,enqueue=enqueue_info)
        personal['game_info']=info['info'];personal['game_info_status']=info['status']
    for identity in identities:
        native = identity.external_id
        if identity.namespace == 'openlibrary.book' and re.fullmatch(r'OL[1-9][0-9]*W', native):
            details['sources'].append({'name': 'Open Library', 'url': f'https://openlibrary.org/works/{native}'})
        elif identity.namespace == 'steam.app' and native.isdecimal():
            details['sources'].append({'name': 'Steam', 'url': f'https://store.steampowered.com/app/{native}/'})
    primary=primary_details(work,details['artwork'])
    return {**details, 'id': work.id, 'type': work.kind, 'title': work.name, 'overview': work.description,
        'poster':primary['poster'],'backdrop':primary['backdrop'],
        **({key:primary[key] for key in ('artwork_preview','backdrop_fallbacks')} if work.kind=='game' else {}),
        'release_date': attrs.get('release_date'), 'year': str(attrs.get('release_date') or '')[:4],
        'genres': [value if isinstance(value, str) else value.get('name') for value in attrs.get('genres') or []
            if isinstance(value, str) or (isinstance(value, dict) and value.get('name'))],
        'countries': attrs.get('countries') or [], 'pages': attrs.get('pages'),
        'platforms': list(dict.fromkeys('PC' if row.get('group')=='PC' or str(row.get('id')) in {'3','6','14'}
            else row.get('name') for row in attrs.get('platforms') or [] if isinstance(row,dict) and row.get('name'))) if work.kind=='game' else [],
        'editions': editions, 'releases': releases, 'prices': prices, 'entry': None, 'tracking_available': False,
        'friends': [], 'friends_average': None, 'seasons': [], **personal}
