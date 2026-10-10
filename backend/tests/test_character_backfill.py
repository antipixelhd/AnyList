"""Only exactly identified shows and unambiguous performances supply artwork."""
import os
import unittest
from types import SimpleNamespace as Row
from unittest.mock import AsyncMock

os.environ.setdefault('SECRET_KEY', 'test-secret')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

from core.character_backfill import match_cast, tvmaze_show
from core.catalogue_providers import ProviderError
from core.screen_characters import normalized_role, reusable_role, bounded_key


def credit(actor=1, role='Alice'):
    return (Row(provider='tmdb', work_id=10, contributor_id=actor, character_label=role), Row(id=actor, name='Actor'))


def member(**changes):
    return {'person': {'id': 1, 'name': 'Actor', 'image': {'original': 'https://example.test/portrait.jpg'}},
            'character': {'id': 2, 'name': 'Alice', 'image': {'original': 'https://example.test/role.jpg'}}, **changes}


class MatchingTests(unittest.TestCase):
    def test_only_character_art_unique_actor_role_and_safe_urls(self):
        data = match_cast([credit()], [member()])
        self.assertEqual(data[0]['character_image_url'], 'https://example.test/role.jpg')
        self.assertEqual(data[0]['contributor_id'], 1)
        self.assertEqual(match_cast([credit(), credit(2)], [member()]), [])
        self.assertEqual(match_cast([credit(role='Bob')], [member()]), [])
        self.assertEqual(match_cast([credit()], [member(character={'id': 2, 'name': 'Alice', 'image': None})]), [])
        self.assertEqual(match_cast([credit()], [member(character={'id': 2, 'name': 'Alice', 'image': {'original': 'javascript:bad'}})]), [])

    def test_role_variants_are_conservative_and_bounded(self):
        self.assertEqual(normalized_role(' ALICE (voice) '), 'alice')
        for name in ('Himself', 'Self / Host', 'Guard', ''):
            self.assertFalse(reusable_role(name))
        self.assertTrue(reusable_role('Tony Stark / Iron Man'))
        self.assertLessEqual(len(bounded_key('x' * 600)), 500)
        self.assertNotEqual(bounded_key('x' * 600), bounded_key('x' * 599 + 'y'))


class LookupTests(unittest.IsolatedAsyncioTestCase):
    async def test_verified_redirect_and_cross_reference(self):
        http = Row(request=AsyncMock(side_effect=[{'_redirect': 'https://api.tvmaze.com/shows/123'},
            {'id': 123, 'externals': {'imdb': 'tt123', 'thetvdb': 456}}]))
        result = await tvmaze_show(http, {'imdb.title': 'tt123', 'tvdb.series': '456'})
        self.assertEqual(result['id'], 123)
        self.assertEqual(http.request.await_args_list[0].kwargs['params'], {'imdb': 'tt123'})

    async def test_not_found_falls_back_to_other_verified_id(self):
        http = Row(request=AsyncMock(side_effect=[ProviderError('not_found'), {'id': 123, 'externals': {'thetvdb': 456}}]))
        self.assertEqual((await tvmaze_show(http, {'imdb.title': 'tt123', 'tvdb.series': '456'}))['id'], 123)
        http.request = AsyncMock(side_effect=ProviderError('not_found'))
        self.assertIsNone(await tvmaze_show(http, {'tvdb.series': '456'}))

    async def test_rejects_redirects_and_disagreeing_show_identity(self):
        for value in ({'_redirect': 'https://evil.test/shows/123'}, {'_redirect': 'https://api.tvmaze.com/shows/123?key=secret'},
                      {'id': 123, 'externals': {'imdb': 'tt999'}}, [], {'id': True},
                      {'id': 123, 'externals': {'imdb': 'tt123', 'thetvdb': 999}}):
            with self.subTest(value=value), self.assertRaises(ProviderError):
                await tvmaze_show(Row(request=AsyncMock(return_value=value)), {'imdb.title': 'tt123', 'tvdb.series': '456'})
