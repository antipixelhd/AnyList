"""Shared name discovery admits canonical IGDB companies to portfolio workers.

Search is candidate discovery, never company identity reconciliation. Only
verified eligible work summaries expose a candidate in contributor results.
"""
import asyncio
import hashlib
import json
from datetime import timedelta

from sqlalchemy import and_, func, select

from core import catalogue
from core.catalogue_normalize import entity
from core.countries import numeric_country
from core.catalogue_providers import ProviderError
from core.game_browse_sources import GameBrowseSources, positive
from core.game_company_works import JOB
from core.game_imdb_enrichment import now, request as admit
from core.game_source_cache import session_factory
from db import AsyncSessionLocal
from models.catalogue import CatalogueEntity, CatalogueIdentity, MetadataSnapshot

LIMIT = 8
MAX_PENDING = 256


class GameCompanyDiscovery:
    def __init__(self, sessions=None, *, sources=None):
        self.sessions = sessions or AsyncSessionLocal
        self.sources = sources or GameBrowseSources(self.sessions)

    async def load(self, term):
        rows = await self.sources.igdb('companies',
            f'fields id,name,logo.url,country; where name ~ *{json.dumps(term)}*; sort id asc; limit {LIMIT};')
        if (not isinstance(rows, list) or len(rows) > LIMIT
                or any(not isinstance(row, dict) or not positive(row.get('id'))
                    or row['id'] > 2147483647 or not isinstance(row.get('name'), str)
                    or not row['name'].strip() for row in rows)
                or len({row['id'] for row in rows}) != len(rows)):
            raise ProviderError('bad_response')
        if not rows:
            return {'ids': []}
        async with self.sessions() as db:
            # Serialize bounded admission with existing catalogue writers, but
            # never hold the lock over provider requests or portfolio ingestion.
            await catalogue.write_lock(db)
            pending = await db.scalar(select(func.count()).select_from(MetadataSnapshot).where(
                MetadataSnapshot.provider == 'igdb', MetadataSnapshot.kind == JOB,
                MetadataSnapshot.next_attempt_at.is_not(None)))
            if pending >= MAX_PENDING:
                raise ProviderError('rate_limited', 60)
            for raw in rows[:MAX_PENDING - pending]:
                country = numeric_country(raw.get('country'))
                values = entity('organization', 'igdb.company', raw, artwork=raw.get('logo'),
                    attributes={'countries': [country]} if country else {})
                company = await catalogue.upsert_entity(db, values, 'igdb')
                await admit(db, str(raw['id']), kind=JOB, provider='igdb', entity_id=company.id)
            if len(rows) > MAX_PENDING - pending:
                # Do not certify an incomplete shortlist as a successful empty
                # result or silently discard candidates at the admission limit.
                raise ProviderError('rate_limited', 60)
            await db.commit()
        return {'ids': [str(raw['id']) for raw in rows]}

    async def search(self, term, *, progressive=False):
        try:
            self.sources.api.validate('igdb', 'game')
        except ProviderError:
            return {'ids': [], 'pending': False, 'partial': False}
        key = hashlib.sha256(json.dumps(['name-substring-v1', term.strip().casefold()]).encode()).hexdigest()
        try:
            async with asyncio.timeout(9):
                read = self.sources.cache.progressive if progressive else self.sources.cache.get
                cached = await read('igdb', 'game_company_search', key,
                    lambda _publish=None: self.load(term.strip()), ttl=timedelta(hours=6), max_stale=timedelta(days=7),
                    **({'wait_seconds': .05} if progressive else {}))
        except (ProviderError, TimeoutError):
            return {'ids': [], 'pending': False, 'partial': True}
        if cached['data'] is None:
            return {'ids': [], 'pending': True, 'partial': False, 'retry_after': 2}
        natives = cached['data']['ids']
        if not natives:
            return {'ids': [], 'pending': False, 'partial': bool(cached.get('error'))}
        async with self.sessions() as db:
            # Resolve aliases and read compact job headers together. No credit
            # or portfolio payload is transferred on repeated search polls.
            rows = (await db.execute(select(
                func.coalesce(CatalogueEntity.canonical_company_id, CatalogueEntity.id),
                MetadataSnapshot.next_attempt_at, MetadataSnapshot.lease_until,
                MetadataSnapshot.id,
            ).select_from(CatalogueEntity).join(CatalogueIdentity, CatalogueIdentity.entity_id == CatalogueEntity.id)
                .outerjoin(MetadataSnapshot, and_(MetadataSnapshot.provider == 'igdb',
                    MetadataSnapshot.kind == JOB,
                    MetadataSnapshot.external_id == CatalogueIdentity.external_id))
                .where(CatalogueIdentity.namespace == 'igdb.company',
                    CatalogueIdentity.external_id.in_(natives)))).all()
        return {'ids': list({row[0] for row in rows}),
            'pending': any(row[3] is None or row[1] or row[2] and row[2] > now() for row in rows),
            'partial': bool(cached.get('error')) or len(rows) != len(natives)}


async def discover_companies(db, term, kind, *, progressive=False):
    if kind not in ('all', 'studio'):
        return {'ids': [], 'pending': False, 'partial': False}
    return await GameCompanyDiscovery(session_factory(db.bind)).search(term, progressive=progressive)
