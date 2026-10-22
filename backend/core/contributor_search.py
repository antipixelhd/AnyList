"""Bounded quick-search discovery using canonical contributors and existing providers."""
import asyncio
import hashlib
from collections import defaultdict
from types import SimpleNamespace

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import load_only

from core import settings_store, tmdb
from core.contributor_details import is_actor, positive_id
from core.credit_roles import work_credit
from core.person_identities import canonical_people
from core.principal_credits import remote_person_kind, number, group
from core.statistics_staff import role_label, role_order
from core.studio_names import studio_name
from core.studio_discovery import verified_studios
from core.studio_groups import matching_studio_members
from core.contributor_evidence import cached_evidence, evidence_key, local_evidence, work_summary
from core.catalog_search import result_filters
from models import GlobalSettings
from models.catalogue import CatalogueEntity, CatalogueIdentity, ContributorSearch

KINDS = ('actor', 'staff', 'studio')
PATHS = {'actor': 'actors', 'staff': 'person', 'studio': 'studio'}
LIMIT = 8
MAX_CANDIDATES = 8
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
    members = matching_studio_members(term) if kind in ('all', 'studio') else ()
    branded_ids = select(CatalogueIdentity.entity_id).where(CatalogueIdentity.namespace == 'tmdb.company',
        CatalogueIdentity.external_id.in_([str(i) for i in members]))
    count = summary.actor_count if kind == 'actor' else summary.staff_count if kind == 'staff' else summary.work_count
    matches = (await db.execute(select(summary.entity_id, summary.kind, summary.name, summary.image_url,
        count.label('work_count'), summary.roles,
        select(CatalogueIdentity.external_id).where(CatalogueIdentity.entity_id == summary.entity_id,
            CatalogueIdentity.namespace == case((summary.kind == 'organization', 'tmdb.company'), else_='tmdb.person'))
            .order_by(CatalogueIdentity.external_id).limit(1).scalar_subquery().label('tmdb_id')).where(
        summary.kind.in_(('organization',) if kind == 'studio' else ('person',) if kind != 'all' else ('person', 'organization')),
        or_(summary.search_text.ilike(pattern, escape='\\'), summary.entity_id.in_(branded_ids) if members else False),
        summary.work_count > 0, count > 0,
    ).order_by(case((func.lower(summary.name) == term.casefold(), 0), else_=1),
               count.desc(), func.lower(summary.name), summary.entity_id).limit(80))).all()
    groups = {k: [] for k in KINDS}
    for row in matches:
        person = SimpleNamespace(id=row.entity_id, kind=row.kind, name=row.name, image_url=row.image_url)
        roles = defaultdict(set)
        for role, label in row.roles:
            credit = SimpleNamespace(role=role, role_label=label, character_label=None)
            section = credit_kind(person, credit)
            if section and (kind in ('all', 'people') or section == kind):
                roles[section].add(role_label(credit))
        for section, labels in roles.items():
            groups[section].append({**result(person, section, labels), '_works': row.work_count,
                '_native': positive_id(row.tmdb_id)})
    return groups


async def game_matches(db, term, kind):
    """Read fresh verified game summaries; never scan careers per keystroke."""
    from core.game_contributor_works import ACTING, COMPANIES
    summary = ContributorSearch
    pattern = '%' + term.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
    count = summary.game_actor_count if kind == 'actor' else summary.game_staff_count if kind == 'staff' else summary.game_work_count
    async with _local_search_slots:
        rows = (await db.execute(select(summary.entity_id, summary.kind, summary.name, summary.image_url,
            summary.game_work_count, summary.game_actor_count, summary.game_staff_count, summary.game_roles).where(
            summary.kind.in_(('organization',) if kind == 'studio' else ('person',) if kind != 'all' else ('person','organization')),
            summary.game_work_count > 0, count > 0, summary.search_text.ilike(pattern, escape='\\'),
        ).order_by(case((func.lower(summary.name) == term.casefold(), 0), else_=1),
            count.desc(), func.lower(summary.name), summary.entity_id).limit(80))).all()
    groups = {section: [] for section in KINDS}
    for row in rows:
        person = SimpleNamespace(id=row.entity_id, kind=row.kind, name=row.name, image_url=row.image_url)
        roles = defaultdict(set)
        for role, label in row.game_roles:
            section = ('studio' if role in COMPANIES else None) if row.kind == 'organization' else 'actor' if role in ACTING else 'staff'
            if section and kind in ('all','people',section):
                roles[section].add(label or role.replace('_',' ').title())
        for section, labels in roles.items():
            works = row.game_actor_count if section == 'actor' else row.game_staff_count if section == 'staff' else row.game_work_count
            item = result(person, section, labels)
            groups[section].append({**item, 'url': item['url'] + '?media_type=game', '_works': works})
    return groups


