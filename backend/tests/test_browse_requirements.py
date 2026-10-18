"""Browse requirements preserve relevance search and worldwide release ranking."""
import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault('SECRET_KEY', 'browse-requirements-tests')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

from core.browse import matches, remote_page, PAGE_SIZE
from core.catalog_search import has_release, has_streaming_offer, media_search_rank, enrich_release_evidence

FILTERS = dict(genres=[], start=None, end=None, status='', provider=None, region='DE', sort='popular')


class BrowseRequirementTests(unittest.IsolatedAsyncioTestCase):
    def test_streaming_rank_and_requirement_use_any_country(self):
        plain = {'name': 'War', 'vote_count': 0}
        for category in ('flatrate', 'free', 'ads'):
            worldwide = {**plain, 'watch_providers': {'BR': {category: [{'provider_id': 8}]}}}
            self.assertTrue(has_release(worldwide, 'series'))
            self.assertTrue(has_streaming_offer(worldwide))
            self.assertFalse(has_streaming_offer(worldwide, 'DE'))
            self.assertGreater(media_search_rank('war', worldwide, 'series'), media_search_rank('war', plain, 'series'))
        rental = {**plain, 'watch_providers': {'BR': {'rent': [{'provider_id': 8}], 'buy': [{'provider_id': 8}]}}}
        self.assertFalse(has_release(rental, 'series'))
        self.assertEqual(media_search_rank('war', rental, 'series'), media_search_rank('war', plain, 'series'))

    def test_actual_theatrical_release_is_an_alternative_to_streaming(self):
        movie = {'title': 'War', 'theatrical_release_dates_by_country': {'JP': ['2020-01-01']}}
        self.assertTrue(has_release(movie, 'movie'))
        self.assertFalse(has_release(movie, 'series'))
        future = {**movie, 'theatrical_release_dates_by_country': {'JP': ['2099-01-01']}}
        self.assertFalse(has_release(future, 'movie'))
        self.assertGreater(media_search_rank('war', future, 'movie'), media_search_rank('war', {'title': 'War'}, 'movie'))

    def test_requirements_are_independent_and_votes_do_not_filter(self):
        predicate = {k: v for k, v in FILTERS.items() if k != 'sort'}
        poster = {'poster_path': '/a.jpg', 'vote_count': 0}
        self.assertTrue(matches(poster, media_type='movie', require_poster=True, **predicate))
        self.assertFalse(matches(poster, media_type='movie', require_release=True, **predicate))
        self.assertTrue(matches({}, media_type='movie', **predicate))

    async def test_search_ignores_obsolete_poster_setting_without_release_requests(self):
        with patch('core.tmdb.search_shows', AsyncMock(return_value={'results': [
                {'id': 1, 'name': 'Title', 'popularity':50}, {'id': 2, 'name': 'Title Two', 'popularity':50, 'poster_path': '/2.jpg'}]})), \
             patch('core.tmdb.get_show_watch_providers', AsyncMock(return_value={'results': {'BR': {'ads': [8]}}})) as offers:
            _, rows = await remote_page('title', 'series', 'fixture',
                {**FILTERS, 'require_poster': True}, 1)
        self.assertEqual([row['id'] for row in rows], [1,2])
        offers.assert_not_awaited()

    async def test_release_reads_skip_known_evidence_and_reuse_streaming_before_cinema(self):
        items = [{'id': 1, 'theatrical_release_dates': ['2020-01-01']},
                 {'id': 2, 'watch_providers': {'BR': {'flatrate': [8]}}}, {'id': 3}]
        with patch('core.tmdb.get_movie_watch_providers', AsyncMock(return_value={'results': {'BR': {'free': [8]}}})) as offers, \
             patch('core.tmdb.get_movie_release_dates', AsyncMock()) as cinema:
            await enrich_release_evidence(items, 'movie', 'fixture')
        offers.assert_awaited_once_with(3, api_key='fixture')
        cinema.assert_not_awaited()

    async def test_release_lookup_concurrency_is_bounded(self):
        active = peak = 0
        async def offers(identifier, api_key):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(.001)
            active -= 1
            return {'results': {}}
        with patch('core.tmdb.get_show_watch_providers', AsyncMock(side_effect=offers)):
            await enrich_release_evidence([{'id': i} for i in range(30)], 'series', 'fixture')
        self.assertLessEqual(peak, 6)

    async def test_release_feed_resumes_in_rank_order_without_rescanning(self):
        feed = [{'id': i, 'imdb_id': f'tt{i}', 'imdb_votes': 0} for i in range(1, 71)]
        async def offers(identifier, api_key):
            return {'results': {'BR': {'free': [8]}}} if identifier > 25 else {'results': {}}
        with patch('core.mdblist_discovery.ranked_rows', AsyncMock(return_value=(feed, False))), \
             patch('core.tmdb.get_show_watch_providers', AsyncMock(side_effect=offers)) as lookup:
            first, rows = await remote_page('', 'series', 'fixture', {**FILTERS, 'require_release': True}, 1, mdblist_key='mdb')
            self.assertEqual([row['id'] for row in rows], list(range(26, 50)))
            self.assertTrue(first['has_more'])
            lookup.reset_mock()
            second, rows = await remote_page('', 'series', 'fixture', {**FILTERS, 'require_release': True}, 2,
                                            mdblist_key='mdb', feed_offset=first['scan_next'])
        self.assertEqual([row['id'] for row in rows], list(range(50, 71)))
        self.assertFalse(second['has_more'])
        self.assertTrue(all(call.args[0] >= 50 for call in lookup.await_args_list))

    async def test_sparse_release_feed_bounds_work_and_returns_continuation(self):
        feed = [{'id': i, 'imdb_id': f'tt{i}'} for i in range(1, 300)]
        with patch('core.mdblist_discovery.ranked_rows', AsyncMock(return_value=(feed, False))), \
             patch('core.tmdb.get_show_watch_providers', AsyncMock(return_value={'results': {}})) as lookup:
            page, rows = await remote_page('', 'series', 'fixture', {**FILTERS, 'require_release': True}, 1, mdblist_key='mdb')
        self.assertEqual(rows, [])
        self.assertTrue(page['has_more'])
        self.assertEqual(page['scan_next'], PAGE_SIZE * 4)
        self.assertEqual(lookup.await_count, PAGE_SIZE * 4)


