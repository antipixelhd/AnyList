"""Category customization through real routes and disposable PostgreSQL."""
import os
os.environ.setdefault('SECRET_KEY','categories-test-only')
os.environ.setdefault('DATABASE_URL','postgresql+asyncpg://test:test@localhost/test')
import asyncio
import unittest
from datetime import date

from core import game_tracking, list_categories
from models import Media
from models.base import MediaType
from models.tracking import TrackedEntry
from schemas_games import GamePatch
from schemas_list_categories import CategoryPatch
import test_games_pg as fixture


@unittest.skipUnless(fixture.URL, 'Requires explicit disposable games PostgreSQL')
class ListCategoriesTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.GamesDatabaseTests.asyncSetUp
    cleanup = fixture.GamesDatabaseTests.cleanup
    save = fixture.GamesDatabaseTests.save

    async def categories(self):
        response = await self.client.get('/tracking/list-categories/game')
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def test_screen_order_is_shared_by_profile_views_and_has_no_edit_capabilities(self):
        endpoint = '/tracking/list-categories/screen'
        order = ['completed', 'watching', 'paused', 'dropped', 'planning']
        async with self.Session() as db:
            movie = Media(title='First alphabetically', media_type=MediaType.movie)
            series = Media(title='Second alphabetically', media_type=MediaType.series)
            db.add_all([movie, series])
            await db.flush()
            db.add_all([TrackedEntry(user_id=self.user.id, media_id=movie.id, status='watching'),
                TrackedEntry(user_id=self.user.id, media_id=series.id, status='completed')])
            await db.commit()
        for changes in ({'labels': {'watching': 'Active'}}, {'hidden': ['paused']}, {'order': order[:-1]}):
            response = await self.client.patch(endpoint, json={'revision': 0, **changes})
            self.assertEqual(response.status_code, 422, response.text)
        saved = await self.client.patch(endpoint, json={'revision': 0, 'order': order})
        self.assertEqual(saved.status_code, 200, saved.text)
        presentation = saved.json()
        self.assertEqual([row['id'] for row in presentation['categories']], order)
        self.assertTrue(all(not (row['can_hide'] or row['can_rename']) for row in presentation['categories']))
        self.assertEqual((await self.categories())['revision'], 0)
        self.viewer = self.other
        base = '/tracking/profile/player'
        for offset, expected in enumerate(order[:2]):
            result = await self.client.get(f'{base}/all/list', params={'limit': 1, 'offset': offset})
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()['results'][0]['status'], expected)
            self.assertEqual(result.json()['category_presentation'], presentation)
        for scope in ('all', 'movie', 'series'):
            result = await self.client.get(f'{base}/{scope}/index')
            self.assertEqual(result.status_code, 200, result.text)
            self.assertEqual(result.json()['category_presentation'], presentation)

    async def test_defaults_and_protected_completed_and_invalid_names(self):
        defaults = await self.categories()
        legacy = list_categories.resolve(preferences={'list_categories': {'game': {'hidden': ['planning']}}})
        planning = next(row for row in legacy.categories if row.id == 'planning')
        self.assertFalse(planning.hidden)
        self.assertFalse(planning.can_hide)
        self.assertTrue(planning.can_rename)
        self.assertEqual([row['label'] for row in defaults['categories']],
            ['In Progress', 'Completed', 'Played', 'Paused', 'Dropped', 'Planning'])
        for changes in ({'labels': {'completed': 'Finished'}}, {'hidden': ['completed']}, {'hidden': ['planning']},
                        {'labels': {'played': ' completed '}}, {'labels': {'played': ' '}},
                        {'order': ['played']}, {'labels': {'played': 'x' * 33}}):
            result = await self.client.patch('/tracking/list-categories/game', json={'revision': 0, **changes})
            self.assertEqual(result.status_code, 422, result.text)
        reordered = ['played', 'planning', 'completed', 'playing', 'paused', 'dropped']
        result = await self.client.patch('/tracking/list-categories/game', json={
            'revision': 0, 'order': reordered, 'labels': {'played': '  My games  '}, 'hidden': ['paused']})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()['categories'][0]['label'], 'My games')
        stale = await self.client.patch('/tracking/list-categories/game', json={'revision': 0, 'hidden': []})
        self.assertEqual(stale.status_code, 409)
        preferences = (await self.client.get('/tracking/preferences')).json()
        self.assertEqual(preferences['list_categories']['game'], result.json())

    async def test_played_dates_and_completion_comparison_exclusion(self):
        row = await self.save(status='played', finish_date='2020-01-01', total_seconds=100)
        self.assertEqual(row['start_date'], date.today().isoformat())
        self.assertFalse(row['completion_comparison_eligible'])
        row = await self.save(revision=row['revision'], status='playing', start_date=None)
        self.assertIsNone(row['start_date'])
        row = await self.save(revision=row['revision'], status='played', start_date=None)
        self.assertIsNone(row['start_date'])

    async def test_populated_expansion_cannot_be_hidden_and_public_order_matches_pages(self):
        await self.save(status='completed')
        child = await self.client.patch(f'/tracking/games/children/{self.child}', json={'status': 'played'})
        self.assertEqual(child.status_code, 200, child.text)
        result = await self.client.patch('/tracking/list-categories/game', json={'revision': 0, 'hidden': ['played']})
        self.assertEqual(result.status_code, 409, result.text)
        order = ['played', 'completed', 'playing', 'paused', 'dropped', 'planning']
        result = await self.client.patch('/tracking/list-categories/game', json={
            'revision': 0, 'order': order, 'labels': {'played': 'Tried'}})
        self.assertEqual(result.status_code, 200, result.text)
        self.viewer = self.other
        first = (await self.client.get('/tracking/games/users/player/list?grouped=true&limit=1')).json()
        second = (await self.client.get('/tracking/games/users/player/list?grouped=true&limit=1&offset=1')).json()
        self.assertEqual(first['results'][0]['subject']['catalogue_id'], self.child)
        self.assertEqual(second['results'][0]['subject']['catalogue_id'], self.base)
        self.assertEqual(first['category_presentation']['categories'][0]['label'], 'Tried')
        index = (await self.client.get('/tracking/games/users/player/index')).json()
        self.assertEqual(index['category_presentation'], first['category_presentation'])

    async def test_hidden_status_is_restored_by_entry_write(self):
        hidden = await self.client.patch('/tracking/list-categories/game', json={'revision': 0, 'hidden': ['played']})
        self.assertEqual(hidden.status_code, 200)
        await self.save(status='played')
        presentation = await self.categories()
        self.assertEqual(presentation['revision'], 2)
        self.assertFalse(next(row for row in presentation['categories'] if row['id'] == 'played')['hidden'])

    async def test_concurrent_hiding_and_membership_write_never_hide_a_populated_category(self):
        async def hide():
            async with self.Session() as db:
                try:
                    await list_categories.save(db, self.user.id, CategoryPatch(revision=0, hidden=['played']))
                except Exception as error:
                    self.assertEqual(error.status_code, 409)

        async def add():
            async with self.Session() as db:
                await game_tracking.save_entry(db, self.user.id, self.base, GamePatch(status='played'))

        await asyncio.gather(hide(), add())
        self.assertFalse(next(row for row in (await self.categories())['categories'] if row['id'] == 'played')['hidden'])

    async def test_backup_restore_reveals_hidden_played_and_keeps_its_dates(self):
        from unittest.mock import AsyncMock
        from models.catalogue import CatalogueIdentity
        from core.game_backup import Backup, export_games, restore_games
        async with self.Session() as db:
            db.add(CatalogueIdentity(entity_id=self.base,namespace='igdb.game',external_id='119133',source='igdb'))
            await db.commit()
        await self.save(status='played',start_date='2000-01-01',finish_date='2000-02-01')
        async with self.Session() as db:
            archive=await export_games(db,self.user.id)
            await list_categories.save(db,self.other.id,CategoryPatch(revision=0,hidden=['played']))
            result=await restore_games(db,self.other.id,Backup.model_validate(archive),cancelled=AsyncMock())
            self.assertEqual(result['errors'],0)
            presentation=await list_categories.read(db,self.other.id)
            self.assertFalse(next(row for row in presentation.categories if row.id=='played').hidden)
            row=(await game_tracking.states(db,self.other.id,[self.base]))[0]
            self.assertEqual(row.start_date,date(2000,1,1))
            self.assertFalse(row.completion_comparison_eligible)
