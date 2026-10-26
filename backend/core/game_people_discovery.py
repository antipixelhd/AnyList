"""Cold Games people discovery reuses canonical identities and portfolio jobs.

Suggestions admit identities only. Actual game/role eligibility comes from
the complete person-source and explicit game-discovery pipeline, never names
or suggestion descriptions. Pending/unmapped careers are not verified empty.
"""
import asyncio
import hashlib
import json
from datetime import timedelta

from sqlalchemy import and_, func, select
from sqlalchemy.orm import aliased

from core import catalogue
from core.catalogue_providers import ProviderError
from core.game_browse_sources import GameBrowseSources
from core.game_imdb_discovery import JOB as DISCOVERY_JOB
from core.game_imdb_enrichment import now, request as admit
from core.game_imdb_person import JOB, COMPONENT
from core.game_imdb_source import IMDbGameSource
from core.game_source_cache import session_factory
from db import AsyncSessionLocal
from models.catalogue import CatalogueEntity, CatalogueIdentity, MetadataSnapshot

MAX_PENDING = 128


class GamePeopleDiscovery:
    def __init__(self, sessions=None, *, sources=None, source=None):
        self.sessions = sessions or AsyncSessionLocal
        self.sources = sources or GameBrowseSources(self.sessions)
        self.source = source or IMDbGameSource(self.sources.api.http)

    async def load(self, term):
        candidates = await self.source.people_candidates(term)
        if not candidates:
            return {'ids': []}
        async with self.sessions() as db:
            await catalogue.write_lock(db)
            pending = await db.scalar(select(func.count()).select_from(MetadataSnapshot).where(
                MetadataSnapshot.provider == 'imdb', MetadataSnapshot.kind == JOB,
                MetadataSnapshot.next_attempt_at.is_not(None)))
            if pending + len(candidates) > MAX_PENDING:
                raise ProviderError('rate_limited', 60)
            for raw in candidates:
                person = await catalogue.upsert_entity(db, {'kind': 'person', 'name': raw['name'],
                    'identities': [{'namespace': 'imdb.person', 'external_id': raw['id']}],
                    **({'image_url': raw['image']} if raw.get('image') else {})}, 'imdb')
                await admit(db, raw['id'], kind=JOB, entity_id=person.id)
            await db.commit()
        return {'ids': [row['id'] for row in candidates]}

    async def search(self, term):
        key = hashlib.sha256(json.dumps(['names-v1', term.strip().casefold()]).encode()).hexdigest()
        try:
            async with asyncio.timeout(9):
                cached = await self.sources.cache.get('imdb', 'game_people_search', key,
                    lambda: self.load(term.strip()), ttl=timedelta(hours=6), max_stale=timedelta(days=7))
        except (ProviderError, TimeoutError):
            return {'ids': [], 'pending': False, 'partial': True}
        natives = cached['data']['ids']
        if not natives:
            return {'ids': [], 'pending': False, 'partial': bool(cached.get('error'))}
        source, discovery, component = (aliased(MetadataSnapshot) for _ in range(3))
        async with self.sessions() as db:
            rows = (await db.execute(select(
                func.coalesce(CatalogueEntity.canonical_person_id, CatalogueEntity.id).label('entity_id'),
                source.id.label('source_id'), source.next_attempt_at.label('source_due'),
                source.lease_until.label('source_lease'), source.error_code.label('source_error'),
                source.coverage['complete'].as_boolean().label('source_complete'),
                discovery.next_attempt_at.label('discovery_due'), discovery.lease_until.label('discovery_lease'),
                discovery.error_code.label('discovery_error'),
                component.coverage['unmapped_games'].as_integer().label('unmapped_games'),
            ).select_from(CatalogueEntity).join(CatalogueIdentity, CatalogueIdentity.entity_id == CatalogueEntity.id)
                .outerjoin(source, and_(source.provider == 'imdb', source.kind == JOB,
                    source.external_id == CatalogueIdentity.external_id))
                .outerjoin(discovery, and_(discovery.provider == 'imdb', discovery.kind == DISCOVERY_JOB,
                    discovery.external_id == CatalogueIdentity.external_id))
                .outerjoin(component, and_(component.provider == 'imdb', component.kind == COMPONENT,
                    component.external_id == CatalogueIdentity.external_id))
                .where(CatalogueIdentity.namespace == 'imdb.person', CatalogueIdentity.external_id.in_(natives)))).all()
        stamp = now()
        def active(due, lease):
            return bool(due or lease and lease > stamp)
        pending = any(row.source_id is None or active(row.source_due, row.source_lease)
            or active(row.discovery_due, row.discovery_lease) for row in rows)
        partial = bool(cached.get('error')) or len(rows) != len(natives) or any(
            row.source_error or row.discovery_error or (row.unmapped_games or 0) > 0
            or not row.source_complete and row.source_id is not None
                and not active(row.source_due, row.source_lease) for row in rows)
        delays = []
        for row in rows:
            if row.source_id is None:
                delays.append(2)
            for due, lease in ((row.source_due, row.source_lease), (row.discovery_due, row.discovery_lease)):
                if lease and lease > stamp:
                    delays.append(2)
                elif due:
                    delays.append(max(1, int((due - stamp).total_seconds()) + 1))
        return {'ids': list({row.entity_id for row in rows}), 'pending': pending, 'partial': bool(partial),
            **({'retry_after': min(3600, min(delays, default=2))} if pending else {})}


async def discover_people(db, term, kind):
    if kind == 'studio':
        return {'ids': [], 'pending': False, 'partial': False}
    return await GamePeopleDiscovery(session_factory(db.bind)).search(term)