def merge_game_matches(groups, games):
    """Only canonical identity combines domains; namesakes remain separate."""
    for section in KINDS:
        known = {row['key']: row for row in groups[section]}
        for row in games[section]:
            if row['key'] not in known:
                groups[section].append(row)
                known[row['key']] = row
            else:
                item = known[row['key']]
                item['name'] = row['name']
                item['roles'] = sorted(set(item.get('roles', ())) | set(row['roles']), key=lambda label:(role_order(label),label))[:2]
                item['_works'] = item.get('_works', 0) + row['_works']
                item['image'] = row.get('image')
    return groups


def ranked_results(groups, term):
    query = term.casefold()
    def rank(item, section):
        name = item.get('name', '').casefold()
        match = 0 if name == query else 1 if name.startswith(query) else 2
        recognition = number(item.get('_popularity'))
        works = item.get('_works', 0)
        if section == 'studio':
            recent = int((item.get('_latest') or '0').replace('-', ''))
            return (match, -recognition, -works, -recent, not bool(item.get('image')), name, item['key'])
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


async def verified_people(db, viewer, groups, api_key):
    if not groups['actor'] and not groups['staff']:
        return False
    filters = await result_filters(db, viewer)
    settings = await db.get(GlobalSettings, 1)
    show_anime = settings is None or settings.show_anime
    jobs, summaries = {}, {}
    for section in ('actor', 'staff'):
        groups[section] = groups[section][:MAX_CANDIDATES]
        for row in groups[section]:
            if row.get('_native'):
                key, config = evidence_key('person', row['_native'], filters, show_anime)
                jobs[key] = config
                summaries.setdefault(key, []).append((section, filters, show_anime))
    evidence, pending = await cached_evidence(db, jobs, viewer.id, enqueue=bool(api_key),
        summaries=summaries)
    for section in ('actor', 'staff'):
        unresolved = [row for row in groups[section] if not (row.get('_native') and
            (evidence.get(evidence_key('person', row['_native'], filters, show_anime)[0]) or {}).get('_fresh') and
            (evidence.get(evidence_key('person', row['_native'], filters, show_anime)[0]) or {}).get('complete'))]
        local = await local_evidence(db, unresolved, section, filters, show_anime) if unresolved else {}
        output = []
        for row in groups[section]:
            key = evidence_key('person', row['_native'], filters, show_anime)[0] if row.get('_native') else None
            value = work_summary(evidence.get(key), section, filters, show_anime)
            # known_for is positive evidence only. A failed shortlist is unknown,
            # not proof of an empty filmography. Full cached credits take priority.
            if value is None and row.get('_known_for'):
                shortlist = {'cast' if section == 'actor' else 'crew':row['_known_for']}
                value = work_summary(shortlist, section, filters, show_anime)
            if local.get(row['key']) or (value and value['works']):
                output.append(row)
        groups[section] = output
    return pending and bool(api_key)


