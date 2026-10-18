"""Real catalogue detail projections and route authorization in disposable schemas."""
import importlib
import unittest
from unittest.mock import AsyncMock, patch

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select, text

import test_statistics_pg as fixture
from dependencies import get_current_user
from core.media_details import work_details
from core.media_images import IMAGE_POLICY_VERSION, BACKGROUND_POLICY_VERSION
from models import Follow, Media, Show, TrackedEntry, TrackingActivity, User, UserProfileData, WatchEvent
from models.base import MediaType, PrivacyLevel
from models.catalogue import (
    BookEdition, CatalogueCredit, CatalogueEntity, CatalogueIdentity,
    CatalogueRelationship, GameRelease, MetadataSnapshot, SteamPriceSnapshot,
)
from routers.catalogue import router


@unittest.skipUnless(fixture.URL, 'Requires explicit disposable statistics PostgreSQL')
class MediaDetailsTests(unittest.IsolatedAsyncioTestCase):
    async def test_preview_is_cached_without_gallery_downloads_and_preserves_existing_data(self):
        await self.login()
        raw={'backdrops':[
            {'file_path':'https://artworks.thetvdb.com/full.jpg','thumbnail':'https://artworks.thetvdb.com/small.jpg',
             'iso_639_1':None,'aspect_ratio':1.778,'vote_count':10}]}
        with (patch('core.settings_store.get_user_tmdb_key',AsyncMock(return_value='fixture')),
              patch('core.settings_store.get_user_tvdb_key',AsyncMock(return_value='fixture')),
              patch('core.screen_artwork.title_artwork',AsyncMock(return_value=raw)) as provider,
              patch('core.media_image_analysis.inspect_image',AsyncMock()) as inspect):
            response=await self.client.get(f'/tracking/title/{self.media_id}/artwork-preview')
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['artwork_preview'],{'image':'https://artworks.thetvdb.com/full.jpg',
                'thumbnail':'https://artworks.thetvdb.com/small.jpg'})
            await self.client.get(f'/tracking/title/{self.media_id}/artwork-preview')
            provider.assert_awaited_once();inspect.assert_not_awaited()
        async with self.Session() as db:
            media=await db.get(Media,self.media_id)
            self.assertNotIn('_anylist_images',media.tmdb_data)
            self.assertTrue(media.tmdb_data['_anylist_artwork_preview']['expires_at'])

    async def test_cast_images_prefer_current_title_then_verified_actor_role_elsewhere(self):
        from core.screen_characters import project_characters
        async with self.Session() as db:
            media = await db.get(Media,self.media_id)
            from core.media_details import screen_work
            work = await screen_work(db,media)
            credit = await db.scalar(select(CatalogueCredit).where(CatalogueCredit.work_id==work.id,CatalogueCredit.role=='actor'))
            actor = await db.get(CatalogueEntity,credit.contributor_id)
            actor.image_url='https://example.test/actor.jpg'
            donor=CatalogueEntity(kind='movie',name='Other appearance',attributes={})
            unrelated=CatalogueEntity(kind='person',name='Different actor',attributes={})
            db.add_all([donor,unrelated]);await db.flush()
            db.add_all([CatalogueCredit(work_id=donor.id,contributor_id=actor.id,provider='tvdb',source_key='donor',role='actor',character_label=credit.character_label,character_image_url='https://example.test/other-role.jpg'),
                CatalogueCredit(work_id=donor.id,contributor_id=unrelated.id,provider='tvdb',source_key='unrelated',role='actor',character_label=credit.character_label,character_image_url='https://example.test/wrong-actor.jpg')])
            await db.flush()
            await project_characters(db,work.id);await project_characters(db,donor.id);await db.commit()
            people=(await work_details(db,work))['people']
            self.assertEqual(people[0]['character_images'],['https://artworks.thetvdb.com/role.jpg','https://example.test/other-role.jpg'])
            self.assertEqual(people[0]['image'],'https://example.test/actor.jpg')
            credit.character_image_url=None;await db.commit()
            self.assertEqual((await work_details(db,work))['people'][0]['character_image'],'https://example.test/other-role.jpg')

    async def asyncSetUp(self):
        await fixture.StatisticsDatabaseTests.asyncSetUp(self)
        self.app.include_router(router)

    async def asyncTearDown(self):
        await fixture.StatisticsDatabaseTests.asyncTearDown(self)

    async def login(self):
        async with self.Session() as db:
            self.viewer = await db.get(User, self.user_id)
        self.app.dependency_overrides[get_current_user] = lambda: self.viewer

    async def test_image_cache_is_separate_non_destructive_and_reused(self):
        self.assertEqual((await self.client.get(f'/tracking/title/{self.media_id}/images')).status_code, 401)
        await self.login()
        async with self.Session() as db:
            media = await db.get(Media, self.media_id)
            media.tmdb_id = 99
            media.imdb_id = 'tt0133093'
            media.tmdb_data = {'protected_fixture': 'keep'}
            await db.commit()
        raw = {'id':99, 'posters':[{'file_path':'https://artworks.thetvdb.com/banners/poster.jpg',
                                  'thumbnail':'https://artworks.thetvdb.com/banners/poster_t.jpg','iso_639_1':'en','aspect_ratio':.667}],
               'backdrops':[{'file_path':'https://artworks.thetvdb.com/banners/scene.jpg','iso_639_1':None,'aspect_ratio':1.778},
                            {'file_path':'https://artworks.thetvdb.com/banners/text.jpg','iso_639_1':'en','aspect_ratio':1.778}]}
        with (patch('core.settings_store.get_user_tmdb_key', AsyncMock(return_value='fixture')),
              patch('core.settings_store.get_user_tvdb_key', AsyncMock(return_value='fixture')),
              patch('core.tmdb.get_movie_light', AsyncMock(return_value={'id':99,'poster_path':'/primary.jpg','backdrop_path':'/text.jpg'})),
              patch('core.media_image_analysis.inspect_image', AsyncMock(return_value=None)) as inspect,
              patch('core.screen_artwork.title_artwork', AsyncMock(return_value=raw)) as fetch):
            data = (await self.client.get(f'/tracking/title/{self.media_id}/images')).json()
            self.assertIn('https://artworks.thetvdb.com/banners/poster.jpg', data['posters'])
            self.assertEqual(data['posters'][0], 'https://image.tmdb.org/t/p/original/primary.jpg')
            self.assertEqual(data['artwork'], ['https://artworks.thetvdb.com/banners/scene.jpg'])
            inspect.assert_any_await('https://artworks.thetvdb.com/banners/poster.jpg',
                                    unittest.mock.ANY, 'https://artworks.thetvdb.com/banners/poster_t.jpg')
            await self.client.get(f'/tracking/title/{self.media_id}/images')
            fetch.assert_awaited_once()
        async with self.Session() as db:
            media = await db.get(Media, self.media_id)
            self.assertEqual(media.tmdb_data['protected_fixture'], 'keep')
        projected = (await self.client.get(f'/tracking/title/{self.media_id}')).json()
        self.assertEqual(projected['artwork'], data['artwork'])
        self.assertEqual(projected['poster'], data['poster'])
        self.assertEqual(projected['backdrop'], 'https://images.metahub.space/background/high/tt0133093/img')
        self.assertNotIn(projected['backdrop'],data['artwork'])

    async def test_image_only_reads_skip_credits_and_people_reads_skip_unrelated_graphs(self):
        await self.login()
        with (patch('core.settings_store.get_user_tmdb_key',AsyncMock(return_value=None)),
              patch('core.media_images.fill_screen_images',AsyncMock()),
              patch('core.media_details.work_details',AsyncMock(side_effect=AssertionError('Image read loaded credits')))):
            response=await self.client.get(f'/tracking/title/{self.media_id}/images')
            self.assertEqual(response.status_code,200,response.text)
        async with self.Session() as db:
            from core.media_details import screen_work
            work=await screen_work(db,await db.get(Media,self.media_id))
            with patch('core.media_related.catalogue_related',AsyncMock(side_effect=AssertionError('People read loaded relations'))):
                people=await work_details(db,work,extras=False)
            self.assertTrue(people['people']);self.assertNotIn('artwork',people)

    async def test_disk_warming_includes_tracked_primary_artwork_only(self):
        from core.image_cache import pre_cache_all_collected
        async with self.Session() as db:
            media=await db.get(Media,self.media_id)
            media.poster_path='/old.jpg';media.backdrop_path='/old-background.jpg'
            media.media_type=MediaType.series
            media.tmdb_data={'_anylist_backdrop':{'version':BACKGROUND_POLICY_VERSION,'image':'https://image.tmdb.org/t/p/original/background.jpg'},'_anylist_images':{'version':IMAGE_POLICY_VERSION,
                'primary_poster':'https://image.tmdb.org/t/p/original/primary.jpg',
                'primary_backdrop':'https://image.tmdb.org/t/p/original/background.jpg',
                'posters':['https://image.tmdb.org/t/p/original/extra.jpg'],
                'artwork':['https://image.tmdb.org/t/p/original/background.jpg','https://image.tmdb.org/t/p/original/extra-art.jpg']}}
            await db.commit()
            with patch('core.image_cache.download_and_cache_image',AsyncMock()) as download,patch('core.image_cache.asyncio.sleep',AsyncMock()):
                await pre_cache_all_collected(db)
            warmed={(call.args[1],call.args[2]) for call in download.await_args_list}
            self.assertEqual(warmed,{('w342','/primary.jpg'),('w1280','/background.jpg')})

    async def test_optional_disk_image_route_serves_cached_bytes_and_disabled_mode_redirects(self):
        import tempfile
        from pathlib import Path
        import httpx
        from core import image_cache
        from models import GlobalSettings
        from routers.media import router as media_router
        self.app.include_router(media_router,prefix='/media')
        async with self.Session() as db:
            settings=await db.get(GlobalSettings,1)
            settings.image_cache_enabled=True;settings.image_cache_limit_gb=None;settings.enable_logged_out_navigation=True
            await db.commit()
        seen=[]
        client=httpx.AsyncClient
        def origin(request):
            seen.append(str(request.url))
            return httpx.Response(200,content=b'image-fixture',headers={'Content-Type':'image/jpeg'})
        with tempfile.TemporaryDirectory() as directory,patch.object(image_cache.settings,'data_dir',Path(directory)),patch.object(image_cache.httpx,'AsyncClient',side_effect=lambda **kw:client(transport=httpx.MockTransport(origin),**kw)):
            first=await self.client.get('/media/image/w300/still.jpg')
            second=await self.client.get('/media/image/w300/still.jpg')
            self.assertEqual(first.status_code,200,first.text);self.assertEqual(second.content,b'image-fixture')
            self.assertIn('immutable',second.headers['Cache-Control']);self.assertEqual(len(seen),1)
        async with self.Session() as db:
            settings=await db.get(GlobalSettings,1);settings.image_cache_enabled=False;await db.commit()
        redirect=await self.client.get('/media/image/w300/still.jpg')
        self.assertTrue(redirect.is_redirect)
        self.assertEqual(redirect.headers['Location'],'https://image.tmdb.org/t/p/w300/still.jpg')

    async def test_cold_background_load_skips_gallery_analysis_and_is_cached_for_first_render(self):
        await self.login()
        async with self.Session() as db:
            media=await db.get(Media,self.media_id);media.media_type=MediaType.series;media.tmdb_id=99;media.backdrop_path='/primary.jpg'
            media.tmdb_data={'protected_fixture':'keep'};await db.commit()
        raw={'id':99,'backdrops':[
            {'file_path':'/text.jpg','iso_639_1':'en','aspect_ratio':1.778,'vote_average':10},
            {'file_path':'/alternative.jpg','iso_639_1':None,'aspect_ratio':1.778,'vote_average':9,'vote_count':50},
            {'file_path':'/primary.jpg','iso_639_1':None,'aspect_ratio':1.778,'vote_average':8,'vote_count':500}]}
        with (patch('core.settings_store.get_user_tmdb_key',AsyncMock(return_value='fixture')),
              patch('core.settings_store.get_user_tvdb_key',AsyncMock(return_value='fixture')),
              patch('core.tmdb.get_title_images',AsyncMock(return_value=raw)) as fetch,
              patch('core.screen_artwork.title_artwork',AsyncMock()) as details,
              patch('core.media_image_analysis.inspect_image',AsyncMock()) as inspect):
            response=await self.client.get(f'/tracking/title/{self.media_id}/background')
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['backdrop'],'https://image.tmdb.org/t/p/original/alternative.jpg')
            self.assertEqual(response.json()['background_status']['state'],'ready')
            await self.client.get(f'/tracking/title/{self.media_id}/background')
            projected=(await self.client.get(f'/tracking/title/{self.media_id}')).json()
            self.assertEqual(projected['backdrop'],response.json()['backdrop'])
            self.assertEqual(projected['image_status']['state'],'pending')
            fetch.assert_awaited_once();details.assert_not_awaited();inspect.assert_not_awaited()
        async with self.Session() as db:
            media=await db.get(Media,self.media_id)
            self.assertEqual(media.tmdb_data['protected_fixture'],'keep')
            self.assertNotIn('_anylist_images',media.tmdb_data)

    async def test_movie_background_uses_direct_metahub_without_gallery_or_keys(self):
        await self.login()
        async with self.Session() as db:
            media=await db.get(Media,self.media_id)
            media.imdb_id='tt0133093'
            media.tmdb_data={'_anylist_images':{'version':IMAGE_POLICY_VERSION,
                'primary_backdrop':'https://artworks.thetvdb.com/banners/old.jpg',
                'artwork':['https://artworks.thetvdb.com/banners/old.jpg']}}
            await db.commit()
        with (patch('core.settings_store.get_user_tmdb_key',AsyncMock(return_value=None)) as key,
              patch('core.tmdb.get_external_ids',AsyncMock(return_value={'imdb_id':'tt0133093'})) as ids,
              patch('core.screen_artwork.title_artwork',AsyncMock()) as gallery):
            hero=(await self.client.get(f'/tracking/title/{self.media_id}/background')).json()
            self.assertEqual(hero['backdrop'],'https://images.metahub.space/background/high/tt0133093/img')
            self.assertEqual(hero['background_status']['state'],'ready')
            projected=(await self.client.get(f'/tracking/title/{self.media_id}')).json()
            self.assertEqual(projected['backdrop'],hero['backdrop'])
            self.assertEqual(projected['artwork'],['https://artworks.thetvdb.com/banners/old.jpg'])
            ids.assert_not_awaited();gallery.assert_not_awaited()
            async with self.Session() as db:
                media=await db.get(Media,self.media_id)
                media.imdb_id=None;media.tmdb_id=99
                await db.commit()
            key.return_value='fixture'
            for _ in range(2):
                bridged=(await self.client.get(f'/tracking/title/{self.media_id}/background')).json()
                self.assertEqual(bridged['backdrop'],hero['backdrop'])
            ids.assert_awaited_once_with(99,'movie',api_key='fixture')
            gallery.assert_not_awaited()

    async def test_background_public_access_uses_the_same_gate_as_the_title(self):
        from dependencies import get_optional_user
        from models import GlobalSettings
        self.app.dependency_overrides[get_optional_user]=lambda:None
        with patch('core.settings_store.get_user_tmdb_key',AsyncMock(return_value=None)):
            self.assertEqual((await self.client.get(f'/tracking/title/{self.media_id}/background')).status_code,200)
            async with self.Session() as db:
                settings=await db.get(GlobalSettings,1);settings.enable_logged_out_navigation=False;await db.commit()
            self.assertEqual((await self.client.get(f'/tracking/title/{self.media_id}/background')).status_code,401)

    async def test_background_failure_records_cooldown_and_retries_without_leaking_unverified_art(self):
        await self.login()
        async with self.Session() as db:
            media=await db.get(Media,self.media_id);media.media_type=MediaType.series;media.tmdb_id=99;media.tmdb_data={};await db.commit()
        with (patch('core.settings_store.get_user_tmdb_key',AsyncMock(return_value='fixture')),
              patch('core.settings_store.get_user_tvdb_key',AsyncMock(return_value='fixture')),
              patch('core.tmdb.get_title_images',AsyncMock(side_effect=RuntimeError('Unavailable'))) as fetch):
            for _ in range(2):
                response=await self.client.get(f'/tracking/title/{self.media_id}/background')
                self.assertEqual(response.status_code,200,response.text)
                self.assertEqual(response.json()['background_status']['state'],'deferred')
                self.assertGreater(response.json()['background_status']['retry_after'],0)
                self.assertIsNone(response.json()['backdrop'])
            fetch.assert_awaited_once()

    async def test_image_provider_failure_preserves_cache_and_cools_down(self):
        await self.login()
        async with self.Session() as db:
            media = await db.get(Media, self.media_id)
            media.tmdb_id = 99
            media.tmdb_data = {'_anylist_images': {'version':IMAGE_POLICY_VERSION, 'posters':['https://example.test/old.jpg']}}
            await db.commit()
        with (patch('core.settings_store.get_user_tmdb_key', AsyncMock(return_value='fixture')),
              patch('core.settings_store.get_user_tvdb_key', AsyncMock(return_value='fixture')),
              patch('core.tmdb.get_movie_light', AsyncMock(return_value={'id':99})),
              patch('core.screen_artwork.title_artwork', AsyncMock(side_effect=RuntimeError('Unavailable'))) as fetch):
            for _ in range(2):
                response = await self.client.get(f'/tracking/title/{self.media_id}/images')
                self.assertEqual(response.status_code, 200)
                self.assertIn('https://example.test/old.jpg', response.json()['posters'])
            fetch.assert_awaited_once()

    async def test_legacy_image_cache_is_refreshed_even_when_its_expiry_is_in_the_future(self):
        await self.login()
        async with self.Session() as db:
            media = await db.get(Media, self.media_id)
            media.tmdb_id = 99
            media.tmdb_data = {'_anylist_images': {'expires_at':'2099-01-01T00:00:00+00:00',
                'retry_after':'2099-01-01T00:00:00+00:00', 'retry_version':0,
                'posters':['https://example.test/unverified.jpg']}}
            await db.commit()
        raw = {'id':99,'posters':[{'file_path':'https://artworks.thetvdb.com/banners/foreign.jpg','iso_639_1':'de'},
            {'file_path':'https://artworks.thetvdb.com/banners/english.jpg','iso_639_1':'en'}],'backdrops':[]}
        with (patch('core.settings_store.get_user_tmdb_key', AsyncMock(return_value='fixture')),
              patch('core.settings_store.get_user_tvdb_key', AsyncMock(return_value='fixture')),
              patch('core.tmdb.get_movie_light', AsyncMock(return_value={'id':99,'poster_path':'/english.jpg'})),
              patch('core.media_image_analysis.inspect_image', AsyncMock(return_value=None)),
              patch('core.screen_artwork.title_artwork', AsyncMock(return_value=raw)) as fetch):
            response = await self.client.get(f'/tracking/title/{self.media_id}/images')
            self.assertEqual(response.json()['posters'], ['https://image.tmdb.org/t/p/original/english.jpg', 'https://artworks.thetvdb.com/banners/english.jpg'])
            fetch.assert_awaited_once()

    async def test_episode_rows_use_canonical_catalogue_and_private_watch_history(self):
        await self.login()
        async with self.Session() as db:
            show=Show(title='Episode fixture',tmdb_id=989)
            db.add(show);await db.flush()
            media=Media(title='Series fixture',media_type=MediaType.series,tmdb_id=989,tmdb_data={
                'seasons':[{'season_number':1,'episode_count':2},{'season_number':2,'episode_count':1}],
                'tracking_episode_ids':[9001,9002,9003],'tracking_catalogue_refreshed_at':'2026-10-11T00:00:00Z'})
            episodes=[Media(title=name,media_type=MediaType.episode,show_id=show.id,tmdb_id=native,
                season_number=number,episode_number=position,release_date=date,runtime=45,poster_path='/still.jpg')
                for native,number,position,name,date in [(9001,1,1,'Watched', '2020-01-01'),(9002,1,2,'Not watched','2020-01-02'),(9003,2,1,'Future','2099-01-01'),(9999,1,3,'Stale identity','2020-01-03')]]
            db.add_all([media,*episodes]);await db.flush()
            other=User(username='other-viewer',email='other-viewer@example.test',api_key='other-fixture')
            db.add(other);await db.flush()
            db.add_all([WatchEvent(user_id=self.user_id,media_id=episodes[0].id,completed=True),
                WatchEvent(user_id=self.user_id,media_id=episodes[0].id,completed=True),
                WatchEvent(user_id=other.id,media_id=episodes[1].id,completed=True)])
            await db.commit();media_id=media.id
        data=(await self.client.get(f'/tracking/title/{media_id}')).json()
        self.assertEqual(data['seasons'][0]['released_count'],2)
        self.assertEqual(data['seasons'][0]['watched_count'],1)
        self.assertEqual([episode['watched'] for episode in data['seasons'][0]['episodes']],[True,False])
        self.assertEqual(data['seasons'][0]['episodes'][0]['image'],'/still.jpg')
        self.assertFalse(data['seasons'][1]['episodes'][0]['released'])
        self.viewer=None
        public=(await self.client.get(f'/tracking/title/{media_id}')).json()
        self.assertFalse(any(episode['watched'] for season in public['seasons'] for episode in season['episodes']))

    async def test_individual_episode_edits_preserve_gaps_and_later_history(self):
        await self.login()
        async with self.Session() as db:
            show=Show(title='Exact episode fixture',tmdb_id=987)
            db.add(show);await db.flush()
            series=Media(title=show.title,media_type=MediaType.series,tmdb_id=987,tmdb_data={
                'seasons':[{'season_number':1,'episode_count':4}],
                'tracking_episode_ids':[8001,8002,8003,8004],'tracking_catalogue_refreshed_at':'2026-10-11T00:00:00Z'})
            episodes=[Media(title=f'Episode {number}',media_type=MediaType.episode,show_id=show.id,
                tmdb_id=8000+number,season_number=1,episode_number=number,release_date='2099-01-01' if number==4 else '2020-01-01') for number in range(1,5)]
            db.add_all([series,*episodes]);await db.commit();series_id=series.id;ids=[episode.id for episode in episodes]
        with patch('core.local_outbound.dispatch_local_tracking_delta',new=AsyncMock()):
            url=f'/tracking/entry/{series_id}/episode/'
            async def check_activity(count, first, last):
                async with self.Session() as db:
                    card=await db.scalar(select(TrackingActivity).where(
                        TrackingActivity.user_id==self.user_id,TrackingActivity.media_id==series_id))
                    self.assertEqual(card.payload['episodes_watched'],count)
                    self.assertEqual(card.payload['position_start'],first)
                    self.assertEqual(card.payload['position'],last)
                    self.assertEqual(card.payload['finished_seasons'],[])
            response=await self.client.patch(url+str(ids[2]),json={'watched':True})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['status'],'watching')
            self.assertEqual(response.json()['progress'],1)
            self.assertEqual(response.json()['season_position'],'S1E3')
            await check_activity(1,'S1E3','S1E3')
            data=(await self.client.get(f'/tracking/title/{series_id}')).json()
            self.assertEqual([row['watched'] for row in data['seasons'][0]['episodes']],[False,False,True,False])
            await self.client.patch(url+str(ids[2]),json={'watched':True})
            await check_activity(1,'S1E3','S1E3')
            async with self.Session() as db:
                self.assertEqual(len(list(await db.scalars(select(WatchEvent).where(WatchEvent.user_id==self.user_id,WatchEvent.media_id==ids[2])))),1)
                self.assertEqual((await db.scalar(select(TrackedEntry).where(
                    TrackedEntry.user_id==self.user_id,TrackedEntry.media_id==series_id))).progress,1)
            for identifier in ids[:2]:
                response=await self.client.patch(url+str(identifier),json={'watched':True})
            self.assertEqual(response.json()['status'],'completed')
            self.assertEqual(response.json()['progress'],3)
            await check_activity(3,'S1E1','S1E3')
            response=await self.client.patch(url+str(ids[0]),json={'watched':False})
            self.assertEqual(response.json()['status'],'watching')
            self.assertEqual(response.json()['progress'],2)
            self.assertEqual(response.json()['season_position'],'S1E3')
            await check_activity(2,'S1E2','S1E3')
            data=(await self.client.get(f'/tracking/title/{series_id}')).json()
            self.assertEqual(data['seasons'][0]['watched_count'],2)
            self.assertEqual([row['watched'] for row in data['seasons'][0]['episodes']],[False,True,True,False])
            self.assertEqual((await self.client.patch(url+str(ids[3]),json={'watched':True})).status_code,409)
            self.assertEqual((await self.client.patch(url+str(self.media_id),json={'watched':True})).status_code,409)

    async def test_favorite_downgrade_requires_legacy_favorite_evidence(self):
        def migrate(conn):
            migration = importlib.import_module('migrations.versions.mt044_independent_favorites')
            params = {'user':self.user_id, 'media':self.media_id}
            conn.execute(text('INSERT INTO media_favorites(user_id,media_id) VALUES (:user,:media)'), params)
            with Operations.context(MigrationContext.configure(conn)):
                # A later import can create an entry with the legacy flag false.
                self.assertFalse(conn.scalar(text('SELECT favorite FROM tracked_entries WHERE user_id=:user AND media_id=:media'), params))
                with self.assertRaisesRegex(RuntimeError, 'favorites must be preserved'):
                    migration.downgrade()
                conn.execute(text('DELETE FROM tracked_entries WHERE user_id=:user AND media_id=:media'), params)
                with self.assertRaisesRegex(RuntimeError, 'favorites must be preserved'):
                    migration.downgrade()
                self.assertEqual(conn.scalar(text('SELECT count(*) FROM media_favorites')), 1)
                conn.execute(TrackedEntry.__table__.insert().values(
                    user_id=self.user_id,media_id=self.media_id,status='planning',rating_mode='manual',favorite=True))
                migration.downgrade()
                self.assertTrue(conn.scalar(text('SELECT favorite FROM tracked_entries WHERE user_id=:user AND media_id=:media'), params))
                migration.upgrade()
                self.assertEqual(conn.scalar(text('SELECT count(*) FROM media_favorites')), 1)
        async with self.engine.begin() as conn:
            await conn.run_sync(migrate)

    async def test_relation_migration_preserves_existing_edges_and_refuses_lossy_downgrade(self):
        def migrate(conn):
            migration = importlib.import_module('migrations.versions.mt043_media_relations')
            with Operations.context(MigrationContext.configure(conn)):
                migration.downgrade()
                source = conn.scalar(text("SELECT id FROM catalogue_entities WHERE kind='movie' LIMIT 1"))
                target = conn.scalar(text("INSERT INTO catalogue_entities(kind,name,attributes) VALUES ('book','Migration source','{}') RETURNING id"))
                params = {'source':source, 'target':target}
                conn.execute(text("INSERT INTO catalogue_relationships(source_id,target_id,relation,provider,attributes) VALUES (:source,:target,'recommendation','tmdb','{}')"), params)
                migration.upgrade()
                conn.execute(text("INSERT INTO catalogue_relationships(source_id,target_id,relation,provider,attributes) VALUES (:source,:target,'based_on','tmdb','{}')"), params)
                with self.assertRaisesRegex(RuntimeError, 'existing data was preserved'):
                    migration.downgrade()
                self.assertEqual(conn.scalar(text('SELECT count(*) FROM catalogue_relationships')), 2)
                conn.execute(text("DELETE FROM catalogue_relationships WHERE relation='based_on'"))
                migration.downgrade()
                self.assertEqual(conn.scalar(text('SELECT count(*) FROM catalogue_relationships')), 1)
                definition = conn.scalar(text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='catalogue_relationships'::regclass AND conname='ck_catalogue_relationship_type'"))
                self.assertNotIn('based_on', definition)
        async with self.engine.begin() as conn:
            await conn.run_sync(migrate)

    async def test_screen_credits_deduplicate_providers_and_retain_multiple_characters(self):
        async with self.Session() as db:
            work = await db.scalar(select(CatalogueEntity).where(CatalogueEntity.kind == 'movie'))
            actor = await db.scalar(select(CatalogueEntity).where(CatalogueEntity.kind == 'person'))
            db.add(CatalogueCredit(work_id=work.id, contributor_id=actor.id, provider='tmdb',
                source_key='extra-role', role='actor', character_label='Second Character'))
            await db.commit()
            details = await work_details(db, work)
            self.assertEqual(len(details['people']), 1)
            self.assertEqual(set(details['people'][0]['characters']), {'Fixture Character', 'Second Character'})
            self.assertEqual(details['people'][0]['url'], f'/actors/catalogue:{actor.id}')
            # The fixture contains two provider credits for this studio.
            self.assertEqual([p['name'] for p in details['organizations']], ['Fixture Studio', 'Fixture Network'])
            self.assertTrue(details['organizations'][0]['url'].startswith('/studio/'))
            self.assertIsNone(details['organizations'][1]['url'])
        response = await self.client.get(f'/tracking/title/{self.media_id}')
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()['entry'])
        self.assertEqual(len(response.json()['people']), 1)

    async def test_game_details_use_stored_releases_relations_prices_and_safe_artwork(self):
        async with self.Session() as db:
            game = CatalogueEntity(kind='game', name='Fixture game', attributes={
                'release_date':'2020-01-01', 'artwork':['javascript:alert(1)', 'https://example.test/game.jpg']})
            other = CatalogueEntity(kind='game', name='Expansion', attributes={})
            release = CatalogueEntity(kind='release', name='PC edition', attributes={})
            db.add_all([game, other, release]); await db.flush()
            db.add(CatalogueIdentity(entity_id=game.id, namespace='steam.app', external_id='123', source='igdb'))
            db.add(GameRelease(entity_id=release.id, work_id=game.id, platform_namespace='igdb.platform',
                platform_id='6', platform_name='PC', region='Worldwide', release_date='2020-01-01'))
            db.add(CatalogueRelationship(source_id=other.id, target_id=game.id, relation='expansion', provider='igdb'))
            db.add(SteamPriceSnapshot(work_id=game.id, steam_app_id='123', country='DE',
                current={'currency':'EUR','amount':7.99,'amountInt':799}, url='https://store.steampowered.com/app/123/', payload={}))
            db.add(MetadataSnapshot(provider='igdb', kind='game', external_id='99', entity_id=game.id,
                payload={'url':'javascript:alert(1)'}, coverage={}))
            await db.commit(); game_id, other_id = game.id, other.id
        self.assertEqual((await self.client.get(f'/catalogue/entities/{game_id}/details')).status_code, 401)
        await self.login()
        response = await self.client.get(f'/catalogue/entities/{game_id}/details')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['artwork'], ['https://example.test/game.jpg'])
        self.assertEqual(data['releases'][0]['platform'], 'PC')
        self.assertEqual(data['relations'][0]['url'], f'/games/{other_id}')
        self.assertTrue(data['relations'][0]['reverse'])
        self.assertEqual(data['prices'][0]['country'], 'DE')
        self.assertEqual(data['sources'], [{'name':'Steam','url':'https://store.steampowered.com/app/123/'}])
        self.assertFalse(data['tracking_available'])

    async def test_following_ratings_respect_privacy_and_country_falls_back_to_screen_metadata(self):
        async with self.Session() as db:
            media = await db.get(Media, self.media_id)
            media.tmdb_data = {'origin_country':['US'], 'production_countries':[{'iso_3166_1':'CA'}]}
            for username, privacy in [('public-friend', PrivacyLevel.public), ('private-friend', PrivacyLevel.private)]:
                friend = User(username=username, email=username+'@example.test', api_key=username+'-fixture-only')
                db.add(friend); await db.flush()
                db.add_all([UserProfileData(user_id=friend.id, privacy_level=privacy,
                    display_name='Display '+username, avatar_path='fixture-avatar.png'),
                    Follow(follower_id=self.user_id, following_id=friend.id),
                    TrackedEntry(user_id=friend.id, media_id=self.media_id, status='completed', manual_score=9)])
            await db.commit()
        await self.login()
        data = (await self.client.get(f'/tracking/title/{self.media_id}')).json()
        self.assertEqual(data['countries'], ['US'])
        self.assertEqual([row['username'] for row in data['friends']], ['public-friend'])
        self.assertEqual(data['friends'][0]['display_name'], 'Display public-friend')
        self.assertTrue(data['friends'][0]['has_avatar'])
        self.assertNotIn('avatar_path', data['friends'][0])
        self.assertEqual(data['friends_average'], 9)

    async def test_book_details_preserve_edition_fields_and_reject_non_work_entities(self):
        async with self.Session() as db:
            book = CatalogueEntity(kind='book', name='Fixture book', attributes={'release_date':'1965','pages':896})
            edition = CatalogueEntity(kind='edition', name='Paperback', image_url='https://example.test/book.jpg', attributes={})
            db.add_all([book, edition]); await db.flush()
            db.add(CatalogueIdentity(entity_id=book.id, namespace='openlibrary.book', external_id='OL123W', source='openlibrary'))
            db.add(BookEdition(entity_id=edition.id, work_id=book.id, pages=896, format='Paperback',
                language='English', release_date='2020-04-01'))
            await db.commit(); book_id, edition_id = book.id, edition.id
        await self.login()
        response = await self.client.get(f'/catalogue/entities/{book_id}/details')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['year'], '1965')
        self.assertEqual(data['editions'][0]['pages'], 896)
        self.assertEqual(data['sources'][0]['url'], 'https://openlibrary.org/works/OL123W')
        self.assertEqual((await self.client.get(f'/catalogue/entities/{edition_id}/details')).status_code, 404)
        self.assertEqual((await self.client.get('/catalogue/entities/0/details')).status_code, 422)

    async def test_relations_cross_types_while_recommendations_stay_in_the_current_type(self):
        async with self.Session() as db:
            work = await db.scalar(select(CatalogueEntity).where(CatalogueEntity.kind == 'movie'))
            book = CatalogueEntity(kind='book', name='Original novel', attributes={})
            movie = CatalogueEntity(kind='movie', name='Similar movie', attributes={'genres':[{'name':'Drama'}, 'Mystery']})
            series = CatalogueEntity(kind='series', name='Related series', attributes={})
            game = CatalogueEntity(kind='game', name='A game', attributes={})
            other_game = CatalogueEntity(kind='game', name='Similar game', attributes={})
            db.add_all([book,movie,series,game,other_game]); await db.flush()
            db.add_all([CatalogueIdentity(entity_id=movie.id, namespace='tmdb.movie', external_id='55', source='tmdb'),
                CatalogueIdentity(entity_id=series.id, namespace='tmdb.series', external_id='66', source='tmdb'),
                CatalogueRelationship(source_id=work.id,target_id=book.id,relation='based_on',provider='manual'),
                CatalogueRelationship(source_id=work.id,target_id=movie.id,relation='recommendation',provider='tmdb'),
                CatalogueRelationship(source_id=work.id,target_id=series.id,relation='spin_off',provider='manual'),
                CatalogueRelationship(source_id=game.id,target_id=other_game.id,relation='recommendation',provider='igdb')])
            await db.commit()
            details = await work_details(db,work)
            self.assertEqual({row['type'] for row in details['relations']}, {'book','series'})
            self.assertEqual([row['type'] for row in details['similar']], ['movie'])
            self.assertEqual(details['similar'][0]['url'], '/discover-title?type=movie&id=55')
            self.assertEqual(details['similar'][0]['genres'], ['Drama','Mystery'])
            self.assertEqual((await work_details(db,game))['similar'][0]['url'], f'/games/{other_game.id}')
            self.assertTrue((await work_details(db,book))['relations'][0]['reverse'])

    async def test_book_series_members_and_author_suggestions_are_separate(self):
        async with self.Session() as db:
            books = [CatalogueEntity(kind='book',name=name,attributes={}) for name in ('First','Second','Other story')]
            series = CatalogueEntity(kind='book_series',name='Fixture trilogy',attributes={})
            author = CatalogueEntity(kind='person',name='Author',attributes={})
            db.add_all([*books,series,author]); await db.flush()
            for book in books:
                db.add(CatalogueCredit(work_id=book.id,contributor_id=author.id,provider='hardcover',source_key=f'author:{book.id}',role='author'))
            for book in books[:2]:
                db.add(CatalogueRelationship(source_id=book.id,target_id=series.id,relation='series_member',provider='hardcover'))
            await db.commit()
            details = await work_details(db,books[0])
            self.assertEqual([row['title'] for row in details['relations']], ['Second'])
            self.assertEqual(details['relations'][0]['label'], 'Fixture trilogy')
            self.assertEqual([row['title'] for row in details['similar']], ['Other story'])

    async def test_collection_cache_is_non_destructive_and_recommendations_exclude_collection_and_other_types(self):
        await self.login()
        async with self.Session() as db:
            media = await db.get(Media,self.media_id)
            media.tmdb_id = 99
            media.tmdb_data = {'belongs_to_collection':{'id':10}, 'protected_fixture':'keep'}
            await db.commit()
        collection = {'id':10,'name':'Fixture collection','parts':[{'id':99,'title':'Current','release_date':'2020-01-01'}, {'id':55,'title':'Earlier','release_date':'2010-01-01'}]}
        recommendations = {'results':[{'id':55,'title':'Earlier'}, {'id':79,'title':'Short movie','vote_count':500,'runtime':2},
            {'id':80,'title':'Documentary','vote_count':500,'genre_ids':[99]}, {'id':78,'title':'Low vote movie','vote_count':249},
            {'id':81,'title':'Future movie','vote_count':0,'release_date':'2099-01-01'},
            {'id':77,'title':'Another movie','vote_count':250,'runtime':30,'poster_path':'/cover.jpg','genre_ids':[878,28,999999]},
            {'id':66,'name':'A series','media_type':'tv'}, {'id':99,'title':'Current'}, {'id':88,'title':'Adult','adult':True}]}
        async with self.Session() as db:
            profile = await db.scalar(select(UserProfileData).where(UserProfileData.user_id == self.user_id))
            profile.excluded_categories = [99]
            await db.commit()
        with (patch('core.settings_store.get_user_tmdb_key',new=AsyncMock(return_value='fixture-only')),
                patch('core.tmdb.get_collection',new=AsyncMock(return_value=collection)) as fetch_collection,
                patch('core.tmdb.get_movie_recommendations',new=AsyncMock(return_value=recommendations)) as fetch_recommendations):
            response = await self.client.get(f'/tracking/title/{self.media_id}/related')
            self.assertEqual(response.status_code,200,response.text)
            data = response.json()
            self.assertEqual([row['title'] for row in data['relations']], ['Earlier'])
            self.assertEqual(data['relations'][0]['label'], 'Previous entry')
            self.assertEqual([row['title'] for row in data['similar']], ['Future movie','Another movie'])
            self.assertEqual(data['similar'][1]['poster'], 'https://image.tmdb.org/t/p/w500/cover.jpg')
            self.assertEqual(data['similar'][1]['genres'], ['Science Fiction','Action'])
            self.assertFalse(data['related_pending'])
            async with self.Session() as db:
                profile = await db.scalar(select(UserProfileData).where(UserProfileData.user_id == self.user_id))
                profile.minimum_movie_runtime = 0
                profile.excluded_categories = []
                await db.commit()
            unfiltered = (await self.client.get(f'/tracking/title/{self.media_id}/related')).json()
            self.assertEqual([row['title'] for row in unfiltered['similar']], ['Short movie', 'Documentary', 'Low vote movie', 'Future movie', 'Another movie'])
            self.assertEqual((await self.client.get(f'/tracking/title/{self.media_id}/related')).status_code,200)
            fetch_collection.assert_awaited_once(); fetch_recommendations.assert_awaited_once()
        async with self.Session() as db:
            self.assertEqual((await db.get(Media,self.media_id)).tmdb_data['protected_fixture'],'keep')
