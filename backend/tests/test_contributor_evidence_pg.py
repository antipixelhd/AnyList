"""Durable admission, leases, retries and filtered local evidence in disposable PG."""
import asyncio
import importlib
import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import delete, func, select, text, update

import test_statistics_pg as fixture
from core import contributor_evidence as evidence
from models.catalogue import CatalogueCredit, CatalogueEntity, ContributorEvidence
from test_contributor_evidence import FILTERS, film


@unittest.skipUnless(fixture.URL, 'Requires explicit disposable PostgreSQL')
class EvidenceDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixture.StatisticsDatabaseTests.asyncSetUp(self)

    async def asyncTearDown(self):
        await fixture.StatisticsDatabaseTests.asyncTearDown(self)

    async def queue(self, native=429, filters=None):
        key, config = evidence.evidence_key('studio',native,filters or FILTERS,True)
        async with self.Session() as db:
            result = await evidence.cached_evidence(db,{key:config},self.user_id)
        return key,result

    async def test_migration_round_trip_and_cross_request_deduplication(self):
        async with self.engine.begin() as conn:
            def migrate(sync):
                module=importlib.import_module('migrations.versions.mt052_contributor_evidence')
                with Operations.context(MigrationContext.configure(sync)):
                    module.downgrade();module.upgrade()
            await conn.run_sync(migrate)
        await asyncio.gather(*(self.queue() for _ in range(5)))
        async with self.Session() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(ContributorEvidence)),1)
            row=await db.scalar(select(ContributorEvidence))
            self.assertNotIn('key',row.config)

    async def test_parallel_workers_share_lease_and_persist_results_without_importing_titles(self):
        key,_=await self.queue()
        started,release=asyncio.Event(),asyncio.Event()
        async def load(job,provider_key):
            started.set();await release.wait()
            return {'works':[film()], 'complete':True}
        with patch.object(evidence,'load_evidence',AsyncMock(side_effect=load)) as provider,patch.object(
            evidence.settings_store,'get_user_tmdb_key',AsyncMock(return_value='private-test-key')):
            first=asyncio.create_task(evidence.run_batch(self.Session))
            await started.wait()
            self.assertEqual(await evidence.run_batch(self.Session),0)
            release.set();await first
            self.assertEqual(provider.await_count,1)
        async with self.Session() as db:
            cached,pending=await evidence.cached_evidence(db,{key:evidence.evidence_key('studio',429,FILTERS,True)[1]},self.user_id)
            self.assertFalse(pending)
            self.assertEqual(evidence.work_summary(cached[key],'studio',FILTERS,True)['works'],1)
            self.assertEqual(await db.scalar(select(func.count()).select_from(CatalogueEntity).where(CatalogueEntity.kind=='movie')),1)

    async def test_failure_is_unknown_and_does_not_erase_previous_evidence_or_retry_on_each_search(self):
        key,_=await self.queue()
        with patch.object(evidence,'load_evidence',AsyncMock(side_effect=RuntimeError('private failure'))),patch.object(
            evidence.settings_store,'get_user_tmdb_key',AsyncMock(return_value='key')):
            await evidence.run_batch(self.Session)
        async with self.Session() as db:
            row=await db.get(ContributorEvidence,key)
            self.assertIsNone(row.payload)
            self.assertEqual(row.attempts,1)
            scheduled=row.next_attempt_at
        _,(_,pending)=await self.queue()
        self.assertFalse(pending)
        async with self.Session() as db:
            self.assertEqual((await db.get(ContributorEvidence,key)).next_attempt_at,scheduled)
            await db.execute(update(ContributorEvidence).values(payload={'works':[film()],'complete':True},
                expires_at=evidence.now()-timedelta(seconds=1),next_attempt_at=evidence.now()))
            await db.commit()
        with patch.object(evidence,'load_evidence',AsyncMock(side_effect=RuntimeError())),patch.object(
            evidence.settings_store,'get_user_tmdb_key',AsyncMock(return_value='key')):
            await evidence.run_batch(self.Session)
        async with self.Session() as db:
            self.assertEqual((await db.get(ContributorEvidence,key)).payload['works'][0]['id'],1)

    async def test_queue_limit_and_filter_variants(self):
        with patch.object(evidence,'MAX_PENDING',2):
            await self.queue(1);await self.queue(2);await self.queue(3)
        async with self.Session() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(ContributorEvidence)),2)
        await self.queue(1,{**FILTERS,'min_votes':1000})
        async with self.Session() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(ContributorEvidence)),3)

    async def test_admission_contention_keeps_refresh_pending_until_job_can_be_queued(self):
        async with self.Session() as owner,owner.begin():
            await owner.scalar(select(func.pg_advisory_xact_lock(724052)))
            _,(_,pending)=await self.queue()
            self.assertTrue(pending)
            self.assertEqual(await owner.scalar(select(func.count()).select_from(ContributorEvidence)),0)
        await self.queue()
        async with self.Session() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(ContributorEvidence)),1)

    async def test_filtered_local_works_not_unfiltered_counts_establish_results(self):
        async with self.Session() as db:
            work=await db.scalar(select(CatalogueEntity).where(CatalogueEntity.kind=='movie'))
            person=await db.scalar(select(CatalogueEntity).where(CatalogueEntity.kind=='person'))
            rows=[{'key':f'catalogue:{person.id}'}]
            self.assertEqual(await evidence.local_evidence(db,rows,'actor',FILTERS,True),{})
            work.attributes={'vote_count':500,'release_date':'2025-01-01'}
            await db.commit()
            self.assertEqual((await evidence.local_evidence(db,rows,'actor',FILTERS,True))[rows[0]['key']]['works'],1)
            self.assertEqual(await evidence.local_evidence(db,rows,'actor',{**FILTERS,'min_votes':1000},True),{})
