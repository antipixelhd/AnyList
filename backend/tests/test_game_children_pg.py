"""Playable child discovery and personal state share canonical game records."""
import unittest

import test_games_pg as fixture
from models.catalogue import CatalogueEntity,CatalogueRelationship


@unittest.skipUnless(fixture.URL,'Requires disposable Games PostgreSQL')
class ChildrenTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixture.GamesDatabaseTests.asyncSetUp
    cleanup=fixture.GamesDatabaseTests.cleanup

    async def test_zero_local_rows_are_unknown_until_successful_source_completes(self):
        from datetime import datetime,timedelta,timezone
        from models.catalogue import MetadataSnapshot
        path=f'/tracking/games/works/{self.unknown}/children'
        result=(await self.client.get(path)).json()
        self.assertEqual((result['total'],result['coverage']),(0,'unknown'))
        async with self.Session() as db:
            row=MetadataSnapshot(provider='igdb',kind='game',external_id='999',entity_id=self.unknown,
                coverage={'children_complete':True},expires_at=datetime.now(timezone.utc).replace(tzinfo=None)+timedelta(days=1))
            db.add(row);await db.commit()
            self.assertEqual((await self.client.get(path)).json()['coverage'],'complete')
            row.error_code='unavailable';await db.commit()
        self.assertEqual((await self.client.get(path)).json()['coverage'],'unknown')

    async def test_complete_child_projection_withdraws_obsolete_edges_but_retains_private_state(self):
        from core.catalogue import ingest_document
        from core.catalogue_normalize import document
        from models.catalogue import CatalogueIdentity
        from models.games import GameChildState
        from sqlalchemy import select
        await self.client.patch(f'/tracking/games/entries/{self.base}',json={'status':'playing'})
        await self.client.patch(f'/tracking/games/children/{self.child}',json={'status':'completed','score':8})
        async with self.Session() as db:
            db.add(CatalogueIdentity(entity_id=self.base,namespace='igdb.game',external_id='119133',source='igdb'));await db.commit()
            doc=document({'kind':'game','name':'Elden fixture','identities':[{'namespace':'igdb.game','external_id':'119133'}],
                'attributes':{'game_class':'game'}})
            await ingest_document(db,doc,'igdb');await db.commit()
            self.assertEqual((await self.client.get(f'/tracking/games/works/{self.base}/children')).json()['total'],2)
            doc['coverage']={'children_complete':True}
            await ingest_document(db,doc,'igdb');await db.commit()
            self.assertEqual((await self.client.get(f'/tracking/games/works/{self.base}/children')).json()['total'],0)
            retained=await db.scalar(select(GameChildState).where(GameChildState.work_id==self.child))
            self.assertEqual(retained.score,8)
        rejected=await self.client.patch(f'/tracking/games/children/{self.child}',json={'status':'playing','revision':retained.revision})
        self.assertEqual(rejected.status_code,409)

    async def test_playable_types_unique_parent_and_sorted_paging(self):
        async with self.Session() as db:
            expansion=await db.get(CatalogueEntity,self.child)
            expansion.attributes={'game_class':'expansion','release_date':'2024-01-01'}
            dlc=await db.get(CatalogueEntity,self.dlc)
            dlc.attributes={'game_class':'dlc','release_date':'2020-01-01'}
            pack=await db.get(CatalogueEntity,self.pack)
            db.add(CatalogueRelationship(source_id=pack.id,target_id=self.base,relation='dlc',provider='igdb'))
            ambiguous=CatalogueEntity(kind='game',name='Ambiguous expansion',attributes={'game_class':'expansion'})
            unknown=CatalogueEntity(kind='game',name='Unknown add-on',attributes={})
            db.add_all([ambiguous,unknown]);await db.flush()
            db.add_all([CatalogueRelationship(source_id=ambiguous.id,target_id=parent,relation='expansion',provider='igdb') for parent in (self.base,self.unknown)])
            db.add(CatalogueRelationship(source_id=unknown.id,target_id=self.base,relation='dlc',provider='igdb'))
            mislabeled=CatalogueEntity(kind='game',name='Base game: Cosmetic Pack',attributes={'game_class':'dlc'})
            db.add(mislabeled);await db.flush()
            db.add(CatalogueRelationship(source_id=mislabeled.id,target_id=self.base,relation='dlc',provider='igdb'))
            await db.commit()
        path=f'/tracking/games/works/{self.base}/children'
        first=await self.client.get(path,params={'limit':1})
        self.assertEqual(first.status_code,200,first.text)
        data=first.json();self.assertEqual((data['total'],data['has_more']),(2,True))
        self.assertEqual(data['results'][0]['id'],self.child)
        self.assertIsNone(data['results'][0]['game_state']['score'])
        second=(await self.client.get(path,params={'offset':1,'limit':1})).json()
        self.assertEqual(second['results'][0]['id'],self.dlc);self.assertFalse(second['has_more'])
        self.assertEqual((await self.client.get(f'/tracking/games/works/{self.child}/children')).status_code,422)
        self.other.games_access=False;self.viewer=self.other
        self.assertEqual((await self.client.get(path)).status_code,403)
        self.viewer=None;self.assertEqual((await self.client.get(path)).status_code,401)

    async def test_child_rating_and_dates_are_viewer_scoped_and_do_not_change_base(self):
        base=(await self.client.patch(f'/tracking/games/entries/{self.base}',json={'status':'playing','score':9})).json()
        result=await self.client.patch(f'/tracking/games/children/{self.child}',json={'status':'completed','score':7,'finish_date':'2024-05-01'})
        self.assertEqual(result.status_code,200,result.text)
        path=f'/tracking/games/works/{self.base}/children'
        state=(await self.client.get(path)).json()['results'][0]['game_state']
        self.assertEqual((state['score'],state['finish_date'],state['listed']),(7,'2024-05-01',True))
        self.assertIsNone(state['total_seconds']);self.assertIsNone(state['achievements']['percentage'])
        self.assertEqual((await self.client.get(f'/tracking/games/works/{self.base}')).json()['score'],9)
        self.viewer=self.other
        other=(await self.client.get(path)).json()['results'][0]['game_state']
        self.assertIsNone(other['score']);self.assertIsNone(other['status'])
        self.viewer=self.user
        await self.client.delete(f'/tracking/games/entries/{self.base}',params={'confirmed':True,'revision':base['revision']})
        retained=(await self.client.get(path)).json()['results'][0]
        self.assertIsNone(retained['entry']);self.assertFalse(retained['game_state']['listed'])
        self.assertEqual(retained['game_state']['score'],7)
