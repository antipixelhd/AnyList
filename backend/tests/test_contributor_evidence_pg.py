"""Durable admission, leases, retries and filtered local evidence in disposable PG."""
import asyncio
import importlib
import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import event, func, select, update

import test_statistics_pg as fixture
from core import contributor_evidence as evidence
from models.catalogue import CatalogueEntity, CatalogueCredit, ContributorEvidence
from test_contributor_evidence import FILTERS, film


@unittest.skipUnless(fixture.URL, 'Requires explicit disposable PostgreSQL')
class EvidenceDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixture.StatisticsDatabaseTests.asyncSetUp(self)

    async def asyncTearDown(self):
        await fixture.StatisticsDatabaseTests.asyncTearDown(self)

    async def queue(self, native=429, filters=None):
        key, config = evidence.evidence_key('studio',native,filters or FILTERS,True)
        async with self.Session() as db:
            result = await evidence.cached_evidence(db,{key:config},self.user_id)
        return key,result

    async def test_cached_summaries_skip_payload_transfer_and_observe_policy_and_expiry(self):
        evidence._summary_cache._store.clear()
        key, config = evidence.evidence_key('person', 888, FILTERS, True)
        expires = evidence.now() + timedelta(days=1)
        async with self.Session() as db, db.begin():
            db.add(ContributorEvidence(kind=key[0], native_id=key[1], variant=key[2], config=config,
                payload={'cast': [film(runtime=12)], 'complete': True}, expires_at=expires))
        statements = []
        def record(conn, cursor, statement, parameters, context, many):
            statements.append(statement)
        async def read(filters=FILTERS):
            statements.clear()
            async with self.Session() as db:
                rows, _ = await evidence.cached_evidence(db, {key: config}, self.user_id, enqueue=False,
                    summaries={key: (('actor', filters, True),)})
            return rows[key]
        event.listen(self.engine.sync_engine, 'before_cursor_execute', record)
        try:
            payload = await read()
            self.assertEqual(len(statements), 2)
            self.assertEqual(evidence.work_summary(payload, 'actor', FILTERS, True)['works'], 0)
            payload = await read()
            self.assertEqual(len(statements), 1)
            self.assertNotIn('.payload', statements[0])
            self.assertIn('_cached_summaries', payload)
            # The request owns its tiny summary even if the shared TTL expires.
            evidence._summary_cache._store.clear()
            self.assertEqual(evidence.work_summary(payload, 'actor', FILTERS, True)['works'], 0)
            permissive = {**FILTERS, 'min_runtime': 0}
            self.assertIsNone(evidence.work_summary(payload, 'actor', permissive, True))
            payload = await read(permissive)
            self.assertEqual(len(statements), 2)
            self.assertEqual(evidence.work_summary(payload, 'actor', permissive, True)['works'], 1)
            async with self.Session() as db, db.begin():
                await db.execute(update(ContributorEvidence).where(ContributorEvidence.native_id == 888).values(
                    expires_at=evidence.now() - timedelta(seconds=1)))
            payload = await read(permissive)
            self.assertEqual(len(statements), 2)
            self.assertFalse(payload['_fresh'])
            self.assertNotIn('_cached_summaries', payload)
            self.assertEqual(evidence.work_summary(payload, 'actor', permissive, True)['works'], 1)
        finally:
            event.remove(self.engine.sync_engine, 'before_cursor_execute', record)

    async def test_migration_round_trip_and_cross_request_deduplication(self):
        async with self.engine.begin() as conn:
            def migrate(sync):
                module=importlib.import_module('migrations.versions.mt052_contributor_evidence')
                with Operations.context(MigrationContext.configure(sync)):
                    module.downgrade();module.upgrade()
            await conn.run_sync(migrate)
        await asyncio.gather(*(self.queue() for _ in range(5)))
        async with self.Session() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(ContributorEvidence)),1)
            row=await db.scalar(select(ContributorEvidence))
            self.assertNotIn('key',row.config)

    async def test_mixed_people_reuse_each_candidates_requested_role_summary(self):
        evidence._summary_cache._store.clear()
        jobs, summaries = {}, {}
        async with self.Session() as db, db.begin():
            for native, section in ((881, 'actor'), (882, 'staff')):
                key, config = evidence.evidence_key('person', native, FILTERS, True)
                jobs[key] = config
                summaries[key] = ((section, FILTERS, True),)
                db.add(ContributorEvidence(kind=key[0], native_id=key[1], variant=key[2], config=config,
                    payload={'cast': [film()], 'crew': [film(job='Director')], 'complete': True},
                    expires_at=evidence.now() + timedelta(days=1)))
        async with self.Session() as db:
            rows, _ = await evidence.cached_evidence(db, jobs, self.user_id, summaries=summaries)
            for key, ((section, filters, show_anime),) in summaries.items():
                self.assertEqual(evidence.work_summary(rows[key], section, filters, show_anime)['works'], 1)
        async with self.Session() as db:
            rows, _ = await evidence.cached_evidence(db, jobs, self.user_id, summaries=summaries)
            self.assertTrue(all('_cached_summaries' in row for row in rows.values()))

    async def test_parallel_workers_share_lease_and_persist_results_without_importing_titles(self):
        key,_=await self.queue()
        started,release=asyncio.Event(),asyncio.Event()
        async def load(job,provider_key):
            started.set();await release.wait()
            return {'works':[film()], 'complete':True}
        with patch.object(evidence,'load_evidence',AsyncMock(side_effect=load)) as provider,patch.object(
            evidence.settings_store,'get_user_tmdb_key',AsyncMock(return_value='private-test-key')):
            first=asyncio.create_task(evidence.run_batch(self.Session))
            await started.wait()
            self.assertEqual(await evidence.run_batch(self.Session),0)
            release.set();await first
            self.assertEqual(provider.await_count,1)
        async with self.Session() as db:
            cached,pending=await evidence.cached_evidence(db,{key:evidence.evidence_key('studio',429,FILTERS,True)[1]},self.user_id)
            self.assertFalse(pending)
            self.assertEqual(evidence.work_summary(cached[key],'studio',FILTERS,True)['works'],1)
            self.assertEqual(await db.scalar(select(func.count()).select_from(CatalogueEntity).where(CatalogueEntity.kind=='movie')),1)

    async def test_negative_evidence_is_shared_until_expiry_then_admitted_once(self):
        key,_=await self.queue(777)
        async with self.Session() as db,db.begin():
            await db.execute(update(ContributorEvidence).where(ContributorEvidence.native_id==777).values(
                payload={'works':[],'complete':True,'scope':'eligibility_sample'},
                expires_at=evidence.now()+timedelta(days=1),next_attempt_at=None))
        _,(cached,pending)=await self.queue(777,{**FILTERS,'excluded_genres':[99], 'min_popularity':1000})
        self.assertFalse(pending)
        self.assertEqual(evidence.work_summary(cached[key],'studio',FILTERS,True)['works'],0)
        async with self.Session() as db,db.begin():
            self.assertEqual(await db.scalar(select(func.count()).select_from(ContributorEvidence)),1)
            await db.execute(update(ContributorEvidence).where(ContributorEvidence.native_id==777).values(
                expires_at=evidence.now()-timedelta(seconds=1)))
        _,(cached,pending)=await self.queue(777)
        self.assertTrue(pending)
        self.assertFalse(cached[key]['_fresh'])
        async with self.Session() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(ContributorEvidence)),1)

    async def test_failure_is_unknown_and_does_not_erase_previous_evidence_or_retry_on_each_search(self):
        key,_=await self.queue()
        with patch.object(evidence,'load_evidence',AsyncMock(side_effect=RuntimeError('private failure'))),patch.object(
            evidence.settings_store,'get_user_tmdb_key',AsyncMock(return_value='key')):
            await evidence.run_batch(self.Session)
        async with self.Session() as db:
            row=await db.get(ContributorEvidence,key)
            self.assertIsNone(row.payload)
            self.assertEqual(row.attempts,1)
            scheduled=row.next_attempt_at
        _,(_,pending)=await self.queue()
        self.assertFalse(pending)
        async with self.Session() as db:
            self.assertEqual((await db.get(ContributorEvidence,key)).next_attempt_at,scheduled)
            await db.execute(update(ContributorEvidence).values(payload={'works':[film()],'complete':True},
                expires_at=evidence.now()-timedelta(seconds=1),next_attempt_at=evidence.now()))
            await db.commit()
        with patch.object(evidence,'load_evidence',AsyncMock(side_effect=RuntimeError())),patch.object(
            evidence.settings_store,'get_user_tmdb_key',AsyncMock(return_value='key')):
            await evidence.run_batch(self.Session)
        async with self.Session() as db:
            self.assertEqual((await db.get(ContributorEvidence,key)).payload['works'][0]['id'],1)

    async def test_queue_limit_and_shared_raw_filter_evidence(self):
        with patch.object(evidence,'MAX_PENDING',2):
            await self.queue(1);await self.queue(2);await self.queue(3)
        async with self.Session() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(ContributorEvidence)),2)
        await self.queue(1,{**FILTERS, 'excluded_genres':[99]})
        async with self.Session() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(ContributorEvidence)),2)

    async def test_admission_contention_keeps_refresh_pending_until_job_can_be_queued(self):
        async with self.Session() as owner,owner.begin():
            await owner.scalar(select(func.pg_advisory_xact_lock(724052)))
            _,(_,pending)=await self.queue()
            self.assertTrue(pending)
            self.assertEqual(await owner.scalar(select(func.count()).select_from(ContributorEvidence)),0)
        await self.queue()
        async with self.Session() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(ContributorEvidence)),1)

    async def test_filtered_local_works_not_unfiltered_counts_establish_results(self):
        async with self.Session() as db:
            work=await db.scalar(select(CatalogueEntity).where(CatalogueEntity.kind=='movie'))
            person=await db.scalar(select(CatalogueEntity).where(CatalogueEntity.kind=='person'))
            rows=[{'key':f'catalogue:{person.id}'}]
            work.attributes = {**work.attributes, 'runtime': 2}
            await db.flush()
            self.assertEqual(await evidence.local_evidence(db,rows,'actor',FILTERS,True),{})
            work.attributes={'vote_count':500,'runtime':30,'release_date':'2025-01-01'}
            await db.commit()
            self.assertEqual((await evidence.local_evidence(db,rows,'actor',FILTERS,True))[rows[0]['key']]['works'],1)
            self.assertEqual(await evidence.local_evidence(db,rows,'actor',{**FILTERS, 'min_runtime':1000},True),{})

    async def test_existence_preserves_adult_anime_genre_roles_and_alias_rules(self):
        async with self.Session() as db:
            work = await db.scalar(select(CatalogueEntity).where(CatalogueEntity.kind == 'movie'))
            person = await db.scalar(select(CatalogueEntity).where(CatalogueEntity.kind == 'person'))
            credit = await db.scalar(select(CatalogueCredit).where(CatalogueCredit.contributor_id == person.id))
            rows = [{'key': f'catalogue:{person.id}'}]
            base = {'runtime': 100, 'release_date': '2025-01-01'}
            for data in ({'adult': True}, {'genre_ids': [16], 'original_language': 'ja'},
                         {'genres': ['animation'], 'origin_country': ['jp']},
                         {'genre_ids': None, 'genres': ['Animation'], 'original_language': 'ja'},
                         {'genres': [{'name': 'Animation'}], 'production_countries': [{'iso_3166_1': 'JP'}]}):
                work.attributes = {**base, **data}
                await db.flush()
                self.assertEqual(await evidence.local_evidence(db, rows, 'actor', FILTERS, False), {})
            work.attributes = {**base, 'genre_ids': [18]}
            await db.flush()
            self.assertEqual(await evidence.local_evidence(db, rows, 'actor',
                {**FILTERS, 'excluded_genres': [18]}, True), {})
            work.attributes = base
            credit.character_label = 'Self (archive footage)'
            await db.flush()
            self.assertEqual(await evidence.local_evidence(db, rows, 'actor', FILTERS, True), {})
            credit.character_label = 'A character'
            credit.role = 'director'
            credit.role_label = 'Director'
            await db.flush()
            self.assertEqual(await evidence.local_evidence(db, rows, 'actor', FILTERS, True), {})
            self.assertTrue(await evidence.local_evidence(db, rows, 'staff', FILTERS, True))
            alias = CatalogueEntity(kind='person', name='Alias', canonical_person_id=person.id, attributes={})
            db.add(alias)
            await db.flush()
            credit.role, credit.role_label = 'contributor', 'contributor'
            db.add(CatalogueCredit(work_id=work.id, contributor_id=alias.id,
                provider='tmdb', source_key='alias-director', role='director', role_label='Director'))
            await db.flush()
            self.assertTrue(await evidence.local_evidence(db, rows, 'staff', FILTERS, True))

    async def test_existence_returns_one_witness_instead_of_whole_credit_counts(self):
        async with self.Session() as db:
            person = await db.scalar(select(CatalogueEntity).where(CatalogueEntity.kind == 'person'))
            for index in range(30):
                work = CatalogueEntity(kind='movie', name=f'Work {index}', attributes={'runtime': 100})
                db.add(work)
                await db.flush()
                db.add(CatalogueCredit(work_id=work.id, contributor_id=person.id,
                    provider='tmdb', source_key=f'witness-{index}', role='actor'))
            await db.flush()
            rows = [{'key': f'catalogue:{person.id}'}]
            found = await evidence.local_evidence(db, rows, 'actor', FILTERS, True)
            self.assertEqual(found, {rows[0]['key']: {'works': 1, 'latest': ''}})
