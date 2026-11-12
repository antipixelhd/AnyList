"""Bounded IGDB dispatcher shared by a discovery service's title acquisitions."""
import asyncio
import re
from contextvars import ContextVar, copy_context
from core.catalogue_providers import ProviderError

collection_size=ContextVar('game_candidate_collection_size',default=4)


class GameCandidateQueries:
    # Durable cache admission spans several awaits. One event-loop yield lets
    # the first miss monopolize the serialized provider lane before siblings
    # arrive. Large lone continuations get 75ms to fill one transport call;
    # initial and competing turns retain their short collection window.
    # without waiting indefinitely for a slow cache.
    COLLECTION_SECONDS=.050
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
        self.pending.append((queries,future,copy_context()))
        if self.flight is None:
            self.flight=asyncio.create_task(self.flush())
            self.flight.add_done_callback(lambda task:task.exception() if not task.cancelled() else None)
        return await future

    async def flush(self):
        # Let sibling title acquisitions reach the same provider boundary.
        try:
            await asyncio.sleep(.075 if collection_size.get()>4 else self.COLLECTION_SECONDS)
            while self.pending:
                batch=[];size=0
                from core.game_work import current, Work
                def group(context):
                    work=context.get(current,current.get())
                    return work.priority,work.owner,tuple(scope for scope in work.scopes if scope.startswith('job:'))
                admission=group(self.pending[0][2])
                while self.pending and size+len(self.pending[0][0])<=10 and group(self.pending[0][2])==admission:
                    item=self.pending.pop(0);batch.append(item);size+=len(item[0])
                self.active=batch
                queries=list(dict.fromkeys(query for values,future,_ in batch if not future.cancelled() for query in values))
                filters=[query for query in queries if not re.search(r'(?:^|;)\s*search\s+',query)]
                groups=([filters] if filters else [])+[[query] for query in queries if query not in filters]
                async def acquire(values):
                    try:
                        rows=await self.sources.igdb_queries(values)
                        if len(rows)!=len(values):raise ProviderError('bad_response')
                        return values,rows,None
                    except Exception as error:return values,None,error
                tasks=[]
                for values in groups:
                    # Each physical request carries only its subscribers. A
                    # later sibling must not borrow an earlier title's turn.
                    subscribers=[item for item in batch if any(query in values for query in item[0])]
                    context=subscribers[0][2].copy()
                    scopes=tuple(dict.fromkeys(scope for _,_,ctx in subscribers
                        for scope in ctx.get(current,current.get()).scopes))
                    context.run(current.set,Work(admission[0],admission[1],scopes))
                    tasks.append(asyncio.create_task(acquire(values),context=context))
                known={};errors={}
                try:
                    for completed in asyncio.as_completed(tasks):
                        values,rows,error=await completed
                        if error:errors.update((query,error) for query in values)
                        else:known.update(zip(values,rows))
                        for requested,future,_ in batch:
                            if future.done():continue
                            error=next((errors[query] for query in requested if query in errors),None)
                            if error:future.set_exception(error)
                            elif all(query in known for query in requested):future.set_result([known[query] for query in requested])
                finally:
                    for task in tasks:
                        if not task.done():task.cancel()
                    await asyncio.gather(*tasks,return_exceptions=True)
                self.active=[]
        finally:
            for _,future,_ in self.active+self.pending:
                if not future.done():future.cancel()
            self.active=[];self.pending=[];self.flight=None

    async def close(self):
        self.closed=True
        task=self.flight
        if task:
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)
        # A task cancelled before its first turn never enters flush's finally.
        for _,future,_ in self.active+self.pending:
            if not future.done():future.cancel()
        self.active=[];self.pending=[];self.flight=None
