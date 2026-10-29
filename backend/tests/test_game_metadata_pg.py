"""Actual approved detail routes hydrate stubs before optional characters."""
import asyncio
import unittest
from copy import deepcopy
from datetime import timedelta
from unittest.mock import AsyncMock,patch

from sqlalchemy import select,func,event,delete
import test_games_pg as fixture
from core.game_metadata import GameMetadata,_flights,header,status,request
from core.game_release_dates import VERSION as RELEASE_VERSION
from core.game_imdb_enrichment import now
from core.catalogue_providers import ProviderError,CatalogueProviders
from core.catalogue_normalize import normalize_igdb,GAME_ARTWORK_VERSION
from models.catalogue import CatalogueEntity,CatalogueIdentity,MetadataSnapshot,CharacterAppearance
from models.games import GameEntry


class Provider:
    validate=lambda *args:None
    def __init__(self):
        self.called=0;self.characters=asyncio.Event();self.release=asyncio.Event();self.fail=None;self.wrong=False;self.fail_characters=False;self.title='Hydrated game';self.optional_active=0;self.optional_max=0
        self.dates={}
    async def detail(self,provider,kind,native,*,on_primary=None):
        self.called+=1
        if self.fail:raise self.fail
        raw={'id':1942 if self.wrong else int(native),'name':self.title,'slug':'hydrated-game','summary':'Full primary synopsis',
            'game_type':{'id':0,'type':'Main Game'},'cover':{'url':'https://images.igdb.com/igdb/image/upload/t_thumb/source.jpg'},
            'genres':[{'id':5,'name':'Adventure'}],'platforms':[{'id':6,'name':'PC (Microsoft Windows)'}],
            '_coverage':{'primary_complete':True,'source_game_id':native,'characters_complete':False,
                'release_precision_version':RELEASE_VERSION,'artwork_policy_version':GAME_ARTWORK_VERSION,'children_complete':True}}
        raw.update(self.dates)
        if on_primary:await on_primary(normalize_igdb(raw),{'game':deepcopy(raw)})
        self.optional_active+=1;self.optional_max=max(self.optional_max,self.optional_active)
        self.characters.set()
        try:await self.release.wait()
        finally:self.optional_active-=1
        if self.fail_characters:raise ProviderError('unavailable',120)
        raw['_characters']=[{'id':7001,'name':'Unvoiced Knight','mug_shot':{'url':'https://images.igdb.com/igdb/image/upload/t_thumb/knight.jpg'}}]
        raw['_coverage']['characters_complete']=True
        return normalize_igdb(raw),{'game':raw}


