"""Real durable rating jobs, partial component caches and approved API access."""
import asyncio
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import select,delete,func,event

import test_games_pg as fixture
from core.game_ratings import GameRatings,JOB,PARTS,snapshot,view,_flights
from core.game_imdb_enrichment import now
from core.catalogue_providers import ProviderError
from models.catalogue import CatalogueEntity,CatalogueIdentity,MetadataSnapshot
from models.games import GameEntry,GameAchievement


def component(part):
    if part=='critic':return {'score':94,'url':'https://www.metacritic.com/game/pc/fixture/','platform':'PC','source':'steam_store'}
    return {'positive':90 if part=='all_time' else 70,'negative':10 if part=='all_time' else 30,'total':100,
        'percentage':90 if part=='all_time' else 70,'description':'Positive','language':'english','purchase_type':'steam',
        'offtopic_filtered':True,'window_start':None if part=='all_time' else 1790120000,'window_end':None if part=='all_time' else 1792712000}


@unittest.skipUnless(fixture.URL,'Requires explicit disposable Games PostgreSQL')
class GameRatingsDatabaseTests(unittest.IsolatedAsyncioTestCase):
    cleanup=fixture.GamesDatabaseTests.cleanup
    async def asyncSetUp(self):
        await fixture.GamesDatabaseTests.asyncSetUp(self)
        async with self.Session() as db:
            work=await db.get(CatalogueEntity,self.base);work.attributes={**work.attributes,'selected_artwork':'preserve'}
            db.add(GameEntry(user_id=self.user.id,work_id=self.base,status='completed',score=9,total_seconds=198000,completion_seconds=144000,notes='Private note'))
            await view(db,work,enqueue=True);await db.commit()
        async def fetch(app,part,stamp):return component(part)
        self.source=SimpleNamespace(fetch=AsyncMock(side_effect=fetch));self.service=GameRatings(self.Session,self.source)

    async def due(self,*parts):
        async with self.Session() as db:
            job=await db.scalar(snapshot(JOB,self.base));job.expires_at=now()-timedelta(seconds=1);job.next_attempt_at=now()-timedelta(seconds=1)
            for part in parts:
                row=await db.scalar(snapshot(f'game_rating_{part}',self.base));row.expires_at=now()-timedelta(seconds=1)
            await db.commit()

    async def request(self,work=None):return await self.client.get(f'/tracking/games/works/{work or self.base}/ratings')

    async def test_complete_idempotent_and_personal_artwork_and_game_structure_are_preserved(self):
        self.assertEqual(await self.service.run(self.base),'ready');self.assertEqual(await self.service.run(self.base),'cached')
        self.assertEqual(self.source.fetch.await_count,3)
        response=await self.request();self.assertEqual(response.status_code,200,response.text)
        data=response.json();self.assertTrue(data['status']['complete']);self.assertEqual(data['ratings']['all_time']['percentage'],90)
        self.assertEqual(data['ratings']['recent']['percentage'],70);self.assertEqual(data['ratings']['critic']['score'],94)
        self.assertEqual(response.headers['cache-control'],'private, no-store')
        async with self.Session() as db:
            entry=await db.scalar(select(GameEntry).where(GameEntry.work_id==self.base));work=await db.get(CatalogueEntity,self.base)
            self.assertEqual((entry.status,entry.score,entry.total_seconds,entry.completion_seconds),('completed',9,198000,144000))
            self.assertEqual(entry.notes,'Private note');self.assertEqual(work.attributes['selected_artwork'],'preserve')
            self.assertEqual(work.field_sources['attributes.game_ratings'],'steam')
            self.assertEqual(await db.scalar(select(func.count()).select_from(MetadataSnapshot).where(MetadataSnapshot.kind.in_([f'game_rating_{part}' for part in PARTS]))),3)

    async def test_failed_component_retries_independently_and_does_not_claim_complete(self):
        async def partial(app,part,stamp):
            if part=='recent':raise ProviderError('rate_limited',120)
            return component(part)
        self.source.fetch.side_effect=partial;self.assertEqual(await self.service.run(self.base),'partial')
        data=(await self.request()).json();self.assertFalse(data['status']['complete']);self.assertTrue(data['status']['pending'])
        self.assertEqual(data['ratings']['critic']['score'],94);self.assertNotIn('recent',data['ratings'])
        self.assertGreaterEqual(data['status']['retry_after'],100)
        await self.due();self.source.fetch.reset_mock();self.source.fetch.side_effect=lambda app,part,stamp:component(part)
        self.assertEqual(await self.service.run(self.base),'ready');self.source.fetch.assert_awaited_once()
        self.assertEqual(self.source.fetch.call_args.args[1],'recent')

    async def test_refresh_failure_keeps_usable_data_and_successful_empty_retires_only_that_component(self):
        await self.service.run(self.base);await self.due('recent')
        self.source.fetch.side_effect=ProviderError('timeout');self.assertEqual(await self.service.run(self.base),'partial')
        self.assertEqual((await self.request()).json()['ratings']['recent']['percentage'],70)
        await self.due('recent');self.source.fetch.side_effect=lambda app,part,stamp:{**component(part),'positive':0,'negative':0,'total':0,'percentage':None}
        self.assertEqual(await self.service.run(self.base),'ready');data=(await self.request()).json()['ratings']
        self.assertIsNone(data['recent']['percentage']);self.assertEqual(data['all_time']['percentage'],90);self.assertEqual(data['critic']['score'],94)

    async def test_concurrent_callers_share_fetch_and_a_cancelled_subscriber_cannot_cancel_shared_work(self):
        entered,release=asyncio.Event(),asyncio.Event()
        async def slow(app,part,stamp):entered.set();await release.wait();return component(part)
        self.source.fetch.side_effect=slow
        first=asyncio.create_task(self.service.run(self.base));second=asyncio.create_task(self.service.run(self.base))
        await asyncio.wait_for(entered.wait(),5);first.cancel()
        with self.assertRaises(asyncio.CancelledError):await first
        release.set();self.assertEqual(await second,'ready');self.assertEqual(self.source.fetch.await_count,3)

    async def test_worker_cancellation_releases_lease_and_retains_published_components(self):
        await self.service.run(self.base);await self.due('recent')
        entered=asyncio.Event()
        async def wait(app,part,stamp):entered.set();await asyncio.Event().wait()
        self.source.fetch.side_effect=wait
        call=asyncio.create_task(self.service.run(self.base));await asyncio.wait_for(entered.wait(),5)
        _flights[(id(self.Session),JOB,self.base)].cancel()
        with self.assertRaises(asyncio.CancelledError):await call
        async with self.Session() as db:
            job=await db.scalar(snapshot(JOB,self.base));self.assertIsNone(job.lease_token);self.assertIsNotNone(job.next_attempt_at)
            self.assertEqual((await db.get(CatalogueEntity,self.base)).attributes['game_ratings']['recent']['percentage'],70)

    async def test_expansion_uses_own_app_and_missing_ambiguous_or_changed_apps_never_borrow_base_ratings(self):
        await self.service.run(self.base)
        self.assertIsNone((await self.request(self.child)).json()['ratings'])
        async with self.Session() as db:
            db.add(CatalogueIdentity(entity_id=self.child,namespace='steam.app',external_id='42',source='igdb'));await db.commit()
        await self.request(self.child);self.assertEqual(await self.service.run(self.child),'ready')
        self.assertEqual(self.source.fetch.call_args.args[0],'42')
        async with self.Session() as db:
            db.add(CatalogueIdentity(entity_id=self.base,namespace='steam.app',external_id='43',source='igdb'));await db.commit()
        self.assertIsNone((await self.request()).json()['ratings'])
        async with self.Session() as db:
            await db.execute(delete(GameAchievement).where(GameAchievement.work_id==self.base))
            await db.execute(delete(CatalogueIdentity).where(CatalogueIdentity.entity_id==self.base,CatalogueIdentity.external_id=='1245620'));await db.commit()
        self.assertIsNone((await self.request()).json()['ratings'])
        self.assertEqual(await self.service.run(self.base),'ready');self.assertEqual(self.source.fetch.call_args.args[0],'43')

    async def test_identity_changes_during_fetch_do_not_publish_wrong_game_values(self):
        async def changed(app,part,stamp):
            async with self.Session() as db:
                await db.execute(delete(GameAchievement).where(GameAchievement.work_id==self.base))
                await db.execute(delete(CatalogueIdentity).where(CatalogueIdentity.entity_id==self.base,CatalogueIdentity.external_id==app));await db.commit()
            return component(part)
        self.source.fetch.side_effect=changed;self.assertEqual(await self.service.run(self.base),'retry')
        async with self.Session() as db:self.assertNotIn('game_ratings',(await db.get(CatalogueEntity,self.base)).attributes)

    async def test_warm_reads_do_not_write_or_hydrate_raw_components_and_denials_do_not_expose_cache(self):
        await self.service.run(self.base)
        async with self.Session() as db:
            job=await db.scalar(snapshot(JOB,self.base));job.payload={'raw':'REVIEW_SENTINEL '*5000};await db.commit()
        statements=[]
        def record(conn,cursor,sql,params,context,many):statements.append(sql)
        event.listen(self.engine.sync_engine,'before_cursor_execute',record)
        try:response=await self.request()
        finally:event.remove(self.engine.sync_engine,'before_cursor_execute',record)
        self.assertEqual(response.status_code,200);self.assertNotIn('SENTINEL',response.text)
        self.assertFalse(any(sql.lstrip().upper().startswith(('INSERT','UPDATE','DELETE')) for sql in statements))
        self.assertFalse(any('catalogue_metadata_snapshots.payload' in sql for sql in statements))
        self.viewer=self.other;self.other.games_access=False;self.assertEqual((await self.request()).status_code,403)
        self.viewer=None;self.assertIn((await self.request()).status_code,(401,403))

    async def test_protected_richer_values_are_not_overwritten_by_refresh(self):
        async with self.Session() as db:
            work=await db.get(CatalogueEntity,self.base)
            work.attributes={**work.attributes,'game_ratings':{'steam_app_id':'1245620','critic':{'score':99,'source':'manual'}}}
            work.protected_fields=['attributes.game_ratings'];await db.commit()
        self.assertEqual(await self.service.run(self.base),'ready')
        self.assertEqual((await self.request()).json()['ratings']['critic']['score'],99)

    async def test_no_steam_game_can_reuse_verified_rawg_critic_without_name_matching(self):
        async with self.Session() as db:
            work=await db.get(CatalogueEntity,self.unknown);work.attributes={**work.attributes,'metacritic':88}
            work.field_sources={'attributes.metacritic':'rawg'};await db.commit()
        self.assertIsNone((await self.request(self.unknown)).json()['ratings'])
        async with self.Session() as db:
            db.add(CatalogueIdentity(entity_id=self.unknown,namespace='rawg.game',external_id='42',source='reviewed'));await db.commit()
        data=(await self.request(self.unknown)).json();self.assertFalse(data['status']['pending'])
        self.assertIsNone(data['ratings']['steam_app_id']);self.assertEqual(data['ratings']['critic'],{'score':88,'url':None,'source':'rawg','platform':None})
        self.source.fetch.assert_not_awaited()
