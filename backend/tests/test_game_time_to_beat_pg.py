"""Game estimates use verified identity, shared jobs and private-safe routes."""
import asyncio
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import select,func

import test_games_pg as fixture
from core.game_time_to_beat import GameTimeToBeat,JOB,COMPONENT,view,snapshot,normalized
from core.game_imdb_enrichment import now
from core.catalogue_providers import ProviderError
from models.catalogue import CatalogueEntity,CatalogueIdentity,MetadataSnapshot
from models.games import GameEntry


@unittest.skipUnless(fixture.URL,'Requires explicit disposable Games PostgreSQL')
class TimeToBeatTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixture.GamesDatabaseTests.asyncSetUp(self)
        async with self.Session() as db:
            work=await db.get(CatalogueEntity,self.base)
            work.attributes={**work.attributes,'igdb_id':'10','selected_artwork':'preserve'}
            db.add_all([CatalogueIdentity(entity_id=self.base,namespace='igdb.game',external_id='10',source='igdb'),
                CatalogueIdentity(entity_id=self.child,namespace='igdb.game',external_id='20',source='igdb')])
            db.add(GameEntry(user_id=self.user.id,work_id=self.base,status='completed',score=9,total_seconds=720000,completion_seconds=360000))
            await view(db,work,enqueue=True);await db.commit()
        self.source=SimpleNamespace(igdb=AsyncMock(return_value=[{'id':1,'game_id':10,'hastily':3600,'normally':7200,'completely':10800,'count':25}]))
        self.service=GameTimeToBeat(self.Session,source=self.source)

    cleanup=fixture.GamesDatabaseTests.cleanup

    async def due(self):
        async with self.Session() as db:
            job=await db.scalar(snapshot(JOB,self.base));job.expires_at=now()-timedelta(seconds=1)
            job.next_attempt_at=now()-timedelta(seconds=1);await db.commit()

    async def test_cached_ingestion_is_idempotent_and_preserves_personal_facts_and_artwork(self):
        self.assertEqual(await self.service.run(self.base),'ready')
        self.assertEqual(await self.service.run(self.base),'cached');self.source.igdb.assert_awaited_once()
        response=await self.client.get(f'/tracking/games/works/{self.base}/time-to-beat')
        self.assertEqual(response.status_code,200,response.text)
        result=response.json();self.assertTrue(result['status']['complete'])
        self.assertEqual(result['estimates']['main_sides_seconds'],7200)
        async with self.Session() as db:
            entry=await db.scalar(select(GameEntry).where(GameEntry.work_id==self.base))
            self.assertEqual((entry.status,entry.score,entry.total_seconds,entry.completion_seconds),('completed',9,720000,360000))
            work=await db.get(CatalogueEntity,self.base)
            self.assertEqual(work.attributes['selected_artwork'],'preserve')
            self.assertEqual(work.field_sources['attributes.completion_estimates'],'igdb')
            self.assertEqual(await db.scalar(select(func.count()).select_from(MetadataSnapshot).where(MetadataSnapshot.kind==COMPONENT)),1)

    async def test_concurrent_consumers_share_fetch_and_provider_failures_keep_last_usable_data(self):
        self.assertEqual(await asyncio.gather(self.service.run(self.base),self.service.run(self.base)),['ready','ready'])
        self.source.igdb.assert_awaited_once();await self.due()
        self.source.igdb.side_effect=ProviderError('unavailable')
        self.assertEqual(await self.service.run(self.base),'retry')
        async with self.Session() as db:
            result=await view(db,await db.get(CatalogueEntity,self.base),enqueue=True)
        self.assertEqual(result['estimates']['main_story_seconds'],3600)
        self.assertFalse(result['status']['complete']);self.assertEqual(result['status']['error'],'unavailable')

    async def test_empty_and_partial_values_are_not_zero_and_empty_refresh_retires_old_estimates(self):
        await self.service.run(self.base);await self.due();self.source.igdb.return_value=[]
        self.assertEqual(await self.service.run(self.base),'ready')
        async with self.Session() as db:
            result=await view(db,await db.get(CatalogueEntity,self.base))
            self.assertTrue(result['status']['complete'])
            self.assertTrue(all(result['estimates'][key] is None for key in ('main_story_seconds','main_sides_seconds','completionist_seconds')))
        result=normalized([{'id':2,'game_id':10,'hastily':0,'normally':9000}], '10')
        self.assertIsNone(result['main_story_seconds']);self.assertIsNone(result['completionist_seconds'])
        self.assertEqual(result['main_sides_seconds'],9000)

    async def test_expansion_uses_its_own_source_and_conflicting_or_changed_identity_is_not_borrowed(self):
        await self.service.run(self.base)
        async with self.Session() as db:
            await view(db,await db.get(CatalogueEntity,self.child),enqueue=True);await db.commit()
        self.source.igdb.return_value=[{'id':2,'game_id':20,'normally':18000}]
        self.assertEqual(await self.service.run(self.child),'ready')
        self.assertIn('game_id=20',self.source.igdb.call_args.args[1])
        async with self.Session() as db:
            work=await db.get(CatalogueEntity,self.base);work.attributes={**work.attributes,'igdb_id':'30'}
            db.add(CatalogueIdentity(entity_id=self.base,namespace='igdb.game',external_id='30',source='reviewed'))
            await db.commit();result=await view(db,work,enqueue=True);await db.commit()
            self.assertIsNone(result['estimates'])
            self.assertTrue(result['status']['pending']);self.assertFalse(result['status']['complete'])
        self.source.igdb.return_value=[{'id':3,'game_id':30,'normally':14400}]
        self.assertEqual(await self.service.run(self.base),'ready')
        self.assertIn('game_id=30',self.source.igdb.call_args.args[1])

    async def test_mismatched_source_or_bad_units_preserve_cache_and_protected_fields(self):
        await self.service.run(self.base);await self.due()
        self.source.igdb.return_value=[{'id':1,'game_id':999,'normally':3}]
        self.assertEqual(await self.service.run(self.base),'retry')
        for value in (-1,True,'3600'):
            with self.assertRaises(ProviderError):normalized([{'id':1,'game_id':10,'normally':value}],'10')
        with self.assertRaises(ProviderError):normalized([{'id':1,'game_id':True,'normally':3600}],'1')
        async with self.Session() as db:
            work=await db.get(CatalogueEntity,self.base)
            work.protected_fields=['attributes.completion_estimates'];await db.commit()
        await self.due();self.source.igdb.return_value=[{'id':2,'game_id':10,'normally':36000}]
        await self.service.run(self.base)
        async with self.Session() as db:
            work=await db.get(CatalogueEntity,self.base)
            self.assertEqual(work.attributes['completion_estimates']['main_sides_seconds'],7200)

    async def test_new_route_denies_unapproved_and_anonymous_readers_without_fetching(self):
        self.viewer.games_access=False
        self.assertEqual((await self.client.get(f'/tracking/games/works/{self.base}/time-to-beat')).status_code,403)
        self.viewer=None
        self.assertIn((await self.client.get(f'/tracking/games/works/{self.base}/time-to-beat')).status_code,(401,403))
        self.source.igdb.assert_not_awaited()

    async def test_missing_or_conflicting_identity_is_unavailable_and_cancellation_releases_lease(self):
        async with self.Session() as db:
            work=await db.get(CatalogueEntity,self.unknown)
            self.assertEqual((await view(db,work,enqueue=True))['status']['state'],'unavailable')
            db.add_all([CatalogueIdentity(entity_id=work.id,namespace='igdb.game',external_id=str(native),source='igdb') for native in (31,32)])
            await db.commit()
            self.assertEqual((await view(db,work,enqueue=True))['status']['state'],'unavailable')
        started=asyncio.Event()
        async def slow(*args):started.set();await asyncio.Future()
        self.source.igdb.side_effect=slow
        task=asyncio.create_task(self.service._run(self.base))
        async with asyncio.timeout(5):await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        async with self.Session() as db:
            job=await db.scalar(snapshot(JOB,self.base));self.assertIsNone(job.lease_token)
            self.assertIsNotNone(job.next_attempt_at)
        self.source.igdb.side_effect=None
        self.assertEqual(await self.service.run(self.base),'ready')

    async def test_auxiliary_estimate_snapshots_do_not_displace_usable_detail_sources(self):
        from core.media_details import work_details
        async with self.Session() as db:
            db.add_all([MetadataSnapshot(provider='igdb',kind=f'provider_detail_{i}',external_id=str(i),entity_id=self.base,
                payload={'url':f'https://example.test/game/{i}'},fetched_at=now()-timedelta(days=i+1)) for i in range(6)])
            await db.commit()
        await self.service.run(self.base)
        async with self.Session() as db:
            detail=await work_details(db,await db.get(CatalogueEntity,self.base))
            self.assertEqual({row['url'] for row in detail['sources']},{f'https://example.test/game/{i}' for i in range(6)})
