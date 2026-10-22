"""Approval, revocation and non-public Games response paths in disposable PG."""
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from sqlalchemy import select

import test_games_pg as fixture
from core import contributor_search
from core.share_preview import card_data
from models import User
from models.catalogue import CatalogueCredit, CatalogueEntity
from routers.admin import router as admin_router


@unittest.skipUnless(fixture.URL, 'Requires explicit disposable Games PostgreSQL')
class GameAccessTests(unittest.IsolatedAsyncioTestCase):
    cleanup = fixture.GamesDatabaseTests.cleanup
    async def asyncSetUp(self):
        await fixture.GamesDatabaseTests.asyncSetUp(self)
        self.client._transport.app.include_router(admin_router, prefix='/admin')
        self.user.is_admin = True
        self.other.games_access = False
        async with self.Session() as db:
            other = await db.get(User,self.other.id)
            other.games_access = False
            owner = await db.get(User, self.user.id)
            owner.is_admin = True
            person = CatalogueEntity(kind='person', name='Restricted performer')
            db.add(person)
            await db.flush()
            self.person_id = person.id
            db.add(CatalogueCredit(work_id=self.base, contributor_id=person.id,
                provider='imdb', source_key='fixture-credit', role='voice_actor'))
            await db.commit()

    async def test_default_denial_admin_grant_and_revocation_preserve_saved_data(self):
        self.assertFalse(self.other.games_access)
        self.viewer = self.other
        path = f'/catalogue/entities/{self.base}/details'
        self.assertEqual((await self.client.get(path)).status_code, 403)
        self.assertEqual((await self.client.patch(f'/admin/users/{self.other.id}/games-access', json={'approved': True})).status_code, 403)
        self.viewer = self.user
        response = await self.client.patch(f'/admin/users/{self.other.id}/games-access', json={'approved': True})
        self.assertEqual(response.status_code, 200, response.text)
        async with self.Session() as db:
            self.viewer = await db.get(User, self.other.id)
            self.assertEqual(self.viewer.games_approved_by, self.user.id)
            self.assertIsNotNone(self.viewer.games_approved_at)
        self.assertEqual((await self.client.patch(f'/tracking/games/entries/{self.base}', json={'status':'playing','notes':'retained'})).status_code, 200)
        self.assertEqual((await self.client.get(path)).status_code, 200)
        self.viewer = self.user
        self.assertEqual((await self.client.patch(f'/admin/users/{self.other.id}/games-access', json={'approved':False})).status_code, 200)
        async with self.Session() as db:
            self.viewer = await db.get(User, self.other.id)
        self.assertEqual((await self.client.get(path)).status_code, 403)
        self.assertEqual((await self.client.get('/tracking/games/users/other/list')).status_code, 403)
        async with self.Session() as db:
            from models.games import GameEntry
            entry = await db.scalar(select(GameEntry).where(GameEntry.user_id==self.other.id))
            self.assertEqual(entry.notes, 'retained')

    async def test_denied_routes_do_not_call_providers_or_expose_shared_game_evidence(self):
        self.viewer = self.other
        with patch('core.game_discovery.search', AsyncMock()) as provider:
            for url in ['/tracking/games/search?q=Elden','/tracking/games/browse',
                        f'/catalogue/entities/{self.base}', f'/catalogue/entities/{self.base}/credits',
                        f'/catalogue/entities/{self.person_id}', f'/catalogue/entities/{self.person_id}/sources',
                        '/catalogue/search?kind=game&q=Elden']:
                response = await self.client.get(url)
                self.assertEqual(response.status_code, 403, (url,response.text))
                self.assertNotIn('Restricted performer',response.text)
            provider.assert_not_awaited()
        with patch.object(contributor_search,'game_matches',AsyncMock()) as games:
            async with self.Session() as db:
                with self.assertRaises(HTTPException) as denied:
                    await contributor_search.search_contributors(db,self.other,'Restricted','actor','game')
                self.assertEqual(denied.exception.status_code,403)
            games.assert_not_awaited()

    async def test_anonymous_previews_never_reuse_a_game_card(self):
        async with self.Session() as db:
            for path in [f'/games/{self.base}', f'/games/{self.child}',
                         f'/actors/catalogue:{self.person_id}?media_type=game','/user/player/games']:
                with self.assertRaises(HTTPException) as denied:
                    await card_data(db,path)
                self.assertEqual(denied.exception.status_code,403)
        self.viewer = None
        self.assertEqual((await self.client.get(f'/catalogue/entities/{self.base}/details')).status_code,403)
        self.assertEqual((await self.client.get('/tracking/people/player/header')).status_code,200)

    async def test_environment_default_is_only_applied_to_new_accounts(self):
        from core.account_defaults import initialize_account_defaults
        with patch('core.config.settings.new_accounts_games_access',True):
            async with self.Session() as db:
                new = User(username='new-player',email='new@example.test',api_key='new-key')
                db.add(new)
                await db.flush()
                initialize_account_defaults(db,new)
                await db.commit()
                self.assertTrue(new.games_access)
                existing = await db.get(User,self.other.id)
                self.assertFalse(existing.games_access)
