"""Real PostgreSQL contributor identity, private state and timeline pagination tests."""

import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import func, select

import test_statistics_pg as fixture
from models import GlobalSettings, Media, User
from models.catalogue import CatalogueCredit, CatalogueEntity, CatalogueIdentity


@unittest.skipUnless(fixture.URL, "Requires explicit disposable statistics PostgreSQL")
class ContributorDetailsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixture.StatisticsDatabaseTests.asyncSetUp(self)
        self.keys = patch('core.contributor_details.settings_store.get_user_tmdb_key', AsyncMock(return_value=None))
        self.key_mock = self.keys.start()
        self.addCleanup(self.keys.stop)
        async with self.Session() as db:
            self.person_id = await db.scalar(select(CatalogueEntity.id).where(CatalogueEntity.kind == 'person'))

    async def asyncTearDown(self):
        await fixture.StatisticsDatabaseTests.asyncTearDown(self)

    async def login(self):
        async with self.Session() as db:
            self.viewer = await db.get(User, self.user_id)

    async def request(self, kind='actor', key=None, **params):
        return await self.client.get(f'/tracking/contributors/{kind}/{key or "catalogue:" + str(self.person_id)}', params=params)

    async def test_viewer_state_is_private_and_anonymous_filters_require_login(self):
        response = await self.request()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['cache-control'], 'private, no-store')
        row = response.json()['works'][0]
        self.assertEqual(row['characters'], ['Fixture Character'])
        self.assertIsNone(row['entry'])
        self.assertIsNone(response.json()['list_count'])
        self.assertEqual((await self.request(list_scope='in')).status_code, 401)
        self.assertEqual((await self.request(status='completed')).status_code, 401)
        await self.login()
        data = (await self.request(list_scope='in', status='completed')).json()
        self.assertEqual(data['list_count'], 1)
        self.assertEqual(data['works'][0]['score'], 8)
        self.assertEqual(data['works'][0]['entry']['status'], 'completed')
        self.assertEqual(data['works'][0]['entry']['manual_score'], 8)
        async with self.Session() as db:
            other = User(email='other@example.test', username='other-contributor', api_key='other-fixture')
            db.add(other); await db.commit()
            self.viewer = other
        other_data = (await self.request()).json()
        self.assertEqual(other_data['list_count'], 0)
        self.assertIsNone(other_data['works'][0]['entry'])

    async def test_complete_credit_set_filtered_before_pagination_and_no_import_on_read(self):
        async with self.Session() as db:
            for i in range(30):
                work = CatalogueEntity(kind='series', name=f'Series {i}', attributes={'first_air_date': f'{2021 + i // 12}-{i % 12 + 1:02d}-01'})
                db.add(work); await db.flush()
                db.add(CatalogueIdentity(entity_id=work.id, namespace='tmdb.series', external_id=str(1000+i), source='tmdb'))
                db.add(CatalogueCredit(work_id=work.id, contributor_id=self.person_id, provider='tmdb', source_key=f'cast-{i}', role='actor', character_label='Role'))
            await db.commit()
            before = await db.scalar(select(func.count()).select_from(Media))
        first = (await self.request()).json()
        second = (await self.request(page=2)).json()
        rows = first['works'] + second['works']
        self.assertEqual((first['total'], len(first['works']), len(rows)), (31, 24, 31))
        self.assertFalse(second['has_more'])
        self.assertEqual(len({row['key'] for row in rows}), 31)
        dates = [row['release_date'] or '' for row in rows]
        self.assertEqual(dates, sorted(dates, reverse=True))
        await self.login()
        own = (await self.request(list_scope='in')).json()
        self.assertEqual(len(own['works']), 1)
        movies = (await self.request(media_type='movie')).json()
        self.assertEqual(len(movies['works']), 1)
        async with self.Session() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(Media)), before)

    async def test_roles_and_characters_merge_but_staff_excludes_cast(self):
        async with self.Session() as db:
            work_id = await db.scalar(select(CatalogueEntity.id).where(CatalogueEntity.kind == 'movie'))
            db.add_all([
                CatalogueCredit(work_id=work_id, contributor_id=self.person_id, provider='tmdb', source_key='second-character', role='actor', character_label='Second Character'),
                CatalogueCredit(work_id=work_id, contributor_id=self.person_id, provider='tmdb', source_key='director', role='director'),
                CatalogueCredit(work_id=work_id, contributor_id=self.person_id, provider='tmdb', source_key='writer', role='writer'),
            ])
            await db.commit()
        actor = (await self.request()).json()['works']
        staff = (await self.request('staff')).json()['works']
        self.assertEqual(len(actor), 1)
        self.assertEqual(set(actor[0]['characters']), {'Fixture Character', 'Second Character'})
        self.assertEqual(set(staff[0]['roles']), {'Director', 'Writer'})
        self.assertEqual(staff[0]['characters'], [])
        self.assertEqual((await self.request('studio', f'catalogue:{self.network_id}')).status_code, 404)
        self.assertEqual((await self.request('actor', f'catalogue:{self.studio_id}')).status_code, 404)
        for key in ('tmdb:0', 'tmdb:9999999999', 'catalogue:-2'):
            self.assertEqual((await self.request(key=key)).status_code, 422)

    async def test_saved_adult_and_disabled_anime_works_are_hidden(self):
        async with self.Session() as db:
            settings = await db.get(GlobalSettings, 1)
            settings.show_anime = False
            for name, attrs in [('Adult', {'adult': True}), ('Anime', {'genres':[{'name':'Animation'}], 'original_language':'ja'})]:
                work = CatalogueEntity(kind='movie', name=name, attributes=attrs)
                db.add(work); await db.flush()
                db.add(CatalogueCredit(work_id=work.id, contributor_id=self.person_id, provider='tmdb', source_key=name, role='actor'))
            await db.commit()
        data = (await self.request()).json()
        self.assertEqual([w['title'] for w in data['works']], ['Fixture movie'])

    async def test_studio_logo_backfill_is_cached_and_preserves_protected_artwork(self):
        from core.studio_artwork import backfill_studio_artwork
        logo = {'id':99,'logos':[{'file_path':'/banner.png','width':1200,'height':400}]}
        with patch('core.studio_artwork.tmdb.get_company_images', AsyncMock(return_value=logo)) as fetch:
            async with self.Session() as db:
                self.assertEqual((await backfill_studio_artwork(db,'fixture'))['updated'],1)
                self.assertEqual((await backfill_studio_artwork(db,'fixture'))['examined'],0)
                fetch.assert_awaited_once()
                studio = await db.get(CatalogueEntity,self.studio_id)
                self.assertEqual(studio.image_url,'https://example.test/logo.png')
                studio.protected_fields = ['attributes.studio_banner']
                await db.commit()
                fetch.return_value = {'id':99,'logos':[{'file_path':'/new.png','width':1200,'height':400}]}
                self.assertEqual((await backfill_studio_artwork(db,'fixture',force=True))['protected'],1)
                self.assertEqual(studio.attributes['studio_banner'],'https://image.tmdb.org/t/p/original/banner.png')
        data = (await self.request('studio',f'catalogue:{self.studio_id}')).json()
        self.assertEqual(data['banner'],'https://image.tmdb.org/t/p/original/banner.png')
        self.assertIsNone((await self.request()).json()['banner'])

    async def test_studio_logo_mismatch_retains_last_good_image_and_retry_eligibility(self):
        from core.studio_artwork import backfill_studio_artwork
        async with self.Session() as db:
            studio = await db.get(CatalogueEntity,self.studio_id)
            studio.attributes = {'studio_banner':'https://example.test/saved.png'}
            await db.commit()
            with patch('core.studio_artwork.tmdb.get_company_images', AsyncMock(return_value={'id':100,'logos':[]})):
                result = await backfill_studio_artwork(db,'fixture')
            self.assertEqual(result['failed'],1)
            self.assertEqual(studio.attributes,{'studio_banner':'https://example.test/saved.png'})

    async def test_profile_backfill_only_fills_missing_fields_and_resumes_without_refetching(self):
        from core.contributor_backfill import backfill_contributors
        async with self.Session() as db:
            person = await db.get(CatalogueEntity, self.person_id)
            person.attributes = {'birthday':'', 'place_of_birth':'My edit'}
            person.protected_fields = ['attributes.birthday']
            db.add(CatalogueIdentity(entity_id=person.id, namespace='tmdb.person', external_id='77', source='tmdb'))
            await db.commit()
        with patch('core.contributor_backfill.tmdb.get_person_profile', AsyncMock(return_value={'id':77,'biography':'Saved biography','birthday':'1970-01-01','place_of_birth':'Provider location','homepage':'javascript:bad'})) as people, \
             patch('core.contributor_backfill.tmdb.get_company', AsyncMock(return_value={'id':99,'description':'Studio biography','headquarters':'HQ'})) as companies:
            async with self.Session() as db:
                first = await backfill_contributors(db, 'fixture')
                person = await db.get(CatalogueEntity, self.person_id)
                self.assertEqual(person.description, 'Saved biography')
                self.assertEqual(person.attributes['birthday'], '')
                self.assertEqual(person.attributes['place_of_birth'], 'My edit')
                self.assertNotIn('homepage', person.attributes)
                self.assertEqual(first['examined'], 2)
                self.assertEqual((await backfill_contributors(db, 'fixture'))['examined'], 0)
                people.assert_awaited_once(); companies.assert_awaited_once()
        with patch('core.contributor_details.tmdb.get_person', AsyncMock(side_effect=RuntimeError('offline'))):
            self.assertEqual((await self.request()).json()['description'], 'Saved biography')

    async def test_tvdb_profile_backfill_uses_verified_id_and_preserves_tmdb_fields(self):
        from core.contributor_backfill import backfill_contributors
        async with self.Session() as db:
            person = await db.get(CatalogueEntity, self.person_id)
            person.attributes = {'birthday':'1970-01-01'}
            db.add(CatalogueIdentity(entity_id=person.id, namespace='tvdb.person', external_id='77', source='tvdb'))
            await db.commit()
        raw = {'id':77,'birth':'1980-01-01','death':'unknown','birthPlace':'Berlin',
               'biographies':[{'language':'fra','biography':'French'},{'language':'eng','biography':'English biography'}],
               'aliases':[{'name':'Alias'}],'image':'/person/profile.jpg'}
        with patch('core.contributor_backfill.tvdb.get_person', AsyncMock(return_value=raw)):
            async with self.Session() as db:
                result = await backfill_contributors(db,'fixture',provider='tvdb')
                self.assertEqual(result['updated'],1)
                person = await db.get(CatalogueEntity,self.person_id)
                self.assertEqual(person.description,'English biography')
                self.assertEqual(person.attributes['birthday'],'1970-01-01')
                self.assertEqual(person.attributes['place_of_birth'],'Berlin')
                self.assertNotIn('deathday',person.attributes)

    async def test_online_fields_replace_empty_cache_but_preserve_protected_empty_edits(self):
        self.key_mock.return_value = 'fixture'
        async with self.Session() as db:
            person = await db.get(CatalogueEntity,self.person_id)
            person.attributes = {'birthday':''}
            person.protected_fields = ['description','image_url']
            db.add(CatalogueIdentity(entity_id=person.id,namespace='tmdb.person',external_id='77',source='tmdb'))
            await db.commit()
        with patch('core.contributor_details.tmdb.get_person', AsyncMock(return_value={'id':77,'birthday':'1970-01-01','biography':'Online biography','profile_path':'/profile.jpg'})):
            data = (await self.request()).json()
            self.assertEqual(data['birthday'],'1970-01-01')
            self.assertIsNone(data['description'])
            self.assertIsNone(data['image'])

    async def test_provider_metadata_identity_and_same_number_movie_series_remain_distinct(self):
        self.key_mock.return_value = 'fixture'
        profile = {'id': 77, 'name': 'Real Person', 'birthday': '1970-01-01', 'biography': 'Biography.',
                   'homepage': 'javascript:alert(1)', 'imdb_id': 'nm77', 'also_known_as': ['Alias'],
                   'combined_credits': {'cast': [
                       {'id': 55, 'media_type': 'movie', 'title': 'Film', 'character': 'One', 'release_date': '2026-01-01'},
                       {'id': 55, 'media_type': 'movie', 'title': 'Film', 'character': 'Two', 'release_date': '2026-01-01'},
                       {'id': 55, 'media_type': 'tv', 'name': 'Series', 'character': 'Three', 'first_air_date': '2025-01-01'},
                       {'id': 56, 'media_type': 'movie', 'title': 'Adult', 'adult': True},
                   ], 'crew': [{'id': 55, 'media_type': 'movie', 'title': 'Film', 'job': 'Director'},
                               {'id': 57, 'media_type': 'movie', 'title': 'Cast only', 'job': 'Actor'}]}}
        with patch('core.contributor_details.tmdb.get_person', AsyncMock(return_value=profile)):
            data = (await self.request(key='tmdb:77')).json()
            self.assertEqual((data['name'], data['birthday'], data['description']), ('Real Person', '1970-01-01', 'Biography.'))
            self.assertEqual(len(data['works']), 2)
            self.assertEqual(set(data['works'][0]['characters']), {'One','Two'})
            self.assertNotIn('Official website', [link['label'] for link in data['links']])
            self.assertEqual((await self.request('staff', 'tmdb:77')).json()['works'][0]['roles'], ['Director'])
        with patch('core.contributor_details.tmdb.get_person', AsyncMock(return_value={**profile, 'id': 88})):
            self.assertEqual((await self.request(key='tmdb:77')).status_code, 404)

    async def test_studio_cursor_keeps_interleaved_sources_in_date_order(self):
        self.key_mock.return_value = 'fixture'
        movie_rows = [{'id': i+2000, 'title': f'Movie {i}', 'release_date': f'{2050-i}-02-01'} for i in range(30)]
        series_rows = [{'id': i+2000, 'name': f'Series {i}', 'first_air_date': f'{2050-i}-01-01'} for i in range(30)]

        async def movies(**kwargs):
            self.assertEqual(kwargs['sort_by'], 'primary_release_date.desc')
            page = kwargs['page']; return {'results': movie_rows[(page-1)*20:page*20], 'total_pages': 2}

        async def shows(**kwargs):
            self.assertEqual(kwargs['sort_by'], 'first_air_date.desc')
            page = kwargs['page']; return {'results': series_rows[(page-1)*20:page*20], 'total_pages': 2}

        with patch('core.contributor_details.tmdb.get_company', AsyncMock(return_value={'id':99,'name':'Online Studio','headquarters':'HQ'})), \
             patch('core.contributor_details.tmdb.discover_movies', AsyncMock(side_effect=movies)) as movie_mock, \
             patch('core.contributor_details.tmdb.discover_shows', AsyncMock(side_effect=shows)):
            data = (await self.request('studio', f'catalogue:{self.studio_id}')).json()
            self.assertEqual(data['name'], 'Fixture Studio')
            self.assertEqual(data['headquarters'], 'HQ')
            all_rows = list(data['works'])
            page = 1
            while data['has_more']:
                page += 1
                response = await self.request('studio', f'catalogue:{self.studio_id}', page=page, cursor=data['next_cursor'], source='provider')
                self.assertEqual(response.status_code, 200)
                data = response.json(); all_rows += data['works']
                self.assertLess(page, 5)
            self.assertEqual(len(all_rows), 61)
            self.assertEqual(len({row['key'] for row in all_rows}), 61)
            dates = [row['release_date'] or '' for row in all_rows]
            self.assertEqual(dates, sorted(dates, reverse=True))
            await self.login()
            movie_mock.reset_mock()
            own = (await self.request('studio', f'catalogue:{self.studio_id}', list_scope='in')).json()
            self.assertEqual(len(own['works']), 1)
            movie_mock.assert_not_called()
            self.assertEqual((await self.request('studio', f'catalogue:{self.studio_id}', cursor='{}')).status_code, 422)

    async def test_studio_failure_keeps_saved_works_and_continuation_returns_retry(self):
        self.key_mock.return_value = 'fixture'
        with patch('core.contributor_details.tmdb.get_company', AsyncMock(side_effect=RuntimeError('private key error'))):
            data = (await self.request('studio', f'catalogue:{self.studio_id}')).json()
            self.assertEqual(len(data['works']), 1)
            self.assertNotIn('private key', data['notice'])
            self.assertEqual((await self.request('studio', f'catalogue:{self.studio_id}', cursor='{}', source='provider')).status_code, 502)
