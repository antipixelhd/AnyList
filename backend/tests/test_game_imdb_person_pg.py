"""Person pages cache whole connections, and project only explicit game bindings."""
import asyncio
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import delete,event,func,select

import test_catalogue_pg as fixture
from core.catalogue_providers import ProviderError
from core.game_imdb_enrichment import now,snapshot
from core.game_imdb_person import GameIMDbPersonEnrichment,request,view,JOB,PAGE,COMPONENT
from core.media_details import work_details
from models.catalogue import CatalogueCredit,CatalogueEntity,CatalogueIdentity,MetadataSnapshot


def credit(title='tt1',kind='videoGame',role='actor'):
    return {'title':{'id':title,'titleType':{'id':kind},'titleText':{'text':'Identical title'}},'category':{'id':role}}


def page(rows,total,more=False,cursor=None,bio='A biography mentioning game work'):
    return {'header':{'id':'nm1','nameText':{'text':'Same person name'},'bio':{'text':{'plainText':bio}}},
        'rows':rows,'total':total,'has_next':more,'cursor':cursor}


@unittest.skipUnless(fixture.URL,'Requires explicit disposable catalogue PostgreSQL')
class PersonEnrichmentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixture.CatalogueDatabaseTests.asyncSetUp(self)
        async with self.Session() as db:
            person=CatalogueEntity(kind='person',name='Existing movie person',description='Primary biography',attributes={})
            game=CatalogueEntity(kind='game',name='IGDB game',attributes={'game_class':'game','igdb_id':'10'})
            db.add_all([person,game]);await db.flush();self.person_id,self.game_id=person.id,game.id
            db.add_all([CatalogueIdentity(entity_id=person.id,namespace='imdb.person',external_id='nm1',source='tmdb'),
                CatalogueIdentity(entity_id=person.id,namespace='tmdb.person',external_id='42',source='tmdb'),
                CatalogueIdentity(entity_id=game.id,namespace='igdb.game',external_id='10',source='igdb'),
                CatalogueIdentity(entity_id=game.id,namespace='imdb.title',external_id='tt1',source='reviewed',evidence={'title_type':'videoGame','igdb_id':'10'})])
            await request(db,self.person_id);await db.commit()
        self.source=SimpleNamespace(credits=AsyncMock(return_value=page([credit(),credit('tt2'),credit('tt3','movie')],3)))
        self.service=GameIMDbPersonEnrichment(self.Session,source=self.source)

    asyncTearDown=fixture.CatalogueDatabaseTests.asyncTearDown

    async def due(self):
        async with self.Session() as db:
            job=await db.scalar(snapshot(JOB,'nm1'));job.next_attempt_at=now()-timedelta(seconds=1)
            job.expires_at=now()-timedelta(seconds=1);await db.commit()

    async def active(self):
        async with self.Session() as db:
            return list(await db.scalars(select(CatalogueCredit).where(CatalogueCredit.provider=='imdb',
                CatalogueCredit.attributes['imdb_person_evidence'].astext=='true',CatalogueCredit.attributes['source_active'].astext=='true')))

    async def test_complete_source_is_cached_but_unmapped_game_coverage_is_partial(self):
        self.assertEqual(await self.service.run('nm1'),'ready')
        self.assertEqual(await self.service.run('nm1'),'cached');self.source.credits.assert_awaited_once_with('nm1',None,person=True)
        rows=await self.active();self.assertEqual(len(rows),1)
        self.assertEqual((rows[0].work_id,rows[0].contributor_id),(self.game_id,self.person_id))
        self.assertIsNone(rows[0].attributes['voice']);self.assertEqual(rows[0].attributes['languages'],[])
        async with self.Session() as db:
            state=await view(db,'nm1')
            self.assertFalse(state['complete']);self.assertTrue(state['pending'])
            self.assertTrue(state['mapping']['source_complete']);self.assertEqual(state['mapping']['state'],'partial')
            self.assertEqual((state['mapping']['source_games'],state['mapping']['mapped_games'],state['mapping']['unmapped_games']),(2,1,1))
            self.assertEqual(state['profile']['biography'],'A biography mentioning game work')
            person=await db.get(CatalogueEntity,self.person_id)
            self.assertEqual(person.description,'Primary biography');self.assertNotIn('biography',person.attributes)
            self.assertEqual(await db.scalar(select(func.count()).select_from(CatalogueEntity).where(CatalogueEntity.kind=='game')),1)
            self.assertEqual(await db.scalar(select(func.count()).select_from(MetadataSnapshot).where(MetadataSnapshot.kind==PAGE)),1)
            # A person connection proves participation, not title-level cast,
            # voice, character labels or source ordering for that title.
            detail=await work_details(db,await db.get(CatalogueEntity,self.game_id),extras=False)
            self.assertEqual(detail['people'],[])

    async def test_pagination_failure_resumes_and_never_becomes_a_complete_empty_filmography(self):
        self.source.credits.side_effect=[page([credit()],2,True,'next'),ProviderError('unavailable')]
        self.assertEqual(await self.service.run('nm1'),'retry');self.assertEqual(await self.active(),[])
        async with self.Session() as db:
            state=await view(db,'nm1');self.assertFalse(state['complete']);self.assertTrue(state['pending'])
            self.assertIsNone(state['profile'])
            self.assertEqual((await db.scalar(snapshot(JOB,'nm1'))).payload['cursor'],'next')
        self.source.credits.side_effect=None;self.source.credits.return_value=page([credit('tt2')],2)
        await self.due();self.assertEqual(await self.service.run('nm1'),'ready')
        self.assertEqual(self.source.credits.call_args.args,('nm1','next'))
        self.assertEqual(len(await self.active()),1)

    async def test_partial_refresh_retains_profile_and_membership_until_complete(self):
        await self.service.run('nm1');first=[row.id for row in await self.active()]
        await self.due()
        self.source.credits.side_effect=[page([credit('tt2')],2,True,'next',bio='Replacement'),ProviderError('unavailable')]
        self.assertEqual(await self.service.run('nm1'),'retry')
        self.assertEqual([row.id for row in await self.active()],first)
        async with self.Session() as db:
            self.assertEqual((await view(db,'nm1'))['profile']['biography'],'A biography mentioning game work')
        self.source.credits.side_effect=None;self.source.credits.return_value=page([credit('tt3','movie')],2)
        await self.due();self.assertEqual(await self.service.run('nm1'),'ready');self.assertEqual(await self.active(),[])
        async with self.Session() as db:self.assertEqual((await view(db,'nm1'))['profile']['biography'],'Replacement')

    async def test_invalid_nested_title_keeps_prior_complete_connection_and_retries(self):
        await self.service.run('nm1');first=[row.id for row in await self.active()]
        await self.due();self.source.credits.return_value=page([{'title':None}],1)
        self.assertEqual(await self.service.run('nm1'),'retry')
        self.assertEqual([row.id for row in await self.active()],first)
        async with self.Session() as db:
            self.assertFalse((await view(db,'nm1'))['complete'])
            self.assertTrue((await db.scalar(snapshot(COMPONENT,'nm1'))).coverage['source_complete'])

    async def test_concurrent_actor_and_staff_requests_share_a_single_connection_and_idempotent_projection(self):
        entered,release=asyncio.Event(),asyncio.Event()
        async def load(*args,**kwargs):entered.set();await release.wait();return page([credit(),credit(role='writer')],2)
        self.source.credits.side_effect=load
        first=asyncio.create_task(self.service.run('nm1'));await entered.wait()
        second=asyncio.create_task(self.service.run('nm1'));release.set()
        self.assertEqual(await asyncio.gather(first,second),['ready','ready']);self.source.credits.assert_awaited_once()
        ids={row.id for row in await self.active()};await self.due();await self.service.run('nm1')
        self.assertEqual({row.id for row in await self.active()},ids)

    async def test_alias_views_share_imdb_job_and_status_polls_skip_large_payloads(self):
        await self.service.run('nm1')
        async with self.Session() as db:
            alias=CatalogueEntity(kind='person',name='Same name',canonical_person_id=self.person_id)
            db.add(alias);await db.flush();state=await request(db,alias.id);self.assertEqual(state['id'],'nm1')
            await db.commit()
        statements=[]
        def record(conn,cursor,statement,parameters,context,many):statements.append(statement)
        event.listen(self.engine.sync_engine,'before_cursor_execute',record)
        try:
            async with self.Session() as db:state=await view(db,'nm1',include_profile=False)
        finally:event.remove(self.engine.sync_engine,'before_cursor_execute',record)
        # Identity work has its own compact header while pending; full source
        # pages and filmography JSON still never enter these status reads.
        self.assertNotIn('profile',state);self.assertEqual(len(statements),4)
        self.assertTrue(state['identity_pending'])
        self.assertTrue(all('catalogue_metadata_snapshots.payload,' not in statement
            and 'catalogue_metadata_snapshots.payload \nFROM' not in statement for statement in statements))

    async def test_new_reviewed_binding_reprojects_cached_pages_without_renewing_source_freshness(self):
        from core.game_imdb_bindings import GameIMDbBindings
        await self.service.run('nm1')
        async with self.Session() as db:
            original=await view(db,'nm1');fetched=original['fetched_at']
            game=CatalogueEntity(kind='game',name='Another IGDB game',attributes={'game_class':'game','igdb_id':'11'})
            db.add(game);await db.flush();other=game.id
            db.add(CatalogueIdentity(entity_id=game.id,namespace='igdb.game',external_id='11',source='igdb'))
            await db.commit()
        header={'id':'tt2','titleText':{'text':'Reviewed game'},'titleType':{'id':'videoGame'}}
        bindings=GameIMDbBindings(self.Session,source=SimpleNamespace(title=AsyncMock(return_value=header)))
        await bindings.review(other,'tt2',7,'Verified the game and edition')
        async with self.Session() as db:
            state=await view(db,'nm1',check_mapping=True)
            self.assertTrue(state['pending']);await db.commit()
        self.assertEqual(await self.service.run('nm1'),'ready');self.source.credits.assert_awaited_once()
        self.assertEqual(len(await self.active()),2)
        async with self.Session() as db:
            refreshed=await view(db,'nm1',check_mapping=True)
            self.assertFalse(refreshed['pending']);self.assertEqual(refreshed['fetched_at'],fetched)
            self.assertNotEqual(refreshed['revision'],original['revision'])
            self.assertEqual(refreshed['mapping']['unmapped_games'],0)

    async def test_cancelled_fetch_retains_committed_cursor_and_releases_its_lease(self):
        entered=asyncio.Event()
        async def load(native,cursor,**kwargs):
            if cursor is None:return page([credit()],2,True,'next')
            entered.set();await asyncio.Event().wait()
        self.source.credits.side_effect=load
        running=asyncio.create_task(self.service._run('nm1'));await entered.wait();running.cancel()
        with self.assertRaises(asyncio.CancelledError):await running
        async with self.Session() as db:
            job=await db.scalar(snapshot(JOB,'nm1'))
            self.assertIsNone(job.lease_token);self.assertEqual(job.payload['cursor'],'next')
        self.source.credits.side_effect=None;self.source.credits.return_value=page([credit('tt2')],2)
        self.assertEqual(await self.service.run('nm1'),'ready')
        self.assertEqual(self.source.credits.call_args.args,('nm1','next'))

    async def test_person_scope_migration_round_trip_preserves_credit_facts(self):
        import importlib
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        await self.service.run('nm1')
        row=(await self.active())[0]
        migration=importlib.import_module('migrations.versions.mt065_person_credit_scope')
        def apply(conn,operation):
            with Operations.context(MigrationContext.configure(conn)):operation()
        async with self.engine.begin() as conn:
            await conn.run_sync(lambda conn:apply(conn,migration.downgrade))
        async with self.Session() as db:
            legacy=await db.get(CatalogueCredit,row.id)
            self.assertEqual(legacy.scope,'title');self.assertTrue(legacy.attributes['imdb_person_evidence'])
        async with self.engine.begin() as conn:
            await conn.run_sync(lambda conn:apply(conn,migration.upgrade))
        async with self.Session() as db:
            restored=await db.get(CatalogueCredit,row.id)
            self.assertEqual((restored.scope,restored.contributor_id,restored.role),('person',self.person_id,'actor'))

    async def test_reviewed_character_facts_can_promote_sparse_participation_to_displayable_cast(self):
        from models.catalogue import CharacterAppearance,CharacterPerformance
        await self.service.run('nm1')
        proof=(await self.active())[0]
        async with self.Session() as db:
            character=CatalogueEntity(kind='character',name='Reviewed role')
            db.add(character);await db.flush()
            appearance=CharacterAppearance(work_id=self.game_id,character_id=character.id,provider='reviewed')
            db.add(appearance);await db.flush()
            db.add(CharacterPerformance(work_id=self.game_id,credit_id=proof.id,appearance_id=appearance.id,source='reviewed'))
            await db.commit()
            detail=await work_details(db,await db.get(CatalogueEntity,self.game_id),extras=False)
            self.assertEqual(len(detail['people']),1)
            self.assertEqual(detail['people'][0]['performances'][0]['source'],'reviewed')
            self.assertEqual(detail['people'][0]['performances'][0]['name'],'Reviewed role')

    async def test_a_video_game_kind_without_its_verified_igdb_root_is_not_enough_to_project_work(self):
        async with self.Session() as db:
            orphan=CatalogueEntity(kind='game',name='Identical title',attributes={'game_class':'game'})
            db.add(orphan);await db.flush()
            db.add(CatalogueIdentity(entity_id=orphan.id,namespace='imdb.title',external_id='tt2',source='reviewed',
                evidence={'title_type':'videoGame','igdb_id':'11'}))
            await db.commit()
        await self.service.run('nm1')
        self.assertEqual([row.work_id for row in await self.active()],[self.game_id])
        async with self.Session() as db:self.assertEqual((await view(db,'nm1'))['mapping']['unmapped_games'],1)

    async def test_cached_remapping_withdraws_proof_whose_game_root_no_longer_matches(self):
        from core.game_imdb_bindings import GameIMDbBindings
        await self.service.run('nm1')
        async with self.Session() as db:
            game=await db.get(CatalogueEntity,self.game_id)
            game.attributes={**game.attributes,'igdb_id':'99'}
            other=CatalogueEntity(kind='game',name='New verified game',attributes={'game_class':'game','igdb_id':'11'})
            db.add(other);await db.flush();other_id=other.id
            db.add(CatalogueIdentity(entity_id=other_id,namespace='igdb.game',external_id='11',source='igdb'))
            await db.commit()
        header={'id':'tt2','titleText':{'text':'Verified game'},'titleType':{'id':'videoGame'}}
        await GameIMDbBindings(self.Session,source=SimpleNamespace(title=AsyncMock(return_value=header))).review(
            other_id,'tt2',7,'Verified the other game and edition')
        async with self.Session() as db:
            await view(db,'nm1',check_mapping=True);await db.commit()
        self.assertEqual(await self.service.run('nm1'),'ready')
        self.assertEqual([row.work_id for row in await self.active()],[other_id])
        self.source.credits.assert_awaited_once()

    async def test_missing_persisted_page_cannot_publish_complete_and_recovers_from_the_start(self):
        self.service.page_budget=1
        self.source.credits.return_value=page([credit()],2,True,'next')
        self.assertEqual(await self.service.run('nm1'),'partial')
        async with self.Session() as db:
            await db.execute(delete(MetadataSnapshot).where(MetadataSnapshot.kind==PAGE));await db.commit()
        self.source.credits.return_value=page([credit('tt2')],2)
        await self.due();self.assertEqual(await self.service.run('nm1'),'retry')
        async with self.Session() as db:
            self.assertIsNone(await db.scalar(snapshot(COMPONENT,'nm1')))
            state=await view(db,'nm1');self.assertFalse(state['complete'])
            self.assertEqual(state['error'],'incomplete_cache')
        self.source.credits.return_value=page([credit()],1)
        await self.due();self.assertEqual(await self.service.run('nm1'),'ready')
        self.assertEqual(self.source.credits.call_args.args,('nm1',None))

    async def test_cold_complete_connection_admits_identity_and_refresh_keeps_its_check_header(self):
        from core.game_person_identity import JOB as IDENTITY_JOB
        self.assertEqual(await self.service.run('nm1'),'ready')
        async with self.Session() as db:
            identity=await db.scalar(snapshot(IDENTITY_JOB,'nm1'))
            self.assertIsNotNone(identity.next_attempt_at)
            checked={'version':1,'state':'unresolved','next_check_at':(now()+timedelta(days=7)).isoformat()}
            component=await db.scalar(snapshot(COMPONENT,'nm1'))
            component.payload={**component.payload,'identity_bridge':checked}
            await db.commit()
        await self.due()
        self.assertEqual(await self.service.run('nm1'),'ready')
        async with self.Session() as db:
            component=await db.scalar(snapshot(COMPONENT,'nm1'))
            self.assertEqual(component.payload['identity_bridge'],checked)
