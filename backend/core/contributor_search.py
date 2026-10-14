"""Bounded quick-search discovery using canonical contributors and existing providers."""
import asyncio
from collections import defaultdict
from types import SimpleNamespace

from sqlalchemy import case, func, select
from sqlalchemy.orm import load_only

from core import settings_store, tmdb
from core.contributor_details import is_actor, positive_id
from core.credit_roles import work_credit
from core.person_identities import canonical_people
from core.principal_credits import remote_person_kind, number, group
from core.statistics_staff import role_label, role_order
from core.studio_names import studio_name
from models.catalogue import CatalogueEntity, CatalogueIdentity, ContributorSearch

KINDS = ('actor', 'staff', 'studio')
PATHS = {'actor': 'actors', 'staff': 'person', 'studio': 'studio'}
LIMIT = 8
# Keep keystroke-triggered sorting from occupying the shared VPS's entire
# connection pool. Waiting searches stay cancellable and do not spawn threads.
_local_search_slots = asyncio.Semaphore(2)


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
    async with _local_search_slots:
        return await _local_matches(db, term, kind)


async def _local_matches(db, term, kind):
    # Counts and roles are maintained when credits change, never aggregated on
    # each keystroke. This read does not touch source credits or entity JSON.
    pattern = '%' + term.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
    summary = ContributorSearch
    count = summary.actor_count if kind == 'actor' else summary.staff_count if kind == 'staff' else summary.work_count
    matches = (await db.execute(select(summary.entity_id, summary.kind, summary.name, summary.image_url,
        count.label('work_count'), summary.roles).where(
        summary.kind.in_(('organization',) if kind == 'studio' else ('person',) if kind != 'all' else ('person', 'organization')),
        summary.search_text.ilike(pattern, escape='\\'), summary.work_count > 0, count > 0,
    ).order_by(case((func.lower(summary.name) == term.casefold(), 0), else_=1),
               count.desc(), func.lower(summary.name), summary.entity_id).limit(80))).all()
    groups = {k: [] for k in KINDS}
    for row in matches:
        person = SimpleNamespace(id=row.entity_id, kind=row.kind, name=row.name, image_url=row.image_url)
        roles = defaultdict(set)
        for role, label in row.roles:
            credit = SimpleNamespace(role=role, role_label=label, character_label=None)
            section = credit_kind(person, credit)
            if section and (kind == 'all' or section == kind):
                roles[section].add(role_label(credit))
        for section, labels in roles.items():
            groups[section].append({**result(person, section, labels), '_works': row.work_count})
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
    return dict((await db.execute(select(ContributorSearch.entity_id, ContributorSearch.work_count > 0)
        .where(ContributorSearch.entity_id.in_(ids), ContributorSearch.screen_count > 0))).all())


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
        CatalogueIdentity.namespace.in_(('tmdb.person', 'tmdb.company')), CatalogueIdentity.external_id.in_(native_ids))
        .options(load_only(CatalogueEntity.id,CatalogueEntity.kind,CatalogueEntity.name,CatalogueEntity.image_url,
            CatalogueEntity.canonical_person_id,CatalogueEntity.canonical_company_id)))).all() if native_ids else []
    roots = await canonical_people(db, [person for _, person in mappings], lightweight=True)
    known = {(identity.namespace, identity.external_id): roots[person.id] for identity, person in mappings}
    principal_status = await known_principal_status(db, {person.id for person in known.values() if person.kind == 'person'})
    studio_ids = {person.id for person in known.values() if person.kind == 'organization'}
    studio_pages = set(await db.scalars(select(ContributorSearch.entity_id).where(
        ContributorSearch.entity_id.in_(studio_ids), ContributorSearch.kind == 'organization',
        ContributorSearch.work_count > 0))) if studio_ids else set()
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
