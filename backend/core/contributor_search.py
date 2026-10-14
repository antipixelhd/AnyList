"""Bounded quick-search discovery using canonical contributors and existing providers."""
import asyncio
from collections import defaultdict
from types import SimpleNamespace

from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import aliased

from core import settings_store, tmdb
from core.contributor_details import is_actor, positive_id
from core.credit_roles import work_credit
from core.person_identities import canonical_people
from core.principal_credits import principal_clause, remote_person_kind, number, group
from core.statistics_staff import role_label, role_order
from core.studio_names import studio_name
from models.catalogue import CatalogueCredit, CatalogueEntity, CatalogueIdentity

KINDS = ('actor', 'staff', 'studio')
PATHS = {'actor': 'actors', 'staff': 'person', 'studio': 'studio'}
LIMIT = 8


def result(person, kind, roles=(), key=None):
    return {'key': key or f'catalogue:{person.id}', 'name': studio_name(person.name) if kind == 'studio' else person.name,
            'image': person.image_url, 'roles': sorted(set(roles), key=lambda label: (role_order(label), label))[:2],
            'url': f'/{PATHS[kind]}/{key or f"catalogue:{person.id}"}'}


def credit_kind(person, credit):
    if person.kind == 'organization':
        return 'studio' if credit.role == 'producer' else None
    if not work_credit(role_label(credit), credit.character_label if is_actor(credit) else None):
        return None
    if not is_actor(credit) and group(credit) is None:
        return None
    return 'actor' if is_actor(credit) else 'staff'


