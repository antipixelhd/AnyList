"""Page-scoped IGDB query batching for bounded concurrent title acquisition."""
import asyncio
from core.catalogue_providers import ProviderError


class GameCandidateQueries:
    # Durable cache admission spans several awaits. One event-loop yield lets
    # the first miss monopolize the serialized provider lane before siblings
    # arrive. Briefly collect those siblings without waiting for a slow cache.
    COLLECTION_SECONDS=.010
    def __init__(self, sources):
        self.sources=sources
        self.cache=sources.cache
        self.pending=[]
        self.flight=None

    async def igdb(self, endpoint, query):
        if endpoint!='games':return await self.sources.igdb(endpoint,query)
        return (await self.igdb_queries([query]))[0]

    async def igdb_queries(self, queries):
        if not isinstance(queries,list) or not 1<=len(queries)<=10:raise ProviderError('invalid_request')
        future=asyncio.get_running_loop().create_future()
        self.pending.append((queries,future))
        if self.flight is None:self.flight=asyncio.create_task(self.flush())
        return await future

    async def flush(self):
        # Let sibling title acquisitions reach the same provider boundary.
        await asyncio.sleep(self.COLLECTION_SECONDS)
        try:
            while self.pending:
                batch=[];size=0
                while self.pending and size+len(self.pending[0][0])<=10:
                    item=self.pending.pop(0);batch.append(item);size+=len(item[0])
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
        finally:self.flight=None
