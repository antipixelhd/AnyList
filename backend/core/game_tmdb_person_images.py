"""Small TMDB portrait projections; never merge people from a find response."""
import asyncio
import re
import weakref

from core import tmdb
from core.catalogue_providers import ProviderError
from core.game_person_images import GamePersonImages

_slots=weakref.WeakKeyDictionary()
TIMEOUT_SECONDS=5


def validate(native):
    if not isinstance(native,str) or not re.fullmatch(r'(?:tmdb:[1-9][0-9]*|imdb:nm[0-9]+)',native):
        raise ProviderError('invalid_request')


class TMDBPersonSource:
    def __init__(self,api_key):self.api_key=api_key

    async def portraits(self,natives):
        loop=asyncio.get_running_loop()
        slots=_slots.setdefault(loop,asyncio.Semaphore(4))
        deadline=loop.time()+TIMEOUT_SECONDS
        async def portrait(native):
            validate(native)
            try:
                # The budget includes waiting for a slot. Optional portraits
                # must leave time for the IMDb fallback even during an outage.
                async with asyncio.timeout_at(deadline):
                    async with slots:
                        namespace,identifier=native.split(':')
                        if namespace=='tmdb':
                            row=await tmdb.get_person_profile(int(identifier),api_key=self.api_key)
                            if not isinstance(row,dict) or type(row.get('id')) is not int or row['id']!=int(identifier):
                                raise ProviderError('identity_conflict')
                        else:
                            raw=await tmdb.find_by_external_id(identifier,'imdb_id',api_key=self.api_key)
                            rows=raw.get('person_results') if isinstance(raw,dict) else None
                            if not isinstance(rows,list) or any(not isinstance(row,dict)
                                or type(row.get('id')) is not int or row['id']<=0 for row in rows):
                                raise ProviderError('bad_response')
                            if len(rows)!=1:return native,{'image':None,'outcome':'unresolved' if not rows else 'ambiguous'}
                            row=rows[0]
                        path=row.get('profile_path')
                        if path is not None and (not isinstance(path,str) or not re.fullmatch(r'/[A-Za-z0-9_.-]+',path)):
                            raise ProviderError('bad_response')
                        return native,{'image':'https://image.tmdb.org/t/p/w185'+path if path else None,
                            'outcome':'portrait' if path else 'missing'}
            except TimeoutError:return native,{'error':'timeout'}
            except ProviderError as error:return native,{'error':error.code}
            except Exception:return native,{'error':'unavailable'}
        return dict(await asyncio.gather(*(portrait(native) for native in natives)))


class GameTMDBPersonImages(GamePersonImages):
    provider='tmdb'
    validate=staticmethod(validate)

    def __init__(self,sessions,*,api_key=None,source=None):
        super().__init__(sessions,source=source or TMDBPersonSource(api_key))