@unittest.skipUnless(os.getenv('TRACKING_TEST_DATABASE_URL'), 'Requires disposable PostgreSQL')
class BrowseRequirementsMigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_migration_preserves_unrelated_preferences_and_removes_obsolete_fields(self):
        import importlib
        import uuid
        from sqlalchemy import text, inspect
        from sqlalchemy.ext.asyncio import create_async_engine
        from alembic.migration import MigrationContext
        from alembic.operations import Operations

        url = os.environ['TRACKING_TEST_DATABASE_URL']
        if 'test' not in url.rsplit('/', 1)[-1]:
            raise RuntimeError('Requires a disposable test database')
        engine = create_async_engine(url)
        try:
            async with engine.connect() as connection:
                transaction = await connection.begin()
                schema = 'browse_migration_test_' + uuid.uuid4().hex
                await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
                await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))

                def verify(sync):
                    sync.execute(text('CREATE TABLE user_profiles (minimum_votes integer NOT NULL DEFAULT 250)'))
                    sync.execute(text('CREATE TABLE user_settings (preferences jsonb)'))
                    from sqlalchemy import bindparam
                    from sqlalchemy.dialects.postgresql import JSONB
                    sync.execute(text('INSERT INTO user_settings VALUES (:preferences)').bindparams(bindparam('preferences', type_=JSONB)),
                                 {'preferences': {'unrelated': 'keep', 'require_poster': True,
                                                  'prefer_streaming_series': False, 'prefer_theatrical_movies': True}})
                    migration = importlib.import_module('migrations.versions.mt054_browse_requirements')
                    with Operations.context(MigrationContext.configure(sync)):
                        migration.upgrade()
                        self.assertEqual(sync.scalar(text('SELECT preferences FROM user_settings')),
                                         {'unrelated': 'keep', 'require_poster': True})
                        self.assertNotIn('minimum_votes', {column['name'] for column in inspect(sync).get_columns('user_profiles')})
                        migration.downgrade()
                        self.assertEqual(sync.scalar(text('SELECT preferences FROM user_settings')), {'unrelated': 'keep'})
                        self.assertIn('minimum_votes', {column['name'] for column in inspect(sync).get_columns('user_profiles')})

                await connection.run_sync(verify)
                await transaction.rollback()
        finally:
            await engine.dispose()


