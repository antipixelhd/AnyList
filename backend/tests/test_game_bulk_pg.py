"""Real database provisional publication, reuse, retirement and privacy."""
import unittest
from unittest.mock import patch
from sqlalchemy import select,func
import test_game_contributor_works_pg as works_fixture
from models.catalogue import CatalogueIdentity,CatalogueCredit,MetadataSnapshot,CatalogueEntity
from core.game_bulk import publish,retire,queue_metadata
from core.game_contributor_works import works_page
fixture=works_fixture.fixture


@unittest.skipUnless(fixture.fixture.URL,'Requires explicit disposable statistics PostgreSQL')
class BulkDatabaseTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=works_fixture.GameContributorTests.asyncSetUp
    asyncTearDown=works_fixture.GameContributorTests.asyncTearDown
    seed=works_fixture.GameContributorTests.seed

    async def test_reuses_identity_preserves_private_state_and_removes_false_positive(self):
        games,_,_=await self.seed(1)
        async with self.Session() as db:
            db.add(CatalogueIdentity(entity_id=games[0].id,namespace='igdb.game',external_id='100',source='igdb'))
            job=MetadataSnapshot(provider='anylist',kind='game_bulk',external_id=f'{self.person_id}:actor',entity_id=self.person_id,
                coverage={'kind':'actor','native':'nm1'})
            db.add(job);await db.flush()
            candidates=[{'game':{'id':native,'name':'Provisional','game_type':0,'platforms':[6]},'source':f'tt{native}','roles':['actor']} for native in (100,200)]
            await publish(db,job,candidates);await db.commit()
            self.assertEqual(await db.scalar(select(func.count()).select_from(CatalogueIdentity).where(CatalogueIdentity.namespace=='imdb.title')),0)
            self.assertEqual(await db.scalar(select(func.count()).select_from(CatalogueCredit).where(CatalogueCredit.provider=='anylist')),0)
            ready=await works_page(db,self.viewer,self.person_id,'actor',key=f'catalogue:{self.person_id}',list_scope='all',status='',page=1,cursor=None)
            self.assertEqual(ready['total'],2)
            self.assertEqual(ready['list_count'],1)
            self.assertNotIn('private game note',str(ready))
            existing=next(row for row in ready['works'] if row['id']==games[0].id)
            self.assertEqual(existing['title'],'Game 000')
            await retire(db,self.person_id,'actor',{'tt200'});await db.commit()
            ready=await works_page(db,self.viewer,self.person_id,'actor',key=f'catalogue:{self.person_id}',list_scope='all',status='',page=1,cursor=None)
            self.assertEqual(ready['total'],1)
            await queue_metadata(db,self.person_id,'actor');await db.commit()
            state=await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.provider=='igdb',MetadataSnapshot.kind=='game',MetadataSnapshot.external_id=='100'))
            self.assertEqual(state.coverage['priority'],'background')

    async def test_completed_negative_wins_a_late_bulk_publication(self):
        from datetime import timedelta
        from core.game_imdb_enrichment import now
        from core.game_imdb_discovery import MAP,VERSION
        async with self.Session() as db:
            job=MetadataSnapshot(provider='anylist',kind='game_bulk',external_id=f'{self.person_id}:actor',entity_id=self.person_id,coverage={'kind':'actor','native':'nm1'})
            db.add_all([job,MetadataSnapshot(provider='wikidata',kind=MAP,external_id='tt200',payload={'status':'excluded','matcher_version':VERSION},expires_at=now()+timedelta(days=1))])
            await db.flush()
            await publish(db,job,[{'game':{'id':200,'name':'False positive','game_type':0,'platforms':[6]},'source':'tt200','roles':['actor']}])
            self.assertEqual(job.payload['rows'],[])
            await db.commit()

    async def test_click_promotes_metadata_and_bulk_cannot_starve_verification(self):
        from datetime import timedelta
        from core.game_imdb_enrichment import now
        from core.game_bulk import defer_verification
        from core.game_metadata import GameMetadata
        games,_,_=await self.seed(1)
        async with self.Session() as db:
            db.add_all([CatalogueIdentity(entity_id=games[0].id,namespace='igdb.game',external_id='100',source='igdb'),
                MetadataSnapshot(provider='anylist',kind='game_bulk',external_id=f'{self.person_id}:actor',entity_id=self.person_id,next_attempt_at=now()),
                MetadataSnapshot(provider='imdb',kind='game_person_job',external_id='nm1',entity_id=self.person_id,lease_token='owned',lease_until=now()+timedelta(minutes=1))])
            await db.flush()
            self.assertTrue(await defer_verification(db,'imdb','game_person_job','nm1','owned',('actor','staff')))
            bulk=await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.kind=='game_bulk'))
            bulk.next_attempt_at=now()-timedelta(seconds=7);await db.commit()
            self.assertFalse(await defer_verification(db,'imdb','game_person_job','nm1','owned',('actor','staff')))
            await queue_metadata(db,self.person_id,'actor');await db.commit()
            service=GameMetadata(self.Session,scope=self.engine)
            with patch.object(service,'start',return_value=None):
                await service.ensure(db,await db.get(CatalogueEntity,games[0].id),wait_seconds=0)
            priority=await db.scalar(select(MetadataSnapshot.coverage['priority'].astext).where(
                MetadataSnapshot.provider=='igdb',MetadataSnapshot.kind=='game',MetadataSnapshot.external_id=='100'))
            self.assertEqual(priority,'interactive')
            queued=await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.provider=='igdb',
                MetadataSnapshot.kind=='game',MetadataSnapshot.external_id=='100'))
            await queue_metadata(db,self.person_id,'actor');await db.refresh(queued)
            self.assertEqual(queued.coverage['priority'],'interactive')
            queued.next_attempt_at=None;queued.expires_at=now()-timedelta(seconds=1)
            queued.coverage={**queued.coverage,'custom_evidence':'keep'};await db.commit()
            await queue_metadata(db,self.person_id,'actor');await db.flush();await db.refresh(queued)
            self.assertIsNotNone(queued.next_attempt_at)
            self.assertEqual(queued.coverage['priority'],'background')
            self.assertEqual(queued.coverage['custom_evidence'],'keep')


import test_game_imdb_discovery_pg as discovery_fixture


@unittest.skipUnless(discovery_fixture.fixture.URL,'Requires explicit disposable Games PostgreSQL')
class BulkFinalProjectionTests(unittest.IsolatedAsyncioTestCase):
    cleanup=discovery_fixture.fixture.GamesDatabaseTests.cleanup
    asyncSetUp=discovery_fixture.PortfolioDiscoveryTests.asyncSetUp

    async def test_one_batch_person_queues_new_works_after_projection(self):
        from sqlalchemy import delete
        async with self.Session() as db:
            await db.execute(delete(CatalogueCredit).where(CatalogueCredit.contributor_id==self.person))
            await db.commit()
        self.assertEqual(await self.service.run('nm1'),'ready')
        self.assertEqual(await self.person_service.run('nm1'),'ready')
        async with self.Session() as db:
            jobs=(await db.execute(select(MetadataSnapshot.external_id,MetadataSnapshot.coverage).where(
                MetadataSnapshot.provider=='igdb',MetadataSnapshot.kind=='game',MetadataSnapshot.external_id.in_(('20','30'))))).all()
            self.assertEqual({row.external_id for row in jobs},{'20','30'})
            self.assertTrue(all(row.coverage['priority']=='background' for row in jobs))
