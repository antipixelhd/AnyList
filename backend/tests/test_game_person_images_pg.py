"""Optional portraits use real durable leases and never alter public people."""
import asyncio
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch

from sqlalchemy import event,select

import test_games_pg as fixture
from core.catalogue_providers import ProviderError
from core.game_imdb_enrichment import now
from core.game_person_images import GamePersonImages,query
from models.catalogue import CatalogueEntity,CatalogueIdentity

URL='https://m.media-amazon.com/images/M/person._V1_.jpg'


@unittest.skipUnless(fixture.URL,'Requires disposable Games PostgreSQL')
class PersonImageTests(unittest.IsolatedAsyncioTestCase):
    cleanup=fixture.GamesDatabaseTests.cleanup

    async def asyncSetUp(self):
        await fixture.GamesDatabaseTests.asyncSetUp(self)
        key=patch('core.settings_store.get_server_tmdb_key',return_value=None)
        key.start();self.addCleanup(key.stop)
        async with self.Session() as db:
            people=[CatalogueEntity(kind='person',name='Identical name',protected_fields=[]) for _ in range(4)]
            db.add_all(people);await db.flush();self.ids=[person.id for person in people]
            for i,person in enumerate(people):
                db.add(CatalogueIdentity(entity_id=person.id,namespace='imdb.person',external_id=f'nm{i+1}',source='imdb'))
            people[2].image_url='https://existing.example/photo.jpg'
            people[3].protected_fields=['image_url']
            await db.commit()
        self.source=SimpleNamespace(portraits=AsyncMock(side_effect=lambda ids:{native:
            {'image':URL if native!='nm2' else None,'width':500,'height':700} for native in ids}))
        self.service=GamePersonImages(self.Session,source=self.source)

    async def expire(self):
        async with self.Session() as db:
            rows=list(await db.scalars(query(['nm1','nm2'])))
            for row in rows:row.expires_at=now()-timedelta(seconds=1);row.next_attempt_at=None
            await db.commit()

    async def test_batch_cache_null_evidence_and_public_entity_preservation(self):
        result=await self.service.get(['nm1','nm2'])
        self.assertEqual(result,{'nm1':{'image':URL,'state':'ready'},'nm2':{'image':None,'state':'ready'}})
        self.assertEqual(await self.service.get(['nm2','nm1']),result)
        self.source.portraits.assert_awaited_once_with(['nm1','nm2'])
        async with self.Session() as db:
            people=list(await db.scalars(select(CatalogueEntity).where(CatalogueEntity.id.in_(self.ids[:2]))))
            self.assertTrue(all(person.image_url is None for person in people))
            rows={row.external_id:row for row in await db.scalars(query(['nm1','nm2']))}
            self.assertTrue(rows['nm2'].coverage['complete']);self.assertFalse(rows['nm2'].coverage['image_supplied'])

    async def test_concurrent_overlapping_batches_share_identity_and_caller_cancellation(self):
        entered,release=asyncio.Event(),asyncio.Event()
        async def source(ids):
            entered.set();await release.wait()
            return {native:{'image':URL,'width':500,'height':700} for native in ids}
        self.source.portraits.side_effect=source
        first=asyncio.create_task(self.service.get(['nm1','nm2']));await entered.wait()
        second=asyncio.create_task(self.service.get(['nm2']));await asyncio.sleep(.02)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):await first
        release.set();self.assertEqual((await second)['nm2']['image'],URL)
        self.source.portraits.assert_awaited_once()

    async def test_failure_cooldown_and_stale_success_preserve_usable_image(self):
        self.source.portraits.side_effect=ProviderError('not_entitled')
        result=await self.service.get(['nm1']);self.assertEqual(result['nm1']['state'],'retry')
        self.assertEqual(await self.service.get(['nm1']),result);self.source.portraits.assert_awaited_once()
        await self.expire()
        self.source.portraits.side_effect=None;self.source.portraits.return_value={'nm1':{'image':URL,'width':500,'height':700}}
        await self.service.get(['nm1']);await self.expire()
        self.source.portraits.side_effect=ProviderError('unavailable')
        self.assertEqual((await self.service.get(['nm1']))['nm1']['image'],URL)
        # Drain just this source identity's refresh to inspect persistent state.
        from core.game_person_images import _flights
        tasks=list(set(_flights.values()))
        if tasks:await asyncio.gather(*tasks)
        async with self.Session() as db:
            row=await db.scalar(query(['nm1']))
            self.assertEqual(row.payload['image'],URL);self.assertEqual(row.error_code,'unavailable')

    async def test_api_preserves_selected_images_and_denies_unapproved_even_with_warm_cache(self):
        await self.service.get(['nm1'])
        with patch('core.game_person_images.GamePersonImages.get',AsyncMock(return_value={'nm1':{'image':URL,'state':'ready'},'nm2':{'image':None,'state':'ready'}})) as provider:
            response=await self.client.post('/tracking/games/people/images',json={'ids':self.ids})
            self.assertEqual(response.status_code,200,response.text)
            self.assertIn('private',response.headers['cache-control'])
            data={row['id']:row for row in response.json()['people']}
            self.assertEqual(data[self.ids[0]]['image'],URL)
            self.assertIsNone(data[self.ids[1]]['image'])
            self.assertEqual(data[self.ids[2]]['image'],'https://existing.example/photo.jpg')
            self.assertIsNone(data[self.ids[3]]['image'])
            self.assertEqual(set(provider.call_args.args[0]),{'nm1','nm2'})
            self.viewer.games_access=False
            self.assertEqual((await self.client.post('/tracking/games/people/images',json={'ids':self.ids})).status_code,403)
            self.viewer=None
            self.assertEqual((await self.client.post('/tracking/games/people/images',json={'ids':self.ids})).status_code,401)
            provider.assert_awaited_once()

    async def test_ambiguous_person_ids_remain_unresolved_and_edits_during_fetch_win(self):
        async with self.Session() as db:
            db.add(CatalogueIdentity(entity_id=self.ids[0],namespace='imdb.person',external_id='nm99',source='reviewed'))
            await db.commit()
        async def enrichment(natives):
            self.assertEqual(natives,['nm2'])
            async with self.Session() as db:
                person=await db.get(CatalogueEntity,self.ids[1]);person.image_url='https://chosen.example/photo.jpg'
                await db.commit()
            return {'nm2':{'image':URL,'state':'ready'}}
        with patch('core.game_person_images.GamePersonImages.get',AsyncMock(side_effect=enrichment)):
            response=await self.client.post('/tracking/games/people/images',json={'ids':self.ids[:2]})
        self.assertEqual(response.status_code,200,response.text)
        data={row['id']:row for row in response.json()['people']}
        self.assertEqual(data[self.ids[0]]['state'],'unavailable')
        self.assertEqual(data[self.ids[1]]['image'],'https://chosen.example/photo.jpg')

    async def test_input_is_bounded_and_typed(self):
        with patch('core.game_person_images.GamePersonImages.get',AsyncMock()) as source:
            for ids in [[],self.ids*5,[True],[-1],['1']]:
                self.assertEqual((await self.client.post('/tracking/games/people/images',json={'ids':ids})).status_code,422)
            source.assert_not_awaited()

    async def test_active_cross_worker_lease_is_pending_and_warm_read_cost_is_bounded(self):
        await self.service.get(['nm1','nm2'])
        statements=[]
        def sql(conn,cursor,statement,parameters,context,many):statements.append(statement)
        event.listen(self.engine.sync_engine,'before_cursor_execute',sql)
        try:
            with patch('core.game_person_images.GamePersonImages.get',self.service.get):
                response=await self.client.post('/tracking/games/people/images',json={'ids':self.ids[:2]})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(len([value for value in statements if value.startswith('SELECT')]),6)
            self.assertTrue(all('catalogue_entities.attributes' not in value for value in statements))
            self.assertTrue(all('game_person_page' not in value for value in statements))
        finally:event.remove(self.engine.sync_engine,'before_cursor_execute',sql)
        async with self.Session() as db:
            row=await db.scalar(query(['nm1']));row.payload=None;row.fetched_at=None
            row.expires_at=None;row.lease_token='other-worker';row.lease_until=now()+timedelta(seconds=30)
            await db.commit()
        self.source.portraits.reset_mock()
        self.assertEqual((await self.service.get(['nm1']))['nm1']['state'],'pending')
        self.source.portraits.assert_not_awaited()

    async def test_eight_people_prefer_tmdb_skip_known_id_lookup_and_batch_only_missing(self):
        async with self.Session() as db:
            people=[CatalogueEntity(kind='person',name=f'Portrait actor {i}',protected_fields=[]) for i in range(8)]
            db.add_all(people);await db.flush();ids=[person.id for person in people]
            for i,person in enumerate(people):
                db.add(CatalogueIdentity(entity_id=person.id,namespace='imdb.person',external_id=f'nm{100+i}',source='imdb'))
                if i<4:db.add(CatalogueIdentity(entity_id=person.id,namespace='tmdb.person',external_id=str(500+i),source='reviewed'))
            await db.commit()
        def profile(identifier,**kwargs):
            return {'id':identifier,'profile_path':f'/photo{identifier}.jpg' if identifier%2==0 else None}
        def find(native,*args,**kwargs):return {'person_results':[profile(500+int(native[2:])-100)]}
        imdb=AsyncMock(side_effect=lambda natives:{native:{'image':URL,'width':500,'height':700} for native in natives})
        with patch('core.settings_store.get_server_tmdb_key',return_value='fixture'), \
            patch('core.tmdb.get_person_profile',AsyncMock(side_effect=profile)) as details, \
            patch('core.tmdb.find_by_external_id',AsyncMock(side_effect=find)) as lookup, \
            patch('core.game_imdb_source.IMDbGameSource.portraits',imdb):
            first=await self.client.post('/tracking/games/people/images',json={'ids':ids})
            self.assertEqual(first.status_code,200,first.text)
            images=[row['image'] for row in first.json()['people']]
            self.assertEqual(sum('image.tmdb.org' in image for image in images),4)
            self.assertEqual(sum(image==URL for image in images),4)
            self.assertEqual(details.await_count,4);self.assertEqual(lookup.await_count,4)
            imdb.assert_awaited_once_with(['nm101','nm103','nm105','nm107'])
            statements=[]
            def sql(conn,cursor,statement,parameters,context,many):statements.append(statement)
            event.listen(self.engine.sync_engine,'before_cursor_execute',sql)
            try:second=await self.client.post('/tracking/games/people/images',json={'ids':ids})
            finally:event.remove(self.engine.sync_engine,'before_cursor_execute',sql)
            self.assertEqual(second.json(),first.json())
            self.assertEqual(len([value for value in statements if value.startswith('SELECT')]),7)
            self.assertTrue(all('catalogue_entities.attributes' not in value for value in statements))
            self.assertFalse(any(value.startswith(('INSERT','UPDATE')) for value in statements))
            self.assertEqual(details.await_count+lookup.await_count,8);imdb.assert_awaited_once()
        async with self.Session() as db:
            self.assertTrue(all(person.image_url is None for person in await db.scalars(
                select(CatalogueEntity).where(CatalogueEntity.id.in_(ids)))))
            negatives=[row for row in await db.scalars(query([f'tmdb:{i}' for i in range(500,504)]+
                [f'imdb:nm{i}' for i in range(104,108)],'tmdb')) if not row.payload['image']]
            self.assertEqual(len(negatives),4)
            self.assertTrue(all(row.expires_at>now()+timedelta(days=6) for row in negatives))

    async def test_tmdb_upgrade_ignores_old_imdb_fallback_and_late_identity_edit(self):
        await self.service.get(['nm1'])
        async def find(*args,**kwargs):
            return {'person_results':[{'id':7,'profile_path':'/preferred.jpg'}]}
        with patch('core.settings_store.get_server_tmdb_key',return_value='fixture'), \
            patch('core.tmdb.find_by_external_id',AsyncMock(side_effect=find)), \
            patch('core.game_imdb_source.IMDbGameSource.portraits',AsyncMock()) as imdb:
            response=await self.client.post('/tracking/games/people/images',json={'ids':[self.ids[0]]})
            self.assertEqual(response.json()['people'][0]['image'],'https://image.tmdb.org/t/p/w185/preferred.jpg')
            imdb.assert_not_awaited()
        async def change_identity(*args,**kwargs):
            async with self.Session() as db:
                db.add(CatalogueIdentity(entity_id=self.ids[1],namespace='tmdb.person',external_id='99',source='reviewed'))
                await db.commit()
            return {'person_results':[{'id':8,'profile_path':'/old-binding.jpg'}]}
        with patch('core.settings_store.get_server_tmdb_key',return_value='fixture'), \
            patch('core.tmdb.find_by_external_id',AsyncMock(side_effect=change_identity)):
            response=await self.client.post('/tracking/games/people/images',json={'ids':[self.ids[1]]})
        self.assertEqual(response.json()['people'][0],{'id':self.ids[1],'image':None,'state':'unavailable'})

    async def test_tmdb_partial_errors_have_cooldowns_and_shared_cancellation_safe_work(self):
        from core.game_tmdb_person_images import GameTMDBPersonImages
        entered,release=asyncio.Event(),asyncio.Event()
        async def fetch(natives):
            entered.set();await release.wait()
            return {'imdb:nm1':{'image':'https://image.tmdb.org/t/p/w185/photo.jpg'},'imdb:nm2':{'error':'timeout'}}
        source=SimpleNamespace(portraits=AsyncMock(side_effect=fetch))
        service=GameTMDBPersonImages(self.Session,source=source)
        first=asyncio.create_task(service.get(['imdb:nm1','imdb:nm2']));await entered.wait()
        second=asyncio.create_task(service.get(['imdb:nm2','imdb:nm1']));await asyncio.sleep(.02)
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):await first
        release.set();result=await second
        self.assertTrue(result['imdb:nm1']['image']);self.assertEqual(result['imdb:nm2']['state'],'retry')
        self.assertEqual(await service.get(['imdb:nm1','imdb:nm2']),result);source.portraits.assert_awaited_once()
        async with self.Session() as db:
            row=await db.scalar(query(['imdb:nm2'],'tmdb'))
            self.assertIsNone(row.fetched_at);self.assertIsNone(row.expires_at)
            self.assertGreater(row.next_attempt_at,now())

    async def test_tmdb_with_no_imdb_identity_and_pending_lease_avoids_fallback(self):
        from core.game_tmdb_person_images import GameTMDBPersonImages
        async with self.Session() as db:
            person=CatalogueEntity(kind='person',name='TMDB only',protected_fields=[])
            db.add(person);await db.flush();person_id=person.id
            db.add(CatalogueIdentity(entity_id=person.id,namespace='tmdb.person',external_id='7',source='reviewed'))
            await db.commit()
        with patch('core.settings_store.get_server_tmdb_key',return_value='fixture'), \
            patch('core.tmdb.get_person_profile',AsyncMock(return_value={'id':7,'profile_path':'/only.jpg'})), \
            patch('core.game_imdb_source.IMDbGameSource.portraits',AsyncMock()) as imdb:
            response=await self.client.post('/tracking/games/people/images',json={'ids':[person_id]})
        self.assertTrue(response.json()['people'][0]['image']);imdb.assert_not_awaited()
        with patch('core.settings_store.get_server_tmdb_key',return_value='fixture'), \
            patch.object(GameTMDBPersonImages,'get',AsyncMock(return_value={'imdb:nm1':{'image':None,'state':'pending'}})), \
            patch.object(GamePersonImages,'get',AsyncMock(return_value={})) as imdb:
            response=await self.client.post('/tracking/games/people/images',json={'ids':[self.ids[0]]})
        self.assertEqual(response.json()['people'][0]['state'],'pending');imdb.assert_awaited_once_with([])
