"""Provider observation age must bound stale ranking reuse, not just fetch age."""
import asyncio
import time
import unittest
from datetime import timedelta
from unittest.mock import AsyncMock

import test_games_pg as fixture
from sqlalchemy import select

from core.catalogue_providers import ProviderError
from core.game_browse import GameBrowse
from core.game_browse_sources import GameBrowseSources
from core.game_source_cache import GameSourceCache, _flights, utcnow
from models.catalogue import MetadataSnapshot


@unittest.skipUnless(fixture.URL,'Requires explicit disposable Games PostgreSQL')
class RankingCacheTests(unittest.IsolatedAsyncioTestCase):
    cleanup=fixture.GamesDatabaseTests.cleanup

    async def asyncSetUp(self):
        await fixture.GamesDatabaseTests.asyncSetUp(self)
        self.cache=GameSourceCache(self.Session)
        self.source=GameBrowseSources(self.Session)
        self.source.reference=AsyncMock(side_effect=lambda endpoint,fields:{'data':{'rows':
            [{'id':121,'name':'IGDB'}] if endpoint=='external_game_sources' else
            [{'id':1,'name':'Visits','external_popularity_source':121}]}})
        self.entered,self.release=asyncio.Event(),asyncio.Event()
        async def ranking(*args):
            self.entered.set();await self.release.wait()
            return [{'game_id':1,'value':5,'calculated_at':int(time.time())}]
        self.source.igdb=AsyncMock(side_effect=ranking)
        self.source.games=AsyncMock(return_value=[{'id':1,'name':'Game','game_type':{'id':0}}])

    async def asyncTearDown(self):
        self.release.set()
        tasks=[task for key,task in list(_flights.items()) if key[0]==id(self.Session)]
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)

    async def seed(self,age,*,expired=False):
        self.observed=int(time.time())-age
        async with self.Session() as db:
            db.add(MetadataSnapshot(provider='igdb',kind='game_browse_ranks',external_id='v1:1:0',
                payload={'rows':[{'game_id':1,'value':1,'calculated_at':self.observed}],
                    'has_more':False,'offset':0,'source':'IGDB Visits','source_at':self.observed},
                fetched_at=utcnow()-timedelta(hours=1),
                expires_at=utcnow()+timedelta(seconds=-1 if expired else 3600)))
            await db.commit()

    async def test_most_played_preview_is_shared_durably_without_resolving_the_remainder(self):
        source=GameBrowseSources(self.Session)
        source.steam=AsyncMock(return_value={'data':{'rows':[{'appid':i,'rank':i} for i in range(1,101)],
            'source':'Steam concurrent players','source_at':int(time.time())},'stale':False})
        source.steam_games=AsyncMock(return_value=[{'uid':str(i),'game':{'id':i,'name':f'Game {i}',
            'game_type':{'id':0}}} for i in range(1,7)])
        query={'category':'most_played','genres':[],'platforms':[],'start':None,'end':None,
            'q':'','min_metacritic':0,'timeline':False,'hide_erotic_games':True}
        pages=await asyncio.gather(*(GameBrowse(source).page(query,limit=6) for _ in range(3)))
        self.assertTrue(all([r['igdb_id'] for r in page['rows']]==list(range(1,7)) for page in pages))
        source.steam_games.assert_awaited_once_with(list(range(1,7)))
        other=GameBrowseSources(self.Session)
        other.steam_games=AsyncMock(side_effect=AssertionError('Warm page must not reach providers'))
        warm=await GameBrowse(other).page(query,limit=6)
        self.assertEqual(warm['rows'],pages[0]['rows'])
        other.steam_games.assert_not_awaited()

    async def test_old_source_waits_for_shared_refresh_even_with_a_recent_fetch(self):
        await self.seed(4*86400)
        query={'category':'trending','q':'','genres':[],'platforms':[],
            'start':None,'end':None,'min_metacritic':0,'timeline':False}
        first=asyncio.create_task(GameBrowse(self.source).page(query,limit=6))
        await asyncio.wait_for(self.entered.wait(),5)
        self.assertFalse(first.done())
        second=asyncio.create_task(GameBrowse(self.source).page(query,limit=6))
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):await first
        self.release.set()
        page=await asyncio.wait_for(second,5)
        self.assertEqual([row['igdb_id'] for row in page['rows']],[1])
        self.assertFalse(page['stale']);self.source.igdb.assert_awaited_once()
        async with self.Session() as db:
            rows=list(await db.scalars(select(MetadataSnapshot).where(MetadataSnapshot.kind=='game_browse_pages')))
            self.assertTrue(rows);self.assertTrue(all(not row.error_code for row in rows))

    async def test_usable_stale_source_still_paints_while_refresh_runs(self):
        await self.seed(3600,expired=True)
        result=await self.source.primitive('Visits','IGDB')
        self.assertTrue(result['stale']);self.assertEqual(result['data']['source_at'],self.observed)
        await asyncio.wait_for(self.entered.wait(),5)
        self.release.set()

    async def test_failed_refresh_does_not_return_outdated_evidence_or_retry_during_cooldown(self):
        await self.seed(4*86400)
        self.source.igdb.side_effect=ProviderError('timeout')
        with self.assertRaisesRegex(ProviderError,'timeout'):await self.source.primitive('Visits','IGDB')
        with self.assertRaisesRegex(ProviderError,'retry_pending'):await self.source.primitive('Visits','IGDB')
        self.source.igdb.assert_awaited_once()
        async with self.Session() as db:
            row=await db.scalar(self.cache.query('igdb','game_browse_ranks','1:0'))
            self.assertEqual(row.payload['source_at'],self.observed)
            self.assertEqual(row.error_code,'timeout');self.assertGreater(row.next_attempt_at,utcnow())
