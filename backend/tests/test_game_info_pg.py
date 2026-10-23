import asyncio
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import select,event
import test_games_pg as fixture
from core.game_info import GameInfo,JOB,view
from core.game_ratings import GameRatings,_flights
from core.game_imdb_enrichment import now,snapshot
from core.catalogue_providers import ProviderError
from models.catalogue import CatalogueEntity,CatalogueIdentity
from models.games import GameEntry
from models import UserProfileData


def info():return {'age_ratings':[{'board':'USK','region':'Germany','rating':'16','descriptors':[],'source':'steam'}],
    'requirements':{'minimum':[{'label':'Memory','value':'12 GB RAM'}],'recommended':[],'platform':'PC','source':'steam'}}


@unittest.skipUnless(fixture.URL,'Requires explicit disposable Games PostgreSQL')
class GameInfoTests(unittest.IsolatedAsyncioTestCase):
    cleanup=fixture.GamesDatabaseTests.cleanup
    async def asyncSetUp(self):
        await fixture.GamesDatabaseTests.asyncSetUp(self)
        async with self.Session() as db:
            work=await db.get(CatalogueEntity,self.base);work.attributes={**work.attributes,'selected_artwork':'keep'}
            db.add(GameEntry(user_id=self.user.id,work_id=self.base,status='completed',score=9,total_seconds=12345,notes='private'))
            await view(db,work,enqueue=True);await db.commit()
        self.source=SimpleNamespace(fetch=AsyncMock(return_value=info()));self.service=GameInfo(self.Session,self.source)

    async def request(self,work=None,**params):return await self.client.get(f'/tracking/games/works/{work or self.base}/info',params=params)

    async def due(self):
        async with self.Session() as db:
            job=await db.scalar(snapshot(JOB,self.base,provider='steam'));job.next_attempt_at=now()-timedelta(seconds=1)
            record=await db.scalar(snapshot('game_store_info',self.base,provider='steam'));record.expires_at=now()-timedelta(seconds=1);await db.commit()

    async def test_idempotent_shared_cache_and_private_safe_warm_reads(self):
        self.assertEqual(await self.service.run(self.base),'ready');self.assertEqual(await self.service.run(self.base),'cached');self.source.fetch.assert_awaited_once()
        statements=[]
        def capture(conn,cursor,sql,params,context,many):statements.append(sql)
        event.listen(self.engine.sync_engine,'before_cursor_execute',capture)
        try:response=await self.request(country='DE')
        finally:event.remove(self.engine.sync_engine,'before_cursor_execute',capture)
        data=response.json();self.assertEqual(response.status_code,200,response.text);self.assertTrue(data['status']['complete'])
        self.assertEqual(response.headers['cache-control'],'private, no-store')
        self.assertEqual((data['country'],data['country_source']),('DE','device'));self.assertNotIn('private',response.text)
        self.assertFalse(any(sql.lstrip().upper().startswith(('INSERT','UPDATE','DELETE')) for sql in statements))
        self.assertFalse(any('catalogue_metadata_snapshots.payload' in sql for sql in statements))
        async with self.Session() as db:
            work=await db.get(CatalogueEntity,self.base);entry=await db.scalar(select(GameEntry).where(GameEntry.work_id==self.base))
            self.assertEqual(work.attributes['selected_artwork'],'keep');self.assertEqual((entry.status,entry.score,entry.total_seconds),('completed',9,12345))
        self.viewer=self.other;self.other.games_access=False;self.assertEqual((await self.request()).status_code,403)
        self.viewer=None;self.assertIn((await self.request()).status_code,(401,403))

    async def test_failed_refresh_keeps_cached_content_and_successful_empty_retires_it(self):
        await self.service.run(self.base);await self.due();self.source.fetch.side_effect=ProviderError('rate_limited',100)
        self.assertEqual(await self.service.run(self.base),'partial');data=(await self.request()).json()
        self.assertEqual(data['info']['requirements']['minimum'][0]['value'],'12 GB RAM');self.assertFalse(data['status']['complete'])
        await self.due();self.source.fetch.side_effect=None;self.source.fetch.return_value={'age_ratings':[],'requirements':None}
        self.assertEqual(await self.service.run(self.base),'ready');self.assertIsNone((await self.request()).json()['info']['requirements'])

    async def test_separate_job_flights_and_cancellation_do_not_alias_rating_work(self):
        entered,release=asyncio.Event(),asyncio.Event()
        async def slow(app,part,stamp):entered.set();await release.wait();return info()
        self.source.fetch.side_effect=slow
        call=asyncio.create_task(self.service.run(self.base));await asyncio.wait_for(entered.wait(),5)
        async with self.Session() as db:
            from core.game_ratings import view as rating_view
            await rating_view(db,await db.get(CatalogueEntity,self.base),enqueue=True);await db.commit()
        ratings=SimpleNamespace(fetch=AsyncMock(return_value={'score':94}))
        self.assertEqual(await GameRatings(self.Session,ratings).run(self.base),'ready');self.assertEqual(ratings.fetch.await_count,3)
        _flights[(id(self.Session),JOB,self.base)].cancel()
        with self.assertRaises(asyncio.CancelledError):await call
        async with self.Session() as db:self.assertIsNone((await db.scalar(snapshot(JOB,self.base,provider='steam'))).lease_until)

    async def test_expansion_and_ambiguous_app_do_not_borrow_base_requirements(self):
        await self.service.run(self.base);self.assertIsNone((await self.request(self.child)).json()['info'])
        async with self.Session() as db:
            db.add(CatalogueIdentity(entity_id=self.base,namespace='steam.app',external_id='43',source='igdb'));await db.commit()
        self.assertIsNone((await self.request()).json()['info'])

    async def test_non_steam_game_needs_verified_igdb_identity_and_age_field_provenance(self):
        async with self.Session() as db:
            work=await db.get(CatalogueEntity,self.unknown);work.attributes={**work.attributes,'age_ratings':[{'board':'PEGI','region':'Europe','rating':'18','source':'igdb'}]}
            work.field_sources={'attributes.age_ratings':'igdb'};await db.commit()
        self.assertIsNone((await self.request(self.unknown)).json()['info'])
        async with self.Session() as db:
            db.add(CatalogueIdentity(entity_id=self.unknown,namespace='igdb.game',external_id='123',source='igdb'));await db.commit()
        data=(await self.request(self.unknown)).json();self.assertEqual(data['info']['age_ratings'][0]['rating'],'18')
        self.assertIsNone(data['info']['steam_app_id']);self.assertFalse(data['status']['pending']);self.source.fetch.assert_not_awaited()
        await self.service.run(self.base)
        async with self.Session() as db:
            work=await db.get(CatalogueEntity,self.base)
            work.attributes={**work.attributes,'age_ratings':[{'board':'USK','region':'Germany','rating':'18','source':'igdb'}]}
            work.field_sources={**work.field_sources,'attributes.age_ratings':'igdb'}
            db.add(CatalogueIdentity(entity_id=self.base,namespace='igdb.game',external_id='119133',source='igdb'));await db.commit()
        data=(await self.request()).json()['info'];self.assertEqual(data['age_ratings'][0]['rating'],'18')
        self.assertEqual(data['age_ratings'][0]['source'],'igdb')
        async with self.Session() as db:self.assertEqual((await db.get(CatalogueEntity,self.base)).attributes['game_info']['age_ratings'][0]['rating'],'16')

    async def test_account_country_wins_and_invalid_hints_are_rejected(self):
        await self.service.run(self.base)
        async with self.Session() as db:
            row=await db.scalar(select(UserProfileData).where(UserProfileData.user_id==self.user.id));row.country='US';await db.commit()
        data=(await self.request(country='DE')).json();self.assertEqual((data['country'],data['country_source']),('US','account'))
        self.assertEqual((await self.request(country='ZZ')).status_code,422)
