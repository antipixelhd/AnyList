"""Bounded IGDB dispatcher shared by a discovery service's title acquisitions."""
import asyncio
from core.catalogue_providers import ProviderError


class GameCandidateQueries:
    # Durable cache admission spans several awaits. One event-loop yield lets
    # the first miss monopolize the serialized provider lane before siblings
    # arrive. Collect for at most 25ms, including competing cache subscribers,
    # without waiting indefinitely for a slow cache.
    COLLECTION_SECONDS=.025
    MAX_PENDING=32
    def __init__(self, sources):
        self.sources=sources
        self.cache=sources.cache
        self.pending=[]
        self.flight=None
        self.active=[]
        self.closed=False

    async def igdb(self, endpoint, query):
        if endpoint!='games':return await self.sources.igdb(endpoint,query)
        return (await self.igdb_queries([query]))[0]

    async def igdb_queries(self, queries):
        if not isinstance(queries,list) or not 1<=len(queries)<=10:raise ProviderError('invalid_request')
        if self.closed:raise ProviderError('unavailable')
        if len(self.pending)+len(self.active)>=self.MAX_PENDING:raise ProviderError('rate_limited',2)
        future=asyncio.get_running_loop().create_future()
        self.pending.append((queries,future))
        if self.flight is None:
            self.flight=asyncio.create_task(self.flush())
            self.flight.add_done_callback(lambda task:task.exception() if not task.cancelled() else None)
        return await future

    async def flush(self):
        # Let sibling title acquisitions reach the same provider boundary.
        try:
            await asyncio.sleep(self.COLLECTION_SECONDS)
            while self.pending:
                batch=[];size=0
                while self.pending and size+len(self.pending[0][0])<=10:
                    item=self.pending.pop(0);batch.append(item);size+=len(item[0])
                self.active=batch
                try:
                    rows=await self.sources.igdb_queries([query for queries,_ in batch for query in queries])
                except Exception as error:
                    for _,future in batch:
                        if not future.done():future.set_exception(error)
                else:
                    offset=0
                    for queries,future in batch:
                        if not future.done():future.set_result(rows[offset:offset+len(queries)])
                        offset+=len(queries)
                self.active=[]
        finally:
            for _,future in self.active+self.pending:
                if not future.done():future.cancel()
            self.active=[];self.pending=[];self.flight=None

    async def close(self):
        self.closed=True
        task=self.flight
        if task:
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)
        # A task cancelled before its first turn never enters flush's finally.
        for _,future in self.active+self.pending:
            if not future.done():future.cancel()
        self.active=[];self.pending=[];self.flight=None
