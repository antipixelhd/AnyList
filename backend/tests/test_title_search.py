"""Title intent regressions and the provider work budget; no database is used."""
import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault('SECRET_KEY', 'title-search-tests')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

from core.catalog_search import search_rank
from core.title_search import candidates, MAX_CORRECTIONS


DOOMSDAY = {'id': 1003596, 'title': 'Avengers: Doomsday', 'popularity': 111.4481, 'vote_count': 0}
BELOW = {'id': 285322, 'name': 'Below', 'popularity': 114.458, 'vote_count': 158}
WAR = {'id': 298852, 'name': 'WAR', 'popularity': 48.9071, 'vote_count': 16}


class TitleRankingTests(unittest.TestCase):
    def test_doomsday_prominence_beats_obscure_exact_titles(self):
        # Captured TMDB candidates, not a hand-authored title exception.
        rivals = [
            {'title': 'Doomsday', 'popularity': 7.7349, 'vote_count': 1461},
            {'title': 'Superman: Doomsday', 'popularity': 5.1826, 'vote_count': 723},
            {'title': 'DOOMSDAY', 'popularity': 1.6621, 'vote_count': 0},
        ]
        self.assertGreater(search_rank('doomsday', DOOMSDAY),
                           max(search_rank('doomsday', item) for item in rivals))

    def test_specific_avengers_query_does_not_become_franchise_popularity_sort(self):
        rivals = [{'title': title, 'vote_count': 1_000_000, 'popularity': 1_000_000}
                  for title in ('Avengers: Endgame', 'Avengers: Infinity War', 'Avengers: Age of Ultron')]
        for query in ('avengers: doom', 'avengers: dooms', 'avengrs dooms'):
            with self.subTest(query=query):
                self.assertGreater(search_rank(query, DOOMSDAY),
                                   max(search_rank(query, item) for item in rivals))

    def test_new_low_vote_series_are_ranked_by_interest(self):
        self.assertGreater(search_rank('war', WAR), search_rank('war', {'name': 'War', 'vote_count': 1}))
        self.assertGreater(search_rank('below', BELOW),
                           search_rank('below', {'name': 'Below', 'popularity': 12.7806, 'vote_count': 17}))
        self.assertGreater(search_rank('below', BELOW),
                           search_rank('below', {'name': 'Below Deck', 'popularity': 26.4272, 'vote_count': 70}))

    def test_movie_and_series_use_identical_scale_without_poster_requirement(self):
        values = {'popularity': 40, 'vote_count': 100}
        self.assertEqual(search_rank('silo', {'title': 'Silo', **values}),
                         search_rank('silo', {'name': 'Silo', **values}))
        self.assertEqual(search_rank('silo', {'title': 'Silo', **values}),
                         search_rank('silo', {'title': 'Silo', 'poster_path': '/poster', **values}))

    def test_translated_provider_match_retains_relevance(self):
        translated = {'title': 'Spirited Away', 'original_title': '千と千尋の神隠し',
                      'popularity': 50, '_provider_query': 'Chihiros Reise ins Zauberland'}
        self.assertGreater(search_rank('Chihiros Reise ins Zauberland', translated), 35)
        self.assertEqual(search_rank('千と千尋の神隠し', translated),
                         search_rank('Spirited Away', translated))


class CandidateBudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_strong_query_uses_one_provider_page_without_metadata_requests(self):
        for kind, row, query, endpoint in (
            ('movie', DOOMSDAY, 'avengers: doom', 'search_movies'),
            ('series', BELOW, 'below', 'search_shows'),
            ('series', WAR, 'war', 'search_shows'),
        ):
            with self.subTest(kind=kind, query=query), \
                 patch('core.tmdb.' + endpoint, AsyncMock(return_value={'results': [row], 'total_pages': 4})) as search, \
                 patch('core.tmdb._get', AsyncMock()) as details:
                data, rows = await candidates(query, kind, 'fixture')
                search.assert_awaited_once_with(query, page=1, api_key='fixture')
                details.assert_not_awaited()
                self.assertEqual(rows[0]['id'], row['id'])
                self.assertEqual(data['total_pages'], 4)

    async def test_typo_corrections_are_bounded_and_recover_missing_letter(self):
        async def response(query, **kwargs):
            return {'results': [DOOMSDAY] if query == 'dooms' else []}
        with patch('core.tmdb.search_movies', AsyncMock(side_effect=response)) as search:
            _, rows = await candidates('avengrs dooms', 'movie', 'fixture')
        self.assertEqual([row['id'] for row in rows], [DOOMSDAY['id']])
        self.assertLessEqual(search.await_count, 1 + MAX_CORRECTIONS)

    async def test_provider_translated_name_is_kept_without_speculative_queries(self):
        row = {'id': 129, 'title': 'Spirited Away', 'original_title': '千と千尋の神隠し',
               'popularity': 50, 'vote_count': 10000}
        query = 'Chihiros Reise ins Zauberland'
        with patch('core.tmdb.search_movies', AsyncMock(return_value={'results': [row]})) as search:
            _, rows = await candidates(query, 'movie', 'fixture')
        search.assert_awaited_once_with(query, page=1, api_key='fixture')
        self.assertEqual([item['id'] for item in rows], [129])

    async def test_correction_does_not_admit_unrelated_popular_titles(self):
        async def response(query, **kwargs):
            return {'results': [] if query == 'mutany' else [
                {'id': 1, 'title': 'Avengers', 'popularity': 100000},
                {'id': 2, 'title': 'Mutiny', 'popularity': 50}]}
        with patch('core.tmdb.search_movies', AsyncMock(side_effect=response)):
            _, rows = await candidates('mutany', 'movie', 'fixture')
        self.assertEqual([item['id'] for item in rows], [2])

    async def test_weak_nonempty_literal_does_not_suppress_better_correction(self):
        literal = {'id': 1, 'title': 'Mutany', 'popularity': .01, 'vote_count': 0}
        corrected = {'id': 2, 'title': 'Mutiny', 'popularity': 80, 'vote_count': 20}
        async def response(query, **kwargs):
            return {'results': [literal] if query == 'mutany' else [corrected] if query == 'mutiny' else []}
        with patch('core.tmdb.search_movies', AsyncMock(side_effect=response)) as search:
            _, rows = await candidates('mutany', 'movie', 'fixture')
        self.assertLessEqual(search.await_count, 1 + MAX_CORRECTIONS)
        self.assertEqual(max(rows, key=lambda row: search_rank('mutany', row))['id'], 2)

    async def test_correction_failures_keep_direct_results_and_deduplicate(self):
        direct = {'id': 1, 'title': 'Mutany'}
        async def response(query, **kwargs):
            if query == 'mutany': return {'results': [direct]}
            if query == 'mutiny': return {'results': [direct, {'id': 2, 'title': 'Unrelated', 'original_title': 'Mutiny'}]}
            raise RuntimeError('temporary provider failure')
        with patch('core.tmdb.search_movies', AsyncMock(side_effect=response)):
            _, rows = await candidates('mutany', 'movie', 'fixture')
        self.assertEqual([row['id'] for row in rows], [1, 2])
        self.assertEqual(rows[0]['_provider_query'], 'mutany')

    async def test_later_pages_never_expand_typos_and_provider_limit_stops_requests(self):
        with patch('core.tmdb.search_shows', AsyncMock(return_value={'results': []})) as search:
            await candidates('mutany', 'series', 'fixture', page=2)
            search.assert_awaited_once_with('mutany', page=2, api_key='fixture')
            data, rows = await candidates('mutany', 'series', 'fixture', page=501)
        self.assertEqual(search.await_count, 1)
        self.assertEqual(rows, [])
        self.assertEqual(data['total_pages'], 500)


if __name__ == '__main__':
    unittest.main()
