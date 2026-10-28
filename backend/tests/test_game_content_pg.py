"""Viewer-specific erotic discovery policy on shared public game evidence."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch

import test_games_pg as fixture
from sqlalchemy import select
from core.game_browse import GameBrowse
from core.game_source_cache import GameSourceCache
from models.users import UserSettings


@unittest.skipUnless(fixture.URL,'Requires explicit disposable Games PostgreSQL')
class GameContentDatabaseTests(unittest.IsolatedAsyncioTestCase):
    cleanup=fixture.GamesDatabaseTests.cleanup

    async def asyncSetUp(self):
        await fixture.GamesDatabaseTests.asyncSetUp(self)
        self.rows=[{'igdb_id':i,'title':name,'type':'game','game_class':'game','is_erotic':erotic,
            'genres':[],'platform_ids':[],'release_date':'2024-01-01'}
            for i,name,erotic in [(1,'Erotic game',True),(2,'Mature-rated game',False),(3,'Other game',False)]]
        source=SimpleNamespace(cache=GameSourceCache(self.Session),facets=AsyncMock(return_value={'genres':[],'platform_groups':[]}),
            attention=AsyncMock(return_value={'data':{'rows':self.rows,'has_more':False,'source':'IGDB Visits'},'stale':False}),
            catalogue_feed=AsyncMock(return_value={'data':{'rows':self.rows,'has_more':False,'source':'IGDB release dates'},'stale':False}))
        service=GameBrowse(source)
        self.source=source
        self.browse=patch('core.game_browse.GameBrowse',return_value=service);self.browse.start();self.addCleanup(self.browse.stop)
        candidates=[{'external_id':str(row['igdb_id']),'name':row['title'],'game_class':'game','is_erotic':row['is_erotic']} for row in self.rows]
        self.search=patch('core.game_discovery.candidates',AsyncMock(return_value=candidates));self.search.start();self.addCleanup(self.search.stop)

    async def browse_ids(self,**params):
        response=await self.client.get('/tracking/games/browse',params={'category':'trending','limit':1,**params})
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    async def search_ids(self):
        response=await self.client.get('/tracking/games/search',params={'q':'game'})
        self.assertEqual(response.status_code,200,response.text)
        return [row['igdb_id'] for row in response.json()['results']]

    async def test_default_toggle_persistence_and_viewer_isolation_across_shared_pages(self):
        initial=await self.client.get('/tracking/preferences')
        self.assertTrue(initial.json()['hide_erotic_games'])
        self.assertEqual([r['igdb_id'] for r in (await self.browse_ids())['results']],[2])
        self.assertEqual(await self.search_ids(),[2,3])
        changed=await self.client.patch('/tracking/preferences',json={'hide_erotic_games':False})
        self.assertEqual(changed.status_code,200,changed.text);self.assertFalse(changed.json()['hide_erotic_games'])
        shown=await self.browse_ids()
        self.assertEqual([r['igdb_id'] for r in shown['results']],[1])
        self.assertEqual(await self.search_ids(),[1,2,3])
        following=await self.browse_ids(cursor=shown['cursor'])
        self.assertEqual([r['igdb_id'] for r in following['results']],[2])
        self.viewer=self.other
        self.assertTrue((await self.client.get('/tracking/preferences')).json()['hide_erotic_games'])
        self.assertEqual([r['igdb_id'] for r in (await self.browse_ids(hide_erotic_games='false'))['results']],[2])
        self.assertEqual(await self.search_ids(),[2,3])
        self.viewer=self.user
        self.assertFalse((await self.client.get('/tracking/preferences')).json()['hide_erotic_games'])
        async with self.Session() as db:
            saved=await db.scalar(select(UserSettings).where(UserSettings.user_id==self.user.id))
            self.assertIs(saved.preferences['hide_erotic_games'],False)
        await self.client.patch('/tracking/preferences',json={'hide_erotic_games':True})
        self.assertEqual(await self.search_ids(),[2,3])
        self.assertEqual((await self.client.patch('/tracking/preferences',json={'hide_erotic_games':None})).status_code,422)

    async def test_timeline_filters_before_page_limit_and_keeps_existing_tracking_access(self):
        result=await self.browse_ids(category='newest',timeline='true')
        self.assertEqual([r['igdb_id'] for r in result['results']],[2])
        await self.client.patch('/tracking/preferences',json={'hide_erotic_games':False})
        result=await self.browse_ids(category='newest',timeline='true')
        self.assertEqual([r['igdb_id'] for r in result['results']],[1])
        self.assertEqual((await self.client.get(f'/tracking/games/works/{self.base}')).status_code,200)