class DiscoveryRegressionTests(unittest.IsolatedAsyncioTestCase):
    def test_title_prefix_beats_released_blockbusters(self):
        intended = {'title': 'Avengers: Doomsday', 'vote_count': 0}
        rivals = [{'title': title, 'vote_count': 1000000, 'popularity': 1000000,
                   'watch_providers': {'DE': {'flatrate': [8]}}}
                  for title in ('Avengers: Endgame', 'Avengers: Infinity War', 'Avengers: Age of Ultron')]
        for term in ('avengers: doom', 'avengers: dooms', 'Avengers Doom'):
            self.assertGreater(media_search_rank(term, intended, 'movie'),
                               max(media_search_rank(term, row, 'movie') for row in rivals))

    def test_upcoming_is_nearest_first_and_newest_stops_at_today(self):
        from datetime import date, timedelta
        from core.browse import remote_params
        for kind, field in [('movie', 'primary_release_date'), ('series', 'first_air_date')]:
            upcoming = remote_params(kind, page=1, **{**FILTERS, 'sort': 'upcoming'})
            self.assertEqual(upcoming['sort_by'], field + '.asc')
            self.assertEqual(upcoming[field + '.gte'], (date.today() + timedelta(days=1)).isoformat())
            newest = remote_params(kind, page=1, **{**FILTERS, 'sort': 'newest'})
            self.assertEqual(newest[field + '.lte'], date.today().isoformat())

    async def test_availability_uses_bulk_discovery_without_title_lookups(self):
        async def discover(url, headers, params):
            start = (params['page'] - 1) * 20
            return {'results': [{'id': i, 'name': 'New release'} for i in range(start, start + 20)], 'total_pages': 3}
        for kind, options in [('movie', {'cinema': True, 'cinema_country': 'DE'}), ('series', {'provider': 8})]:
            with patch('core.tmdb._get', AsyncMock(side_effect=discover)) as bulk, \
                 patch('core.tmdb.get_movie', AsyncMock()) as movie, \
                 patch('core.tmdb.get_show', AsyncMock()) as series, \
                 patch('core.mdblist_discovery.ranked_rows', AsyncMock()) as feed:
                first, rows = await remote_page('', kind, 'fixture', {**FILTERS, **options}, 1, mdblist_key='mdb')
                self.assertEqual([row['id'] for row in rows], list(range(40)))
                self.assertTrue(first['has_more'])
                second, rows = await remote_page('', kind, 'fixture', {**FILTERS, **options}, 2, mdblist_key='mdb')
                self.assertEqual([row['id'] for row in rows], list(range(40, 80)))
                self.assertFalse(second['has_more'])
                params = bulk.await_args_list[0].kwargs['params']
                if kind == 'movie': self.assertEqual(params['region'], 'DE')
                else:
                    self.assertEqual(params['watch_region'], 'DE')
                    self.assertEqual(params['with_watch_monetization_types'], 'flatrate|free|ads')
                movie.assert_not_awaited(); series.assert_not_awaited(); feed.assert_not_awaited()

    async def test_vote_floor_applies_to_discovery_but_not_search(self):
        from core.browse import remote_params
        predicate = {k:v for k,v in FILTERS.items() if k != 'sort'}
        self.assertFalse(matches({'vote_count': 249}, media_type='movie', min_votes=250, **predicate))
        self.assertFalse(matches({}, media_type='series', min_votes=250, **predicate))
        self.assertFalse(matches({'imdb_votes': 250, 'vote_count': 1}, media_type='movie', min_votes=250, **predicate))
        self.assertTrue(matches({'imdb_votes': 1, 'vote_count': 250}, media_type='movie', min_votes=250, **predicate))
        self.assertEqual(remote_params('series', page=1, min_votes=250, **FILTERS)['vote_count.gte'], 250)
        with patch('core.tmdb.search_movies', AsyncMock(return_value={'results': [{'id': 1, 'title': 'Avengers: Doomsday', 'vote_count': 0}], 'total_pages': 1})):
            _, rows = await remote_page('avengers: doom', 'movie', 'fixture', {**FILTERS, 'min_votes': 250}, 1)
        self.assertEqual([row['id'] for row in rows], [1])
