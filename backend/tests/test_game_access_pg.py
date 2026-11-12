"""Approval, revocation and non-public Games response paths in disposable PG."""
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from sqlalchemy import select

import test_games_pg as fixture
from core import contributor_search
from core.share_preview import card_data
from models import User
from models.catalogue import CatalogueCredit, CatalogueEntity, CatalogueIdentity
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

    async def test_imdb_read_paths_require_approval_and_binding_requires_an_admin_review(self):
        async with self.Session() as db:
            db.add(CatalogueIdentity(entity_id=self.base,namespace='igdb.game',external_id='10',source='igdb'))
            await db.commit()
        base=f'/tracking/games/works/{self.base}'
        self.viewer=self.other
        with patch('core.game_imdb_source.IMDbGameSource.title',AsyncMock()) as source:
            self.assertEqual((await self.client.get('/catalogue/resolve?namespace=igdb.game&external_id=10')).status_code,403)
            self.assertEqual((await self.client.get(f'/catalogue/characters?work_id={self.base}')).status_code,403)
            with patch('core.catalogue.refresh_prices',AsyncMock()) as prices:
                self.assertEqual((await self.client.post(f'/catalogue/entities/{self.base}/steam-prices/10')).status_code,403)
                prices.assert_not_awaited()
            for path in ['/credits','/imdb','/imdb/candidates?q=Elden','/imdb/candidates/tt123']:
                self.assertEqual((await self.client.get(base+path)).status_code,403,path)
            self.assertEqual((await self.client.get('/tracking/games/people/nm1/imdb')).status_code,403)
            self.assertEqual((await self.client.get('/tracking/games/companies/1/works/status')).status_code,403)
            for suffix in ['/titles','/titles/tt123']:
                self.assertEqual((await self.client.get('/tracking/games/people/nm1/imdb'+suffix)).status_code,403)
            self.other.games_access=True
            self.assertEqual((await self.client.get(base+'/credits')).status_code,200)
            self.assertEqual((await self.client.get(base+'/imdb')).status_code,200)
            self.assertEqual((await self.client.get(base+'/imdb/candidates?q=Elden')).status_code,403)
            for suffix in ['/titles','/titles/tt123']:
                self.assertEqual((await self.client.get('/tracking/games/people/nm1/imdb'+suffix)).status_code,403)
            self.assertEqual((await self.client.post(base+'/imdb',json={'imdb_id':'tt123',
                'reason':'Verified identity','confirmed_game_and_edition':True})).status_code,403)
            source.assert_not_awaited()
            self.viewer=self.user
            self.assertEqual((await self.client.post(base+'/imdb',json={'imdb_id':'tt123','reason':'Verified identity'})).status_code,422)
            source.return_value={'id':'tt123','titleText':{'text':'Game'},'titleType':{'id':'videoGame'}}
            response=await self.client.post(base+'/imdb',json={'imdb_id':'tt123','reason':'Reviewed game and edition',
                'confirmed_game_and_edition':True})
            self.assertEqual(response.status_code,200,response.text)
            self.assertTrue(response.json()['binding']['reviewed'])
        self.viewer=self.other
        body=(await self.client.get(base+'/imdb')).json()
        self.assertNotIn('evidence',body['binding'])

    async def test_admin_review_only_selects_cached_video_game_members(self):
        from datetime import timedelta
        from core.game_imdb_enrichment import now
        from core.game_imdb_review import write_index
        from models.catalogue import MetadataSnapshot
        native='nm123';generation='a'*32
        async with self.Session() as db:
            db.add(CatalogueIdentity(entity_id=self.person_id,namespace='imdb.person',external_id=native,source='reviewed'))
            db.add_all([
                MetadataSnapshot(provider='imdb',kind='game_person_job',external_id=native,entity_id=self.person_id,
                    coverage={'complete':True,'state':'complete'},expires_at=now()+timedelta(days=30)),
                MetadataSnapshot(provider='imdb',kind='game_person_credits',external_id=native,entity_id=self.person_id,
                    payload={'generation':generation,'index_generation':generation},coverage={'source_complete':True})])
            await write_index(db,native,generation,[{'title':{'id':'tt123','titleType':{'id':'videoGame'},
                'titleText':{'text':'Unmapped game'}},'category':{'id':'actor'}}],entity_id=self.person_id)
            await db.commit()
        path=f'/tracking/games/people/{native}/imdb/titles'
        response=await self.client.get(path)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['total'],1)
        self.assertEqual((await self.client.get(path+'/tt123')).status_code,200)
        self.assertEqual((await self.client.get(path+'/tt999')).status_code,404)
        self.assertEqual((await self.client.get(path,params={'generation':'b'*32})).status_code,409)
