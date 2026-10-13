"""Bounded quick-search discovery using canonical contributors and existing providers."""
import asyncio
from collections import defaultdict
from types import SimpleNamespace

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import aliased

from core import settings_store, tmdb
from core.contributor_details import is_actor, positive_id
from core.credit_roles import work_credit
from core.person_identities import canonical_people
from core.statistics_staff import role_label, role_order
from models.catalogue import CatalogueCredit, CatalogueEntity, CatalogueIdentity

KINDS = ('actor', 'staff', 'studio')
PATHS = {'actor': 'actors', 'staff': 'person', 'studio': 'studio'}
LIMIT = 8


def result(person, kind, roles=(), key=None):
    return {'key': key or f'catalogue:{person.id}', 'name': person.name,
            'image': person.image_url, 'roles': sorted(set(roles), key=lambda label: (role_order(label), label))[:2],
            'url': f'/{PATHS[kind]}/{key or f"catalogue:{person.id}"}'}


def credit_kind(person, credit):
    if person.kind == 'organization':
        return 'studio' if credit.role == 'producer' else None
    if not work_credit(role_label(credit), credit.character_label if is_actor(credit) else None):
        return None
    return 'actor' if is_actor(credit) else 'staff'


async def local_matches(db, term, kind):
    alias = aliased(CatalogueEntity)
    # Search old names too, but emit only the canonical identity.
    pattern = '%' + term.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
    aliases = select(alias.canonical_person_id).where(alias.name.ilike(pattern, escape='\\'), alias.canonical_person_id.is_not(None))
    entities = list(await db.scalars(select(CatalogueEntity).where(
        CatalogueEntity.kind.in_(('organization',) if kind == 'studio' else ('person',) if kind != 'all' else ('person', 'organization')),
        CatalogueEntity.canonical_person_id.is_(None),
        or_(CatalogueEntity.name.ilike(pattern, escape='\\'), CatalogueEntity.id.in_(aliases)),
    ).order_by(case((func.lower(CatalogueEntity.name) == term.casefold(), 0), else_=1),
               func.lower(CatalogueEntity.name), CatalogueEntity.id).limit(80)))
    groups = {k: [] for k in KINDS}
    if not entities:
        return groups
    contributor = aliased(CatalogueEntity)
    work = aliased(CatalogueEntity)
    # Collapse repeated film credits in SQL; quick search needs roles, not filmographies.
    rows = (await db.execute(select(func.coalesce(contributor.canonical_person_id, contributor.id), CatalogueCredit.role, CatalogueCredit.role_label).join(
        contributor, contributor.id == CatalogueCredit.contributor_id).join(work, work.id == CatalogueCredit.work_id).where(
        func.coalesce(contributor.canonical_person_id, contributor.id).in_([p.id for p in entities]),
        work.kind.in_(('movie', 'series')),
        ~func.coalesce(CatalogueCredit.character_label, '').op('~*')(r'\y(uncredited|archive footage|archive sound)\y'),
    ).distinct())).all()
    by_id = {p.id: p for p in entities}
    roles = defaultdict(set)
    for identity, role, label in rows:
        credit = SimpleNamespace(role=role, role_label=label, character_label=None)
        section = credit_kind(by_id[identity], credit)
        if section and (kind == 'all' or section == kind):
            roles[(identity, section)].add(role_label(credit))
    for person in entities:
        for section in KINDS:
            if (person.id, section) in roles:
                groups[section].append(result(person, section, roles[(person.id, section)]))
    return groups


async def search_contributors(db, viewer, term, kind='all'):
    groups = await local_matches(db, term, kind)
    key = await settings_store.get_user_tmdb_key(db, viewer.id)
    if not key:
        return {'results': {section: rows[:LIMIT] for section, rows in groups.items()}}

    async def remote(company=False):
        try:
            async with asyncio.timeout(8):
                response = await (tmdb.search_company(term, api_key=key) if company else tmdb.search_people(term, api_key=key))
            return response.get('results') or []
        except Exception:
            return None

    people, companies = await asyncio.gather(
        remote() if kind != 'studio' else asyncio.sleep(0, result=[]),
        remote(True) if kind in ('all', 'studio') else asyncio.sleep(0, result=[]))
    failed = people is None or companies is None
    sources = [('person', row) for row in (people or [])[:20]] + [('company', row) for row in (companies or [])[:20]]
    native_ids = {str(row['id']) for _, row in sources if positive_id(row.get('id'))}
    mappings = (await db.execute(select(CatalogueIdentity, CatalogueEntity).join(
        CatalogueEntity, CatalogueEntity.id == CatalogueIdentity.entity_id).where(
        CatalogueIdentity.namespace.in_(('tmdb.person', 'tmdb.company')), CatalogueIdentity.external_id.in_(native_ids)))).all() if native_ids else []
    roots = await canonical_people(db, [person for _, person in mappings])
    known = {(identity.namespace, identity.external_id): roots[person.id] for identity, person in mappings}
    studio_ids = {person.id for person in known.values() if person.kind == 'organization'}
    studio_pages = set(await db.scalars(select(CatalogueCredit.contributor_id).where(
        CatalogueCredit.contributor_id.in_(studio_ids), CatalogueCredit.role == 'producer').distinct())) if studio_ids else set()
    seen = {section: {row['key'] for row in rows} for section, rows in groups.items()}
    for provider_kind, raw in sources:
        native = positive_id(raw.get('id'))
        if not native or not raw.get('name') or raw.get('adult'):
            continue
        section = 'studio' if provider_kind == 'company' else 'actor' if raw.get('known_for_department') == 'Acting' else 'staff'
        if kind != 'all' and section != kind:
            continue
        person = known.get((f'tmdb.{provider_kind}', str(native)))
        identity = f'catalogue:{person.id}' if person else f'tmdb:{native}'
        if identity in seen[section]:
            continue
        # Cached organizations without production credits have no studio page.
        if person and section == 'studio' and person.id not in studio_pages:
            continue
        if person:
            item = result(person, section)
        else:
            image = raw.get('logo_path') if section == 'studio' else raw.get('profile_path')
            item = {'key': identity, 'name': raw['name'], 'image': f'https://image.tmdb.org/t/p/w185{image}' if image else None,
                    'roles': [] if section == 'studio' else [raw.get('known_for_department') or 'Crew'],
                    'url': f'/{PATHS[section]}/{identity}'}
        groups[section].append(item)
        seen[section].add(identity)
    return {'results': {section: rows[:LIMIT] for section, rows in groups.items()}, 'partial': failed}
