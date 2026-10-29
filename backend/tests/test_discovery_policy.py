import os
import asyncio
import unittest
from datetime import date
from unittest.mock import AsyncMock, patch

os.environ.setdefault('SECRET_KEY', 'discovery-tests-only')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

from core.discovery_policy import DiscoveryPolicy, tuning
from core.release_events import events, future, window
from core import chronological_discovery as discovery


class PolicyTests(unittest.TestCase):
    def test_surface_and_kind_tuning_are_independent_and_zero_disables(self):
        preferences = {'discovery_tuning': {'newest': {'movie': {'min_votes': 10, 'min_popularity': 30}}}}
        self.assertEqual(tuning(preferences, 'movie', 'newest'), DiscoveryPolicy(10, 25))
        self.assertEqual(tuning(preferences, 'series', 'newest'), DiscoveryPolicy(250, 25))
        self.assertEqual(tuning(preferences, 'movie', 'search', 250), DiscoveryPolicy(250, 25))
        self.assertEqual(tuning({'minimum_popularity': 0}, 'movie', 'upcoming', 0), DiscoveryPolicy())

    def test_votes_or_popularity_qualify_inclusive_boundaries_and_disabled_thresholds(self):
        policy = DiscoveryPolicy(250, 25)
        for item in ({'vote_count': 250, 'popularity': 1}, {'vote_count': 0, 'popularity': 25},
                     {'vote_count': 10000, 'popularity': 21.886}):
            self.assertTrue(policy.accepts(item), item)
        for item in ({}, {'vote_count': 249, 'popularity': 24.9},
                     {'imdb_votes': 100000, 'vote_count': 249, 'popularity': 1}):
            self.assertFalse(policy.accepts(item), item)
        self.assertFalse(DiscoveryPolicy(0, 25).accepts({'vote_count': 10000, 'popularity': 1}))
        self.assertFalse(DiscoveryPolicy(250, 0).accepts({'vote_count': 249, 'popularity': 100}))
        self.assertTrue(DiscoveryPolicy().accepts({}))
        self.assertTrue(policy.could_accept({'popularity': 1}))
        self.assertFalse(policy.could_accept({'vote_count': 249, 'popularity': 1}))

    def test_account_popularity_applies_to_search_and_browse_and_zero_disables(self):
        for kind in ('movie', 'series'):
            for surface in ('search', 'newest', 'upcoming', 'popular', 'score'):
                self.assertEqual(tuning({}, kind, surface).min_popularity, 25)
                self.assertEqual(tuning({'minimum_popularity': 30}, kind, surface).min_popularity, 30)
                self.assertEqual(tuning({'minimum_popularity': 0}, kind, surface).min_popularity, 0)
                self.assertEqual(tuning({}, kind, surface).min_votes, 250)

    def test_provider_counts_missing_values_and_popularity_thresholds(self):
        self.assertFalse(DiscoveryPolicy(250).accepts({'imdb_votes': 100_000, 'vote_count': 249}))
        self.assertTrue(DiscoveryPolicy().accepts({}))
        for value in (None, 'NaN', float('inf'), -1, 'invalid'):
            self.assertFalse(DiscoveryPolicy(min_popularity=25).accepts({'popularity': value}))
        for threshold, expected in ((25, True), (30, False)):
            self.assertEqual(DiscoveryPolicy(min_popularity=threshold).accepts({'popularity': 27}), expected)


