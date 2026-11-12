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
    async def test_discovery_workers_share_connections_and_close_them_on_shutdown(self):
        from core.game_imdb_discovery import GamePersonDiscovery
        entered=asyncio.Event()
        clients=[]
        service=GamePersonDiscovery(None)
        async def worker():
            clients.append(service.source.sources.api.http.client)
            if len(clients)==2:
                entered.set()
            await asyncio.Event().wait()
        service.worker=worker
        task=asyncio.create_task(service.serve())
        try:
            await asyncio.wait_for(entered.wait(),1)
            self.assertIsNotNone(clients[0])
            self.assertIs(clients[0],clients[1])
            self.assertFalse(clients[0].is_closed)
        finally:
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertTrue(clients[0].is_closed)
        self.assertIsNone(service.source.sources.api.http.client)

    async def test_search_admission_keeps_only_its_subscribers_publication_order(self):
        from core.game_work import current,work_context
        calls={}
        async def fetch(queries):
            calls[tuple(queries)]=current.get().scopes
            return [[query] for query in queries]
        batch=GameCandidateQueries(SimpleNamespace(cache=object(),igdb_queries=fetch))
        async def title(order):
            with work_context(2,'user:1','job:career',replace=True):
                with work_context(scope=f'candidate-order:{order:04}'):
                    return await batch.igdb_queries([f'search "title {order}";'])
        await asyncio.gather(title(0),title(1))
        self.assertIn('candidate-order:0000',calls[('search "title 0";',)])
        self.assertNotIn('candidate-order:0001',calls[('search "title 0";',)])
        self.assertIn('candidate-order:0001',calls[('search "title 1";',)])
        self.assertNotIn('candidate-order:0000',calls[('search "title 1";',)])

    async def test_identical_queries_share_transport_and_preserve_each_order(self):
        source=SimpleNamespace(cache=object(),igdb_queries=AsyncMock(side_effect=lambda queries:[[q] for q in queries]))
        batch=GameCandidateQueries(source)
        first,second=await asyncio.gather(batch.igdb_queries(['one','two']),batch.igdb_queries(['two','one']))
        self.assertEqual(first,[['one'],['two']]);self.assertEqual(second,[['two'],['one']])
        source.igdb_queries.assert_awaited_once_with(['one','two'])

    async def test_fast_title_finishes_before_sibling_search_and_failure(self):
        entered,release=asyncio.Event(),asyncio.Event()
        async def fetch(queries):
            if queries==['search "slow";']:
                entered.set();await release.wait();raise ProviderError('unavailable')
            return [[q] for q in queries]
        batch=GameCandidateQueries(SimpleNamespace(cache=object(),igdb_queries=fetch))
        first=asyncio.create_task(batch.igdb_queries(['search "fast";','exact fast']))
        second=asyncio.create_task(batch.igdb_queries(['search "slow";','exact slow']))
        await entered.wait()
        try:self.assertEqual(await asyncio.wait_for(first,1),[['search "fast";'],['exact fast']])
        finally:release.set()
        with self.assertRaises(ProviderError):await second

    async def test_staggered_cache_misses_share_the_same_provider_request(self):
        source=SimpleNamespace(cache=object(),igdb_queries=AsyncMock(side_effect=lambda queries:[[query] for query in queries]))
        batch=GameCandidateQueries(source)
        from unittest.mock import patch
        entered,release=asyncio.Event(),asyncio.Event()
        sleep=asyncio.sleep
        async def collect(delay):
            if delay==batch.COLLECTION_SECONDS:entered.set();await release.wait()
            else:await sleep(delay)
        with patch('core.game_candidate_queries.asyncio.sleep',side_effect=collect):
            first=asyncio.create_task(batch.igdb_queries(['query 1','exact 1']))
            await entered.wait()
            rest=[asyncio.create_task(batch.igdb_queries([f'query {i}',f'exact {i}'])) for i in range(2,5)]
            await sleep(0);release.set()
            result=await asyncio.gather(first,*rest)
        source.igdb_queries.assert_awaited_once()
        self.assertEqual(len(source.igdb_queries.await_args.args[0]),8)
        self.assertEqual(result[0],[['query 1'],['exact 1']])

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
        result=await asyncio.gather(*(batch.igdb_queries([f'query {i}',f'exact {i}']) for i in range(7)))
        self.assertEqual(sizes,[10,4])
        self.assertEqual(result,[[[f'query {i}'],[f'exact {i}']] for i in range(7)])

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

    async def test_queue_bound_rejects_excess_work_and_close_cancels_all_waiters(self):
        entered,release=asyncio.Event(),asyncio.Event()
        async def fetch(queries):entered.set();await release.wait();return [[query] for query in queries]
        batch=GameCandidateQueries(SimpleNamespace(cache=object(),igdb_queries=fetch))
        calls=[asyncio.create_task(batch.igdb('games',str(i))) for i in range(batch.MAX_PENDING)]
        await entered.wait()
        with self.assertRaises(ProviderError) as error:await batch.igdb('games','overflow')
        self.assertEqual(error.exception.code,'rate_limited')
        await batch.close()
        values=await asyncio.gather(*calls,return_exceptions=True)
        self.assertTrue(all(isinstance(value,asyncio.CancelledError) for value in values))
        self.assertIsNone(batch.flight);self.assertEqual(batch.pending,[])
        with self.assertRaises(ProviderError):await batch.igdb('games','closed')

    async def test_close_before_dispatch_starts_settles_the_queued_caller(self):
        source=SimpleNamespace(cache=object(),igdb_queries=AsyncMock())
        batch=GameCandidateQueries(source)
        task=asyncio.create_task(batch.igdb('games','queued'))
        await asyncio.sleep(0)
        await batch.close()
        with self.assertRaises(asyncio.CancelledError):await asyncio.wait_for(task,1)
        source.igdb_queries.assert_not_awaited()

    async def test_one_failed_dispatch_does_not_discard_the_next_queued_batch(self):
        source=SimpleNamespace(cache=object(),igdb_queries=AsyncMock(side_effect=[
            ProviderError('unavailable'),[['recovered']]]))
        batch=GameCandidateQueries(source)
        first=asyncio.create_task(batch.igdb_queries(['query']*10))
        second=asyncio.create_task(batch.igdb('games','next'))
        values=await asyncio.gather(first,second,return_exceptions=True)
        self.assertIsInstance(values[0],ProviderError)
        self.assertEqual(values[1],['recovered'])
