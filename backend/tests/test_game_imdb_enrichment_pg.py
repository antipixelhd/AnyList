"""Durable full/partial IMDb ingestion and existing-game/person boundaries."""
import asyncio
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import event, func, select

import test_catalogue_pg as fixture
from core import catalogue
from core.catalogue_providers import ProviderError
from core.game_imdb_enrichment import GameIMDbEnrichment, request, status, snapshot, now, JOB, COMPONENT, PAGE
from models import User, Media
from models.base import MediaType
from models.catalogue import (CatalogueCredit, CatalogueEntity, CatalogueIdentity, MetadataSnapshot,
                              CharacterAppearance, CharacterPerformance)
from models.games import GameEntry


def actor(native):
    return {'name':{'id':native,'nameText':{'text':'Exact namesake'}},'category':{'id':'actor','text':'Cast'},
            'characters':[{'name':'Hero'}],'attributes':[{'text':'voice'}]}


def page(rows,total,more=False,cursor=None):
    return {'header':{'id':'tt123','titleText':{'text':'IMDb name must not replace IGDB'},
        'titleType':{'id':'videoGame'}},'rows':rows,'total':total,'has_next':more,'cursor':cursor}


@unittest.skipUnless(fixture.URL,'Requires explicit disposable catalogue PostgreSQL')
class IMDbEnrichmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_supported_v6_binding_accepts_new_title_credit_publication(self):
        from core.game_title_match import decide
        from test_game_title_match import game
        proof={**decide({'id':'tt123','title':'IGDB game','year':2020},
            [game(name='IGDB game',year=2020)],complete=True,branches=[])['evidence'],'version':6}
        async with self.Session() as db:
            binding=await db.scalar(select(CatalogueIdentity).where(CatalogueIdentity.namespace=='imdb.title',CatalogueIdentity.external_id=='tt123'))
            binding.source='matched';binding.evidence={'title_type':'videoGame','igdb_id':'10','matcher_version':6,'matching':proof,'mapping_state':'active'}
            await db.commit()
        self.assertEqual(await self.service.run(self.work_id),'ready')
        self.assertEqual(len(await self.active()),2)

    async def asyncSetUp(self):
        await fixture.CatalogueDatabaseTests.asyncSetUp(self)
        async with self.engine.begin() as conn:
            await conn.run_sync(lambda conn:GameEntry.__table__.create(conn))
        async with self.Session() as db:
            work=CatalogueEntity(kind='game',name='IGDB game',image_url='https://images.igdb.com/selected.jpg',
                attributes={'game_class':'game','igdb_id':'10','release_date':'2020-01-01'},
                protected_fields=['image_url'])
            user=User(username='player',email='player@example.test',api_key='fixture-player',games_access=True)
            db.add_all([work,user]);await db.flush()
            self.work_id,self.user_id=work.id,user.id
            db.add_all([CatalogueIdentity(entity_id=work.id,namespace='igdb.game',external_id='10',source='igdb'),
                CatalogueIdentity(entity_id=work.id,namespace='imdb.title',external_id='tt123',source='reviewed',
                    evidence={'title_type':'videoGame'})])
            db.add(GameEntry(user_id=user.id,work_id=work.id,status='playing',notes='Private draft',total_seconds=12345))
            await request(db,work.id);await db.commit()
        self.source=SimpleNamespace(credits=AsyncMock(return_value=page([actor('nm1'),actor('nm2')],2)))
        self.bindings=SimpleNamespace(source=self.source,
            resolve=AsyncMock(return_value={'status':'bound','binding':{'id':'tt123'}}))
        self.service=GameIMDbEnrichment(self.Session,bindings=self.bindings)

    asyncTearDown=fixture.CatalogueDatabaseTests.asyncTearDown

    async def active(self):
        async with self.Session() as db:
            return list(await db.scalars(select(CatalogueCredit).where(CatalogueCredit.work_id==self.work_id,
                CatalogueCredit.provider=='imdb',CatalogueCredit.attributes['source_active'].astext=='true')
                .order_by(CatalogueCredit.position)))

    async def due(self):
        async with self.Session() as db:
            job=await db.scalar(snapshot(JOB,self.work_id))
            job.next_attempt_at=now()-timedelta(seconds=1)
            job.expires_at=now()-timedelta(seconds=1)
            await db.commit()

    async def test_full_ingestion_is_idempotent_and_cached_without_touching_private_state(self):
        self.assertEqual(await self.service.run(self.work_id),'ready')
        first=[row.id for row in await self.active()]
        self.assertEqual(len(first),2)
        self.assertEqual(await self.service.run(self.work_id),'cached')
        self.source.credits.assert_awaited_once()
        await self.due()
        self.assertEqual(await self.service.run(self.work_id),'ready')
        self.assertEqual([row.id for row in await self.active()],first)
        async with self.Session() as db:
            work=await db.get(CatalogueEntity,self.work_id)
            self.assertEqual((work.name,work.image_url),('IGDB game','https://images.igdb.com/selected.jpg'))
            self.assertEqual(await db.scalar(select(func.count()).select_from(CatalogueEntity).where(CatalogueEntity.kind=='game')),1)
            entry=await db.scalar(select(GameEntry).where(GameEntry.user_id==self.user_id))
            self.assertEqual((entry.notes,entry.total_seconds),('Private draft',12345))
            component=await db.scalar(snapshot(COMPONENT,'tt123'))
            self.assertTrue(component.coverage['source_complete'])
            self.assertFalse(component.coverage['credits_complete'])
            self.assertEqual(await db.scalar(select(func.count()).select_from(MetadataSnapshot).where(MetadataSnapshot.kind==PAGE)),1)

    async def test_interrupted_pagination_is_partial_and_resumes_after_the_last_committed_page(self):
        self.source.credits.side_effect=[page([actor('nm1')],2,True,'next'),ProviderError('unavailable')]
        self.assertEqual(await self.service.run(self.work_id),'retry')
        self.assertEqual(len(await self.active()),1)
        async with self.Session() as db:
            self.assertIsNone(await db.scalar(snapshot(COMPONENT,'tt123')))
            state=await status(db,self.work_id)
            self.assertFalse(state['complete']);self.assertTrue(state['pending'])
            job=await db.scalar(snapshot(JOB,self.work_id))
            self.assertEqual(job.payload['cursor'],'next')
        self.source.credits.side_effect=None
        self.source.credits.return_value=page([actor('nm2')],2)
        await self.due()
        self.assertEqual(await self.service.run(self.work_id),'ready')
        self.assertEqual(self.source.credits.call_args.args,('tt123','next'))
        self.assertEqual(len(await self.active()),2)

    async def test_partial_refresh_preserves_a_complete_cached_projection_until_publication(self):
        await self.service.run(self.work_id)
        first=[row.id for row in await self.active()]
        async with self.Session() as db:
            original=await db.scalar(snapshot(COMPONENT,'tt123'))
            fetched=original.fetched_at
        await self.due()
        self.source.credits.side_effect=[page([actor('nm3')],2,True,'next'),ProviderError('unavailable')]
        self.assertEqual(await self.service.run(self.work_id),'retry')
        self.assertEqual([row.id for row in await self.active()],first)
        async with self.Session() as db:
            self.assertEqual((await db.scalar(snapshot(COMPONENT,'tt123'))).fetched_at,fetched)
        self.source.credits.side_effect=None
        self.source.credits.return_value=page([actor('nm4')],2)
        await self.due()
        self.assertEqual(await self.service.run(self.work_id),'ready')
        self.assertFalse(set(first)&{row.id for row in await self.active()})
        self.assertEqual(len(await self.active()),2)

    async def test_concurrent_requests_share_work_and_do_not_block_a_tracking_write(self):
        entered,release=asyncio.Event(),asyncio.Event()
        async def load(*args):
            entered.set();await release.wait()
            return page([actor('nm1')],1)
        self.source.credits.side_effect=load
        first=asyncio.create_task(self.service.run(self.work_id))
        await entered.wait()
        second=asyncio.create_task(self.service.run(self.work_id))
        async with self.Session() as db:
            entry=await db.scalar(select(GameEntry).where(GameEntry.user_id==self.user_id))
            entry.notes='New pending write committed during fetch'
            await db.commit()
        release.set()
        self.assertEqual(await asyncio.gather(first,second),['ready','ready'])
        self.source.credits.assert_awaited_once()
        async with self.Session() as db:
            self.assertEqual((await db.scalar(select(GameEntry))).notes,'New pending write committed during fetch')

    async def test_refresh_preserves_a_reviewed_character_association_on_a_retired_source_credit(self):
        await self.service.run(self.work_id)
        reviewed=(await self.active())[0]
        async with self.Session() as db:
            character=CatalogueEntity(kind='character',name='Reviewed character')
            db.add(character);await db.flush()
            appearance=CharacterAppearance(work_id=self.work_id,character_id=character.id,provider='reviewed')
            db.add(appearance);await db.flush()
            performance=CharacterPerformance(work_id=self.work_id,credit_id=reviewed.id,
                appearance_id=appearance.id,source='reviewed',language='en')
            db.add(performance);await db.commit()
            performance_id=performance.id
        await self.due()
        self.source.credits.return_value=page([actor('nm3')],1)
        self.assertEqual(await self.service.run(self.work_id),'ready')
        active=await self.active()
        self.assertEqual(len(active),2)
        self.assertIn(reviewed.id,{row.id for row in active})
        async with self.Session() as db:
            preserved=await db.get(CharacterPerformance,performance_id)
            self.assertEqual((preserved.credit_id,preserved.source,preserved.language),(reviewed.id,'reviewed','en'))
            # Source completeness describes the new retrieved connection; it
            # must not count or erase independently reviewed catalogue facts.
            component=await db.scalar(snapshot(COMPONENT,'tt123'))
            self.assertEqual(component.payload['received'],1)

    async def test_existing_movie_person_uses_verified_external_id_and_namesakes_stay_distinct(self):
        async with self.Session() as db:
            person=CatalogueEntity(kind='person',name='Movie actor',attributes={})
            db.add(person);await db.flush();root=person.id
            db.add(CatalogueIdentity(entity_id=root,namespace='tmdb.person',external_id='42',source='tmdb'))
            db.add(Media(title='Movie actor',media_type=MediaType.person,tmdb_id=42,imdb_id='nm1'))
            await db.commit()
        self.assertEqual(await self.service.run(self.work_id),'ready')
        async with self.Session() as db:
            one=await catalogue.resolve_identity(db,'imdb.person','nm1')
            two=await catalogue.resolve_identity(db,'imdb.person','nm2')
            self.assertEqual(one.entity_id,root)
            self.assertNotEqual(two.entity_id,root)
            self.assertEqual(await db.scalar(select(func.count()).select_from(CatalogueEntity).where(CatalogueEntity.kind=='person')),2)

    async def test_five_exact_namesakes_remain_five_people(self):
        self.source.credits.return_value=page([actor(f'nm{i}') for i in range(1,6)],5)
        await self.service.run(self.work_id)
        async with self.Session() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(CatalogueEntity).where(
                CatalogueEntity.kind=='person',CatalogueEntity.name=='Exact namesake')),5)

    async def test_title_refresh_does_not_retire_independent_person_connection_evidence(self):
        await self.service.run(self.work_id)
        async with self.Session() as db:
            person=CatalogueEntity(kind='person',name='Person-only participation')
            db.add(person);await db.flush()
            proof=CatalogueCredit(work_id=self.work_id,contributor_id=person.id,provider='imdb',role='actor',
                source_key='person:nm3:tt123:actor',scope='person',attributes={'source_active':True,'imdb_person_evidence':True})
            db.add(proof);await db.commit();proof_id=proof.id
        await self.due();self.source.credits.return_value=page([actor('nm1')],1)
        self.assertEqual(await self.service.run(self.work_id),'ready')
        async with self.Session() as db:
            self.assertTrue((await db.get(CatalogueCredit,proof_id)).attributes['source_active'])

    async def test_status_and_cached_admission_read_only_small_coverage_headers(self):
        await self.service.run(self.work_id)
        statements=[]
        def record(conn,cursor,statement,parameters,context,many):statements.append(statement)
        event.listen(self.engine.sync_engine,'before_cursor_execute',record)
        try:
            async with self.Session() as db:
                state=await request(db,self.work_id)
                self.assertTrue(state['complete'])
                self.assertFalse(state['pending'])
        finally:event.remove(self.engine.sync_engine,'before_cursor_execute',record)
        self.assertEqual(len(statements),1)
        self.assertNotIn('payload',statements[0])
        self.assertIn('coverage',statements[0])

    async def test_cancellation_releases_its_lease_without_losing_the_committed_cursor(self):
        entered=asyncio.Event()
        async def load(native,cursor):
            if cursor is None:return page([actor('nm1')],2,True,'next')
            entered.set();await asyncio.Event().wait()
        self.source.credits.side_effect=load
        running=asyncio.create_task(self.service._run(self.work_id))
        await entered.wait();running.cancel()
        with self.assertRaises(asyncio.CancelledError):await running
        async with self.Session() as db:
            job=await db.scalar(snapshot(JOB,self.work_id))
            self.assertIsNone(job.lease_token)
            self.assertEqual(job.payload['cursor'],'next')
        self.source.credits.side_effect=None
        self.source.credits.return_value=page([actor('nm2')],2)
        self.assertEqual(await self.service.run(self.work_id),'ready')
        self.assertEqual(self.source.credits.call_args.args,('tt123','next'))
