"""Wishlist-owned planning, canonical admission and purchase-safe reconciliation."""
import unittest

from sqlalchemy import select
import test_games_pg as games_fixture
import test_steam_pg as fixture
from core import steam_cache, steam_sync, game_tracking
from core.steam import SteamError
from models import SteamConnection
from models.catalogue import CatalogueIdentity
from models.games import GameEntry
from schemas_games import GamePatch


@unittest.skipUnless(games_fixture.URL, 'Requires disposable Games PostgreSQL')
class WishlistDatabaseTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.SteamDatabaseTests.asyncSetUp
    cleanup = games_fixture.GamesDatabaseTests.cleanup
    connect = fixture.SteamDatabaseTests.connect
    sync = fixture.SteamDatabaseTests.sync

    async def import_wishlist(self, *, enabled=True, selected=None, rows=None):
        self.source.wishlist.return_value = rows if rows is not None else [fixture.game('40',0)]
        await self.connect()
        async with self.Session() as db:
            state = await db.get(SteamConnection,self.id)
            state.sync_wishlist = enabled
            await db.commit()
        body = {'generation':1, 'selected_apps':[]}
        if selected is not None:
            body['selected_wishlist'] = selected
        response = await self.client.post(f'/connections/steam/{self.id}/import',json=body)
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    async def entry(self, app='40'):
        async with self.Session() as db:
            return await db.scalar(select(GameEntry).join(CatalogueIdentity,CatalogueIdentity.entity_id==GameEntry.work_id).where(
                GameEntry.user_id==self.user.id,CatalogueIdentity.namespace=='steam.app',CatalogueIdentity.external_id==app))

    async def test_default_selection_disabled_opt_in_and_saved_draft(self):
        self.source.wishlist.return_value = [fixture.game('40',0)]
        await self.connect()
        review = (await self.client.get(f'/connections/steam/{self.id}')).json()
        self.assertFalse(review['sync_wishlist'])
        self.assertEqual([g['app_id'] for g in review['wishlist']],['40'])
        body = {'generation':1,'wishlist_overrides':{'40':True}}
        self.assertEqual((await self.client.put(f'/connections/steam/{self.id}/draft',json=body)).status_code,200)
        self.assertEqual((await self.client.get(f'/connections/steam/{self.id}')).json()['draft']['wishlist_overrides'],{'40':True})
        response = await self.client.post(f'/connections/steam/{self.id}/import',json={**body,'selected_apps':[]})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual((await self.entry()).status,'planning')
        self.source.wishlist.reset_mock()
        self.source.wishlist.return_value=[]
        await self.sync()
        self.source.wishlist.assert_not_awaited()
        self.assertIsNotNone(await self.entry())

    async def test_disabled_default_imports_no_wishlist_and_rejects_forged_ids(self):
        self.source.wishlist.return_value=[fixture.game('40',0)]
        await self.connect()
        path=f'/connections/steam/{self.id}/import'
        response=await self.client.post(path,json={'generation':1,'selected_apps':[],'selected_wishlist':['999999']})
        self.assertEqual(response.status_code,422)
        response=await self.client.post(path,json={'generation':1,'selected_apps':[]})
        self.assertEqual(response.status_code,200,response.text)
        self.assertIsNone(await self.entry())

    async def test_add_remove_only_managed_unedited_planning_and_retry_mapping(self):
        result = await self.import_wishlist(rows=[fixture.game('40',0),fixture.game('60',0)])
        self.assertEqual(result['wishlist_added'],1)
        self.assertIsNone(await self.entry('60'))
        async with self.Session() as db:
            state=await db.get(SteamConnection,self.id)
            self.assertEqual(state.wishlist_state['pending'],['60'])
            self.assertIsNone(await db.scalar(select(CatalogueIdentity.id).where(
                CatalogueIdentity.namespace=='steam.app',CatalogueIdentity.external_id=='60')))
            row=await self.entry()
            await game_tracking.save_entry(db,self.user.id,row.work_id,GamePatch(revision=row.revision,notes='manual decision'))
        await steam_cache.save_public(self.Session,[{'app_id':'60','igdb_classification':{
            'version':1,'state':'bound','igdb_id':'900060','game_class':'game','checked_at':steam_sync.now().isoformat()}}])
        self.assertEqual((await self.sync())['wishlist_added'],1)
        self.assertEqual((await self.sync())['wishlist_added'],0)
        self.source.wishlist.return_value=[]
        self.assertEqual((await self.sync())['wishlist_removed'],1)
        self.assertIsNone(await self.entry('60'))
        self.assertEqual((await self.entry()).notes,'manual decision')
        self.assertIsNone((await self.entry()).total_seconds)

    async def test_purchase_preserves_status_and_joins_normal_auto_playing(self):
        await self.import_wishlist()
        before=await self.entry()
        response=await self.client.patch(f'/connections/steam/{self.id}',json={
            'sync_wishlist':True,'auto_add_games':True,'sync_playtime':True,'sync_achievements':False})
        self.assertEqual(response.status_code,200,response.text)
        self.source.wishlist.return_value=[]
        self.source.library.return_value.append(fixture.game('40',0))
        self.assertEqual((await self.sync())['wishlist_removed'],0)
        row=await self.entry();self.assertEqual((row.id,row.status,row.start_date),(before.id,'planning',None))
        self.source.library.return_value[-1]=fixture.game('40',31)
        await self.sync()
        self.assertEqual((await self.entry()).status,'playing')
        self.assertEqual((await self.entry()).total_seconds,1860)

    async def test_new_wishlist_additions_use_admission_and_preserve_manual_favorites(self):
        await self.import_wishlist(rows=[])
        await steam_cache.save_public(self.Session,[{'app_id':'50','steam_app_type':'software'}])
        self.source.wishlist.return_value=[fixture.game('40',0),fixture.game('50',0),fixture.game('60',0)]
        self.assertEqual((await self.sync())['wishlist_added'],1)
        row=await self.entry()
        self.assertEqual(row.status,'planning')
        self.assertIsNone(await self.entry('50'))
        self.assertIsNone(await self.entry('60'))
        async with self.Session() as db:
            self.assertIsNone(await db.scalar(select(CatalogueIdentity.id).where(
                CatalogueIdentity.namespace=='steam.app',CatalogueIdentity.external_id=='60')))
            await game_tracking.set_favorite(db,self.user.id,row.work_id,True)
            await db.commit()
        self.source.wishlist.return_value=[]
        self.assertEqual((await self.sync())['wishlist_removed'],0)
        self.assertEqual((await self.entry()).id,row.id)

    async def test_private_failed_snapshot_and_user_deleted_entry_are_not_empty_or_new(self):
        await self.import_wishlist()
        row=await self.entry()
        self.source.wishlist.side_effect=SteamError('private wishlist')
        await self.sync()
        self.assertEqual((await self.entry()).id,row.id)
        async with self.Session() as db:
            await game_tracking.remove_entry(db,self.user.id,row.work_id,row.revision)
        self.source.wishlist.side_effect=None
        await self.sync()
        self.assertIsNone(await self.entry())

    async def test_existing_completed_owned_and_rejected_games_never_become_planning(self):
        async with self.Session() as db:
            await game_tracking.save_entry(db,self.user.id,self.base,GamePatch(status='completed',notes='keep'))
        await steam_cache.save_public(self.Session,[{'app_id':'50','steam_app_type':'software'},
            {'app_id':'60','igdb_classification':{'version':1,'state':'bound','igdb_id':'900060',
                'game_class':'dlc','checked_at':steam_sync.now().isoformat()}}])
        result=await self.import_wishlist(rows=[fixture.game('1245620',0),fixture.game('50',0),fixture.game('60',0)])
        self.assertEqual(result['wishlist_added'],0)
        async with self.Session() as db:
            row=await db.scalar(select(GameEntry).where(GameEntry.user_id==self.user.id,GameEntry.work_id==self.base))
            self.assertEqual((row.status,row.notes),('completed','keep'))
        self.assertIsNone(await self.entry('50'));self.assertIsNone(await self.entry('60'))

    async def test_purchase_via_other_app_same_canonical_game_is_not_a_removal(self):
        await self.import_wishlist()
        before=await self.entry()
        async with self.Session() as db:
            db.add(CatalogueIdentity(entity_id=before.work_id,namespace='steam.app',external_id='70',source='igdb'))
            await db.commit()
        self.source.library.return_value.append(fixture.game('70',0))
        self.source.wishlist.return_value=[]
        self.assertEqual((await self.sync())['wishlist_removed'],0)
        self.assertEqual((await self.entry()).id,before.id)