async def local_matches(db, term, kind):
    alias = aliased(CatalogueEntity)
    # Search old names too, but emit only the canonical identity.
    pattern = '%' + term.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
    aliases = select(func.coalesce(alias.canonical_person_id, alias.canonical_company_id)).where(
        alias.name.ilike(pattern, escape='\\'),
        or_(alias.canonical_person_id.is_not(None), alias.canonical_company_id.is_not(None)))
    contributor = aliased(CatalogueEntity)
    work = aliased(CatalogueEntity)
    qualified = or_(and_(CatalogueEntity.kind == 'organization', CatalogueCredit.role == 'producer'),
                    and_(CatalogueEntity.kind == 'person', principal_clause(CatalogueCredit, work.kind)))
    matches = (await db.execute(select(CatalogueEntity,
        func.count(func.distinct(CatalogueCredit.work_id)).label('work_count')).join(
        contributor, func.coalesce(contributor.canonical_person_id, contributor.canonical_company_id, contributor.id) == CatalogueEntity.id).join(
        CatalogueCredit, CatalogueCredit.contributor_id == contributor.id).join(work, work.id == CatalogueCredit.work_id).where(
        CatalogueEntity.kind.in_(('organization',) if kind == 'studio' else ('person',) if kind != 'all' else ('person', 'organization')),
        CatalogueEntity.canonical_person_id.is_(None),
        CatalogueEntity.canonical_company_id.is_(None),
        or_(CatalogueEntity.name.ilike(pattern, escape='\\'), CatalogueEntity.id.in_(aliases),
            CatalogueEntity.attributes['also_known_as'].astext.ilike(pattern, escape='\\')),
        work.kind.in_(('movie', 'series')), qualified,
        (CatalogueCredit.role == 'actor') if kind == 'actor' else
        (CatalogueCredit.role != 'actor') if kind == 'staff' else True,
    ).group_by(CatalogueEntity.id).order_by(
        case((func.lower(CatalogueEntity.name) == term.casefold(), 0), else_=1),
        func.count(func.distinct(CatalogueCredit.work_id)).desc(),
        func.lower(CatalogueEntity.name), CatalogueEntity.id).limit(80))).all()
    entities = [person for person, _ in matches]
    work_counts = {person.id: count for person, count in matches}
    groups = {k: [] for k in KINDS}
    if not entities:
        return groups
    contributor = aliased(CatalogueEntity)
    work = aliased(CatalogueEntity)
    # Collapse repeated film credits in SQL; quick search needs roles, not filmographies.
    rows = (await db.execute(select(func.coalesce(contributor.canonical_person_id, contributor.canonical_company_id, contributor.id), CatalogueCredit.role, CatalogueCredit.role_label).join(
        contributor, contributor.id == CatalogueCredit.contributor_id).join(work, work.id == CatalogueCredit.work_id).where(
        func.coalesce(contributor.canonical_person_id, contributor.canonical_company_id, contributor.id).in_([p.id for p in entities]),
        work.kind.in_(('movie', 'series')),
        or_(and_(contributor.kind == 'organization', CatalogueCredit.role == 'producer'),
            and_(contributor.kind == 'person', principal_clause(CatalogueCredit, work.kind))),
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
                groups[section].append({**result(person, section, roles[(person.id, section)]),
                                       '_works': work_counts[person.id]})
    return groups


def ranked_results(groups, term):
    query = term.casefold()
    def rank(item, section):
        name = item.get('name', '').casefold()
        match = 0 if name == query else 1 if name.startswith(query) else 2
        recognition = number(item.get('_popularity'))
        works = item.get('_works', 0)
        return (match, -works if section == 'studio' else -recognition,
                -recognition if section == 'studio' else -works,
                not bool(item.get('image')), name, item['key'])
    return {section: [{key: value for key, value in item.items() if not key.startswith('_')}
                     for item in sorted(rows, key=lambda item: rank(item, section))[:LIMIT]]
            for section, rows in groups.items()}


async def known_principal_status(db, ids):
    if not ids:
        return {}
    person = aliased(CatalogueEntity)
    work = aliased(CatalogueEntity)
    canonical = func.coalesce(person.canonical_person_id, person.id)
    return dict((await db.execute(select(canonical,
        func.bool_or(principal_clause(CatalogueCredit, work.kind))).join(
        person, person.id == CatalogueCredit.contributor_id).join(work, work.id == CatalogueCredit.work_id)
        .where(canonical.in_(ids), work.kind.in_(('movie', 'series'))).group_by(canonical))).all())


async def search_contributors(db, viewer, term, kind='all'):
    groups = await local_matches(db, term, kind)
    key = await settings_store.get_user_tmdb_key(db, viewer.id)
    if not key:
        return {'results': ranked_results(groups, term)}

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
    principal_status = await known_principal_status(db, {person.id for person in known.values() if person.kind == 'person'})
    studio_ids = {person.id for person in known.values() if person.kind == 'organization'}
    company = aliased(CatalogueEntity)
    root_company = func.coalesce(company.canonical_company_id, company.id)
    studio_pages = set(await db.scalars(select(root_company).select_from(CatalogueCredit).join(
        company, company.id == CatalogueCredit.contributor_id).where(
        root_company.in_(studio_ids), CatalogueCredit.role == 'producer').distinct())) if studio_ids else set()
    seen = {section: {row['key'] for row in rows} for section, rows in groups.items()}
    for provider_kind, raw in sources:
        native = positive_id(raw.get('id'))
        if not native or not raw.get('name') or raw.get('adult'):
            continue
        section = 'studio' if provider_kind == 'company' else remote_person_kind(raw)
        if section is None:
            continue
        if kind != 'all' and section != kind:
            continue
        person = known.get((f'tmdb.{provider_kind}', str(native)))
        if person and section != 'studio' and principal_status.get(person.id) is False:
            continue
        identity = f'catalogue:{person.id}' if person else f'tmdb:{native}'
        if identity in seen[section]:
            for item in groups[section]:
                if item['key'] == identity:
                    item['_popularity'] = number(raw.get('popularity'))
            continue
        # Cached organizations without production credits have no studio page.
        if person and section == 'studio' and person.id not in studio_pages:
            continue
        if person:
            item = result(person, section)
        else:
            image = raw.get('logo_path') if section == 'studio' else raw.get('profile_path')
            item = {'key': identity, 'name': studio_name(raw['name']) if section == 'studio' else raw['name'], 'image': f'https://image.tmdb.org/t/p/w185{image}' if image else None,
                    'roles': [] if section == 'studio' else [raw.get('known_for_department') or 'Crew'],
                    'url': f'/{PATHS[section]}/{identity}'}
        groups[section].append({**item, '_popularity': number(raw.get('popularity'))})
        seen[section].add(identity)
    return {'results': ranked_results(groups, term), 'partial': failed}
