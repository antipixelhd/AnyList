"""Durable admission, leases, retries and filtered local evidence in disposable PG."""
import asyncio
import importlib
import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import event, func, select, update

import test_statistics_pg as fixture
from core import contributor_evidence as evidence
from models.catalogue import CatalogueEntity, CatalogueCredit, ContributorEvidence
from test_contributor_evidence import FILTERS, film


@unittest.skipUnless(fixture.URL, 'Requires explicit disposable PostgreSQL')
class EvidenceDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_uses_tmdb_shortlist_when_local_credits_are_nonprincipal(self):
        from core import catalogue, contributor_search as search
        from core.catalogue_normalize import normalize_tmdb
        from models import UserProfileData

        native = 19292
        name = 'Adam Sandler'
        viewer = SimpleNamespace(id=self.user_id)
        with patch.object(search.settings_store, 'get_user_tmdb_key', AsyncMock(return_value='key')), \
             patch.object(search.tmdb, 'search_people', AsyncMock(return_value={'results': [{
                 'id': native, 'name': name, 'known_for_department': 'Acting',
                 'known_for': [film(id=42, genre_ids=[18])],
             }]})) as discover, \
             patch.object(search.tmdb, 'get_person', AsyncMock(return_value={
                 'id': native, 'combined_credits': {'cast': [film(character='Self (archive footage)')], 'crew': []},
             })) as filmography:
            async with self.Session() as db:
                for title in (
                    {'id': 13, 'title': 'Marty Supreme', 'credits': {'crew': [
                        {'id': native, 'name': name, 'job': 'Thanks', 'credit_id': 'thanks'},
                    ]}},
                    {'id': 14, 'name': 'The Oscars', 'aggregate_credits': {'cast': [
                        {'id': native, 'name': name, 'order': 1810, 'roles': [
                            {'character': 'Self', 'credit_id': 'guest'}]},
                    ]}},
                ):
                    await catalogue.ingest_document(
                        db, normalize_tmdb(title, 'movie' if 'title' in title else 'series'), 'tmdb')
                await db.execute(update(CatalogueCredit).where(CatalogueCredit.source_key == 'guest')
                    .values(attributes={'principal': False}))
                profile = await db.get(UserProfileData, self.user_id)
                profile.excluded_categories = []
                await db.commit()
                search._candidate_cache._store.clear()
                self.assertEqual((await search.local_matches(db, name, 'actor'))['actor'], [])

                for _ in range(2):
                    groups, failed, pending = await search.screen_matches(db, viewer, name, 'actor')
                    self.assertEqual([row['name'] for row in groups['actor']], [name])
                    self.assertFalse(failed or pending)
                discover.assert_awaited_once()
                filmography.assert_not_awaited()
                key, _ = evidence.evidence_key('person', native, FILTERS, True)
                self.assertIsNone(await db.get(ContributorEvidence, key))

                profile.excluded_categories = [18]
                await db.commit()
                groups, _, pending = await search.screen_matches(db, viewer, name, 'actor', {})
                self.assertEqual(groups['actor'], [])
                self.assertTrue(pending)
                filmography.assert_not_awaited()

            await evidence.run_batch(self.Session)
            async with self.Session() as db:
                for _ in range(2):
                    groups, _, pending = await search.screen_matches(db, viewer, name, 'actor', {})
                    self.assertEqual(groups['actor'], [])
                    self.assertFalse(pending)
            filmography.assert_awaited_once()

    async def test_studio_witness_parks_sample_and_another_viewer_resumes_shared_pages(self):
        from core import studio_discovery
        from models import User, UserProfileData
        async with self.Session() as db:
            profile = await db.scalar(select(UserProfileData).where(UserProfileData.user_id == self.user_id))
            profile.excluded_categories = []
            profile.minimum_movie_runtime = 30
            other = User(email='witness-reader@example.test', username='witness-reader', api_key='witness-reader-key')
            db.add(other)
            await db.commit()
        key, _ = await self.queue(771)
        movies = AsyncMock(return_value={'results': [film(runtime=90, genre_ids=[99], popularity=80)], 'total_pages': 2})
        shows = AsyncMock(return_value={'results': [], 'total_pages': 0})
        policy = {'studio_filters': FILTERS, 'show_anime': True}
        async def search(filters=FILTERS, viewer=None):
            async with self.Session() as db:
                return await studio_discovery.verified_studios(db, viewer or SimpleNamespace(id=self.user_id),
                    [{'key': 'tmdb:771', '_native': 771, 'name': 'Witness studio', 'url': '/studio/tmdb:771'}], 'key',
                    policy={**policy, 'studio_filters': filters})
        with patch.object(evidence.tmdb, 'discover_movies', movies), patch.object(evidence.tmdb, 'discover_shows', shows), \
             patch.object(evidence.settings_store, 'get_user_tmdb_key', AsyncMock(return_value='key')):
            await evidence.run_batch(self.Session)
            async with self.Session() as db:
                row = await db.get(ContributorEvidence, key)
                self.assertFalse(row.payload['complete'])
                self.assertEqual(row.payload['pages'], {'movie': 2, 'series': None})
                self.assertIsNone(row.next_attempt_at)
            # Fresh partial summaries must stay small without claiming complete
            # coverage or queueing unnecessary pages for the ordinary viewer.
            for _ in range(2):
                rows, pending = await search()
                self.assertEqual(len(rows), 1)
                self.assertFalse(pending)
            stricter = {**FILTERS, 'excluded_genres': [99]}
            ordinary, strict = await asyncio.gather(search(), search(stricter, other))
            self.assertEqual(len(ordinary[0]), 1)
            self.assertFalse(strict[0])
            self.assertTrue(strict[1])
            movies.return_value = {'results': [film(id=2, runtime=90, genre_ids=[18], popularity=40)], 'total_pages': 2}
            await asyncio.gather(evidence.run_batch(self.Session), evidence.run_batch(self.Session))
            rows, pending = await search(stricter, other)
            self.assertEqual(len(rows), 1)
            self.assertFalse(pending)
            self.assertEqual(movies.await_count, 2)
            self.assertEqual(shows.await_count, 1)
            self.assertEqual([call.kwargs['page'] for call in movies.await_args_list], [1, 2])
        async with self.Session() as db:
            row = await db.get(ContributorEvidence, key)
            self.assertTrue(row.payload['complete'])
            self.assertEqual(len(row.payload['works']), 2)

    async def test_studio_without_a_witness_finishes_sample_and_reuses_verified_negative(self):
        from core import studio_discovery
        key, _ = await self.queue(772)
        filters = {**FILTERS, 'excluded_genres': [99]}
        provider = AsyncMock(return_value={'results': [film(runtime=90, genre_ids=[99])], 'total_pages': 50})
        async def search():
            async with self.Session() as db:
                return await studio_discovery.verified_studios(db, SimpleNamespace(id=self.user_id),
                    [{'key': 'tmdb:772', '_native': 772, 'name': 'No qualifying works', 'url': '/studio/tmdb:772'}], 'key',
                    policy={'studio_filters': filters, 'show_anime': True})
        with patch.object(evidence.tmdb, 'discover_movies', provider), patch.object(evidence.tmdb, 'discover_shows', provider), \
             patch.object(evidence.settings_store, 'get_user_tmdb_key', AsyncMock(return_value='key')):
            await evidence.run_batch(self.Session)
            self.assertEqual(await search(), ([], True))
            await evidence.run_batch(self.Session)
            self.assertEqual(await search(), ([], False))
            self.assertEqual(await search(), ([], False))
            self.assertEqual(provider.await_count, 4)
        async with self.Session() as db:
            self.assertTrue((await db.get(ContributorEvidence, key)).payload['complete'])

    async def test_verification_admits_four_people_or_two_studios_under_same_request_ceiling(self):
        for kind, natives, expected in (('person', range(781, 786), 4), ('studio', range(791, 796), 2)):
            async with self.Session() as db:
                for native in natives:
                    key, config = evidence.evidence_key(kind, native, FILTERS, True)
                    db.add(ContributorEvidence(kind=key[0], native_id=key[1], variant=key[2], config=config,
                        next_attempt_at=evidence.now()))
                await db.commit()
            acquired = []
            async def load(job, key):
                acquired.append(job['kind'])
                await asyncio.sleep(.01)
                return {'complete': True, 'works': []}
            with patch.object(evidence, 'load_evidence', load), patch.object(evidence.settings_store,
                    'get_user_tmdb_key', AsyncMock(return_value='key')):
                self.assertEqual(await evidence.run_batch(self.Session), expected)
                self.assertEqual(sum(1 if value == 'person' else 2 for value in acquired), 4)
                # Drain this fixture before checking the next media kind.
                while await evidence.run_batch(self.Session):
                    pass

    async def test_failed_partial_sample_keeps_cooldown_and_unknown_coverage(self):
        key, _ = await self.queue(773)
        async with self.Session() as db:
            row = await db.get(ContributorEvidence, key)
            row.payload = {'works': [film(genre_ids=[99])], 'complete': False, 'pages': {'movie': 2, 'series': None}}
            row.expires_at = evidence.now() + timedelta(days=7)
            row.attempts = 4
            await db.commit()
        with patch.object(evidence, 'load_evidence', AsyncMock(side_effect=TimeoutError)), patch.object(
                evidence.settings_store, 'get_user_tmdb_key', AsyncMock(return_value='key')):
            await evidence.run_batch(self.Session)
        _, (views, pending) = await self.queue(773)
        self.assertFalse(pending)
        self.assertIsNone(evidence.work_summary(views[key], 'studio', {**FILTERS, 'excluded_genres': [99]}, True))
        async with self.Session() as db:
            row = await db.get(ContributorEvidence, key)
            self.assertEqual(row.attempts, 5)
            self.assertIsNone(row.next_attempt_at)
            self.assertGreater(row.expires_at, evidence.now() + timedelta(hours=5))

    async def test_studios_with_only_one_media_page_left_fill_four_provider_slots(self):
        async with self.Session() as db:
            for native in range(801, 805):
                key, config = evidence.evidence_key('studio', native, FILTERS, True)
                db.add(ContributorEvidence(kind=key[0], native_id=key[1], variant=key[2], config=config,
                    payload={'works': [], 'complete': False, 'pages': {'movie': 2, 'series': None}},
                    expires_at=evidence.now() + timedelta(days=1), next_attempt_at=evidence.now()))
            await db.commit()
        arrived, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def movies(**options):
            calls.append(options)
            if len(calls) == 4:
                arrived.set()
            await release.wait()
            return {'results': [], 'total_pages': 2}
        with patch.object(evidence.tmdb, 'discover_movies', movies), patch.object(evidence.tmdb, 'discover_shows',
                AsyncMock(side_effect=AssertionError('Finished series pages must not be fetched'))), patch.object(
                evidence.settings_store, 'get_user_tmdb_key', AsyncMock(return_value='key')):
            task = asyncio.create_task(evidence.run_batch(self.Session))
            try:
                await asyncio.wait_for(arrived.wait(), 3)
            finally:
                release.set()
                count = await task
            self.assertEqual(count, 4)
        self.assertEqual([call['page'] for call in calls], [2, 2, 2, 2])

    async def test_expired_parked_studio_refreshes_first_pages_without_renewing_old_works(self):
        key, _ = await self.queue(774)
        async with self.Session() as db:
            row = await db.get(ContributorEvidence, key)
            row.payload = {'works': [film(id=1)], 'complete': False, 'pages': {'movie': 2, 'series': None}}
            row.expires_at = evidence.now() - timedelta(seconds=1)
            await db.commit()
        movies = AsyncMock(return_value={'results': [film(id=2)], 'total_pages': 2})
        shows = AsyncMock(return_value={'results': [], 'total_pages': 0})
        with patch.object(evidence.tmdb, 'discover_movies', movies), patch.object(evidence.tmdb, 'discover_shows', shows), \
             patch.object(evidence.settings_store, 'get_user_tmdb_key', AsyncMock(return_value='key')):
            await evidence.run_batch(self.Session)
        self.assertEqual(movies.await_args.kwargs['page'], 1)
        self.assertEqual(shows.await_args.kwargs['page'], 1)
        async with self.Session() as db:
            row = await db.get(ContributorEvidence, key)
            self.assertEqual([work['id'] for work in row.payload['works']], [2])
            self.assertFalse(row.payload['complete'])

    async def test_shared_policy_read_matches_existing_policies_and_is_request_scoped(self):
        from core.catalog_search import result_filters, browse_preferences
        from models import GlobalSettings, User
        from models.profile import UserProfileData
        from models.users import UserSettings
        statements = []
        def record(conn, cursor, statement, parameters, context, many):
            statements.append(statement)
        async with self.Session() as db:
            reader = User(email='policy-reader@example.test', username='policy-reader', api_key='policy-reader-key')
            db.add(reader); await db.flush()
            db.add(UserSettings(user_id=reader.id, preferences={'minimum_popularity': 12}))
            profile = await db.scalar(select(UserProfileData).where(UserProfileData.user_id == self.user_id))
            profile.minimum_movie_runtime = 99
            profile.excluded_categories = [16, 99]
            db.add(UserSettings(user_id=self.user_id, preferences={'minimum_popularity': 65,
                'discovery_tuning': {'search': {'movie': {'min_votes': 0}}}}))
            (await db.get(GlobalSettings, 1)).show_anime = False
            await db.commit()
            for viewer in (None, SimpleNamespace(id=self.user_id), reader):
                filters = await result_filters(db, viewer)
                preferences = await browse_preferences(db, viewer, 'movie', 'search')
                statements.clear()
                event.listen(self.engine.sync_engine, 'before_cursor_execute', record)
                try:
                    policy = await evidence.verification_policy(db, viewer)
                finally:
                    event.remove(self.engine.sync_engine, 'before_cursor_execute', record)
                self.assertEqual(len(statements), 1)
                self.assertEqual(policy, {'filters': filters,
                    'studio_filters': {**filters, 'min_popularity': preferences['min_popularity']}, 'show_anime': False})
            profile.minimum_movie_runtime = 10
            (await db.get(GlobalSettings, 1)).show_anime = True
            await db.commit()
            changed = await evidence.verification_policy(db, SimpleNamespace(id=self.user_id))
            self.assertEqual(changed['filters']['min_runtime'], 10)
            self.assertTrue(changed['show_anime'])

    async def test_concurrent_viewers_keep_distinct_eligibility_with_shared_evidence(self):
        from core import contributor_search as search
        from models import User
        from models.profile import UserProfileData
        key, config = evidence.evidence_key('person', 889, FILTERS, True)
        async with self.Session() as db:
            other = User(email='strict-reader@example.test', username='strict-reader', api_key='strict-reader-key')
            db.add(other); await db.flush()
            db.add(UserProfileData(user_id=other.id, minimum_movie_runtime=200))
            db.add(ContributorEvidence(kind=key[0], native_id=key[1], variant=key[2], config=config,
                payload={'cast': [film(runtime=90)], 'crew': [], 'complete': True},
                expires_at=evidence.now() + timedelta(days=1)))
            await db.commit()
        async def read(viewer):
            async with self.Session() as db:
                policy = await evidence.verification_policy(db, viewer)
                groups = {'actor': [{'key': 'tmdb:889', '_native': 889}], 'staff': [], 'studio': []}
                await search.verified_people(db, viewer, groups, None, {}, policy=policy)
                return groups['actor']
        for _ in range(2):
            ordinary, strict = await asyncio.gather(read(SimpleNamespace(id=self.user_id)), read(other))
            self.assertEqual(len(ordinary), 1)
            self.assertEqual(strict, [])

    async def test_role_projection_preserves_stored_credits_and_other_policy_reads(self):
        evidence._summary_cache._store.clear()
        requests, jobs = {}, {}
        payload = {'cast': [film(runtime=90), film(id=2, runtime=90)], 'crew': [film(job='Director', runtime=90)],
            'complete': True, 'person_identity': {'name': 'Preserved'},
            'domain_availability': {'actor': {'movie': 'available'}}}
        async with self.Session() as db:
            for native, sections in ((881, ('actor',)), (882, ('staff',)), (883, ('actor', 'staff'))):
                key, config = evidence.evidence_key('person', native, FILTERS, True)
                jobs[key] = config
                requests[key] = [(section, FILTERS, True, True) for section in sections]
                db.add(ContributorEvidence(kind=key[0], native_id=key[1], variant=key[2], config=config,
                    payload=payload, expires_at=evidence.now() + timedelta(days=1)))
            await db.commit()
        async with self.Session() as db:
            views, _ = await evidence.cached_evidence(db, jobs, self.user_id, summaries=requests)
            for key, requested in requests.items():
                view = views[key]
                self.assertEqual('cast' in view, any(r[0] == 'actor' for r in requested))
                self.assertEqual('crew' in view, any(r[0] == 'staff' for r in requested))
                self.assertNotIn('person_identity', view)
                for section, filters, anime, existence in requested:
                    self.assertEqual(evidence.work_summary(view, section, filters, anime,
                        existence_only=existence)['works'], 1)
            stored = list(await db.scalars(select(ContributorEvidence)))
            self.assertTrue(all(row.payload == payload for row in stored))
        # Different roles and stricter policies must read their own projection,
        # rather than treating the first viewer's positive as shared eligibility.
        key = next(iter(jobs))
        async with self.Session() as db:
            stricter = {**FILTERS, 'min_runtime': 1000}
            views, _ = await evidence.cached_evidence(db, {key: jobs[key]}, self.user_id,
                summaries={key: [('staff', stricter, True, True)]})
            self.assertIn('crew', views[key])
            self.assertEqual(evidence.work_summary(views[key], 'staff', stricter, True, existence_only=True)['works'], 0)
            full, _ = await evidence.cached_evidence(db, {key: jobs[key]}, self.user_id, enqueue=False)
            self.assertEqual({k: v for k, v in full[key].items() if not k.startswith('_')}, payload)

    async def test_expired_positive_search_evidence_refreshes_without_loading_or_priority(self):
        from core import contributor_search as search,search_demand
        from models.search_demand import SearchDemand
        key,config=evidence.evidence_key('person',887,FILTERS,True)
        async with self.Session() as db:
            db.add(ContributorEvidence(kind=key[0],native_id=key[1],variant=key[2],config=config,
                payload={'cast':[film(runtime=90)],'crew':[],'complete':True},
                expires_at=evidence.now()-timedelta(days=1)))
            await db.commit()
            demand=await search_demand.begin(db,self.user_id,'established-search',1,'performer','movies_series')
        def groups(native=887):
            return {'actor':[{'key':f'tmdb:{native}','name':'Performer','_native':native}], 'staff':[],'studio':[]}
        async with self.Session() as db:
            with patch.object(search,'result_filters',AsyncMock(return_value=FILTERS)),search_demand.context(demand):
                ready=groups();sections={}
                pending=await search.verified_people(db,SimpleNamespace(id=self.user_id),ready,'key',sections)
            self.assertFalse(pending)
            self.assertFalse(sections['actor'])
            self.assertEqual(len(ready['actor']),1)
            row=await db.scalar(select(ContributorEvidence).where(ContributorEvidence.native_id==887))
            self.assertIsNotNone(row.next_attempt_at)
            subscription=await db.get(SearchDemand,demand[0])
            self.assertNotIn(evidence.demand_scope(*key),subscription.targets)
        async with self.Session() as db:
            with patch.object(search,'result_filters',AsyncMock(return_value={**FILTERS,'min_runtime':200})),search_demand.context(demand):
                filtered=groups();sections={}
                pending=await search.verified_people(db,SimpleNamespace(id=self.user_id),filtered,'key',sections)
            self.assertTrue(pending)
            self.assertTrue(sections['actor'])
            self.assertFalse(filtered['actor'])
            subscription=await db.get(SearchDemand,demand[0])
            self.assertIn(evidence.demand_scope(*key),subscription.targets)

    async def test_last_queue_slot_prefers_unknown_over_established_refresh(self):
        from core import contributor_search as search
        key,config=evidence.evidence_key('person',887,FILTERS,True)
        async with self.Session() as db:
            db.add(ContributorEvidence(kind=key[0],native_id=key[1],variant=key[2],config=config,
                payload={'cast':[film(runtime=90)],'crew':[],'complete':True},
                expires_at=evidence.now()-timedelta(days=1)))
            await db.commit()
        groups={'actor':[{'key':f'tmdb:{native}','name':'Performer','_native':native}
            for native in (887,888)],'staff':[],'studio':[]}
        async with self.Session() as db:
            with patch.object(search,'result_filters',AsyncMock(return_value=FILTERS)),patch.object(evidence,'MAX_PENDING',1):
                self.assertTrue(await search.verified_people(db,SimpleNamespace(id=self.user_id),groups,'key',{}))
            self.assertEqual([row['key'] for row in groups['actor']],['tmdb:887'])
            queued=list(await db.scalars(select(ContributorEvidence.native_id).where(
                ContributorEvidence.next_attempt_at.is_not(None))))
            self.assertEqual(queued,[888])

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