class ReleaseEventTests(unittest.TestCase):
    def test_movie_premiere_identity_survives_country_and_date_enrichment(self):
        cached = {'id': 1561726, 'title': 'Masterplan', 'release_date': '2026-10-17'}
        fresh = {**cached, 'release_date': '2026-10-16', 'release_dates': {'results': [
            {'iso_3166_1': 'DE', 'release_dates': [{'type': 4, 'release_date': '2026-10-16T00:00:00Z'}]}]}}
        before = events(cached, 'movie', country='DE')[0]
        after = events(fresh, 'movie', country='DE')[0]
        self.assertNotEqual(before['release_date'], after['release_date'])
        self.assertNotEqual(before['release_country'], after['release_country'])
        self.assertEqual(before['release_event'], after['release_event'])

    def test_date_precision_and_overlapping_windows(self):
        self.assertEqual(window('2028-02'), (date(2028, 2, 1), date(2028, 2, 29)))
        for invalid in ('2027-02-29', '2026-00', '2026-01-00', '2026-13', '2026-01-01T00:00:00Z', ''):
            self.assertIsNone(window(invalid))
        self.assertTrue(future('2027', today=date(2026, 10, 18)))
        self.assertFalse(future('2026', today=date(2026, 10, 18)))
        self.assertTrue(future('2026', today=date(2026, 10, 18), upcoming=True))
        self.assertFalse(future('2025', today=date(2026, 10, 18), upcoming=True))

    def test_premiere_and_returns_are_distinct_but_season_one_is_not_duplicated(self):
        show = {'id': 1, 'name': 'A series', 'first_air_date': '2020-01-01', 'number_of_seasons': 8,
            'seasons': [{'season_number': 1, 'air_date': '2020-01-01'},
                        {'season_number': 2, 'air_date': '2099-02'}, {'season_number': 3, 'year': 2100}],
            'next_episode_to_air': {'season_number': 2, 'episode_number': 1, 'air_date': '2099-02'}}
        rows = events(show, 'series')
        self.assertEqual([row['release_date'] for row in rows], ['2020-01-01', '2099-02', '2100'])
        self.assertEqual(len(set(row['release_event'] for row in rows)), 3)
        self.assertEqual(events(show, 'series', upcoming_only=True)[0]['title'], 'A series · Season 2')
        self.assertEqual(events(show, 'series', upcoming_only=True, include_returning=False), [])
        self.assertEqual(events({'id': 1, 'name': 'Unannounced', 'status': 'Returning Series', 'number_of_seasons': 9}, 'series', upcoming_only=True), [])

    def test_theatrical_evidence_and_general_local_fallback(self):
        movie = {'id': 1, 'title': 'A movie', 'release_date': '2099-01-01', 'release_dates': {'results': [
            {'iso_3166_1': 'DE', 'release_dates': [{'type': 4, 'release_date': '2099-02-03T00:00:00Z'}]},
            {'iso_3166_1': 'GB', 'release_dates': [{'type': 3, 'release_date': '2099-03-04T00:00:00Z'}]}]}}
        self.assertEqual(events(movie, 'movie', country='DE', cinema=True, upcoming_only=True), [])
        german = events(movie, 'movie', country='DE', upcoming_only=True)[0]
        self.assertEqual((german['release_date'], german['release_country']), ('2099-02-03', 'DE'))
        worldwide = events(movie, 'movie', country='FR', upcoming_only=True)[0]
        self.assertEqual((worldwide['release_date'], worldwide['release_country']), ('2099-01-01', None))
        british = events(movie, 'movie', country='GB', cinema=True, upcoming_only=True)[0]
        self.assertEqual((british['release_date'], british['release_country']), ('2099-03-04', 'GB'))

    def test_known_aired_episode_overrules_contradictory_upcoming_season_metadata(self):
        show={'id':1,'name':'Already airing','first_air_date':'2020-01-01',
            'seasons':[{'season_number':2,'air_date':'2099-01-01','status':'Planned'}],
            'last_episode_to_air':{'season_number':2,'episode_number':1,'air_date':'2020-01-01'}}
        self.assertEqual(events(show,'series',upcoming_only=True),[])

    def test_theatrical_rereleases_have_distinct_country_and_window_identities(self):
        movie={'id':1,'title':'A classic','theatrical_release_dates_by_country':{'DE':['2000-01-01','2020-01-01','2099-01-01','2099-02-01']}}
        self.assertEqual([row['release_date'] for row in events(movie,'movie',cinema=True,country='DE')], ['2000-01-01'])
        self.assertEqual(events(movie,'movie',cinema=True,country='DE',upcoming_only=True), [])
        releases=events(movie,'movie',cinema=True,country='DE',include_rereleases=True)
        self.assertEqual(len(releases),4)
        self.assertEqual(len({row['release_event'] for row in releases}),4)
        general=events(movie,'movie',country='DE',include_rereleases=True)
        self.assertEqual([row['release_date'] for row in general],[row['release_date'] for row in releases])
        self.assertEqual(len({row['release_event'] for row in general}),4)
        revised={**movie,'theatrical_release_dates_by_country':{'DE':['1999-01-01','2020-01-01','2099-01-01']}}
        self.assertEqual(events(revised,'movie',cinema=True,country='DE')[0]['release_event'],releases[0]['release_event'])
        future=events(movie,'movie',cinema=True,country='DE',upcoming_only=True,include_rereleases=True)
        self.assertEqual([row['release_date'] for row in future],['2099-01-01'])


class ChronologicalTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        discovery._cache._store.clear()
        self.filters = dict(genres=[], start=None, end=None, status='upcoming', provider=None, region='DE', sort='upcoming', min_popularity=25)

    def test_country_is_distribution_evidence_not_origin_or_language(self):
        self.assertTrue(discovery.audience_relevant({'original_language': 'ko', 'popularity': 100}, 'DE'))
        self.assertTrue(discovery.audience_relevant({'popularity': 27, 'networks': [{'origin_country': 'DE'}]}, 'DE'))
        self.assertFalse(discovery.audience_relevant({'popularity': 27, 'original_language': 'de', 'origin_country': ['DE']}, 'DE'))

    async def test_upcoming_returns_sorted_before_paging_and_reuses_snapshot(self):
        async def provider(url, *, headers, params):
            if '/discover/' in url:
                self.assertTrue('first_air_date.gte' in params or 'air_date.gte' in params)
                self.assertEqual(params['sort_by'], 'popularity.desc')
                return {'total_pages': 1, 'results': [{'id': 1, 'name': 'Later', 'popularity': 90}, {'id': 2, 'name': 'Earlier', 'popularity': 80}]}
            identifier = int(url.rsplit('/', 1)[1])
            return {'id': identifier, 'name': 'Later' if identifier == 1 else 'Earlier', 'popularity': 90,
                'first_air_date': '2020-01-01', 'seasons': [{'season_number': 2, 'air_date': '2099-03-01' if identifier == 1 else '2099-02-01'}]}
        with patch('core.tmdb._get', AsyncMock(side_effect=provider)) as fetch:
            first, rows = await discovery.page('series', 'fixture', self.filters, size=1)
            second, later = await discovery.page('series', 'fixture', self.filters, offset=first['scan_next'], size=1)
        self.assertEqual(rows[0]['title'], 'Earlier · Season 2')
        self.assertEqual(later[0]['title'], 'Later · Season 2')
        self.assertTrue(first['has_more'])
        self.assertFalse(second['has_more'])
        self.assertEqual(fetch.await_count, 4)
        self.assertEqual(set(first['event_keys']), {rows[0]['release_event'], later[0]['release_event']})

    async def test_scan_budget_is_independent_of_floor_and_partial_is_explicit(self):
        with patch('core.tmdb._get', AsyncMock(return_value={'total_pages': 500, 'results': [{'id': 1, 'popularity': 1}]})) as fetch:
            data, rows = await discovery.page('series', 'fixture', self.filters)
        self.assertEqual(rows, [])
        self.assertEqual(fetch.await_count, discovery.PROVIDER_PAGES)
        self.assertTrue(data['partial'])
        self.assertFalse(data['has_more'])

    async def test_show_all_cannot_displace_curated_candidates_at_provider_budget_boundary(self):
        async def provider(url, *, headers, params):
            if '/discover/' in url:
                self.assertNotIn('without_genres', params)
                self.assertNotIn('vote_count.gte', params)
                self.assertNotIn('with_runtime.gte', params)
                return {'total_pages': 1, 'results': [
                    {'id': 1, 'title': 'Known', 'genre_ids': [18], 'popularity': 80, 'vote_count': 300},
                    {'id': 2, 'title': 'Obscure reality', 'genre_ids': [10764], 'popularity': 1}]}
            identifier = int(url.rsplit('/', 1)[1])
            return {'id': identifier, 'title': 'Known' if identifier == 1 else 'Obscure reality',
                'release_date': '2099-01-01', 'popularity': 80 if identifier == 1 else 1,
                'vote_count': 300 if identifier == 1 else 0,
                'genres': [{'id': 18 if identifier == 1 else 10764}]}
        curated = {**self.filters, 'min_votes': 250, 'min_runtime': 30, 'excluded_genres': [10764]}
        broadened = {**curated, 'min_votes': 0, 'min_popularity': 0, 'min_runtime': 0,
            'excluded_genres': [], 'audience_curation': False}
        with patch('core.tmdb._get', AsyncMock(side_effect=provider)):
            _, normal = await discovery.page('movie', 'fixture', curated)
            _, all_rows = await discovery.page('movie', 'fixture', broadened)
        self.assertEqual([row['id'] for row in normal], [1])
        self.assertEqual({row['id'] for row in all_rows}, {1, 2})

    async def test_unfiltered_preserves_explicit_genre_provider_and_country_cinema(self):
        filters = {**self.filters, 'min_popularity': 0, 'audience_curation': False, 'genres': [18], 'provider': 8, 'cinema': True, 'cinema_country': 'DE'}
        async def provider(url, *, headers, params):
            if '/discover/' in url:
                self.assertEqual(params['with_genres'], '18')
                self.assertEqual(params['with_watch_providers'], 8)
                self.assertEqual(params['with_release_type'], '2|3')
                self.assertEqual(params['region'], 'DE')
                return {'total_pages': 1, 'results': [{'id': 1, 'title': 'Small film', 'genre_ids': [18]}]}
            return {'id': 1, 'title': 'Small film', 'genres': [{'id': 18}], 'release_date': '2099-01-01',
                'watch/providers': {'results': {'DE': {'flatrate': [{'provider_id': 8}]}}},
                'release_dates': {'results': [{'iso_3166_1': 'DE', 'release_dates': [{'type': 3, 'release_date': '2099-02-01'}]}]}}
        with patch('core.tmdb._get', AsyncMock(side_effect=provider)):
            _, rows = await discovery.page('movie', 'fixture', filters)
        self.assertEqual(rows[0]['release_date'], '2099-02-01')

    async def test_shared_selection_survives_one_cancel_but_cancels_orphaned_work(self):
        entered=asyncio.Event(); finish=asyncio.Event(); cancelled=asyncio.Event()
        async def selection(*args, **kwargs):
            entered.set()
            try:
                await finish.wait()
            except asyncio.CancelledError:
                cancelled.set();raise
            return {'rows':[],'partial':False}
        with patch.object(discovery,'_selection',side_effect=selection) as build:
            first=asyncio.create_task(discovery.page('series','fixture',self.filters))
            await entered.wait()
            second=asyncio.create_task(discovery.page('series','fixture',self.filters))
            await asyncio.sleep(0)
            first.cancel()
            await asyncio.gather(first,return_exceptions=True)
            self.assertFalse(cancelled.is_set())
            finish.set();await second
            self.assertEqual(build.call_count,1)
            discovery._cache._store.clear();finish.clear();entered.clear()
            orphan=asyncio.create_task(discovery.page('series','fixture',self.filters))
            await entered.wait();orphan.cancel()
            await asyncio.gather(orphan,return_exceptions=True)
            await asyncio.wait_for(cancelled.wait(),1)
            self.assertFalse(discovery._pending)

    async def test_selection_capacity_is_bounded_without_hidden_extra_work(self):
        entered=asyncio.Event();release=asyncio.Event()
        async def selection(*args, **kwargs):
            entered.set();await release.wait();return {'rows':[],'partial':False}
        with patch.object(discovery,'MAX_SELECTIONS',1),patch.object(discovery,'_selection',side_effect=selection) as build:
            first=asyncio.create_task(discovery.page('series','fixture',self.filters))
            await entered.wait()
            with self.assertRaises(discovery.tmdb.TMDBUnavailable):
                await discovery.page('movie','fixture',self.filters)
            release.set();await first
            self.assertEqual(build.call_count,1)

    def test_domestic_distribution_does_not_become_global_through_a_high_score(self):
        domestic={'popularity':138,'watch/providers':{'results':{'JP':{'flatrate':[{'provider_id':8}]}}}}
        self.assertFalse(discovery.audience_relevant(domestic,'DE'))
        self.assertTrue(discovery.audience_relevant(domestic,'JP'))
        self.assertTrue(discovery.audience_relevant({'popularity':100},'DE'))
        global_hit={**domestic,'watch/providers':{'results':{code:{'flatrate':[{'provider_id':8}]} for code in ('JP','US','GB')}}}
        self.assertTrue(discovery.audience_relevant(global_hit,'DE'))
