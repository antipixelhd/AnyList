"""Approved regional offers, durable shared refresh and country isolation."""
import asyncio
import unittest
from datetime import timedelta
from unittest.mock import AsyncMock,patch

from sqlalchemy import select,delete,func,event

import test_games_pg as fixture
from core import game_offers as offers
from core.catalogue_providers import ProviderError
from core.config import settings
from models import UserProfileData
from models.catalogue import CatalogueEntity,CatalogueIdentity,MetadataSnapshot,SteamPriceSnapshot
from models.games import GameEntry


def source_price(current=True):
    return {'itad_id':'018d937f-1212-7232-b23f-a046f6fd4a57','current':{'currency':'EUR','amountInt':549} if current else None,
        'historical_low':{'currency':'EUR','amountInt':199},'historical_low_at':'2024-06-27',
        'url':'https://store.steampowered.com/app/1245620/','payload':{'provider':'SOURCE_SENTINEL '*1000}}


@unittest.skipUnless(fixture.URL,'Requires explicit disposable Games PostgreSQL')
class GameOffersDatabaseTests(unittest.IsolatedAsyncioTestCase):
    cleanup=fixture.GamesDatabaseTests.cleanup
    async def asyncSetUp(self):
        await fixture.GamesDatabaseTests.asyncSetUp(self)
        self.key=patch.object(settings,'itad_api_key','fixture-itad-key');self.key.start();self.addCleanup(self.key.stop)
        async with self.Session() as db:
            for country,currency,amount in [('DE','EUR',799),('US','USD',999),('GB','GBP',899)]:
                db.add(SteamPriceSnapshot(work_id=self.base,steam_app_id='1245620',country=country,
                    current={'currency':currency,'amountInt':amount},historical_low={'currency':currency,'amountInt':299},
                    historical_low_at='2024-01-01',url='https://store.steampowered.com/app/1245620/',
                    payload={'raw':'PRICE_SENTINEL '*5000}))
                db.add(MetadataSnapshot(provider='itad',kind='price',external_id=f'1245620:{country}',entity_id=self.base,
                    fetched_at=offers.now(),expires_at=offers.now()+timedelta(hours=6),payload={'raw':'JOB_SENTINEL '*5000}))
            await db.commit()

    async def request(self,hint=None,work_id=None):
        return await self.client.get(f'/tracking/games/works/{work_id or self.base}/offers',params={'country':hint} if hint else {})

    async def set_country(self,value,user_id=None):
        async with self.Session() as db:
            profile=await db.scalar(select(UserProfileData).where(UserProfileData.user_id==(user_id or self.user.id)))
            profile.country=value;await db.commit()

    async def expire(self,country='DE'):
        async with self.Session() as db:
            row=await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.provider=='itad',MetadataSnapshot.external_id==f'1245620:{country}'))
            row.expires_at=offers.now()-timedelta(seconds=1);row.next_attempt_at=None;await db.commit()

    async def test_account_wins_hint_and_region_does_not_modify_saved_country(self):
        await self.set_country('DE')
        response=await self.request('US');self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.headers['cache-control'],'private, no-store')
        data=response.json();self.assertEqual((data['country'],data['country_source']),('DE','account'))
        self.assertEqual([(r['country'],r['current']['currency']) for r in data['offers']],[('DE','EUR')])
        self.assertNotIn('SENTINEL',response.text)
        await self.set_country(None)
        data=(await self.request('GB')).json();self.assertEqual((data['country'],data['country_source']),('GB','device'))
        async with self.Session() as db:
            self.assertIsNone(await db.scalar(select(UserProfileData.country).where(UserProfileData.user_id==self.user.id)))
        data=(await self.request()).json();self.assertEqual((data['country'],data['country_source']),('US','fallback'))
        self.assertEqual(data['offers'][0]['current']['currency'],'USD')

    async def test_viewers_share_public_cache_but_never_each_others_country(self):
        await self.set_country('DE');await self.set_country('GB',self.other.id)
        self.assertEqual((await self.request('US')).json()['country'],'DE')
        self.viewer=self.other
        self.assertEqual((await self.request('DE')).json()['offers'][0]['country'],'GB')
        self.viewer=self.user
        self.assertEqual((await self.request('GB')).json()['offers'][0]['country'],'DE')

    async def test_detail_projection_ignores_more_recent_foreign_rows_and_preserves_personal_state(self):
        await self.set_country('DE')
        async with self.Session() as db:
            db.add(GameEntry(user_id=self.user.id,work_id=self.base,status='completed',score=9,total_seconds=198000,
                notes='Personal note stays private'))
            de=await db.scalar(select(SteamPriceSnapshot).where(SteamPriceSnapshot.country=='DE'));de.fetched_at=offers.now()-timedelta(days=10)
            for country in ['AT','CH','CA','NZ','AU','FR','JP','KR','BR']:
                db.add(SteamPriceSnapshot(work_id=self.base,steam_app_id='1245620',country=country,
                    current={'currency':'USD','amountInt':1234},payload={}))
            await db.commit()
        response=await self.client.get(f'/catalogue/entities/{self.base}/details?country=US')
        self.assertEqual(response.status_code,200,response.text)
        data=response.json();self.assertEqual([r['country'] for r in data['prices']],['DE'])
        self.assertEqual(data['game_offer_context']['country_source'],'account')
        self.assertEqual(data['subject'],{'kind':'game','catalogue_id':self.base})
        self.assertEqual((data['entry']['status'],data['entry']['score'],data['entry']['total_seconds']),('completed',9,198000))
        self.assertNotIn('SENTINEL',response.text)

    async def test_missing_device_hint_does_not_queue_a_fallback_country_before_browser_detection(self):
        async with self.Session() as db:
            game=CatalogueEntity(kind='game',name='First visit',attributes={'game_class':'game'});db.add(game);await db.flush();game_id=game.id
            db.add(CatalogueIdentity(entity_id=game.id,namespace='steam.app',external_id='99998',source='igdb'));await db.commit()
        data=(await self.request(work_id=game_id)).json()
        self.assertEqual((data['country'],data['country_source'],data['status']['pending']),('US','fallback',False))
        async with self.Session() as db:
            self.assertIsNone(await db.scalar(select(MetadataSnapshot.id).where(MetadataSnapshot.external_id=='99998:US')))
        self.assertTrue((await self.request('DE',game_id)).json()['status']['pending'])
        async with self.Session() as db:
            self.assertIsNotNone(await db.scalar(select(MetadataSnapshot.id).where(MetadataSnapshot.external_id=='99998:DE')))

    async def test_warm_reads_only_project_offer_fields_and_do_no_writes(self):
        await self.set_country('DE');statements=[]
        def record(conn,cursor,sql,params,context,many):statements.append(sql)
        event.listen(self.engine.sync_engine,'before_cursor_execute',record)
        try:response=await self.request('US')
        finally:event.remove(self.engine.sync_engine,'before_cursor_execute',record)
        self.assertEqual(response.status_code,200,response.text)
        self.assertFalse(any(sql.lstrip().upper().startswith(('INSERT','UPDATE','DELETE')) for sql in statements))
        self.assertFalse(any('catalogue_steam_prices.payload' in sql or 'catalogue_metadata_snapshots.payload' in sql for sql in statements))

    async def test_demand_admission_and_concurrent_workers_share_existing_price_lease(self):
        await self.set_country('DE');await self.expire()
        replies=await asyncio.gather(*(self.request('US') for _ in range(5)))
        self.assertTrue(all(reply.json()['status']['pending'] for reply in replies))
        entered,release=asyncio.Event(),asyncio.Event()
        async def fetch(app,country):entered.set();await release.wait();return source_price()
        client=AsyncMock();client.steam_prices.side_effect=fetch
        first=asyncio.create_task(offers.run_due(self.Session,client=client,limit=1))
        await asyncio.wait_for(entered.wait(),5)
        self.assertEqual(await offers.run_due(self.Session,client=client,limit=1),0)
        release.set();self.assertEqual(await first,1);client.steam_prices.assert_awaited_once_with('1245620','DE')
        data=(await self.request('US')).json();self.assertFalse(data['status']['pending'])
        self.assertEqual(data['offers'][0]['current']['amountInt'],549)
        async with self.Session() as db:
            count=await db.scalar(select(func.count()).select_from(MetadataSnapshot).where(MetadataSnapshot.provider=='itad',MetadataSnapshot.external_id=='1245620:DE'))
            self.assertEqual(count,1)

    async def test_committed_admission_wakes_the_price_worker(self):
        await self.set_country('DE');await self.expire()
        wake=asyncio.Event()
        with patch.object(offers,'_wake',wake):
            self.assertTrue((await self.request()).json()['status']['pending'])
            self.assertTrue(wake.is_set())
            async with self.Session() as db:
                row=await offers.price_header(db,'1245620:DE')
                self.assertIsNotNone(row.next_attempt_at)

    async def test_two_regions_refresh_concurrently_with_bounded_shared_leases(self):
        await self.set_country(None)
        for country in ('DE','GB'):
            await self.expire(country);await self.request(country)
        entered,release=asyncio.Event(),asyncio.Event();countries=[]
        async def fetch(app,country):
            countries.append(country)
            if len(countries)==2:entered.set()
            await release.wait();return source_price()
        client=AsyncMock();client.steam_prices.side_effect=fetch
        task=asyncio.create_task(offers.run_due(self.Session,client=client,limit=2))
        try:
            await asyncio.wait_for(entered.wait(),5)
            self.assertCountEqual(countries,['DE','GB'])
        finally:release.set();await task
        self.assertEqual(task.result(),2)

    async def test_failure_preserves_current_and_low_with_bounded_retry(self):
        await self.set_country('DE');await self.expire();await self.request()
        client=AsyncMock();client.steam_prices.side_effect=ProviderError('timeout')
        self.assertEqual(await offers.run_due(self.Session,client=client),0)
        data=(await self.request('US')).json();self.assertEqual(data['offers'][0]['current']['amountInt'],799)
        self.assertEqual(data['offers'][0]['historical_low']['amountInt'],299)
        self.assertTrue(data['status']['pending']);self.assertGreater(data['status']['retry_after'],60)
        self.assertEqual(await offers.run_due(self.Session,client=client),0);self.assertEqual(client.steam_prices.await_count,1)

    async def test_cancellation_releases_lease_and_successful_absence_is_not_zero(self):
        await self.set_country('DE');await self.expire();await self.request()
        client=AsyncMock();client.steam_prices.side_effect=asyncio.CancelledError
        with self.assertRaises(asyncio.CancelledError):await offers.run_due(self.Session,client=client)
        async with self.Session() as db:
            state=await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.external_id=='1245620:DE'))
            self.assertIsNone(state.lease_token);state.next_attempt_at=offers.now();await db.commit()
        client.steam_prices.side_effect=None;client.steam_prices.return_value=source_price(False)
        self.assertEqual(await offers.run_due(self.Session,client=client),1)
        data=(await self.request()).json();self.assertIsNone(data['offers'][0]['current'])
        self.assertEqual(data['offers'][0]['historical_low']['amountInt'],199)

    async def test_no_key_no_app_ambiguous_app_and_denied_users_never_trigger_provider_work(self):
        with patch.object(settings,'itad_api_key',None):
            self.assertFalse((await self.request('DE')).json()['status']['pending'])
            self.assertEqual(await offers.run_due(self.Session),0)
        self.assertEqual((await self.request('DE',self.unknown)).json()['offers'],[])
        async with self.Session() as db:
            db.add(CatalogueIdentity(entity_id=self.base,namespace='steam.app',external_id='999999',source='igdb'));await db.commit()
        self.assertIsNone((await self.request('DE')).json()['steam_app_id'])
        self.viewer=self.other;self.other.games_access=False
        self.assertEqual((await self.request('DE')).status_code,403)
        self.viewer=None;self.assertIn((await self.request('DE')).status_code,(401,403))

    async def test_legacy_refresh_uses_account_country_and_rejects_invalid_iso_codes(self):
        await self.set_country('DE');await self.expire()
        client=AsyncMock();client.steam_prices.return_value=source_price()
        with patch('routers.catalogue.providers',AsyncMock(return_value=client)):
            response=await self.client.post(f'/catalogue/entities/{self.base}/steam-prices/1245620?country=US')
        self.assertEqual(response.status_code,200,response.text);client.steam_prices.assert_awaited_once_with('1245620','DE')
        self.assertEqual((await self.request('ZZ')).status_code,422)

    async def test_identity_changes_during_fetch_cannot_publish_under_the_old_game(self):
        async with self.Session() as db:
            game=CatalogueEntity(kind='game',name='Identity race',attributes={'game_class':'game'});db.add(game);await db.flush();game_id=game.id
            db.add(CatalogueIdentity(entity_id=game.id,namespace='steam.app',external_id='99999',source='igdb'));await db.commit()
        await self.request('DE',game_id)
        async def changed(app,country):
            async with self.Session() as db:
                await db.execute(delete(CatalogueIdentity).where(CatalogueIdentity.external_id=='99999'))
                db.add(CatalogueIdentity(entity_id=self.unknown,namespace='steam.app',external_id='99999',source='reviewed'));await db.commit()
            return source_price()
        client=AsyncMock();client.steam_prices.side_effect=changed
        self.assertEqual(await offers.run_due(self.Session,client=client),0)
        async with self.Session() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(SteamPriceSnapshot).where(SteamPriceSnapshot.work_id==game_id)),0)

class OfferSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_demand_wakes_an_idle_worker_without_the_five_second_tick(self):
        wake,first,second=asyncio.Event(),asyncio.Event(),asyncio.Event()
        calls=0
        async def run_due(factory):
            nonlocal calls
            calls+=1
            (first if calls==1 else second).set()
            return 0
        with patch.object(offers,'_wake',wake),patch.object(offers,'run_due',run_due):
            task=asyncio.create_task(offers.scheduler())
            try:
                await asyncio.wait_for(first.wait(),1)
                offers.notify_refresh({'status':{'state':'pending','pending':True}})
                await asyncio.wait_for(second.wait(),1)
                self.assertEqual(calls,2)
            finally:
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):await task
