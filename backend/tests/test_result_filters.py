"""Result policy, provider forwarding, and partial preference validation."""
import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault('SECRET_KEY', 'results-filter-tests')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

from core.catalog_search import matches_result_filters, search_rank, has_streaming_offer, enrich_search_evidence
from core.cinema_releases import theatrical_dates
from core.browse import remote_params, remote_page
from core import tmdb
from schemas import UserProfileUpdate
from pydantic import ValidationError


class ResultPolicyTests(unittest.TestCase):
    def test_shared_rank_balances_exact_match_with_popularity_and_availability(self):
        exact_low = {'title':'War', 'vote_count':10, 'popularity':8, 'poster_path':'/war.jpg'}
        exact_high = {'name':'War', 'vote_count':1000, 'popularity':80, 'poster_path':'/war.jpg'}
        prefix = {'title':'War Stories', 'vote_count':100000}
        self.assertGreater(search_rank('war', exact_high), search_rank('war', exact_low))
        self.assertGreater(search_rank('war', prefix, streaming_available=True), search_rank('war', exact_low))
        self.assertGreater(search_rank('war', exact_high, streaming_available=True), search_rank('war', prefix, streaming_available=True))
        self.assertGreater(search_rank('war', exact_low, streaming_available=True), search_rank('war', exact_low))
        self.assertEqual(search_rank('war', exact_low, streaming_available=True, prefer_streaming=False), search_rank('war', exact_low))
        self.assertGreater(search_rank('war', exact_low), search_rank('war', {**exact_low, 'poster_path':None}))
        self.assertTrue(has_streaming_offer({'watch_providers':{'DE':{'flatrate':[{'provider_id':1}]}}}, 'DE'))
        self.assertFalse(has_streaming_offer({'watch_providers':{'DE':{'rent':[{'provider_id':1}]}}}, 'DE'))

    def test_planned_theatrical_release_is_evidence_before_opening(self):
        future = {'title':'War', 'vote_count':10, 'release_dates':{'results':[
            {'iso_3166_1':'DE','release_dates':[{'type':3,'release_date':'2099-10-01T00:00:00.000Z'}]}
        ]}}
        self.assertEqual(theatrical_dates(future, 'DE'), ['2099-10-01'])
        self.assertGreater(search_rank('war', future, theatrical=True, prefer_theatrical=True),
                           search_rank('war', future, theatrical=False, prefer_theatrical=True))

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
    async def test_search_enrichment_uses_offer_and_future_release_metadata(self):
        items = [{'id': 42, 'title': 'War', 'vote_count': 10}]
        providers = {'results': {'DE': {'flatrate': [{'provider_id': 1}]}}}
        releases = {'results': [{'iso_3166_1': 'DE', 'release_dates': [
            {'type': 3, 'release_date': '2099-10-01T00:00:00.000Z'}]}]}
        with patch('core.tmdb.get_movie_watch_providers', AsyncMock(return_value=providers)) as offers, \
             patch('core.tmdb.get_movie_release_dates', AsyncMock(return_value=releases)) as dates:
            await enrich_search_evidence(items, 'war', 'movie', 'fixture', 'DE', theatrical=True)
        offers.assert_awaited_once_with(42, api_key='fixture')
        dates.assert_awaited_once_with(42, api_key='fixture')
        self.assertTrue(has_streaming_offer(items[0], 'DE'))
        self.assertEqual(theatrical_dates(items[0], 'DE'), ['2099-10-01'])

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
