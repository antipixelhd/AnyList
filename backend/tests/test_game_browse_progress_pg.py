"""Shared critic verification publishes ordered facts, never a false empty page."""
import asyncio
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import test_statistics_pg as fixture
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from core.catalogue_providers import ProviderError
from core.game_browse import GameBrowse
from core.game_source_cache import GameSourceCache, _flights, utcnow
from models.catalogue import MetadataSnapshot


@unittest.skipUnless(fixture.URL,'Requires explicit disposable PostgreSQL')
class ProgressiveCriticTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixture.StatisticsDatabaseTests.asyncSetUp(self)
        self.factories=[self.Session]
        self.started=asyncio.Event();self.first=asyncio.Event();self.prefix=asyncio.Event();self.finish=asyncio.Event()
        self.fail=False
        self.rows=[{'igdb_id':i,'title':f'Game{i}','year':'2024','release_date':'2024-01-01',
            'genres':[],'platform_ids':[]} for i in range(1,5)]
        async def scores(games,*,on_progress):
            self.started.set()
            await on_progress({2:95})  # A later qualifying game must wait for rank1.
            await self.first.wait()
            await on_progress({1:85,2:95})
            self.prefix.set()
            await self.finish.wait()
            if self.fail:raise ProviderError('unavailable')
            return {1:85,2:95,4:79}
        self.source=SimpleNamespace(cache=GameSourceCache(self.Session),critic_scores=AsyncMock(side_effect=scores),
            attention=AsyncMock(return_value={'data':{'rows':self.rows,'has_more':False,'source':'IGDB Visits'},'stale':False}))
        self.service=GameBrowse(self.source)
        self.query={'category':'trending','q':'','genres':[],'platforms':[],
            'start':None,'end':None,'min_metacritic':80,'timeline':False}

    async def asyncTearDown(self):
        self.first.set();self.finish.set()
        tasks=[task for key,task in list(_flights.items()) if key[0] in {id(factory) for factory in self.factories}]
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
        await fixture.StatisticsDatabaseTests.asyncTearDown(self)

    async def read(self,service=None):
        return await (service or self.service).page(self.query,limit=3,wait_seconds=.02)

    async def wait_job(self):
        tasks=[task for key,task in list(_flights.items()) if key[0] in {id(factory) for factory in self.factories}]
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)

    async def test_ordered_prefix_is_shared_and_final_membership_is_unchanged(self):
        first=await self.read()
        await self.started.wait()
        self.assertTrue(first['verification_pending']);self.assertEqual(first['rows'],[])
        self.assertFalse(first['has_more']);self.assertIsNone(first['cursor'])
        # Another process has a different in-process scope but shares the lease.
        other=async_sessionmaker(self.engine,expire_on_commit=False);self.factories.append(other)
        second=GameBrowse(SimpleNamespace(**{**vars(self.source),'cache':GameSourceCache(other)}))
        before=await self.read(second)
        self.assertEqual(before['rows'],[])
        self.first.set();await self.prefix.wait()
        prefix=await self.read(second)
        self.assertEqual([r['igdb_id'] for r in prefix['rows']],[1,2])
        self.assertTrue(prefix['verification_pending'])
        self.assertFalse(prefix['has_more']);self.assertIsNone(prefix['cursor'])
        async with self.Session() as db:
            stored=await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.kind=='game_browse_pages'))
            self.assertFalse(stored.coverage['page_complete'])
        self.finish.set();await self.wait_job()
        final=await self.read()
        self.assertEqual([r['igdb_id'] for r in final['rows']],[1,2])
        self.assertTrue(final['partial']);self.assertNotIn('verification_pending',final)
        self.assertNotIn('verification_error',final)
        self.source.critic_scores.assert_awaited_once()
        self.source.attention.assert_awaited_once()
        async with self.Session() as db:
            stored=await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.kind=='game_browse_pages'))
            self.assertTrue(stored.coverage['page_complete'])

    async def test_failure_retains_prefix_and_cooldown_then_retry_recovers(self):
        self.fail=True
        await self.read();await self.started.wait();self.first.set();await self.prefix.wait()
        self.finish.set();await self.wait_job()
        failed=await self.read()
        self.assertEqual([r['igdb_id'] for r in failed['rows']],[1,2])
        self.assertEqual(failed['verification_error'],'unavailable')
        self.assertNotIn('verification_pending',failed)
        self.assertEqual(await self.read(),failed)
        self.source.critic_scores.assert_awaited_once()
        async with self.Session() as db:
            await db.execute(update(MetadataSnapshot).where(MetadataSnapshot.kind=='game_browse_pages')
                .values(next_attempt_at=utcnow()-timedelta(seconds=1)))
            await db.commit()
        self.fail=False
        await self.read();await self.wait_job()
        final=await self.read()
        self.assertNotIn('verification_error',final)
        self.assertNotIn('verification_pending',final)
        self.assertEqual([r['igdb_id'] for r in final['rows']],[1,2])
        self.assertEqual(self.source.critic_scores.await_count,2)

    async def test_departing_request_does_not_cancel_shared_work(self):
        caller=asyncio.create_task(self.service.page(self.query,limit=3,wait_seconds=10))
        await self.started.wait();caller.cancel()
        with self.assertRaises(asyncio.CancelledError):await caller
        self.first.set();await self.prefix.wait()
        self.assertTrue((await self.read())['verification_pending'])
        self.finish.set();await self.wait_job()
        self.assertNotIn('verification_pending',await self.read())
        self.source.critic_scores.assert_awaited_once()

    async def test_orphaned_lease_keeps_prefix_until_expiry_then_reclaims(self):
        await self.read();await self.started.wait();self.first.set();await self.prefix.wait()
        jobs=[task for key,task in list(_flights.items()) if key[0]==id(self.Session)]
        for task in jobs:task.cancel()
        await asyncio.gather(*jobs,return_exceptions=True)
        # Cancellation resembles a stopped worker: another scope must not steal
        # a lease that could still belong to a live process.
        other=async_sessionmaker(self.engine,expire_on_commit=False);self.factories.append(other)
        service=GameBrowse(SimpleNamespace(**{**vars(self.source),'cache':GameSourceCache(other)}))
        retained=await self.read(service)
        self.assertEqual([r['igdb_id'] for r in retained['rows']],[1,2])
        self.source.critic_scores.assert_awaited_once()
        async with self.Session() as db:
            row=await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.kind=='game_browse_pages'))
            row.lease_until=utcnow()-timedelta(seconds=1);await db.commit()
        self.finish.set()
        await self.read(service);await self.wait_job()
        final=await self.read(service)
        self.assertNotIn('verification_pending',final)
        self.assertEqual([r['igdb_id'] for r in final['rows']],[1,2])
        self.assertEqual(self.source.critic_scores.await_count,2)
