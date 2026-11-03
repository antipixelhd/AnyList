import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ.setdefault('SECRET_KEY','batch-test-only')
os.environ.setdefault('DATABASE_URL','postgresql+asyncpg://test:test@localhost/test')
from core.catalogue_providers import ProviderError
from core.game_candidate_queries import GameCandidateQueries


class CandidateQueryTests(unittest.IsolatedAsyncioTestCase):
    async def test_staggered_cache_misses_share_the_same_provider_request(self):
        source=SimpleNamespace(cache=object(),igdb_queries=AsyncMock(side_effect=lambda queries:[[query] for query in queries]))
        batch=GameCandidateQueries(source)
        first=asyncio.create_task(batch.igdb_queries(['search 1','exact 1']))
        await asyncio.sleep(.001)
        rest=[asyncio.create_task(batch.igdb_queries([f'search {i}',f'exact {i}'])) for i in range(2,5)]
        result=await asyncio.gather(first,*rest)
        source.igdb_queries.assert_awaited_once()
        self.assertEqual(len(source.igdb_queries.await_args.args[0]),8)
        self.assertEqual(result[0],[['search 1'],['exact 1']])

    async def test_a_slow_sibling_does_not_block_an_already_ready_query(self):
        entered,release=asyncio.Event(),asyncio.Event()
        calls=[]
        async def fetch(queries):calls.append(queries);entered.set();return [[query] for query in queries]
        batch=GameCandidateQueries(SimpleNamespace(cache=object(),igdb_queries=fetch))
        async def slow_cache():
            await release.wait()
            return await batch.igdb('games','late')
        late=asyncio.create_task(slow_cache())
        task=asyncio.create_task(batch.igdb('games','ready'))
        await asyncio.wait_for(entered.wait(),1)
        self.assertEqual(await task,['ready'])
        self.assertFalse(late.done())
        release.set();self.assertEqual(await late,['late'])
        self.assertEqual(calls,[['ready'],['late']])

    async def test_siblings_share_bounded_requests_and_keep_their_own_order(self):
        sizes=[]
        async def fetch(queries):
            sizes.append(len(queries));await asyncio.sleep(0)
            return [[query] for query in queries]
        source=SimpleNamespace(cache=object(),igdb_queries=AsyncMock(side_effect=fetch))
        batch=GameCandidateQueries(source)
        result=await asyncio.gather(*(batch.igdb_queries([f'search {i}',f'exact {i}']) for i in range(7)))
        self.assertEqual(sizes,[10,4])
        self.assertEqual(result,[[[f'search {i}'],[f'exact {i}']] for i in range(7)])

    async def test_failure_reaches_every_waiter_and_next_batch_recovers(self):
        source=SimpleNamespace(cache=object(),igdb_queries=AsyncMock(side_effect=ProviderError('unavailable')))
        batch=GameCandidateQueries(source)
        results=await asyncio.gather(batch.igdb('games','first'),batch.igdb('games','second'),return_exceptions=True)
        self.assertTrue(all(isinstance(value,ProviderError) for value in results))
        source.igdb_queries.side_effect=None;source.igdb_queries.return_value=[['recovered']]
        self.assertEqual(await batch.igdb('games','third'),['recovered'])

    async def test_cancelling_one_waiter_preserves_another_and_invalid_batches_fail(self):
        entered,release=asyncio.Event(),asyncio.Event()
        async def fetch(queries):entered.set();await release.wait();return [[query] for query in queries]
        source=SimpleNamespace(cache=object(),igdb_queries=fetch)
        batch=GameCandidateQueries(source)
        first=asyncio.create_task(batch.igdb('games','first'));second=asyncio.create_task(batch.igdb('games','second'))
        await entered.wait();first.cancel();release.set()
        with self.assertRaises(asyncio.CancelledError):await first
        self.assertEqual(await second,['second'])
        for invalid in ([],['query']*11):
            with self.assertRaises(ProviderError):await batch.igdb_queries(invalid)
