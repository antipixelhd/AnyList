"""Duplicate source records preserve the primary game and independent versions."""
import unittest
from sqlalchemy import select,delete,func

import test_games_pg as fixture
import test_game_imdb_discovery_pg as portfolio_fixture
from core.game_credit_aliases import review_alias,KIND
from core.game_credit_parents import person_bindings,review_parent
from core.game_contributor_works import works_page
from core.game_imdb_person import queue_projection,view
from core.game_imdb_enrichment import snapshot
from models import User
from models.catalogue import CatalogueIdentity,CatalogueCredit,MetadataSnapshot,CatalogueEntity
from models.games import GameEntry


def headers(alias='tt2',primary='tt1'):
    return {native:{'id':native,'titleType':{'id':'videoGame'},'titleText':{'text':'Duplicate' if native==alias else 'Main'},
        'releaseYear':{'year':2020}} for native in (alias,primary)}


@unittest.skipUnless(fixture.URL,'Requires disposable Games PostgreSQL')
class DuplicateTitleTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=portfolio_fixture.PortfolioDiscoveryTests.asyncSetUp
    cleanup=portfolio_fixture.PortfolioDiscoveryTests.cleanup

    async def review(self,db,**kwargs):
        user=await db.get(User,self.user.id);user.is_admin=True
        await review_alias(db,'tt2',self.base,primary_title_id='tt1',title='Duplicate',
            source_url='https://example.test/verified-credits',reviewer_id=user.id,
            reason='Both source IDs describe the same release and independently confirmed cast.',
            identities=headers(),**kwargs)

    async def test_duplicate_contributes_once_without_replacing_primary_or_adding_a_child_label(self):
        from core.game_imdb_review import review_titles
        async with self.Session() as db:
            await self.review(db);await queue_projection(db,'nm1');await db.commit()
        self.assertEqual(await self.person_service.run('nm1'),'ready')
        async with self.Session() as db:
            state=await view(db,'nm1');self.assertEqual(state['mapping']['unmapped_games'],1)
            page=await works_page(db,None,self.person,'actor',key='test',list_scope='all',status='',page=1,cursor=None)
            self.assertEqual([row['id'] for row in page['works']],[self.base])
            self.assertFalse(page['works'][0].get('credit_sources'))
            await db.execute(delete(CatalogueCredit).where(CatalogueCredit.contributor_id==self.person,
                CatalogueCredit.attributes['imdb_title_id'].astext=='tt1'))
            page=await works_page(db,None,self.person,'actor',key='test',list_scope='all',status='',page=1,cursor=None)
            self.assertEqual([row['id'] for row in page['works']],[self.base])
            self.assertFalse(page['works'][0].get('credit_sources'))
            review=await review_titles(db,'nm1',scope='all')
            self.assertEqual(next(r for r in review['results'] if r['imdb_id']=='tt2')['status'],'bound')
            primary=await db.scalar(select(CatalogueIdentity.external_id).where(CatalogueIdentity.entity_id==self.base,CatalogueIdentity.namespace=='imdb.title'))
            self.assertEqual(primary,'tt1')
            entry=await db.scalar(select(GameEntry).where(GameEntry.work_id==self.base))
            self.assertEqual((entry.total_seconds,entry.notes),(3600,'Pending private state'))
            self.assertEqual(await db.scalar(select(func.count()).select_from(CatalogueEntity).where(CatalogueEntity.kind=='game')),self.original_games)
            record=await db.scalar(snapshot(KIND,'tt2'));self.assertEqual(record.payload['primary_title_id'],'tt1')
            self.assertEqual(await db.scalar(select(func.count()).select_from(MetadataSnapshot).where(MetadataSnapshot.kind=='game_credit_alias_review')),1)

    async def test_primary_change_or_staleness_disables_alias_until_reviewed_again(self):
        async with self.Session() as db:
            await self.review(db);await db.commit()
            bound=person_bindings(['tt2']);self.assertIsNotNone(await db.scalar(select(bound.c.work_id)))
            primary=await db.scalar(select(CatalogueIdentity).where(CatalogueIdentity.namespace=='imdb.title',CatalogueIdentity.external_id=='tt1'))
            primary.evidence={**primary.evidence,'mapping_state':'stale'};await db.flush()
            bound=person_bindings(['tt2']);self.assertIsNone(await db.scalar(select(bound.c.work_id)))
            primary.evidence={**primary.evidence,'mapping_state':'active'};primary.external_id='tt9';await db.flush()
            bound=person_bindings(['tt2']);self.assertIsNone(await db.scalar(select(bound.c.work_id)))

    async def test_review_rejects_non_admin_wrong_primary_and_existing_direct_bindings(self):
        async with self.Session() as db:
            with self.assertRaises(ValueError):
                await review_alias(db,'tt2',self.base,primary_title_id='tt1',title='Duplicate',
                    source_url='https://example.test/credits',reviewer_id=self.user.id,reason='Confirmed duplicate',identities=headers())
            user=await db.get(User,self.user.id);user.is_admin=True
            with self.assertRaises(ValueError):
                await review_alias(db,'tt2',self.base,primary_title_id='tt9',title='Duplicate',
                    source_url='https://example.test/credits',reviewer_id=user.id,reason='Confirmed duplicate',identities=headers(primary='tt9'))
            db.add(CatalogueIdentity(entity_id=self.pack,namespace='imdb.title',external_id='tt2',source='reviewed',evidence={}))
            await db.flush()
            with self.assertRaises(ValueError):await self.review(db)

    async def test_reviewed_child_takes_precedence_over_duplicate_alias(self):
        async with self.Session() as db:
            await self.review(db)
            await review_parent(db,'tt2',self.base,title='Main: DLC',kind='dlc',source_url='https://example.test/dlc',reviewer_id=self.user.id)
            await db.commit()
            bound=person_bindings(['tt2']);rows=(await db.execute(select(bound))).all()
            self.assertEqual(len(rows),1);self.assertEqual(rows[0].credit_kind,'dlc')
            with self.assertRaises(ValueError):await self.review(db)

    async def test_later_conflicting_identity_disables_alias_without_selecting_another_game(self):
        async with self.Session() as db:
            await self.review(db)
            db.add(CatalogueIdentity(entity_id=self.pack,namespace='imdb.title',external_id='tt2',source='matched',evidence={}))
            await db.flush()
            bound=person_bindings(['tt2']);self.assertIsNone(await db.scalar(select(bound.c.work_id)))

    async def test_review_cannot_move_an_existing_duplicate_to_another_game(self):
        async with self.Session() as db:
            await self.review(db)
            record=await db.scalar(snapshot(KIND,'tt2'));record.entity_id=self.pack;await db.flush()
            with self.assertRaises(ValueError):await self.review(db)
            self.assertEqual(await db.scalar(select(func.count()).select_from(MetadataSnapshot).where(MetadataSnapshot.kind=='game_credit_alias_review')),1)
