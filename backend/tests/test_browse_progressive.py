"""Independent shelves and shared preview-to-timeline ownership."""
import asyncio
import os
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

os.environ.setdefault('SECRET_KEY', 'progressive-tests-only')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')
from core import browse, chronological_discovery as discovery
from core.discovery_policy import DiscoveryPolicy
from core.release_snapshots import ReleaseSnapshots, FRESH_SECONDS


class PreviewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory=self.enterContext(TemporaryDirectory())
        self.enterContext(patch.object(discovery,'_snapshots',ReleaseSnapshots(lambda:Path(directory))))
        discovery._cache._store.clear()
        self.filters=dict(genres=[],start=None,end=date.today().isoformat(),status='',provider=None,
            region='DE',sort='newest',min_popularity=25)

    async def asyncTearDown(self):
        await discovery.close()

    async def test_six_card_preview_leaves_complete_window_for_timeline_without_duplicate_acquisition(self):
        await self.exercise_shared_preview()

    async def test_timeline_started_first_still_publishes_six_cards_and_persists_preview_owned_selection(self):
        await self.exercise_shared_preview(timeline_first=True)
        self.assertTrue(list(discovery._snapshots.directory().glob('*.json')))

    async def exercise_shared_preview(self, timeline_first=False):
        blocked=asyncio.Event(); entered=asyncio.Event(); acquired=[]
        async def provider(url, **kwargs):
            if '/discover/' in url:
                return {'total_pages':1,'results':[dict(id=i,name=f'Title {i}',popularity=80,
                    first_air_date=date.today().isoformat() if i<=6 else '2020-01-01') for i in range(1,14)]}
            identifier=int(url.rsplit('/',1)[1]);acquired.append(identifier)
            if identifier>6:
                entered.set();await blocked.wait()
            return dict(id=identifier,name=f'Title {identifier}',popularity=80,
                first_air_date=date.today().isoformat() if identifier<=6 else '2020-01-01',
                seasons=[] if identifier<=6 else [{'season_number':2,'air_date':date.today().isoformat()}])
        with patch('core.tmdb._get',AsyncMock(side_effect=provider)):
            full=None
            if timeline_first:
                full=asyncio.create_task(discovery.page('series','fixture',self.filters))
                await entered.wait()
            meta, preview=await asyncio.wait_for(discovery.page('series','fixture',self.filters,size=6,preview=True),1)
            self.assertEqual(len(preview),6)
            self.assertTrue(meta['partial'])
            self.assertTrue(discovery._pending)
            await entered.wait()
            # Explicit route defaults reuse the shelf job, even with no end date.
            if full is None:full=asyncio.create_task(discovery.page('series','fixture',{**self.filters,'end':None,
                'cinema':False,'audience_curation':True,'include_returning':True,'include_rereleases':None,'cinema_country':None}))
            await asyncio.sleep(0)
            self.assertFalse(full.done())
            blocked.set(); _, rows=await full
        self.assertEqual(len(rows),13)
        self.assertEqual(len(acquired),len(set(acquired)))
        self.assertEqual(len([row for row in rows if row.get('release_season')==2]),7)
        self.assertEqual([row['release_event'] for row in rows], sorted([row['release_event'] for row in rows],reverse=True))
        self.assertFalse(discovery._pending)

    async def test_fewer_than_six_results_finishes_without_hanging(self):
        with patch('core.tmdb._get',AsyncMock(return_value={'results':[],'total_pages':1})):
            _, rows=await asyncio.wait_for(discovery.page('series','fixture',self.filters,size=6,preview=True),1)
        self.assertEqual(rows,[])
        self.assertFalse(discovery._pending)

    async def test_preview_overlaps_later_discovery_pages_and_full_pool_keeps_final_summaries(self):
        blocked=asyncio.Event();entered=asyncio.Event();details=[]
        async def provider(url, *, params, cache_ttl, **kwargs):
            self.assertEqual(cache_ttl,2*3600)
            if '/discover/' in url:
                if params['page']>1:
                    entered.set();await blocked.wait()
                identifiers=range(1,7) if params['page']==1 else range(1,13)
                return {'total_pages':2,'results':[dict(id=i,name='First' if params['page']==1 else 'Latest',
                    first_air_date=date.today().isoformat(),popularity=80) for i in identifiers]}
            identifier=int(url.rsplit('/',1)[1]);details.append(identifier)
            return dict(id=identifier,first_air_date=date.today().isoformat(),popularity=80)
        with patch('core.tmdb._get',AsyncMock(side_effect=provider)):
            _,preview=await asyncio.wait_for(discovery.page('series','fixture',self.filters,size=6,preview=True),1)
            await entered.wait();self.assertFalse(blocked.is_set())
            self.assertEqual(len(preview),6)
            full=asyncio.create_task(discovery.page('series','fixture',self.filters))
            await asyncio.sleep(0);self.assertFalse(full.done())
            blocked.set();_,rows=await full
        self.assertEqual(len(rows),12)
        self.assertTrue(all(row['title']=='Latest' for row in rows))
        self.assertEqual(len(details),len(set(details)))

    async def test_snapshot_is_still_fresh_after_three_hours_for_another_provider_key(self):
        import json,time
        with patch('core.tmdb._get',AsyncMock(return_value={'results':[],'total_pages':1})):
            await discovery.page('series','first-key',self.filters,preview=True)
        discovery._cache._store.clear()
        path=next(discovery._snapshots.directory().glob('*.json'));saved=json.loads(path.read_text())
        saved['fetched_at']=time.time()-3*3600;path.write_text(json.dumps(saved))
        with patch('core.tmdb._get',AsyncMock(side_effect=AssertionError('warm public selection refetched'))) as read:
            _,rows=await discovery.page('series','second-key',self.filters)
        self.assertEqual(rows,[]);read.assert_not_awaited()

    async def test_cancelling_a_cold_read_cleans_up_overlapped_discovery(self):
        tails_entered=asyncio.Event();details_entered=asyncio.Event();cancelled=asyncio.Event()
        tail_count=tail_cancels=0
        async def provider(url,*,params,**kwargs):
            nonlocal tail_count,tail_cancels
            if '/discover/' in url and params['page']==1:
                return {'total_pages':2,'results':[dict(id=i,name='Candidate',popularity=80,
                    first_air_date=date.today().isoformat()) for i in range(1,7)]}
            if '/discover/' in url:
                tail_count+=1
                if tail_count==2:tails_entered.set()
                try:await asyncio.Event().wait()
                except asyncio.CancelledError:
                    tail_cancels+=1
                    if tail_cancels==2:cancelled.set()
                    raise
            details_entered.set();await asyncio.Event().wait()
        with patch('core.tmdb._get',AsyncMock(side_effect=provider)):
            request=asyncio.create_task(discovery.page('series','fixture',self.filters))
            await asyncio.wait_for(tails_entered.wait(),1)
            await asyncio.wait_for(details_entered.wait(),1)
            request.cancel();await asyncio.gather(request,return_exceptions=True)
            await asyncio.wait_for(cancelled.wait(),1)
        self.assertFalse(discovery._pending)

    async def test_finished_preview_survives_memory_cache_loss_without_provider_requests(self):
        with patch('core.tmdb._get',AsyncMock(return_value={'results':[],'total_pages':1})):
            await discovery.page('series','fixture',self.filters,preview=True)
        discovery._cache._store.clear()
        with patch('core.tmdb._get',AsyncMock(side_effect=AssertionError('restart reacquired evidence'))) as read:
            _,rows=await discovery.page('series','fixture',self.filters)
        self.assertEqual(rows,[]);read.assert_not_awaited()

    async def test_stale_preview_paints_while_timeline_waits_for_fresh_selection(self):
        import json,time
        from datetime import timedelta
        upper=date.today();lower=upper-timedelta(days=discovery.WINDOW_DAYS-1)
        identity=discovery.selection_identity('series',self.filters,lower.isoformat(),upper.isoformat(),True,'')
        await discovery._snapshots.write(identity,{'rows':[],'partial':False})
        path=discovery._snapshots.path(identity);saved=json.loads(path.read_text())
        saved['fetched_at']=time.time()-FRESH_SECONDS-100;path.write_text(json.dumps(saved))
        entered=asyncio.Event();release=asyncio.Event()
        async def build(*args,**kwargs):
            entered.set();await release.wait();return {'rows':[],'partial':False}
        with patch.object(discovery,'_selection',side_effect=build) as acquire:
            await asyncio.wait_for(discovery.page('series','fixture',self.filters,preview=True),1)
            await entered.wait()
            timeline=asyncio.create_task(discovery.page('series','fixture',self.filters))
            await asyncio.sleep(.01);self.assertFalse(timeline.done())
            release.set();await timeline
            self.assertEqual(acquire.call_count,1)

    async def test_disk_cache_bounds_expiry_and_failed_refresh_preserve_usable_snapshot(self):
        import json,time
        from datetime import timedelta
        from core import release_snapshots
        with patch.object(release_snapshots,'MAX_RECORDS',2):
            for identity in range(3):
                await discovery._snapshots.write(identity,{'rows':[],'partial':False})
        self.assertEqual(len(list(discovery._snapshots.directory().glob('*.json'))),2)
        upper=date.today();lower=upper-timedelta(days=discovery.WINDOW_DAYS-1)
        identity=discovery.selection_identity('series',self.filters,lower.isoformat(),upper.isoformat(),True,'')
        await discovery._snapshots.write(identity,{'rows':[],'partial':False})
        path=discovery._snapshots.path(identity);saved=json.loads(path.read_text())
        saved['fetched_at']=time.time()-FRESH_SECONDS-100;path.write_text(json.dumps(saved))
        original=path.read_bytes()
        with patch.object(discovery,'_selection',AsyncMock(side_effect=RuntimeError('provider down'))):
            _,rows=await discovery.page('series','fixture',self.filters,preview=True)
            self.assertEqual(rows,[])
            await asyncio.gather(*(entry[0] for entry in list(discovery._pending.values())),return_exceptions=True)
            self.assertEqual(path.read_bytes(),original)
            with self.assertRaises(RuntimeError):
                await discovery.page('series','fixture',self.filters)
        saved['fetched_at']=time.time()-release_snapshots.STALE_SECONDS-1;path.write_text(json.dumps(saved))
        self.assertIsNone(await discovery._snapshots.read(identity))

    async def test_stale_preview_remains_available_when_refresh_capacity_is_full(self):
        import json,time
        from datetime import timedelta
        upper=date.today();lower=upper-timedelta(days=discovery.WINDOW_DAYS-1)
        identity=discovery.selection_identity('series',self.filters,lower.isoformat(),upper.isoformat(),True,'')
        await discovery._snapshots.write(identity,{'rows':[],'partial':False})
        path=discovery._snapshots.path(identity);saved=json.loads(path.read_text())
        saved['fetched_at']=time.time()-FRESH_SECONDS-100;path.write_text(json.dumps(saved))
        with patch.object(discovery,'MAX_SELECTIONS',0),patch.object(discovery,'_selection',AsyncMock()) as build:
            _,rows=await discovery.page('series','fixture',self.filters,preview=True)
            self.assertEqual(rows,[]);build.assert_not_awaited()
            with self.assertRaises(discovery.tmdb.TMDBUnavailable):
                await discovery.page('series','fixture',self.filters)

    async def test_ready_shelf_is_delivered_before_slow_newest_and_disconnect_cleans_reads(self):
        blocked=asyncio.Event(); cancelled=asyncio.Event()
        async def remote(term,kind,key,filters,*args,**kwargs):
            if filters['sort']=='newest':
                try:await blocked.wait()
                except asyncio.CancelledError:cancelled.set();raise
            return {},[]
        async def compose(*args,**kwargs):return {'results':[]}
        policies={sort:DiscoveryPolicy() for _,values in browse.section_specs() for sort in [values['sort']]}
        with patch('core.discovery_policy.viewer_policies',AsyncMock(return_value=policies)), \
             patch.object(browse,'remote_page',side_effect=remote), \
             patch.object(browse,'browse_page',side_effect=compose), \
             patch('core.tracking_editor.attach_editor_context',AsyncMock()):
            stream=browse.iter_browse_sections(None,None,media_type='series',region='DE',show_anime=True,key='fixture')
            first=await asyncio.wait_for(anext(stream),1)
            self.assertEqual(first['index'],0)
            await stream.aclose()
        self.assertTrue(cancelled.is_set())


