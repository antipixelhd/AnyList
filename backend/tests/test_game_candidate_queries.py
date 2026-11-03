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
