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

    async def test_work_progress_ignores_accepted_validation_and_tracks_missing_candidates(self):
        from core.game_bulk import status
        from core.game_imdb_discovery import MAP,VERSION
        from core.game_imdb_enrichment import now
        from datetime import timedelta
        games,_,_=await self.seed(1)
        async with self.Session() as db:
            db.add(MetadataSnapshot(provider='anylist',kind='game_bulk',external_id=f'{self.person_id}:actor',
                entity_id=self.person_id,coverage={'source_complete':True},payload={
                    'source_ids':['tt1','tt2','tt3'],'rows':[{'source':'tt1','work_id':games[0].id,'role':'actor'}]}))
            await db.flush()
            state=await status(db,self.person_id,'actor',{'pending':True})
            self.assertEqual((state['works_current'],state['works_total'],state['works_pending']),(1,3,True))
            for native,outcome in [('tt2','excluded'),('tt3','verified')]:
                db.add(MetadataSnapshot(provider='wikidata',kind=MAP,external_id=native,
                    payload={'matcher_version':VERSION,'status':outcome},expires_at=now()+timedelta(days=1)))
            await db.flush()
            state=await status(db,self.person_id,'actor',{'pending':True})
            self.assertEqual(state['works_total'],2)
            db.add(CatalogueCredit(work_id=games[0].id,contributor_id=self.person_id,provider='imdb',scope='person',
                source_key='published-tt3',role='actor',attributes={'imdb_title_id':'tt3','source_active':True}))
            await db.flush()
            state=await status(db,self.person_id,'actor',{'pending':True})
            self.assertEqual((state['works_total'],state['works_pending'],state['pending']),(1,False,True))

    async def test_character_skeleton_requires_pending_work_and_stops_on_failure(self):
        from core.game_contributor_works import pending_characters
        from core.game_imdb_enrichment import now
        games,_,_=await self.seed(1)
        async with self.Session() as db:
            job=MetadataSnapshot(provider='igdb',kind='game',external_id='10',entity_id=games[0].id,
                coverage={'characters_complete':False},next_attempt_at=now())
            db.add(job);await db.flush()
            self.assertEqual(await pending_characters(db,{games[0].id}),{games[0].id})
            job.error_code='unavailable';await db.flush()
            self.assertEqual(await pending_characters(db,{games[0].id}),set())
            db.add_all([CatalogueCredit(work_id=games[0].id,contributor_id=self.person_id,provider='imdb',scope='person',
                source_key='waiting-caption',role='actor',attributes={'source_active':True}),
                MetadataSnapshot(provider='imdb',kind='game_person_discovery',external_id='nm1',entity_id=self.person_id,next_attempt_at=now())])
            await db.flush()
            self.assertEqual(await pending_characters(db,{games[0].id}),{games[0].id})
            job.coverage={'characters_complete':True};job.next_attempt_at=None;job.error_code=None
            await db.flush()
            self.assertEqual(await pending_characters(db,{games[0].id}),set(),
                'Completed empty character acquisition must not regain a skeleton during career validation')


    async def test_shared_filtered_source_survives_a_cancelled_bulk_subscriber(self):
        import asyncio
        from core.game_imdb_source import IMDbGameSource
        from core.catalogue_providers import ProviderHTTP
        ready=asyncio.Event();release=asyncio.Event();calls=[]
        async def query(http,*args,**kwargs):
            self.assertIsNone(http.client)
            calls.append(kwargs['json']);ready.set();await release.wait()
            return {'data':{'name':{'id':'nm1','nameText':{'text':'Fixture'},'credits':{
                'total':0,'edges':[],'pageInfo':{'hasNextPage':False,'endCursor':None}}}}}
        source=IMDbGameSource(ProviderHTTP(session_factory=self.Session))
        with patch.object(ProviderHTTP,'request',query):
            first=asyncio.create_task(source.credits('nm1',person=True))
            await asyncio.wait_for(ready.wait(),5)
            second=asyncio.create_task(IMDbGameSource(ProviderHTTP(session_factory=self.Session)).credits('nm1',person=True))
            first.cancel();await asyncio.gather(first,return_exceptions=True)
            release.set();result=await asyncio.wait_for(second,5)
            self.assertEqual(result['rows'],[])
            self.assertEqual((await source.credits('nm1',person=True))['total'],0)
        self.assertEqual(len(calls),1)
        self.assertIn('first: 250',calls[0]['query'])

    async def test_reviewed_main_game_and_custom_update_parent_survive_projection(self):
        from core.game_imdb_person import project_mapped
        from core.game_credit_parents import KIND
        from test_game_imdb_person_pg import credit
        games,_,_=await self.seed(2)
        async with self.Session() as db:
            db.add_all([CatalogueIdentity(entity_id=self.person_id,namespace='imdb.person',external_id='nm1',source='imdb'),
                CatalogueIdentity(entity_id=games[0].id,namespace='igdb.game',external_id='7483',source='igdb'),
                CatalogueIdentity(entity_id=games[0].id,namespace='imdb.title',external_id='tt2073708',source='reviewed',evidence={'igdb_id':'7483','title_type':'videoGame','mapping_state':'active'}),
                CatalogueIdentity(entity_id=games[1].id,namespace='igdb.game',external_id='2903',source='igdb'),
                MetadataSnapshot(provider='imdb',kind=KIND,external_id='tt43706487',entity_id=games[1].id,
                    payload={'kind':'update','title':'Warframe: Tau','source_url':'https://www.warframe.com/en/tau'},coverage={'reviewed':True})])
            await db.flush()
            await project_mapped(db,'nm1',[credit('tt2073708'),credit('tt43706487')],'fresh')
            await db.commit()
            credits=(await db.execute(select(CatalogueCredit.work_id,CatalogueCredit.attributes).where(
                CatalogueCredit.contributor_id==self.person_id,CatalogueCredit.provider=='imdb'))).all()
            self.assertEqual({row.work_id for row in credits},{games[0].id,games[1].id})
            self.assertEqual(next(row.attributes['credited_child']['kind'] for row in credits if row.work_id==games[1].id),'update')

    async def test_missing_titles_get_a_separate_durable_discovery_order(self):
        from core.game_imdb_review import write_index,INDEX,PRIORITY_INDEX
        async with self.Session() as db:
            db.add(MetadataSnapshot(provider='anylist',kind='game_bulk',external_id=f'{self.person_id}:actor',
                entity_id=self.person_id,payload={'rows':[{'source':'tt1','work_id':1,'role':'actor'}],'priority_sources':['tt3']}))
            await db.flush()
            rows=[{'category':{'id':'actor'},'title':{'id':native,'titleType':{'id':'videoGame'},
                'titleText':{'text':native},'releaseYear':{'year':year}}} for native,year in [('tt1',2025),('tt2',2020),('tt3',None)]]
            result=await write_index(db,'nm1','generation',rows,entity_id=self.person_id)
            self.assertTrue(result['index_priority'])
            ordered=await db.scalar(select(MetadataSnapshot.payload).where(MetadataSnapshot.kind==PRIORITY_INDEX,
                MetadataSnapshot.external_id=='nm1:generation:priority:0'))
            self.assertEqual([row['id'] for row in ordered['titles']],['tt3','tt2','tt1'])
            original=await db.scalar(select(MetadataSnapshot.payload).where(MetadataSnapshot.kind==INDEX,
                MetadataSnapshot.external_id=='nm1:generation:0'))
            self.assertEqual([row['id'] for row in original['titles']],['tt1','tt2','tt3'])
            self.assertEqual(await db.scalar(select(func.count()).select_from(MetadataSnapshot).where(
                MetadataSnapshot.kind==INDEX,MetadataSnapshot.external_id.like('nm1:generation:%'))),1)

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
        async with self.Session() as db:
            db.add(MetadataSnapshot(provider='anylist',kind='game_bulk',external_id=f'{self.person}:actor',
                entity_id=self.person,payload={'rows':[{'source':'tt2','work_id':self.base,'role':'actor'}]}))
            await db.commit()
        self.assertEqual(await self.service.run('nm1'),'ready')
        async with self.Session() as db:
            job=await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.kind=='game_bulk'))
            self.assertEqual(len(job.payload['rows']),1,'Retain positive until credits are published')
        self.assertEqual(await self.person_service.run('nm1'),'ready')
        async with self.Session() as db:
            job=await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.kind=='game_bulk'))
            self.assertEqual(job.payload['rows'],[])

        async with self.Session() as db:
            jobs=(await db.execute(select(MetadataSnapshot.external_id,MetadataSnapshot.coverage).where(
                MetadataSnapshot.provider=='igdb',MetadataSnapshot.kind=='game',MetadataSnapshot.external_id.in_(('20','30'))))).all()
            self.assertEqual({row.external_id for row in jobs},{'20','30'})
            self.assertTrue(all(row.coverage['priority']=='background' for row in jobs))