@unittest.skipUnless(os.getenv('STATISTICS_TEST_DATABASE_URL'), 'Requires disposable PostgreSQL')
class BrowseStreamDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_new_http_client_reuses_completed_public_selection_without_reusing_viewer_state(self):
        import json,httpx
        from test_statistics_pg import StatisticsDatabaseTests
        from models import User,Media
        from models.base import MediaType
        from models.tracking import TrackedEntry
        fixture=StatisticsDatabaseTests(methodName='runTest')
        directory=self.enterContext(TemporaryDirectory())
        self.enterContext(patch.object(discovery,'_snapshots',ReleaseSnapshots(lambda:Path(directory))))
        discovery._cache._store.clear()
        await fixture.asyncSetUp()
        try:
            owner=fixture.viewer
            async with fixture.Session() as db:
                movie=await db.get(Media,fixture.media_id)
                movie.tmdb_id=1000;movie.release_date=date.today().isoformat()
                movie.tmdb_data={**movie.tmdb_data,'popularity':80,'vote_count':500}
                series=Media(title='Shared series',media_type=MediaType.series,tmdb_id=1000,
                    release_date=date.today().isoformat(),tmdb_data={'popularity':80,'vote_count':500})
                other=User(username='other-client',email='other-client@example.test',api_key='other-key')
                db.add_all([series,other]);await db.flush()
                db.add(TrackedEntry(user_id=owner.id,media_id=series.id,status='watching',rating_mode='manual',manual_score=7))
                await db.commit()
            raw=dict(id=1000,title='Shared movie',name='Shared series',popularity=80,vote_count=500,
                release_date=date.today().isoformat(),first_air_date=date.today().isoformat(),genre_ids=[18],runtime=100)
            async def provider(url,**kwargs):
                return {'results':[raw],'total_pages':1} if '/discover/' in url else raw
            async def provider_key(db,viewer_id):return f'key-for-{viewer_id}'
            with patch('core.tmdb._get',AsyncMock(side_effect=provider)) as read, \
                 patch('core.settings_store.get_user_tmdb_key',side_effect=provider_key), \
                 patch('core.external_scores.effective_mdblist_key',AsyncMock(return_value='fixture')), \
                 patch('core.mdblist_discovery.ranked_rows',AsyncMock(return_value=([raw],False))):
                for kind in ('movie','series'):
                    fixture.viewer=owner
                    response=await fixture.client.get('/tracking/browse/sections',params={'media_type':kind,'region':'DE','stream':'true'})
                    updates=[json.loads(line) for line in response.text.splitlines()]
                    self.assertEqual(len(updates),4,response.text)
                    owned=[row for update in updates for row in update['section']['results']]
                    self.assertTrue(any(row['score'] in (7,8) for row in owned))
                    calls=read.await_count;self.assertGreater(calls,0)
                    discovery._cache._store.clear()  # Also prove durable, worker-independent reuse.
                    fixture.viewer=other
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=fixture.app),base_url='http://other-client') as client:
                        response=await client.get('/tracking/browse/sections',params={'media_type':kind,'region':'DE','stream':'true'})
                    updates=[json.loads(line) for line in response.text.splitlines()]
                    self.assertEqual(len(updates),4,response.text)
                    self.assertEqual(read.await_count,calls)
                    rows=[row for update in updates for row in update['section']['results']]
                    self.assertTrue(rows)
                    self.assertTrue(all(row['score'] is None and row['list_status'] is None for row in rows))
        finally:
            await discovery.close()
            await fixture.asyncTearDown()

    async def test_stream_keeps_session_open_private_state_fresh_and_authentication_required(self):
        import json
        from test_statistics_pg import StatisticsDatabaseTests
        from models import User, Media
        fixture=StatisticsDatabaseTests(methodName='runTest')
        await fixture.asyncSetUp()
        try:
            async with fixture.Session() as db:
                media=await db.get(Media,fixture.media_id)
                media.tmdb_data={**media.tmdb_data,'popularity':80,'vote_count':500}
                await db.commit()
            with patch('core.settings_store.get_user_tmdb_key',AsyncMock(return_value=None)), \
                 patch('core.external_scores.effective_mdblist_key',AsyncMock(return_value=None)):
                owner=await fixture.client.get('/tracking/browse/sections?stream=true')
                self.assertEqual(owner.status_code,200,owner.text)
                self.assertEqual(owner.headers['cache-control'],'private, no-store')
                self.assertEqual(owner.headers['x-accel-buffering'],'no')
                sections=[json.loads(line) for line in owner.text.splitlines()]
                self.assertEqual({update['index'] for update in sections},set(range(4)))
                owned=[row for update in sections for row in update['section']['results'] if row['id']==fixture.media_id]
                self.assertTrue(owned)
                self.assertTrue(all(row['list_status']=='completed' and row['score']==8 for row in owned))
                async with fixture.Session() as db:
                    other=User(username='other-viewer',email='other@example.test',api_key='other-fixture')
                    db.add(other);await db.commit();fixture.viewer=other
                response=await fixture.client.get('/tracking/browse/sections?stream=true')
                rows=[row for line in response.text.splitlines() for row in json.loads(line)['section']['results']]
                self.assertTrue(all(row['list_status'] is None and row['score'] is None for row in rows))
                fixture.viewer=None
                self.assertEqual((await fixture.client.get('/tracking/browse/sections?stream=true')).status_code,401)
        finally:
            await fixture.asyncTearDown()