@unittest.skipUnless(fixture.URL,'Requires explicit disposable Games PostgreSQL')
class GameMetadataTests(unittest.IsolatedAsyncioTestCase):
    cleanup=fixture.GamesDatabaseTests.cleanup
    async def test_slow_primary_returns_usable_shell_then_public_primary_without_private_payload(self):
        proceed=asyncio.Event();original=self.provider.detail
        async def slow(*args,**kwargs):await proceed.wait();return await original(*args,**kwargs)
        self.provider.detail=slow
        response=await asyncio.wait_for(self.detail(),2)
        self.assertEqual(response.status_code,200,response.text)
        data=response.json();self.assertEqual(data['title'],'Game stub')
        self.assertFalse(data['game_metadata_status']['primary_ready'])
        self.assertTrue(data['game_metadata_status']['pending']);self.assertEqual(data['entry']['score'],9)
        proceed.set();await self.finish()
        response=await self.client.get(f'/tracking/games/works/{self.base}/primary')
        self.assertEqual(response.status_code,200,response.text)
        data=response.json();self.assertEqual(data['title'],'Hydrated game')
        self.assertEqual(data['poster'],'https://example.test/selected.jpg')
        for field in ('entry','notes','people','organizations','game_characters','achievement_unlocks','total_seconds','completion_seconds'):
            self.assertNotIn(field,data)
        self.assertEqual(response.headers['cache-control'],'private, no-store')
        combined=await self.client.get(f'/tracking/games/works/{self.base}/metadata?primary=true')
        self.assertEqual(combined.status_code,200,combined.text)
        self.assertEqual(combined.json()['primary']['title'],'Hydrated game')
        self.assertNotIn('entry',combined.json()['primary'])
        revision=combined.json()['primary_revision']
        unchanged=await self.client.get(f'/tracking/games/works/{self.base}/metadata',params={'primary':'true','revision':revision})
        self.assertNotIn('primary',unchanged.json())

    async def test_scheduler_admits_later_primary_while_optional_character_slots_are_busy(self):
        ids=[]
        async with self.Session() as db:
            for native in ('9101','9102','9103'):
                work=CatalogueEntity(kind='game',name='Queued primary',attributes={'game_class':'game','igdb_id':native})
                db.add(work);await db.flush();ids.append(work.id)
                db.add(CatalogueIdentity(entity_id=work.id,namespace='igdb.game',external_id=native,source='igdb'))
                await db.flush();await request(db,work)
            await db.commit()
        await asyncio.wait_for(self.service.tick(),1)
        async def primary_ready(native):
            while True:
                flight=_flights.get((id(self.engine),native))
                if flight and flight.primary.is_set():return flight
                await asyncio.sleep(.01)
        await asyncio.wait_for(asyncio.gather(primary_ready('9101'),primary_ready('9102')),3)
        self.assertEqual(self.provider.optional_max,2)
        await asyncio.wait_for(self.service.tick(),1)
        third=await asyncio.wait_for(primary_ready('9103'),3)
        self.assertEqual(third.entity_id,ids[-1])
        self.assertFalse(third.task.done());self.assertFalse(self.provider.release.is_set())
        self.assertEqual(self.provider.optional_max,2)
        self.provider.release.set()
        await asyncio.gather(*[flight.task for (scope,_),flight in list(_flights.items()) if scope==id(self.engine)])

    async def test_status_shares_a_short_primary_wait_without_cancelling_shared_provider_work(self):
        proceed=asyncio.Event();original=self.provider.detail
        async def slow(*args,**kwargs):await proceed.wait();return await original(*args,**kwargs)
        self.provider.detail=slow
        response=await asyncio.wait_for(self.detail(),2)
        self.assertFalse(response.json()['game_metadata_status']['primary_ready'])
        waiter=asyncio.create_task(self.client.get(f'/tracking/games/works/{self.base}/metadata'))
        await asyncio.sleep(.05);self.assertFalse(waiter.done())
        proceed.set()
        response=await asyncio.wait_for(waiter,2)
        self.assertTrue(response.json()['primary_ready']);self.assertEqual(self.provider.called,1)
        self.assertFalse(self.provider.release.is_set());await self.finish()

    async def seed_cached_artwork(self,queued=False):
        stamp=now()-timedelta(days=1);expires=stamp+timedelta(days=7)
        small='https://images.igdb.com/igdb/image/upload/t_thumb/small.jpg'
        large='https://images.igdb.com/igdb/image/upload/t_thumb/large.jpg'
        gallery=[small,large,*[f'https://example.test/extra-{index}.jpg' for index in range(21)]]
        async with self.Session() as db:
            work=await db.get(CatalogueEntity,self.base)
            work.attributes={**work.attributes,'artwork':gallery,'scene_artwork':[small,large],'artwork_policy_version':1}
            if queued:
                work.attributes={**work.attributes,'backdrop_path':'https://example.test/selected-background.jpg'}
                work.protected_fields=[*work.protected_fields,'attributes.backdrop_path']
            db.add(MetadataSnapshot(provider='igdb',kind='game',external_id='119133',entity_id=self.base,
                fetched_at=stamp,expires_at=expires,next_attempt_at=now()+timedelta(minutes=2) if queued else None,
                error_code='unavailable' if queued else None,
                coverage={'characters_complete':True,'release_precision_version':RELEASE_VERSION,'artwork_policy_version':1},
                payload={'game':{'id':119133,'screenshots':[{'url':small,'width':1920,'height':1080},
                    {'url':large,'width':3840,'height':2160}],'artworks':[],
                    '_characters':'RAW_CHARACTER_SENTINEL '*50000}}))
            await db.commit()
            saved=await header(db,'119133')
        return saved.fetched_at,saved.expires_at,gallery,large

    async def test_cached_geometry_policy_upgrade_avoids_provider_work_and_preserves_complete_gallery(self):
        from core.media_details import primary_details
        stamp,expires,gallery,large=await self.seed_cached_artwork()
        async with self.Session() as db:
            work=await db.get(CatalogueEntity,self.base);_,state=await request(db,work);await db.commit()
            self.assertTrue(state['complete']);self.assertFalse(state['pending'])
            self.assertEqual(work.attributes['artwork'],gallery)
            self.assertEqual(work.attributes['scene_artwork'][0],large)
            self.assertEqual(primary_details(work)['backdrop'],large)
            row=await header(db,'119133')
            self.assertEqual((row.fetched_at,row.expires_at),(stamp,expires))
        statements=[]
        def record(conn,cursor,sql,params,context,many):statements.append(sql)
        event.listen(self.engine.sync_engine,'before_cursor_execute',record)
        try:
            async with self.Session() as db:
                await request(db,await db.get(CatalogueEntity,self.base));await db.commit()
        finally:event.remove(self.engine.sync_engine,'before_cursor_execute',record)
        self.assertFalse(any('catalogue_metadata_snapshots.payload' in sql for sql in statements))
        self.assertFalse(any(sql.lstrip().upper().startswith(('INSERT','UPDATE','DELETE')) for sql in statements))
        self.assertEqual(self.provider.called,0)

    async def test_cached_policy_upgrade_preserves_selected_art_and_retry_state(self):
        from core.media_details import primary_details
        stamp,expires,gallery,large=await self.seed_cached_artwork(queued=True)
        async with self.Session() as db:
            before=await header(db,'119133');work=await db.get(CatalogueEntity,self.base)
            _,state=await request(db,work);await db.commit()
            self.assertTrue(state['pending']);self.assertEqual(state['error'],'unavailable')
            self.assertEqual(work.attributes['artwork'],gallery)
            self.assertEqual(primary_details(work)['backdrop'],'https://example.test/selected-background.jpg')
            row=await header(db,'119133')
            self.assertEqual((row.fetched_at,row.expires_at,row.next_attempt_at,row.error_code),
                (stamp,expires,before.next_attempt_at,before.error_code))
            entry=await db.scalar(select(GameEntry).where(GameEntry.work_id==self.base))
            self.assertEqual((entry.total_seconds,entry.completion_seconds,entry.score),(198000,144000,9))
        self.assertEqual(self.provider.called,0)

    async def test_legacy_artwork_refreshes_missing_types_without_replacing_selected_background(self):
        for version in (1,2):
            with self.subTest(version=version):
                stamp,expires,gallery,large=await self.seed_cached_artwork()
                async with self.Session() as db:
                    saved=await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.external_id=='119133',MetadataSnapshot.kind=='game'))
                    saved.coverage={**saved.coverage,'artwork_policy_version':version}
                    saved.payload={'game':{**saved.payload['game'],'artworks':[{'url':'https://cdn.test/legacy.jpg','width':1920,'height':1080}]}}
                    work=await db.get(CatalogueEntity,self.base)
                    work.attributes={**work.attributes,'artwork_policy_version':version,'backdrop_path':large}
                    work.protected_fields=[*work.protected_fields,'attributes.backdrop_path']
                    await db.commit()
                    _,state=await request(db,work);await db.commit()
                    row=await header(db,'119133')
                    self.assertTrue(state['pending'])
                    self.assertLess(row.expires_at,expires)
                    self.assertEqual(work.attributes['backdrop_path'],large)
                    self.assertEqual(work.attributes['artwork'],gallery)
                    await db.delete(saved);await db.commit()

    async def asyncSetUp(self):
        await fixture.GamesDatabaseTests.asyncSetUp(self)
        async with self.Session() as db:
            work=await db.get(CatalogueEntity,self.base);work.name='Game stub';work.image_url='https://example.test/selected.jpg'
            work.protected_fields=['image_url'];db.add(CatalogueIdentity(entity_id=self.base,namespace='igdb.game',external_id='119133',source='igdb'))
            work.field_sources={'name':'igdb'}
            db.add(GameEntry(user_id=self.user.id,work_id=self.base,status='completed',score=9,total_seconds=198000,completion_seconds=144000,notes='private note'))
            await db.commit()
        self.provider=Provider();self.service=GameMetadata(self.Session,scope=self.engine,providers=self.provider)
        self.patch=patch('core.game_metadata.CatalogueProviders',return_value=self.provider);self.patch.start();self.addCleanup(self.patch.stop)

    async def asyncTearDown(self):
        tasks=[flight.task for (scope,_),flight in list(_flights.items()) if scope==id(self.engine)]
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)

    async def detail(self):return await self.client.get(f'/catalogue/entities/{self.base}/details')
    async def finish(self):
        flight=_flights.get((id(self.engine),'119133'));self.provider.release.set()
        if flight:await asyncio.shield(flight.task)

    async def test_cached_legacy_day_upgrades_to_partial_release_and_preserves_tracking(self):
        from models.catalogue import GameRelease
        from core.game_metadata import request
        self.provider.dates={'first_release_date':1803859200,'release_dates':[{'id':982776,
            'date':1803859200,'date_format':{'id':1,'format':'YYYYMM'},'y':2027,'m':3,
            'platform':{'id':6,'name':'PC (Microsoft Windows)'}}]}
        async with self.Session() as db:
            work=await db.get(CatalogueEntity,self.base)
            work.attributes={**work.attributes,'release_date':'2027-03-01'}
            work.field_sources={**work.field_sources,'attributes.release_date':'igdb'}
            db.add(MetadataSnapshot(provider='igdb',kind='game',external_id='119133',entity_id=self.base,
                fetched_at=now(),expires_at=now()+timedelta(days=30),coverage={'characters_complete':True},payload={}))
            await db.commit()
        data=(await self.detail()).json()
        self.assertEqual(data['release_date'],'2027')  # Honest cached year while verification runs.
        await self.finish()
        data=(await self.detail()).json()
        self.assertEqual(data['release_date'],'2027-03')
        self.assertEqual(data['releases'][0]['release_date'],'2027-03')
        async with self.Session() as db:
            release=await db.scalar(select(GameRelease).where(GameRelease.work_id==self.base))
            self.assertEqual(release.release_date,'2027-03')
            work=await db.get(CatalogueEntity,self.base)
            _,state=await request(db,work)
            self.assertFalse(state['pending'])
            entry=await db.scalar(select(GameEntry).where(GameEntry.work_id==self.base))
            self.assertEqual((entry.total_seconds,entry.completion_seconds,entry.score),(198000,144000,9))
        self.assertEqual(self.provider.called,1)

    async def test_real_page_returns_primary_before_characters_and_preserves_concurrent_tracking_and_artwork(self):
        response=await asyncio.wait_for(self.detail(),8);self.assertEqual(response.status_code,200,response.text)
        data=response.json();self.assertEqual(data['overview'],'Full primary synopsis');self.assertEqual(data['title'],'Hydrated game')
        self.assertTrue(data['game_metadata_status']['primary_ready']);self.assertFalse(data['game_character_status']['complete'])
        self.assertFalse(data['game_metadata_status']['complete'])
        self.assertTrue(data['game_character_status']['pending']);self.assertFalse(self.provider.release.is_set())
        self.assertEqual(data['poster'],'https://example.test/selected.jpg');self.assertEqual(data['entry']['score'],9)
        async with self.Session() as db:
            entry=await db.scalar(select(GameEntry).where(GameEntry.work_id==self.base));entry.score=8;entry.total_seconds=200000
            job=await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.kind=='game_credit_job'))
            job.coverage={'state':'complete','complete':True};job.next_attempt_at=None;job.expires_at=now()+timedelta(days=1)
            await db.commit()
        credits=(await self.client.get(f'/tracking/games/works/{self.base}/credits')).json()
        self.assertTrue(credits['character_status']['pending']);self.assertFalse(credits['game_credit_status']['pending'])
        await self.finish();data=(await self.detail()).json()
        self.assertTrue(data['game_character_status']['complete']);self.assertFalse(data['game_character_status']['pending'])
        self.assertEqual(data['game_characters'][0]['name'],'Unvoiced Knight');self.assertEqual(data['entry']['score'],8)
        self.assertEqual(data['entry']['total_seconds'],200000);self.assertEqual(data['entry']['completion_seconds'],144000)
        self.assertEqual(data['entry']['notes'],'private note');self.assertEqual(self.provider.called,1)
        async with self.Session() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(CatalogueEntity).where(CatalogueEntity.kind=='game')),5)
            self.assertEqual(await db.scalar(select(func.count()).select_from(GameEntry)),1)

    async def test_concurrent_pages_and_cancelled_waiter_share_the_same_source_work(self):
        ready=asyncio.Event();proceed=asyncio.Event();original=self.provider.detail
        async def slow(*args,**kwargs):ready.set();await proceed.wait();return await original(*args,**kwargs)
        self.provider.detail=slow
        first=asyncio.create_task(self.detail());second=asyncio.create_task(self.detail())
        await asyncio.wait_for(ready.wait(),5);first.cancel()
        with self.assertRaises(asyncio.CancelledError):await first
        proceed.set();self.assertEqual((await asyncio.wait_for(second,8)).status_code,200)
        self.assertEqual(self.provider.called,1);await self.finish()

    async def test_failure_retains_stub_user_state_and_reuses_durable_backoff(self):
        self.provider.fail=ProviderError('unavailable',120)
        for _ in range(2):
            response=await self.detail();self.assertEqual(response.status_code,200,response.text)
            data=response.json();self.assertEqual(data['title'],'Game stub');self.assertEqual(data['entry']['score'],9)
            self.assertFalse(data['game_metadata_status']['primary_ready']);self.assertEqual(data['game_metadata_status']['error'],'unavailable')
        self.assertEqual(self.provider.called,1)

    async def test_warm_status_reads_skip_payload_provider_and_writes_and_access_gate_precedes_admission(self):
        await self.detail();await self.finish()
        async with self.Session() as db:
            row=await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.provider=='igdb',MetadataSnapshot.kind=='game'))
            row.payload={'raw':'METADATA_SENTINEL '*50000};await db.commit()
        statements=[]
        def record(conn,cursor,sql,params,context,many):statements.append(sql)
        event.listen(self.engine.sync_engine,'before_cursor_execute',record)
        try:response=await self.client.get(f'/tracking/games/works/{self.base}/metadata')
        finally:event.remove(self.engine.sync_engine,'before_cursor_execute',record)
        self.assertTrue(response.json()['primary_ready']);self.assertEqual(response.headers['cache-control'],'private, no-store')
        self.assertFalse(any('catalogue_metadata_snapshots.payload' in sql for sql in statements))
        self.assertFalse(any(sql.lstrip().upper().startswith(('INSERT','UPDATE','DELETE')) for sql in statements))
        self.viewer=self.other;self.other.games_access=False;self.assertEqual((await self.detail()).status_code,403)
        self.assertEqual((await self.client.get(f'/tracking/games/works/{self.base}/metadata')).status_code,403)
        self.viewer=None;self.assertIn((await self.detail()).status_code,(401,403));self.assertEqual(self.provider.called,1)

    async def test_ambiguous_roots_and_wrong_provider_identity_never_bind_or_create_games(self):
        async with self.Session() as db:
            db.add(CatalogueIdentity(entity_id=self.base,namespace='igdb.game',external_id='42',source='igdb'));await db.commit()
        data=(await self.detail()).json();self.assertFalse(data['game_metadata_status']['primary_ready']);self.assertEqual(self.provider.called,0)
        async with self.Session() as db:
            await db.execute(delete(CatalogueIdentity).where(CatalogueIdentity.entity_id==self.base,CatalogueIdentity.external_id=='42'));await db.commit()
        self.provider.wrong=True;data=(await self.detail()).json()
        self.assertEqual(data['game_metadata_status']['error'],'identity_conflict')
        async with self.Session() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(CatalogueEntity).where(CatalogueEntity.kind=='game')),5)
            self.assertEqual((await db.get(CatalogueEntity,self.base)).name,'Game stub')

    async def test_cancelling_worker_releases_lease_without_erasing_primary_checkpoint(self):
        await self.detail();flight=_flights[(id(self.engine),'119133')];flight.task.cancel()
        with self.assertRaises(asyncio.CancelledError):await flight.task
        async with self.Session() as db:
            row=await header(db,'119133');self.assertIsNone(row.lease_until);self.assertIsNotNone(row.next_attempt_at)
            self.assertTrue(status(row,self.base)['primary_ready'])
            self.assertEqual((await db.get(CatalogueEntity,self.base)).description,'Full primary synopsis')

    async def test_known_exclusions_do_not_fetch_and_scheduler_discards_invalid_queued_identity(self):
        async with self.Session() as db:
            work=await db.get(CatalogueEntity,self.pack);db.add(CatalogueIdentity(entity_id=self.pack,namespace='igdb.game',external_id='42',source='igdb'));await db.commit()
            native,state=await request(db,work);self.assertIsNone(native);self.assertFalse(state['pending'])
            work=await db.get(CatalogueEntity,self.base);await request(db,work)
            await db.execute(delete(CatalogueIdentity).where(CatalogueIdentity.entity_id==self.base,CatalogueIdentity.namespace=='igdb.game'));await db.commit()
        await self.service.tick()
        async with self.Session() as db:self.assertIsNone((await header(db,'119133')).next_attempt_at)
        self.assertEqual(self.provider.called,0)

    async def test_stale_refresh_never_replaces_last_complete_content_with_failed_primary_stage(self):
        await self.detail();await self.finish()
        async with self.Session() as db:
            row=await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.provider=='igdb',MetadataSnapshot.kind=='game'))
            row.expires_at=now()-timedelta(seconds=1);await db.commit()
        self.provider.release.clear();self.provider.characters.clear();self.provider.title='Incomplete new title';self.provider.fail_characters=True
        response=await self.detail();self.assertEqual(response.json()['title'],'Hydrated game')
        await asyncio.wait_for(self.provider.characters.wait(),5)
        async with self.Session() as db:
            self.assertEqual((await db.get(CatalogueEntity,self.base)).name,'Hydrated game')
            row=await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.provider=='igdb',MetadataSnapshot.kind=='game'))
            self.assertTrue(row.coverage['characters_complete'])
        await self.finish();response=await self.detail()
        self.assertEqual(response.json()['title'],'Hydrated game');self.assertTrue(response.json()['game_metadata_status']['primary_ready'])
        self.assertEqual(self.provider.called,2)

    async def test_failed_optional_characters_preserve_new_primary_and_report_retry_without_claiming_complete(self):
        self.provider.fail_characters=True
        response=await self.detail();self.assertTrue(response.json()['game_metadata_status']['primary_ready'])
        await self.finish()
        data=(await self.client.get(f'/tracking/games/works/{self.base}/credits')).json()
        self.assertFalse(data['game_character_status']['complete']);self.assertTrue(data['character_status']['pending'])
        self.assertEqual(data['character_status']['error'],'unavailable')
        self.assertGreaterEqual(data['character_status']['retry_after'],100)
        self.assertEqual((await self.detail()).json()['overview'],'Full primary synopsis');self.assertEqual(self.provider.called,1)

    async def test_identity_change_between_phases_blocks_late_character_publication(self):
        await self.detail()
        async with self.Session() as db:
            await db.execute(delete(CatalogueIdentity).where(CatalogueIdentity.entity_id==self.base,CatalogueIdentity.namespace=='igdb.game'));await db.commit()
        await self.finish()
        async with self.Session() as db:
            self.assertEqual((await header(db,'119133')).error_code,'identity_conflict')
            self.assertEqual(await db.scalar(select(func.count()).select_from(CharacterAppearance).where(CharacterAppearance.work_id==self.base)),0)

    async def test_slow_character_work_does_not_reserve_primary_slots_for_other_requested_games(self):
        targets=[('119133',self.base)]
        async with self.Session() as db:
            for native in ('301','302'):
                work=CatalogueEntity(kind='game',name=f'Stub {native}',attributes={'game_class':'game'})
                db.add(work);await db.flush();db.add(CatalogueIdentity(entity_id=work.id,namespace='igdb.game',external_id=native,source='igdb'))
                targets.append((native,work.id))
            await db.commit()
        flights=[self.service.start(native,work) for native,work in targets]
        await asyncio.wait_for(asyncio.gather(*(flight.primary.wait() for flight in flights)),8)
        self.assertEqual(self.provider.called,3);self.assertEqual(self.provider.optional_max,2)
        async with self.Session() as db:
            for native,work in targets:self.assertTrue(status(await header(db,native),work)['primary_ready'])
        self.provider.release.set();await asyncio.gather(*(flight.task for flight in flights))

    async def test_new_result_resolve_uses_the_same_primary_first_cache_without_creating_tracking(self):
        response=await asyncio.wait_for(self.client.post('/tracking/games/resolve/401'),8)
        self.assertEqual(response.status_code,200,response.text);identifier=response.json()['id']
        self.assertFalse(self.provider.release.is_set());self.assertEqual(self.provider.called,1)
        async with self.Session() as db:
            self.assertEqual((await db.get(CatalogueEntity,identifier)).description,'Full primary synopsis')
            self.assertIsNone(await db.scalar(select(GameEntry).where(GameEntry.work_id==identifier)))
        flight=_flights[(id(self.engine),'401')];self.provider.release.set();await flight.task
        response=await self.client.post('/tracking/games/resolve/401');self.assertEqual(response.json()['id'],identifier)
        self.assertEqual(self.provider.called,1)

    async def test_exhausted_wait_budget_leaves_shared_work_running_and_status_can_finish_without_navigation(self):
        proceed=asyncio.Event();original=self.provider.detail
        async def slow(*args,**kwargs):await proceed.wait();return await original(*args,**kwargs)
        self.provider.detail=slow
        async with self.Session() as db:
            state,changed=await asyncio.wait_for(self.service.ensure(db,await db.get(CatalogueEntity,self.base),wait_seconds=0),2)
        self.assertFalse(state['primary_ready']);self.assertFalse(changed)
        flight=_flights[(id(self.engine),'119133')];self.assertFalse(flight.task.done())
        proceed.set();await asyncio.wait_for(flight.primary.wait(),5)
        data=(await self.client.get(f'/tracking/games/works/{self.base}/metadata')).json()
        self.assertTrue(data['primary_ready']);await self.finish()


