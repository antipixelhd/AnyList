"""Shared compact game-credit evidence, independently of career acquisition."""
import asyncio
from datetime import timedelta

from core.catalogue_providers import ProviderHTTP
from core.game_imdb_source import IMDbGameSource, identifier, page
from core.game_source_cache import GameSourceCache
from core.game_work import Work, Priority, current

_batches = {}
FIELDS = '''id credits(first:4,filter:{titleType:"videoGame"}) {
 total pageInfo {hasNextPage endCursor} edges {node {
 title {id titleText {text} titleType {id} releaseYear {year}}
 category {id} ... on Cast {characters {name} attributes {text}}
 ... on Crew {jobs {text}} }} }'''


class PresenceBatch:
    def __init__(self, sessions):
        self.sessions = sessions
        self.pending = {}
        self.task = None

    async def get(self, native):
        future = asyncio.get_running_loop().create_future()
        self.pending[native] = (future, current.get())
        if self.task is None:
            self.task = asyncio.create_task(self.flush())
        return await asyncio.shield(future)

    async def flush(self):
        try:
            # Collect overlapping search terms/users into at most eight aliases.
            await asyncio.sleep(.05)
            while self.pending:
                batch = {key:self.pending.pop(key) for key in list(self.pending)[:8]}
                scopes = tuple(dict.fromkeys(scope for _, work in batch.values() for scope in work.scopes))
                token = current.set(Work(Priority.FINISH, 'shared-search-check', scopes))
                try:
                    query = 'query SearchGameCredits {' + ' '.join(
                        f'p{i}:name(id:"{native}"){{{FIELDS}}}' for i, native in enumerate(batch)) + '}'
                    raw = await IMDbGameSource(ProviderHTTP(session_factory=self.sessions)).query(query, {})
                    for i, (native, (future, _)) in enumerate(batch.items()):
                        from core.catalogue_providers import ProviderError
                        person = raw.get(f'p{i}')
                        if not isinstance(person, dict) or person.get('id') != native:
                            raise ProviderError('bad_response')
                        result = page(person.get('credits'), 4)
                        if (len(result['rows']) > result['total'] or
                            not result['has_next'] and len(result['rows']) != result['total']):
                            raise ProviderError('bad_response')
                        from core.game_imdb_person import game_rows
                        if game_rows(result['rows'])[1] or any(
                            row['title']['titleType']['id'] != 'videoGame' for row in result['rows']):
                            raise ProviderError('bad_response')
                        # Parsing validates pagination: malformed/failed aliases
                        # cannot establish a cached zero.
                        if not future.done():
                            future.set_result({'total':result['total'], 'rows':result['rows'],
                                               'complete':not result['has_next']})
                except BaseException as error:
                    for future, _ in batch.values():
                        if not future.done():
                            future.set_exception(error)
                    if isinstance(error, asyncio.CancelledError):
                        raise
                finally:
                    current.reset(token)
        finally:
            self.task = None
            if not self.pending:
                for key, value in list(_batches.items()):
                    if value is self:
                        _batches.pop(key, None)


async def presence(sessions, native):
    identifier(native, person=True)
    cache = GameSourceCache(sessions, max_records=512)
    async def load():
        key = (id(asyncio.get_running_loop()), id(sessions))
        batch = _batches.setdefault(key, PresenceBatch(sessions))
        return await batch.get(native)
    result = await cache.get('imdb', 'game_person_presence', native, load,
        ttl=timedelta(days=1), max_stale=timedelta(days=1))
    if result['stale'] or result.get('error'):
        from core.catalogue_providers import ProviderError
        raise ProviderError('retry_pending')
    return result['data']
