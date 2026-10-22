import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault('SECRET_KEY', 'games-test-only')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

from core import game_discovery
from core.catalogue_providers import CatalogueProviders, ProviderError


class GameDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        game_discovery._slots = asyncio.Semaphore(2)

    async def test_queued_searches_hold_no_provider_lane_and_cancel_without_dispatch(self):
        started, release = asyncio.Event(), asyncio.Event()
        active = peak = 0
        async def result(*args):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            if active == 2:
                started.set()
            await release.wait()
            active -= 1
            return []
        with patch.object(CatalogueProviders, 'search', side_effect=result) as provider:
            first = [asyncio.create_task(game_discovery.candidates(q)) for q in ('one', 'two')]
            await started.wait()
            queued = asyncio.create_task(game_discovery.candidates('three'))
            await asyncio.sleep(0)
            self.assertEqual(provider.call_count, 2)
            queued.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await queued
            release.set()
            await asyncio.gather(*first)
            self.assertEqual(provider.call_count, 2)
            self.assertEqual(peak, 2)
        await asyncio.sleep(0)
        self.assertEqual(game_discovery._searches, {})

    async def test_failed_public_flight_can_retry_and_unique_work_is_bounded(self):
        with patch.object(CatalogueProviders, 'search', AsyncMock(side_effect=[ProviderError('unavailable'), []])) as provider:
            with self.assertRaises(ProviderError):
                await game_discovery.candidates('Retry game')
            self.assertEqual(await game_discovery.candidates('Retry game'), [])
            self.assertEqual(provider.call_count, 2)
        with patch.dict(game_discovery._searches, {str(i): None for i in range(32)}):
            with self.assertRaisesRegex(ProviderError, 'rate_limited'):
                await game_discovery.candidates('Different query')

    async def test_cancelled_viewer_does_not_cancel_shared_public_search(self):
        started, release = asyncio.Event(), asyncio.Event()
        async def result(*args):
            started.set()
            await release.wait()
            return [{'external_id': '240009'}]
        with patch.object(CatalogueProviders, 'search', side_effect=result) as provider:
            first = asyncio.create_task(game_discovery.candidates('Erdtree'))
            await started.wait()
            second = asyncio.create_task(game_discovery.candidates('erdtree'))
            await asyncio.sleep(0)
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first
            release.set()
            self.assertEqual(await second, [{'external_id': '240009'}])
            self.assertEqual(provider.call_count, 1)
        await asyncio.sleep(0)
        self.assertEqual(game_discovery._searches, {})

    async def test_provider_search_deduplicates_canonical_expansion_and_edition(self):
        api = CatalogueProviders()
        api.validate = lambda *args: None
        parent = {'id': 119133, 'name': 'Base', 'game_type': {'id': 0}}
        expansion = {'id': 240009, 'name': 'Erdtree', 'game_type': {'id': 2}, 'parent_game': parent}
        api.igdb = AsyncMock(return_value=[expansion, {'id': 900, 'version_parent': expansion},
            {'id': 3, 'name': 'Small DLC', 'game_type': {'id': 1}}])
        rows = await api.search('igdb', 'game', 'Erdtree')
        self.assertEqual([row['external_id'] for row in rows], ['240009'])
        self.assertIn('version_parent.parent_game.game_type.type', api.igdb.call_args.args[1])

    async def test_provider_search_folds_verified_ports_into_original_without_name_matching(self):
        api=CatalogueProviders();api.validate=lambda *args:None
        base={'id':1960,'name':'Original','game_type':{'id':0}}
        port={'id':201090,'name':'Different localized title','game_type':{'id':11},'parent_game':base}
        remake={'id':100,'name':'Original','game_type':{'id':8},'parent_game':base}
        api.igdb=AsyncMock(return_value=[port,base,{'id':999,'version_parent':port},remake,
            {**port,'id':998,'parent_game':1960}])
        rows=await api.search('igdb','game','Original')
        self.assertEqual([r['external_id'] for r in rows],['1960','100'])
        self.assertEqual(rows[0]['name'],'Original')
        self.assertEqual(api.igdb.await_count,1)

    async def test_partial_last_word_recovers_only_matching_canonical_candidates(self):
        api=CatalogueProviders();api.validate=lambda *args:None
        api.igdb=AsyncMock(side_effect=[[],[
            {'id':119133,'name':'Elden Ring','game_type':{'id':0}},
            {'id':5,'name':'Elden Sword','game_type':{'id':0}},
            {'id':7,'name':'Elden R Cosmetic Pack','game_type':{'id':13}}]])
        rows=await api.search('igdb','game','Elden R')
        self.assertEqual([row['external_id'] for row in rows],['119133'])
        self.assertEqual(api.igdb.await_count,2)
        self.assertIn('search "Elden";',api.igdb.call_args.args[1])

    async def test_partial_search_does_not_turn_provider_failure_into_empty_or_fan_out(self):
        api=CatalogueProviders();api.validate=lambda *args:None
        api.igdb=AsyncMock(side_effect=ProviderError('unavailable'))
        with self.assertRaises(ProviderError):await api.search('igdb','game','Elden R')
        self.assertEqual(api.igdb.await_count,1)

    async def test_child_coverage_does_not_certify_unknown_addon_types_as_empty(self):
        api=CatalogueProviders()
        api._igdb_pages=AsyncMock(return_value=([],True))
        for children,expected in [([],True),([{'id':2,'name':'Pack','game_type':{'id':13}}],True),
                ([{'id':2,'name':'Future type','game_type':{'id':999}}],False)]:
            api.igdb=AsyncMock(return_value=[{'id':1,'name':'Base','game_type':{'id':0},'dlcs':children}])
            doc,_=await api._detail('igdb','game',1)
            self.assertEqual(doc['coverage']['children_complete'],expected)
