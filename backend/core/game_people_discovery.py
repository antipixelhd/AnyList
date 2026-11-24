"""Cold Games people discovery reuses canonical identities and portfolio jobs.

Suggestions admit identities only. Actual game/role eligibility comes from
the complete person-source and explicit game-discovery pipeline, never names
or suggestion descriptions. Pending/unmapped careers are not verified empty.
"""
import asyncio
import hashlib
import json
from datetime import timedelta

from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import aliased

from core import catalogue
from core.catalogue_providers import ProviderError
from core.game_browse_sources import GameBrowseSources
from core.game_credit_roles import CREDIT_POLICY_VERSION, SOURCE_ACTING, source_role
from core.game_imdb_discovery import JOB as DISCOVERY_JOB
from core.game_imdb_enrichment import now, request as admit
from core.game_imdb_person import JOB, COMPONENT, VERSION as PERSON_VERSION
from core.game_imdb_source import IMDbGameSource
from core.game_imdb_snapshot import DATASET, FALLBACK_ERRORS, people_samples
from core.game_source_cache import session_factory
from core.game_work import job_key
from db import AsyncSessionLocal
from models.catalogue import CatalogueEntity, CatalogueIdentity, ContributorSearch, MetadataSnapshot

MAX_PENDING = 128


class GamePeopleDiscovery:
    def __init__(self, sessions=None, *, sources=None, source=None):
        self.sessions = sessions or AsyncSessionLocal
        self.sources = sources or GameBrowseSources(self.sessions)
        self.source = source or IMDbGameSource(self.sources.api.http)

    async def load(self, term):
        local = {}
        fallback = unavailable = False
        try:
            candidates = await self.source.people_candidates(term)
        except ProviderError as error:
            if error.code not in FALLBACK_ERRORS or not isinstance(self.source, IMDbGameSource):
                raise
            local, fallback = await people_samples(self.sessions, term)
            if not fallback:
                raise
            candidates = [{'id': native, 'name': row['name'], 'snapshot': True} for native, row in local.items()]
        if not candidates:
            return {'ids': [], 'fallback': fallback}
        samples = {}
        from core.game_search_samples import qualified_games
        async with self.sessions() as db:
            qualified=await qualified_games(db,'imdb.person',[row['id'] for row in candidates])
        established={native for native,roles in qualified.items() if roles['actor'] or roles['staff']}
        if isinstance(self.source, IMDbGameSource):
            # Reuse fresh complete source headers without transferring careers.
            async with self.sessions() as db:
                totals = dict((await db.execute(select(MetadataSnapshot.external_id,
                    MetadataSnapshot.coverage['total'].as_integer()).where(
                        MetadataSnapshot.provider == 'imdb', MetadataSnapshot.kind == JOB,
                        MetadataSnapshot.external_id.in_([r['id'] for r in candidates]),
                        MetadataSnapshot.coverage['complete'].as_boolean().is_(True),
                        MetadataSnapshot.expires_at > now(), MetadataSnapshot.error_code.is_(None)))).all())
            from core.game_people_presence import presence
            unknown = [r['id'] for r in candidates if r['id'] not in established and totals.get(r['id']) is None]
            if not fallback:
                values = await asyncio.gather(*(presence(self.sessions, native) for native in unknown),
                    return_exceptions=True)
                failures = {}
                for native, value in zip(unknown, values):
                    if isinstance(value, (ProviderError, TimeoutError)):
                        failures[native] = value
                    elif isinstance(value, BaseException):
                        raise value
                    else:
                        samples[native] = value
                if failures:
                    local, fallback = await people_samples(self.sessions, term)
                    unavailable = not fallback or any(
                        isinstance(error, ProviderError) and error.code not in FALLBACK_ERRORS
                        for error in failures.values())
                    unknown = [native for native, error in failures.items()
                        if not isinstance(error, ProviderError) or error.code in FALLBACK_ERRORS]
                else:
                    unknown = []
            for native in unknown:
                if native in local:
                    samples[native] = {**local[native]['sample'], 'snapshot': True}
            totals.update({native: value['total'] for native, value in samples.items()})
            candidates = [r for r in candidates if r['id'] in established or totals.get(r['id'],0) > 0]
            if not candidates:
                return {'ids': [], 'fallback': fallback, 'unavailable': unavailable}
        async with self.sessions() as db:
            await catalogue.write_lock(db)
            pending = await db.scalar(select(func.count()).select_from(MetadataSnapshot).where(
                MetadataSnapshot.provider == 'imdb', MetadataSnapshot.kind == JOB,
                MetadataSnapshot.next_attempt_at.is_not(None)))
            existing = set(await db.scalars(select(MetadataSnapshot.external_id).where(
                MetadataSnapshot.provider == 'imdb', MetadataSnapshot.kind == JOB,
                MetadataSnapshot.external_id.in_([raw['id'] for raw in candidates]),
                (MetadataSnapshot.next_attempt_at.is_not(None)) | (MetadataSnapshot.expires_at > now()))))
            if pending + sum(raw['id'] not in existing for raw in candidates) > MAX_PENDING:
                raise ProviderError('rate_limited', 60)
            for raw in candidates:
                person = await catalogue.upsert_entity(db, {'kind': 'person', 'name': raw['name'],
                    'identities': [{'namespace': 'imdb.person', 'external_id': raw['id']}],
                    **({'image_url': raw['image']} if raw.get('image') else {})}, DATASET if raw.get('snapshot') else 'imdb')
                if qualified.get(raw['id'],{}).get('established') or samples.get(raw['id'], {}).get('snapshot'):
                    from core.game_work import work_context, Priority
                    from core.search_demand import context
                    with context(None), work_context(Priority.RETRY):
                        await admit(db,raw['id'],kind=JOB,entity_id=person.id)
                else:
                    await admit(db, raw['id'], kind=JOB, entity_id=person.id)
            await db.commit()
        return {'ids': [row['id'] for row in candidates],
                'samples': {r['id']:samples[r['id']] for r in candidates if r['id'] in samples},
                'fallback': fallback, 'unavailable': unavailable}

    async def search(self, term, *, progressive=False, kind='people'):
        key = hashlib.sha256(json.dumps(['names-v2', term.strip().casefold()]).encode()).hexdigest()
        try:
            async with asyncio.timeout(9):
                read = self.sources.cache.progressive if progressive else self.sources.cache.get
                cached = await read('imdb', 'game_people_search', key,
                    lambda _publish=None: self.load(term.strip()),
                    ttl=lambda data: timedelta(minutes=1) if data.get('fallback') or data.get('unavailable')
                    else timedelta(hours=6), max_stale=timedelta(days=7),
                    **({'wait_seconds': .05} if progressive else {}))
        except (ProviderError, TimeoutError):
            return {'ids': [], 'pending': False, 'partial': True}
        if cached['data'] is None:
            return {'ids': [], 'pending': True, 'partial': False, 'retry_after': 2}
        natives = cached['data']['ids']
        fallback = bool(cached['data'].get('fallback'))
        degraded = bool(cached.get('error') or cached['data'].get('unavailable'))
        if not natives:
            return {'ids': [], 'pending': False, 'partial': degraded or fallback, 'unavailable': degraded}
        source, discovery, component = (aliased(MetadataSnapshot) for _ in range(3))
        stamp = now()
        # Source role totals can settle a role while title matching is still active.
        # Only a fresh, complete connection under the current role rules proves absence.
        complete_roles = and_(
            source.coverage['complete'].as_boolean().is_(True),
            component.expires_at > stamp,
            component.coverage['source_complete'].as_boolean().is_(True),
            component.coverage['person_version'].as_integer() == PERSON_VERSION,
            component.coverage['credit_policy_version'].as_integer() == CREDIT_POLICY_VERSION,
            component.coverage['omitted_rows'].as_integer() == 0,
        )
        role_coverage = component.coverage['role_coverage']
        async with self.sessions() as db:
            rows = (await db.execute(select(
                CatalogueIdentity.external_id.label('native'),
                func.coalesce(CatalogueEntity.canonical_person_id, CatalogueEntity.id).label('entity_id'),
                source.id.label('source_id'), source.next_attempt_at.label('source_due'),
                source.lease_until.label('source_lease'), source.error_code.label('source_error'),
                source.coverage['complete'].as_boolean().label('source_complete'),
                discovery.next_attempt_at.label('discovery_due'), discovery.lease_until.label('discovery_lease'),
                discovery.error_code.label('discovery_error'),
                component.coverage['unmapped_games'].as_integer().label('unmapped_games'),
                ContributorSearch.game_actor_count.label('actor_works'),
                ContributorSearch.game_staff_count.label('staff_works'),
                case((complete_roles, role_coverage['actor']['raw_source'].as_integer())).label('actor_source'),
                case((complete_roles, role_coverage['staff']['raw_source'].as_integer())).label('staff_source'),
            ).select_from(CatalogueEntity).join(CatalogueIdentity, CatalogueIdentity.entity_id == CatalogueEntity.id)
                .outerjoin(source, and_(source.provider == 'imdb', source.kind == JOB,
                    source.external_id == CatalogueIdentity.external_id))
                .outerjoin(discovery, and_(discovery.provider == 'imdb', discovery.kind == DISCOVERY_JOB,
                    discovery.external_id == CatalogueIdentity.external_id))
                .outerjoin(component, and_(component.provider == 'imdb', component.kind == COMPONENT,
                    component.external_id == CatalogueIdentity.external_id))
                .outerjoin(ContributorSearch,ContributorSearch.entity_id==func.coalesce(
                    CatalogueEntity.canonical_person_id,CatalogueEntity.id))
                .where(CatalogueIdentity.namespace == 'imdb.person', CatalogueIdentity.external_id.in_(natives)))).all()

        def active(due, lease):
            return bool(due or lease and lease > stamp)

        def may_have_role(raw_source, sample, section):
            if raw_source is not None:
                return raw_source != 0
            return not sample or not sample['complete'] or any(role != 'thanks' and
                (role in SOURCE_ACTING) == (section == 'actor') for role in map(source_role, sample['rows']))

        samples = cached['data'].get('samples', {}) if not cached.get('stale') and not cached.get('error') else {}
        waiting = {'actor': [], 'staff': []}
        needs_verification = set()
        delays = []
        for row in rows:
            sample = samples.get(row.native)
            sections = [
                section for section, works, raw_source in (
                    ('actor', row.actor_works, row.actor_source), ('staff', row.staff_works, row.staff_source))
                if kind in ('people', 'all', section) and not works and may_have_role(raw_source, sample, section)
            ]
            if not sections or sample and sample.get('snapshot'):
                continue
            needs_verification.add(row.native)
            if not (row.source_id is None or active(row.source_due, row.source_lease)
                    or active(row.discovery_due, row.discovery_lease)):
                continue
            job = {'id': row.entity_id, 'scopes': [job_key('imdb', JOB, row.native),
                job_key('imdb', DISCOVERY_JOB, row.native)]}
            for section in sections:
                waiting[section].append(job)
            if row.source_id is None:
                delays.append(2)
            for due, lease in ((row.source_due, row.source_lease), (row.discovery_due, row.discovery_lease)):
                if lease and lease > stamp:
                    delays.append(2)
                elif due:
                    delays.append(max(1, int((due - stamp).total_seconds()) + 1))

        pending = any(waiting.values())
        failed_rows = [row for row in rows if row.native in needs_verification and (
            row.source_error or row.discovery_error
            or not row.source_complete and row.source_id is not None
                and not active(row.source_due, row.source_lease))]
        discovery_unavailable = degraded or len(rows) != len(natives)
        unavailable = discovery_unavailable or bool(failed_rows)
        # A fresh principal fallback can leave extra roles unknown for an
        # established person. The caller can check whether their card is visible.
        coverage_only = (fallback and not cached.get('stale') and not discovery_unavailable and failed_rows
            and all((row.actor_works or row.staff_works) and row.native not in samples
                and row.source_error in FALLBACK_ERRORS and not row.discovery_error for row in failed_rows))
        unmapped = any(row.native in needs_verification and (row.unmapped_games or 0) > 0 for row in rows)
        return {'ids': list({row.entity_id for row in rows}), 'pending': pending, 'partial': unavailable or unmapped or fallback,
            'samples': samples, 'pending_sections':{section:bool(jobs) for section,jobs in waiting.items()},
            'pending_jobs':waiting,
            'unavailable': unavailable,
            **({'coverage_only_ids': {row.entity_id for row in failed_rows}} if coverage_only else {}),
            **({'retry_after': min(3600, min(delays, default=2))} if pending else {})}


async def discover_people(db, term, kind, *, progressive=False):
    if kind == 'studio':
        return {'ids': [], 'pending': False, 'partial': False}
    return await GamePeopleDiscovery(session_factory(db.bind)).search(term, progressive=progressive,kind=kind)
