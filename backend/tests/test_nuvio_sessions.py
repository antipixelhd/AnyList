"""Access-token reuse must preserve rotating credentials and expiry boundaries."""
import asyncio
import os
import unittest
from collections import OrderedDict
from unittest.mock import AsyncMock, patch

import httpx

os.environ.setdefault('SECRET_KEY', 'test-secret')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')
from core import nuvio


class NuvioSessionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        cache = patch.object(nuvio, '_session_cache', OrderedDict())
        cache.start()
        self.addCleanup(cache.stop)
        self.session = nuvio.NuvioSession('access', 'rotated', 7200, 'account')

    async def test_reuses_rotated_session_across_runs_without_extending_expiry(self):
        with (
            patch.object(nuvio, '_refresh_session', AsyncMock(return_value=self.session)) as refresh,
            patch.object(nuvio, 'monotonic', return_value=100) as clock,
        ):
            await nuvio.refresh_session('https://example.test', 'original')
            clock.return_value = 400
            cached = await nuvio.refresh_session('https://example.test/', 'rotated')
            self.assertEqual(cached.expires_in, 6900)
            clock.return_value = 7271
            await nuvio.refresh_session('https://example.test', 'original')
        self.assertEqual(refresh.await_count, 2)
        self.assertEqual(refresh.await_args.args[1], 'rotated')

    async def test_concurrent_calls_share_one_refresh(self):
        async def authenticate(*args):
            await asyncio.sleep(0)
            return self.session

        with patch.object(nuvio, '_refresh_session', AsyncMock(side_effect=authenticate)) as refresh:
            sessions = await asyncio.gather(*(
                nuvio.refresh_session('https://example.test', 'original') for _ in range(20)))
        refresh.assert_awaited_once()
        self.assertTrue(all(session.access_token == 'access' for session in sessions))

    async def test_five_minute_pulls_share_one_refresh_over_two_hours(self):
        with (
            patch.object(nuvio, '_refresh_session', AsyncMock(return_value=self.session)) as refresh,
            patch.object(nuvio, 'monotonic', return_value=0) as clock,
            patch.object(nuvio, 'get_profiles', AsyncMock(return_value=[{'profile_index': 1}])),
            patch.object(nuvio, '_rpc', AsyncMock(return_value=0)),
        ):
            token = 'original'
            for run in range(24):
                clock.return_value = run * 300
                probe = await nuvio.prepare_pull('https://example.test', token, 1,
                    categories=frozenset({'watched_items'}))
                token = probe.session.refresh_token
        refresh.assert_awaited_once()

    async def test_different_server_or_credentials_get_separate_sessions(self):
        with patch.object(nuvio, '_refresh_session', AsyncMock(return_value=self.session)) as refresh:
            await nuvio.refresh_session('https://one.test', 'original')
            await nuvio.refresh_session('https://two.test', 'original')
            await nuvio.refresh_session('https://one.test', 'reconnected')
        self.assertEqual(refresh.await_count, 3)

    async def test_unauthorized_rpc_evicts_all_credential_aliases(self):
        transport = httpx.MockTransport(lambda request: httpx.Response(401, json={'message': 'expired'}))
        with patch.object(nuvio, '_refresh_session', AsyncMock(return_value=self.session)) as refresh:
            await nuvio.refresh_session('https://example.test', 'original')
            async with httpx.AsyncClient(transport=transport) as client:
                with self.assertRaises(nuvio.NuvioAPIError):
                    await nuvio._rpc(client, 'https://example.test', 'access', 'sync_pull_profiles')
            self.assertEqual(len(nuvio._session_cache), 0)
            await nuvio.refresh_session('https://example.test', 'rotated')
        self.assertEqual(refresh.await_count, 2)

    async def test_cache_stays_bounded(self):
        with (
            patch.object(nuvio, '_SESSION_CACHE_SIZE', 4),
            patch.object(nuvio, '_refresh_session', AsyncMock(return_value=self.session)),
        ):
            for index in range(10):
                await nuvio.refresh_session(f'https://server-{index}.test', 'original')
        self.assertLessEqual(len(nuvio._session_cache), 4)
