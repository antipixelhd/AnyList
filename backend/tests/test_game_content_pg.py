"""Viewer-specific erotic discovery policy on shared public game evidence."""
import unittest
from datetime import date, timedelta
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

    async def test_mobile_policy_migration_roundtrip_keeps_search_functions_valid(self):
        import importlib
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        from sqlalchemy import text
        migration=importlib.import_module('migrations.versions.mt077_mobile_game_policy')
        def apply(connection):
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                definition=connection.execute(text("SELECT pg_get_functiondef('refresh_game_contributor_search(integer[])'::regprocedure)")).scalar_one()
                self.assertIn("w.attributes->>'mobile_only'",definition)
                migration.downgrade()
                migration.upgrade()
        async with self.engine.begin() as connection:await connection.run_sync(apply)

    async def asyncSetUp(self):
        await fixture.GamesDatabaseTests.asyncSetUp(self)
        self.rows=[{'igdb_id':i,'title':name,'type':'game','game_class':'game','is_erotic':erotic,
            'genres':[],'platform_ids':[],'release_date':'2024-01-01','rating_count':5}
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

    async def test_relevance_preferences_show_all_search_and_cursor_scope(self):
        today = date.today()
        shared = {'type':'game','game_class':'game','genres':[],'platform_ids':[]}
        self.rows[:] = [
            dict(shared, igdb_id=1,title='Unrated',release_date='2024-01-01'),
            dict(shared, igdb_id=2,title='Followed',release_date='2024-01-01',hypes=2),
            dict(shared, igdb_id=3,title='Rated',release_date='2024-01-01',rating_count=5,total_rating=79),
            dict(shared, igdb_id=4,title='Tagged',release_date='2024-01-01',hypes=5,ai_keywords=[38148]),
            dict(shared, igdb_id=5,title='Upcoming rated',release_date=(today+timedelta(days=1)).isoformat(),rating_count=10,hypes=2),
            dict(shared, igdb_id=6,title='Upcoming followed',release_date=(today+timedelta(days=2)).isoformat(),hypes=5),
        ]
        def ids(page): return [row['igdb_id'] for row in page['results']]
        first = await self.browse_ids()
        self.assertEqual(ids(first), [2])
        self.assertTrue(first['cursor'])
        self.assertEqual(ids(await self.browse_ids(limit=24,category='newest',timeline='true',status='upcoming')), [6])
        self.assertEqual(ids(await self.browse_ids(limit=24,unfiltered='true')), [1,2,3,4,5,6])
        changed = await self.client.patch('/tracking/preferences',json={
            'game_min_follows': 3, 'hide_ai_artwork': True, 'game_released_min_score': 70,
        })
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertEqual(changed.json()['game_min_follows'], 3)
        self.assertEqual(changed.json()['game_min_ratings'], 5)
        self.assertTrue(changed.json()['hide_ai_artwork'])
        stale = await self.client.get('/tracking/games/browse',params={'limit':1,'cursor':first['cursor']})
        self.assertEqual(stale.status_code, 422)
        self.assertEqual(ids(await self.browse_ids(limit=24,category='newest',timeline='true')), [3])
        self.assertEqual(ids(await self.browse_ids(limit=24,unfiltered='true')), [1,2,3,5,6])
        search = await self.browse_ids(limit=24,q='title')
        self.assertEqual(ids(search), [1,2,3,4,5,6])
        self.assertNotIn('ai_keywords',search['results'][3])
        self.viewer = self.other
        self.assertEqual(ids(await self.browse_ids(limit=24,category='newest',timeline='true')), [2,3,4])
