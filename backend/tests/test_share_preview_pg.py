"""Use the existing isolated PostgreSQL fixture, never the app database."""
import unittest
from unittest.mock import AsyncMock, patch

import test_statistics_pg as fixture
from core import share_preview as preview
from core.share_preview import card_data
from models import GlobalSettings, Media, User, UserProfileData
from models.base import PrivacyLevel
from models.catalogue import CatalogueCredit, CatalogueEntity
from routers.sharing import router


@unittest.skipUnless(fixture.URL, 'Requires explicit disposable statistics PostgreSQL')
class ShareDatabaseTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.StatisticsDatabaseTests.asyncSetUp
    asyncTearDown = fixture.StatisticsDatabaseTests.asyncTearDown
    build = fixture.StatisticsDatabaseTests.build

    async def test_profile_snapshot_and_privacy_are_rechecked_after_image_is_cached(self):
        await self.build()
        self.app.include_router(router)
        self.viewer = None  # Messaging crawlers never have a session or API key.
        with patch.object(preview, 'fetch_image', AsyncMock(return_value=None)):
            response = await self.client.get('/sharing/image', params={'path':'/user/statistics-fixture'})
        self.assertEqual(response.status_code, 200, response.text[:100] if response.status_code != 200 else '')
        self.assertEqual(response.headers['content-type'], 'image/jpeg')
        self.assertEqual(response.headers['cache-control'], 'no-store')
        async with self.Session() as db:
            card, _ = await card_data(db, '/user/statistics-fixture')
            self.assertEqual(card['stats'][0], {'value':'1','label':'Movies watched'})
            profile = await db.get(UserProfileData, self.user_id)
            profile.privacy_level = PrivacyLevel.private
            other = User(email='share-reader@example.test', username='share-reader', api_key='share-reader')
            db.add(other)
            await db.commit()
            self.viewer = other
        for endpoint in ['metadata','image']:
            response = await self.client.get(f'/sharing/{endpoint}', params={'path':'/user/statistics-fixture'}, headers={'Authorization':'Bearer ignored'})
            self.assertEqual(response.status_code, 403)

    async def test_media_and_global_gate(self):
        self.app.include_router(router)
        self.viewer = None
        async with self.Session() as db:
            media = await db.get(Media, self.media_id)
            media.imdb_rating = 8.3
            await db.commit()
            card, _ = await card_data(db, f'/title/{self.media_id}')
            self.assertEqual(card['title'], 'Fixture movie')
            self.assertEqual(card['stats'], [{'value':'8.3','label':'IMDb'}])
        for endpoint in ['metadata', 'image']:
            with patch.object(preview, 'fetch_image', AsyncMock(return_value=None)):
                response = await self.client.get(f'/sharing/{endpoint}', params={'path':f'/title/{self.media_id}'})
            self.assertEqual(response.status_code, 200, response.text[:100] if response.status_code != 200 else '')
        async with self.Session() as db:
            settings = await db.get(GlobalSettings, 1)
            settings.enable_logged_out_navigation = False
            await db.commit()
        for endpoint in ['metadata', 'image']:
            response = await self.client.get(f'/sharing/{endpoint}', params={'path':f'/title/{self.media_id}'})
            self.assertEqual(response.status_code, 401)

    async def test_contributor_counts_exceed_page_size_and_latest_excludes_future(self):
        async with self.Session() as db:
            actor = CatalogueEntity(kind='person',name='Share Actor', image_url='https://example.test/portrait.jpg', attributes={'birthday':'1980-01-01'})
            db.add(actor)
            await db.flush()
            for index in range(30):
                work = CatalogueEntity(kind='series' if index % 2 else 'movie', name=f'Work {index}',
                    attributes={'release_date':f'2020-01-{index+1:02}', 'vote_count':500,'runtime':100,'backdrop_path':f'/backdrop-{index}.jpg'})
                db.add(work)
                await db.flush()
                db.add_all([CatalogueCredit(work_id=work.id, contributor_id=actor.id, provider='tmdb', source_key=f'{index}-a',role='actor'),
                    CatalogueCredit(work_id=work.id, contributor_id=actor.id, provider='tvdb', source_key=f'{index}-b',role='actor')])
            future = CatalogueEntity(kind='movie',name='Future',attributes={'release_date':'2999-01-01','vote_count':500,'runtime':100,'backdrop_path':'/future.jpg'})
            db.add(future)
            await db.flush()
            db.add(CatalogueCredit(work_id=future.id,contributor_id=actor.id,provider='tmdb',source_key='future',role='actor'))
            await db.commit()
            with patch('core.settings_store.get_user_tmdb_key',AsyncMock(return_value=None)):
                card, _ = await card_data(db, f'/actors/catalogue:{actor.id}')
            self.assertEqual(card['stats'][1:], [{'value':'16','label':'Movies'}, {'value':'15','label':'Series'}])
            self.assertTrue(card['background'].endswith('/backdrop-29.jpg'))
            # Actor-only evidence does not establish a crew portfolio. Give
            # the crew preview its own verified role rather than relying on a
            # person's name/actor credits to populate an unrelated page.
            db.add(CatalogueCredit(work_id=work.id,contributor_id=actor.id,provider='tmdb',source_key='share-director',role='director'))
            await db.commit()
        self.app.include_router(router)
        self.viewer = None
        for path in [f'/actors/catalogue:{actor.id}', f'/person/catalogue:{actor.id}', f'/studio/catalogue:{self.studio_id}']:
            for endpoint in ['metadata', 'image']:
                with patch.object(preview, 'fetch_image', AsyncMock(return_value=None)), \
                     patch('core.settings_store.get_user_tmdb_key', AsyncMock(return_value=None)):
                    response = await self.client.get(f'/sharing/{endpoint}', params={'path': path})
                self.assertEqual(response.status_code, 200, f'{path}: {response.text[:100] if response.status_code != 200 else ""}')
