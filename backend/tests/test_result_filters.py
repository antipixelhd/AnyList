"""Result policy, provider forwarding, and partial preference validation."""
import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault('SECRET_KEY', 'results-filter-tests')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

from core.catalog_search import matches_result_filters
from core.browse import remote_params, remote_page
from core import tmdb
from schemas import UserProfileUpdate
from pydantic import ValidationError


class ResultPolicyTests(unittest.TestCase):
    def test_runtime_boundary_unknown_and_series(self):
        for runtime, expected in ((2, False), (29, False), (30, True), (31, True), (None, True), (0, True)):
            self.assertEqual(matches_result_filters({'runtime': runtime}, 'movie', min_runtime=30), expected)
        self.assertTrue(matches_result_filters({'runtime': 2}, 'movie', min_runtime=0))
        self.assertTrue(matches_result_filters({'runtime': 2}, 'series', min_runtime=30))

    def test_any_excluded_category_matches_ids_objects_or_names(self):
        for item in ({'genre_ids':[18,99]}, {'genres':[{'id':10764,'name':'Localized label'}]},
                     {'genres':['Documentary']}, {'genres':[{'name':'Documentary'}]}):
            self.assertFalse(matches_result_filters(item, 'movie', excluded_genres=[99,10764]))
        self.assertTrue(matches_result_filters({'genre_ids':[18]}, 'movie', excluded_genres=[99,10764]))
        self.assertTrue(matches_result_filters({'genre_ids':[99]}, 'movie', excluded_genres=[]))

    def test_profile_patch_deduplicates_and_preserves_unrelated_preferences(self):
        self.assertEqual(UserProfileUpdate(minimum_movie_runtime=0,excluded_categories=[99,99,10764]).model_dump(exclude_unset=True),
                         {'minimum_movie_runtime':0,'excluded_categories':[99,10764]})
        for body in ({'minimum_movie_runtime':-1},{'minimum_movie_runtime':1441},{'minimum_movie_runtime':None},
                     {'excluded_categories':[18]},{'excluded_categories':None}):
            with self.assertRaises(ValidationError):UserProfileUpdate(**body)

    def test_browse_sends_type_specific_provider_exclusions_and_movie_runtime(self):
        filters=dict(page=1,genres=[],start=None,end=None,status='',provider=None,region='US',sort='newest',
                     min_runtime=30,excluded_genres=[99,10764,10770])
        movie=remote_params('movie',**filters)
        series=remote_params('series',**filters)
        self.assertEqual(movie['with_runtime.gte'],30)
        self.assertEqual(movie['without_genres'],'99,10770')
        self.assertNotIn('with_runtime.gte',series)
        self.assertEqual(series['without_genres'],'99,10764')


class ProviderFilterTests(unittest.IsolatedAsyncioTestCase):
    async def test_discover_response_obeys_runtime_and_category_policy(self):
        filters=dict(genres=[],start=None,end=None,status='',provider=None,region='US',sort='newest',
                     min_runtime=30,excluded_genres=[99])
        with patch('core.tmdb._get', AsyncMock(return_value={'results':[
            {'id':1,'runtime':2},{'id':2,'runtime':30,'genre_ids':[99]},
            {'id':3,'runtime':30,'genre_ids':[18]},{'id':4,'genre_ids':[18]}]})):
            _, rows=await remote_page('', 'movie', 'fixture', filters, 1)
        self.assertEqual([row['id'] for row in rows],[3,4])

    async def test_studio_discovery_filters_at_provider_without_extra_detail_calls(self):
        with patch('core.tmdb._get', AsyncMock(return_value={'results':[]})) as fetch:
            await tmdb.discover_movies(with_companies=1,minimum_runtime=30,excluded_genre_ids=[99,10764])
            self.assertEqual(fetch.await_args.kwargs['params']['with_runtime.gte'],30)
            self.assertEqual(fetch.await_args.kwargs['params']['without_genres'],'99')
            await tmdb.discover_shows(with_companies=1,minimum_runtime=30,excluded_genre_ids=[99,10764])
            self.assertNotIn('with_runtime.gte',fetch.await_args.kwargs['params'])
            self.assertEqual(fetch.await_args.kwargs['params']['without_genres'],'99,10764')
