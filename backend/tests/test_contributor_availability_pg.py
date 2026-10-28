"""Verified empty controls use compact evidence and respect Games approval."""
import unittest
import re
from datetime import timedelta
from unittest.mock import AsyncMock,patch

from sqlalchemy import event,func,select

import test_statistics_pg as database
import test_game_contributor_works_pg as fixture
from core import contributor_evidence as evidence
from core.contributor_availability import movie_job
from core.game_imdb_enrichment import now
from core.game_imdb_person import JOB,COMPONENT,VERSION as PERSON_VERSION
from core.game_credit_roles import CREDIT_POLICY_VERSION
from core.game_person_identity import VERSION as IDENTITY_VERSION
from models.catalogue import CatalogueEntity,CatalogueIdentity,CatalogueCredit,MetadataSnapshot,ContributorEvidence


@unittest.skipUnless(database.URL,'Requires explicit disposable PostgreSQL')
class AvailabilityDatabaseTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixture.GameContributorTests.asyncSetUp
    asyncTearDown=fixture.GameContributorTests.asyncTearDown

    async def person(self,*,tmdb=True,imdb=True):
        async with self.Session() as db:
            entity=CatalogueEntity(kind='person',name='Availability person');db.add(entity);await db.flush()
            if tmdb:db.add(CatalogueIdentity(entity_id=entity.id,namespace='tmdb.person',external_id='777',source='tmdb'))
            if imdb:db.add(CatalogueIdentity(entity_id=entity.id,namespace='imdb.person',external_id='nm777',source='tmdb'))
            await db.commit();return entity.id

    async def read(self,entity,kind='actor'):
        return await self.client.get(f'/tracking/contributors/{kind}/catalogue:{entity}/availability')

    async def person_cache(self,entity,*,unmapped=0,legacy=False):
        async with self.Session() as db:
            from core.game_imdb_bindings import revision
            coverage={'source_complete':True,'state':'mapped','person_version':PERSON_VERSION}
            coverage['binding_revision']=await revision(db)
            if not legacy:
                coverage['credit_policy_version']=CREDIT_POLICY_VERSION
                coverage['role_coverage']={kind:{'source':unmapped,'unmapped':unmapped} for kind in ('actor','staff')}
            db.add_all([MetadataSnapshot(provider='imdb',kind=JOB,external_id='nm777',entity_id=entity,
                coverage={'complete':True,'state':'complete'},expires_at=now()+timedelta(days=1)),
                MetadataSnapshot(provider='imdb',kind=COMPONENT,external_id='nm777',entity_id=entity,
                    coverage=coverage,payload={'raw':'LARGE_PERSON_EVIDENCE '*50000,
                        'identity_bridge':{'version':IDENTITY_VERSION,'state':'unresolved','next_check_at':(now()+timedelta(days=1)).isoformat()}},
                    fetched_at=now(),expires_at=now()+timedelta(days=1))])
            await db.commit()

    async def test_background_admission_is_shared_and_reads_never_fetch_providers(self):
        entity=await self.person()
        with patch('core.tmdb.get_person',AsyncMock(side_effect=AssertionError('provider on read'))) as tmdb, \
             patch('core.game_imdb_source.IMDbGameSource.credits',AsyncMock(side_effect=AssertionError('provider on read'))) as imdb:
            for _ in range(2):
                response=await self.read(entity);self.assertEqual(response.status_code,200,response.text)
                self.assertEqual(response.json()['domains'],{'movie':'unknown','series':'unknown','game':'unknown'})
                self.assertTrue(response.json()['pending'])
            tmdb.assert_not_awaited();imdb.assert_not_awaited()
        async with self.Session() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(ContributorEvidence)),1)
            self.assertEqual(await db.scalar(select(func.count()).select_from(MetadataSnapshot).where(MetadataSnapshot.kind==JOB)),1)

    async def test_complete_role_specific_sources_enable_only_meaningful_choices_and_skip_raw_payloads(self):
        entity=await self.person();await self.person_cache(entity)
        key,config=movie_job('actor',777)
        async with self.Session() as db:
            db.add(ContributorEvidence(kind=key[0],native_id=key[1],variant=key[2],config=config,
                expires_at=now()+timedelta(days=1),payload={'domain_availability':{
                    'actor':{'movie':'available','series':'empty'},'staff':{'movie':'empty','series':'available'}},'raw':'LARGE_MOVIE_EVIDENCE '*50000}))
            await db.commit()
        statements=[]
        def record(conn,cursor,statement,parameters,context,many):statements.append(statement)
        event.listen(self.engine.sync_engine,'before_cursor_execute',record)
        try:
            actor=await self.read(entity);staff=await self.read(entity,'staff')
        finally:event.remove(self.engine.sync_engine,'before_cursor_execute',record)
        self.assertEqual(actor.json()['domains'],{'movie':'available','series':'empty','game':'empty'})
        self.assertEqual(staff.json()['domains'],{'movie':'empty','series':'available','game':'empty'})
        self.assertFalse(actor.json()['pending'])
        self.assertEqual(actor.headers['cache-control'],'private, no-store')
        self.assertNotIn('LARGE_',actor.text+staff.text)
        # JSON scalar reads such as payload ->> generation are compact headers,
        # not a transfer of the full source blob.
        payload_reads=[sql for sql in statements if re.search(
            r'catalogue_(?:metadata_snapshots|contributor_evidence)\.payload\s*(?:AS\s+\w+\s*)?(?:,|FROM\b|$)',sql,re.I)]
        self.assertFalse(payload_reads,'\n'.join(payload_reads))

    async def test_unmapped_or_partial_games_never_disable_and_legacy_cache_reprojects_in_background(self):
        entity=await self.person(tmdb=False);await self.person_cache(entity,unmapped=1)
        self.assertEqual((await self.read(entity)).json()['domains']['game'],'unknown')
        async with self.Session() as db:
            component=await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.kind==COMPONENT))
            component.coverage={**component.coverage,'role_coverage':{'actor':{'source':1,'unmapped':0}}}
            await db.commit()
        # Identity coverage alone does not prove all game/parent metadata is
        # resolved when a positive source connection has no eligible works.
        self.assertEqual((await self.read(entity)).json()['domains']['game'],'unknown')
        async with self.Session() as db:
            component=await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.kind==COMPONENT))
            component.coverage={key:value for key,value in component.coverage.items() if key not in ('credit_policy_version','role_coverage')};await db.commit()
        result=(await self.read(entity)).json()
        self.assertEqual(result['domains']['game'],'unknown');self.assertTrue(result['pending'])
        async with self.Session() as db:
            job=await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.kind==JOB))
            self.assertTrue(job.payload['projection_only'])

    async def test_provider_negative_preserves_other_stored_works_and_dlc_does_not_enable_games(self):
        entity=await self.person();await self.person_cache(entity)
        key,config=movie_job('actor',777)
        async with self.Session() as db:
            db.add(ContributorEvidence(kind=key[0],native_id=key[1],variant=key[2],config=config,
                expires_at=now()+timedelta(days=1),payload={'domain_availability':{'actor':{'movie':'empty','series':'empty'}}}))
            movie=CatalogueEntity(kind='movie',name='Other provider movie')
            dlc=CatalogueEntity(kind='game',name='Ordinary DLC',attributes={'game_class':'dlc'})
            db.add_all([movie,dlc]);await db.flush()
            for work in (movie,dlc):db.add(CatalogueCredit(work_id=work.id,contributor_id=entity,provider='wikidata',source_key=str(work.id),role='actor'))
            await db.commit()
        self.assertEqual((await self.read(entity)).json()['domains'],{'movie':'available','series':'empty','game':'empty'})
        async with self.Session() as db:
            job=await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.kind==JOB))
            job.expires_at=now()-timedelta(seconds=1);await db.commit()
        result=(await self.read(entity)).json()
        self.assertEqual(result['domains']['game'],'unknown');self.assertTrue(result['pending'])

    async def test_missing_identities_remain_unknown_and_revoked_viewers_cannot_read_shared_evidence(self):
        entity=await self.person(tmdb=False,imdb=False)
        response=await self.read(entity)
        self.assertEqual(response.json()['domains'],dict.fromkeys(('movie','series','game'),'unknown'))
        self.assertFalse(response.json()['pending'])
        self.viewer.games_access=False
        denied=await self.read(entity);self.assertEqual(denied.status_code,403)
        self.assertNotIn('domains',denied.text)
        self.viewer=None
        denied=await self.read(entity);self.assertEqual(denied.status_code,401)
        self.assertNotIn('domains',denied.text)

    async def test_person_background_worker_populates_shared_summaries_once_and_failures_keep_cooldown(self):
        entity=await self.person(imdb=False)
        self.assertTrue((await self.read(entity)).json()['pending'])
        self.key_mock.return_value='fixture'
        profile={'id':777,'combined_credits':{'cast':[{'id':1,'title':'Film','media_type':'movie'}],'crew':[]}}
        with patch('core.tmdb.get_person',AsyncMock(return_value=profile)) as provider:
            await evidence.run_batch(self.Session)
            result=(await self.read(entity)).json()
            self.assertEqual(result['domains'],{'movie':'available','series':'empty','game':'unknown'})
            self.assertFalse(result['pending'])
            self.assertEqual((await self.read(entity,'staff')).json()['domains']['movie'],'empty')
            await evidence.run_batch(self.Session)
            provider.assert_awaited_once()
        async with self.Session() as db:
            job=await db.scalar(select(ContributorEvidence));job.payload=None;job.attempts=5
            job.expires_at=now()+timedelta(hours=6);await db.commit()
        result=(await self.read(entity)).json()
        self.assertEqual(result['domains']['movie'],'unknown');self.assertFalse(result['pending'])
        async with self.Session() as db:
            self.assertIsNone((await db.scalar(select(ContributorEvidence))).next_attempt_at)

    async def test_company_background_verification_is_distinct_from_search_sampling(self):
        async with self.Session() as db:
            company=CatalogueEntity(kind='organization',name='Verified company');db.add(company);await db.flush()
            db.add(CatalogueIdentity(entity_id=company.id,namespace='tmdb.company',external_id='888',source='tmdb'))
            await db.commit();entity=company.id
        self.assertTrue((await self.read(entity,'studio')).json()['pending'])
        self.key_mock.return_value='fixture'
        with patch('core.tmdb.discover_movies',AsyncMock(return_value={'results':[],'total_results':0})), \
             patch('core.tmdb.discover_shows',AsyncMock(return_value={'results':[],'total_results':0})):
            await evidence.run_batch(self.Session)
        result=(await self.read(entity,'studio')).json()
        self.assertEqual(result['domains'],{'movie':'empty','series':'empty','game':'unknown'})
        self.assertFalse(result['pending'])