async def _discover_candidates(db, viewer, term, kind, key):
    groups = await local_matches(db, term, kind)
    if not key:
        return groups, False

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
    sources = [('person', row) for row in (people or [])[:MAX_CANDIDATES]] + [('company', row) for row in (companies or [])[:MAX_CANDIDATES]]
    native_ids = {str(row['id']) for _, row in sources if positive_id(row.get('id'))}
    mappings = (await db.execute(select(CatalogueIdentity, CatalogueEntity).join(
        CatalogueEntity, CatalogueEntity.id == CatalogueIdentity.entity_id).where(
        CatalogueIdentity.namespace.in_(('tmdb.person', 'tmdb.company')), CatalogueIdentity.external_id.in_(native_ids))
        .options(load_only(CatalogueEntity.id,CatalogueEntity.kind,CatalogueEntity.name,CatalogueEntity.image_url,
            CatalogueEntity.canonical_person_id,CatalogueEntity.canonical_company_id)))).all() if native_ids else []
    roots = await canonical_people(db, [person for _, person in mappings], lightweight=True)
    known = {(identity.namespace, identity.external_id): roots[person.id] for identity, person in mappings}
    principal_status = await known_principal_status(db, {person.id for person in known.values() if person.kind == 'person'})
    seen = {section: {row['key'] for row in rows} for section, rows in groups.items()}
    for provider_kind, raw in sources:
        native = positive_id(raw.get('id'))
        if not native or not raw.get('name') or raw.get('adult'):
            continue
        section = 'studio' if provider_kind == 'company' else remote_person_kind(raw)
        if section is None:
            continue
        if kind not in ('all', 'people') and section != kind:
            continue
        person = known.get((f'tmdb.{provider_kind}', str(native)))
        if person and section != 'studio' and principal_status.get(person.id) is False:
            continue
        identity = f'catalogue:{person.id}' if person else f'tmdb:{native}'
        if identity in seen[section]:
            for item in groups[section]:
                if item['key'] == identity:
                    item['_popularity'] = number(raw.get('popularity'))
                    item['_native'] = item.get('_native') or native
                    if section != 'studio': item['_known_for'] = raw.get('known_for') or []
            continue
        if person:
            item = result(person, section)
        else:
            image = raw.get('logo_path') if section == 'studio' else raw.get('profile_path')
            item = {'key': identity, 'name': studio_name(raw['name']) if section == 'studio' else raw['name'], 'image': f'https://image.tmdb.org/t/p/w185{image}' if image else None,
                    'roles': [] if section == 'studio' else [raw.get('known_for_department') or 'Crew'],
                    'url': f'/{PATHS[section]}/{identity}'}
        groups[section].append({**item, '_popularity': number(raw.get('popularity')),
            '_native':native, **({'_known_for':raw.get('known_for') or []} if section != 'studio' else {})})
        seen[section].add(identity)
    return groups, failed


_candidate_cache = tmdb._TTLCache(maxsize=128)


async def search_contributors(db, viewer, term, kind='all', work_domain='all'):
    from core.game_access import allowed, require
    if work_domain == 'game':
        require(viewer)
    elif not allowed(viewer):
        work_domain = 'movies_series'
    if work_domain == 'game':
        groups = await game_matches(db, term, kind)
        return {'results': ranked_results(groups, term), 'partial': False, 'verification_pending': False,
            'game_coverage': 'saved_credits'}
    key = await settings_store.get_user_tmdb_key(db, viewer.id)
    identity = (viewer.id, term.strip().casefold(), kind, hashlib.sha256(str(key).encode()).digest())
    cached = _candidate_cache.get(identity)
    if cached is None:
        groups, failed = await _discover_candidates(db, viewer, term, kind, key)
        if not failed:
            _candidate_cache.set(identity, {'groups': groups}, 60)
    else:
        groups, failed = cached['groups'], False
    # _TTLCache already isolates the shortlist with copies on set/get. Merge
    # fresh game evidence only after movie/series verification, outside it.
    # Re-evaluate current saved filters on every poll. Only name/identity discovery
    # is cached; shared work evidence is independently versioned and leased.
    for row in groups['studio']:
        name = row.get('name', '').casefold()
        query = term.casefold()
        row['_match'] = 0 if name == query else 1 if name.startswith(query) else 2
    groups['studio'], studios_pending = await verified_studios(db, viewer, groups['studio'], key)
    people_pending = await verified_people(db, viewer, groups, key)
    if work_domain == 'all':
        groups = merge_game_matches(groups, await game_matches(db, term, kind))
    return {'results': ranked_results(groups, term), 'partial': failed,
            'verification_pending':studios_pending or people_pending,
            **({'game_coverage':'saved_credits'} if work_domain == 'all' else {})}
