import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault('SECRET_KEY', 'test-only-secret')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

from core import contributor_images as images, tmdb


class ContributorImageTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        images._failures._store.clear()
        tmdb._cache._store.clear()
        tmdb._breaker_record_success()
        self.resolve = patch.object(images, 'resolve_contributor', AsyncMock(return_value=(None, 42)))
        self.resolve.start()
        self.key = patch.object(images.settings_store, 'get_user_tmdb_key', AsyncMock(return_value='fixture'))
        self.key.start()

    async def asyncTearDown(self):
        self.resolve.stop(); self.key.stop()
        await tmdb.close()

    async def load(self, kind='actor'):
        return await images.contributor_images(None, None, kind=kind, key='tmdb:42')

    async def test_cold_concurrent_actor_and_staff_share_one_request_and_warm_empty_results_are_cached(self):
        async def request(*args):
            await asyncio.sleep(.01)
            return {'id':42, 'profiles':[]}
        with patch.object(tmdb, '_request', AsyncMock(side_effect=request)) as provider:
            first, second = await asyncio.gather(self.load(), self.load('staff'))
            self.assertEqual(first, second)
            self.assertEqual(first['image_status']['state'], 'ready')
            self.assertEqual(first['posters'], [])
            await self.load()
            provider.assert_awaited_once()

    async def test_selects_five_distinct_safe_portraits_without_downloading_images(self):
        rows=[{'file_path':f'/{n}.jpg','vote_average':n,'aspect_ratio':.667} for n in range(8)]
        rows += [rows[-1], {'file_path':'https://unsafe.test/image.jpg','vote_average':20},
                 {'file_path':'/landscape.jpg','vote_average':20,'aspect_ratio':2}]
        with patch.object(tmdb, '_request', AsyncMock(return_value={'id':42,'profiles':rows})) as provider:
            result=await self.load()
        self.assertEqual(result['posters'],[f'https://image.tmdb.org/t/p/original/{n}.jpg' for n in [7,6,5,4,3]])
        self.assertEqual(provider.call_args.args[1],tmdb.TMDB_BASE+'/person/42/images')

    async def test_failure_has_a_cooldown_and_never_becomes_a_successful_empty_gallery(self):
        with patch.object(tmdb, 'get_person_images', AsyncMock(side_effect=RuntimeError('provider outage'))) as provider:
            first=await self.load(); second=await self.load()
        self.assertEqual(first,second)
        self.assertEqual(first['image_status']['state'],'deferred')
        provider.assert_awaited_once()

    async def test_missing_identity_is_terminal_and_wrong_provider_identity_is_rejected(self):
        with patch.object(images,'resolve_contributor',AsyncMock(return_value=(SimpleNamespace(),None))), \
             patch.object(tmdb,'get_person_images',AsyncMock()) as provider:
            self.assertEqual((await self.load())['image_status']['state'],'unavailable')
            provider.assert_not_awaited()
        with patch.object(tmdb,'get_person_images',AsyncMock(return_value={'id':99,'profiles':[]})):
            self.assertEqual((await self.load())['image_status']['state'],'deferred')