class SourceCheckpointTests(unittest.IsolatedAsyncioTestCase):
    async def test_partial_character_page_is_not_a_complete_metadata_snapshot(self):
        from types import SimpleNamespace
        row=SimpleNamespace(entity_id=1,fetched_at=now(),expires_at=now()+timedelta(days=7),next_attempt_at=None,
            lease_until=None,error_code=None,characters_complete=False,artwork_policy_version=GAME_ARTWORK_VERSION)
        state=status(row,1)
        self.assertTrue(state['primary_ready']);self.assertFalse(state['complete']);self.assertFalse(state['pending'])

    async def test_real_igdb_adapter_publishes_root_and_edition_primary_before_character_request_finishes(self):
        for edition in (False,True):
            api=CatalogueProviders()
            api.validate=lambda *args:None  # Transport is mocked; never require real provider credentials.
            root={'id':119133,'name':'Primary game','game_type':{'id':0,'type':'Main Game'}}
            api.igdb=AsyncMock(side_effect=[[{'id':555,'name':'Edition','version_parent':119133}],[root]] if edition else [[root]])
            entered,release=asyncio.Event(),asyncio.Event()
            async def pages(*args):entered.set();await release.wait();return [],True
            api._igdb_pages=pages;primary=AsyncMock()
            task=asyncio.create_task(api.detail('igdb','game','555' if edition else '119133',on_primary=primary))
            await asyncio.wait_for(entered.wait(),2)
            primary.assert_awaited_once();doc=primary.call_args.args[0]
            self.assertTrue(doc['coverage']['primary_complete']);self.assertFalse(doc['coverage']['characters_complete'])
            self.assertIn({'namespace':'igdb.game','external_id':'119133'},doc['work']['identities'])
            if edition:self.assertIn({'namespace':'igdb.game','external_id':'555'},doc['work']['identities'])
            release.set();doc,_=await task;self.assertTrue(doc['coverage']['characters_complete'])
