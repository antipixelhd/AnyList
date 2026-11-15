"""Active query ownership and shared identity verification in disposable PG."""
import asyncio
import importlib
import re
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import func, select

import test_statistics_pg as fixture
from core import search_demand as demand
from core.catalogue_providers import ProviderError
from core.game_imdb_source import IMDbGameSource
from core.game_imdb_person import JOB
from core.game_people_discovery import GamePeopleDiscovery
from core.game_work import choose, job_order, job_key
from models.catalogue import MetadataSnapshot, CatalogueIdentity, CatalogueEntity, ContributorEvidence
from models.search_demand import SearchDemand


@unittest.skipUnless(fixture.URL, 'Requires disposable PostgreSQL')
class SearchDemandTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.StatisticsDatabaseTests.asyncSetUp
    asyncTearDown = fixture.StatisticsDatabaseTests.asyncTearDown

    async def begin(self, viewer, client, generation, term='Beyond Two'):
        async with self.Session() as db:
            return await demand.begin(db, viewer, client, generation, term, 'all')

    async def mark(self, context, targets):
        with demand.context(context):
            async with self.Session() as db:
                await demand.mark(db, targets)
                await db.commit()

    async def active(self, target):
        async with self.Session() as db:
            return bool(await db.scalar(select(demand.active(target))))

    async def test_polls_renew_the_lease_only_when_needed_without_losing_targets(self):
        started = demand.now()
        with patch.object(demand, 'now', return_value=started):
            context = await self.begin(1, 'lease-window', 1)
            await self.mark(context, ['shared'])
        halfway = started + demand.LEASE / 2
        with patch.object(demand, 'now', return_value=halfway - timedelta(seconds=1)):
            await self.begin(1, 'lease-window', 1)
        async with self.Session() as db:
            row = await db.get(SearchDemand, context[0])
            self.assertEqual(row.expires_at, started + demand.LEASE)
        with patch.object(demand, 'now', return_value=halfway):
            await self.begin(1, 'lease-window', 1)
        async with self.Session() as db:
            row = await db.get(SearchDemand, context[0])
            self.assertEqual(row.expires_at, halfway + demand.LEASE)
            self.assertEqual(row.targets, ['shared'])

    async def test_query_replacement_cannot_demote_another_viewer_or_reactivate_late_work(self):
        a = await self.begin(1, 'window-a', 1)
        b = await self.begin(2, 'window-b', 1)
        await asyncio.gather(self.mark(a, ['shared', 'old']), self.mark(b, ['shared']))
        newer = await self.begin(1, 'window-a', 2, 'New query')
        await self.mark(a, ['late-old'])
        await self.begin(1, 'window-a', 1)
        await self.mark(newer, ['new'])
        self.assertFalse(await self.active('old'))
        self.assertFalse(await self.active('late-old'))
        self.assertTrue(await self.active('shared'))
        async with self.Session() as db:
            await demand.release(db, 1, 'window-a', 1)
        self.assertTrue(await self.active('new'))
        async with self.Session() as db:
            await demand.release(db, 2, 'window-b', 1)
        self.assertFalse(await self.active('shared'))

    async def test_simultaneous_sections_union_targets_and_polls_do_not_duplicate_them(self):
        context = await self.begin(1, 'same-window', 1)
        await asyncio.gather(self.mark(context, ['people', 'shared']), self.mark(context, ['studio', 'shared']))
        await self.mark(context, ['people', 'studio'])
        async with self.Session() as db:
            row = await db.get(SearchDemand, context[0])
            self.assertEqual(set(row.targets), {'people', 'studio', 'shared'})
            self.assertEqual(len(row.targets), 3)
            row.expires_at = demand.now() - timedelta(seconds=1)
            await db.commit()
        self.assertFalse(await self.active('people'))

    async def test_release_before_request_is_a_tombstone_and_same_query_can_reopen(self):
        async with self.Session() as db:
            await demand.release(db, 1, 'window', 1)
        await self.mark(await self.begin(1, 'window', 1), ['late'])
        self.assertFalse(await self.active('late'))
        await self.mark(await self.begin(1, 'window', 2), ['reopened'])
        self.assertTrue(await self.active('reopened'))

    async def test_existing_shared_jobs_are_promoted_and_fair_between_viewers(self):
        targets = [job_key('imdb', JOB, native) for native in ('nm1', 'nm2')]
        async with self.Session() as db:
            db.add_all([MetadataSnapshot(provider='imdb', kind=JOB, external_id=native,
                next_attempt_at=demand.now()) for native in ('nm1', 'nm2', 'nm3')])
            await db.commit()
        a = await self.begin(1, 'a', 1)
        b = await self.begin(2, 'b', 1)
        await self.mark(a, [targets[1]])
        await self.mark(b, [targets[0]])
        async with self.Session() as db:
            natives = list(await db.scalars(select(MetadataSnapshot.external_id).order_by(job_order(), MetadataSnapshot.external_id)))
            searches = await demand.subscribers(db, targets)
        self.assertEqual(set(natives[:2]), {'nm1', 'nm2'})
        tickets = [SimpleNamespace(id=str(i), provider='imdb', priority=2, owner='system',
            scopes=[target], created_at=demand.now()) for i, target in enumerate(targets)]
        selected = choose(tickets, {}, {'imdb:1:search-user:1':demand.now()}, demand.now(), searches)
        self.assertEqual(selected[2], 'imdb:1:search-user:2')

    async def test_negative_candidates_share_one_check_and_never_admit_a_career(self):
        source = IMDbGameSource()
        source.people_candidates = AsyncMock(return_value=[{'id':'nm1', 'name':'Beyond Two', 'image':None}])
        service = GamePeopleDiscovery(self.Session, source=source)
        calls = []
        async def query(_source, text, variables):
            calls.append(text)
            return {alias:{'id':native, 'credits':{'total':0, 'edges':[],
                'pageInfo':{'hasNextPage':False, 'endCursor':None}}}
                for alias, native in re.findall(r'(p\d+):name\(id:"(nm\d+)"\)', text)}
        with patch.object(IMDbGameSource, 'query', query):
            rows = await asyncio.gather(service.search('Beyond'), service.search('Beyond Two'))
        self.assertEqual(len(calls), 1)
        self.assertTrue(all(not row['pending'] and not row['partial'] and not row['ids'] for row in rows))
        async with self.Session() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(MetadataSnapshot).where(MetadataSnapshot.kind == JOB)), 0)
            self.assertEqual(await db.scalar(select(func.count()).select_from(CatalogueIdentity).where(CatalogueIdentity.namespace == 'imdb.person')), 0)

    async def test_provider_failure_is_unknown_and_does_not_cache_a_negative(self):
        from core.game_people_presence import presence
        with patch.object(IMDbGameSource, 'query', AsyncMock(side_effect=ProviderError('timeout'))):
            with self.assertRaises(ProviderError):
                await presence(self.Session, 'nm10')
        async with self.Session() as db:
            row = await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.kind == 'game_person_presence'))
            self.assertIsNone(row.payload)
            self.assertIsNotNone(row.error_code)

    async def test_fresh_complete_source_headers_skip_another_presence_call(self):
        source = IMDbGameSource()
        source.people_candidates = AsyncMock(return_value=[{'id':'nm1','name':'Screen performer','image':None}])
        async with self.Session() as db:
            db.add(MetadataSnapshot(provider='imdb',kind=JOB,external_id='nm1',
                coverage={'complete':True,'total':0}, expires_at=demand.now()+timedelta(days=1)))
            await db.commit()
        with patch('core.game_people_presence.presence', AsyncMock(side_effect=AssertionError('Fresh source must be reused'))):
            result = await GamePeopleDiscovery(self.Session, source=source).search('Screen performer')
        self.assertFalse(result['pending'])
        self.assertFalse(result['ids'])

    async def test_full_queue_still_accepts_another_subscriber_to_existing_work(self):
        source = SimpleNamespace(people_candidates=AsyncMock(return_value=[{'id':'nm1','name':'Shared performer','image':None}]))
        service = GamePeopleDiscovery(self.Session, source=source)
        first = await service.search('Shared performer')
        self.assertTrue(first['pending'])
        with patch('core.game_people_discovery.MAX_PENDING',1):
            repeated = await service.search('Shared perform')
        self.assertTrue(repeated['pending'])
        self.assertFalse(repeated['partial'])
        async with self.Session() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(MetadataSnapshot).where(MetadataSnapshot.kind == JOB)),1)

    async def test_cancelled_reader_does_not_cancel_shared_identity_checks(self):
        from core.game_people_presence import presence
        started, finish = asyncio.Event(), asyncio.Event()
        queries = []
        async def query(_source, text, variables):
            queries.append(text);started.set();await finish.wait()
            return {alias:{'id':native, 'credits':{'total':0,'edges':[],
                'pageInfo':{'hasNextPage':False,'endCursor':None}}}
                for alias,native in re.findall(r'(p\d+):name\(id:"(nm\d+)"\)',text)}
        with patch.object(IMDbGameSource,'query',query):
            departed = asyncio.create_task(presence(self.Session,'nm1'))
            retained = asyncio.create_task(presence(self.Session,'nm1'))
            other = asyncio.create_task(presence(self.Session,'nm2'))
            await asyncio.wait_for(started.wait(), 3)
            departed.cancel()
            with self.assertRaises(asyncio.CancelledError):await departed
            finish.set()
            self.assertEqual([r['total'] for r in await asyncio.gather(retained,other)],[0,0])
        self.assertEqual(sum(text.count('name(id:"nm1")') for text in queries),1)
        self.assertEqual(sum(text.count('name(id:"nm2")') for text in queries),1)

    async def test_batch_collector_combines_different_identities(self):
        from core.game_people_presence import PresenceBatch
        queries = []
        async def query(_source,text,variables):
            queries.append(text)
            return {alias:{'id':native,'credits':{'total':0,'edges':[],
                'pageInfo':{'hasNextPage':False,'endCursor':None}}}
                for alias,native in re.findall(r'(p\d+):name\(id:"(nm\d+)"\)',text)}
        with patch.object(IMDbGameSource,'query',query):
            batch=PresenceBatch(self.Session)
            await asyncio.gather(batch.get('nm1'),batch.get('nm2'))
        self.assertEqual(len(queries),1)

    async def test_bound_positive_sample_is_usable_before_the_career_and_preserves_roles(self):
        from core.game_search_samples import sample_matches
        from test_game_imdb_person_pg import credit
        async with self.Session() as db:
            person = CatalogueEntity(kind='person', name='Early performer', attributes={})
            game = CatalogueEntity(kind='game', name='Verified game', attributes={'game_class':'game', 'igdb_id':'77'})
            db.add_all([person, game]); await db.flush()
            db.add_all([CatalogueIdentity(entity_id=person.id, namespace='imdb.person', external_id='nm1', source='imdb'),
                CatalogueIdentity(entity_id=game.id, namespace='igdb.game', external_id='77', source='igdb'),
                CatalogueIdentity(entity_id=game.id, namespace='imdb.title', external_id='tt1', source='reviewed',
                    evidence={'title_type':'videoGame','igdb_id':'77'})])
            await db.commit()
            samples = {'nm1':{'rows':[credit('tt1'), credit('tt1', role='composer'), credit('tt1',role='thanks')], 'total':300, 'complete':False}}
            result = await sample_matches(db, samples, 'people')
            self.assertEqual([r['name'] for r in result['actor']], ['Early performer'])
            self.assertEqual(result['staff'][0]['roles'], ['Composer'])
            self.assertFalse((await sample_matches(db, samples, 'people', result))['actor'])
            binding = await db.scalar(select(CatalogueIdentity).where(CatalogueIdentity.namespace == 'imdb.title'))
            binding.evidence = {**binding.evidence, 'mapping_state':'stale'}
            await db.commit()
            self.assertFalse((await sample_matches(db, samples, 'people'))['actor'])

    async def test_active_studio_evidence_precedes_abandoned_prefix_jobs(self):
        from core import contributor_evidence as evidence
        keys = [evidence.evidence_key('studio', native, {}, True) for native in (101, 102, 103)]
        async with self.Session() as db:
            for offset, (key, config) in enumerate(keys):
                db.add(ContributorEvidence(kind=key[0], native_id=key[1], variant=key[2], config=config,
                    next_attempt_at=demand.now() - timedelta(seconds=20-offset)))
            await db.commit()
        await self.mark(await self.begin(self.user_id, 'studio-window', 1), [evidence.demand_scope(*keys[2][0])])
        acquired = []
        async def load(job, key):
            acquired.append(job['native_id'])
            return {'works':[], 'complete':True}
        with patch.object(evidence, 'load_evidence', load), patch.object(evidence.settings_store,
            'get_user_tmdb_key', AsyncMock(return_value='fixture')):
            self.assertEqual(await evidence.run_batch(self.Session), 2)
        self.assertEqual(acquired[0], 103)

    async def test_weighted_people_batch_gives_competing_viewer_the_next_turn(self):
        from core import contributor_evidence as evidence
        keys = [evidence.evidence_key('person', native, {}, True) for native in range(201, 209)]
        studio = evidence.evidence_key('studio', 301, {}, True)
        async with self.Session() as db:
            for offset, (key, config) in enumerate([*keys, studio]):
                db.add(ContributorEvidence(kind=key[0], native_id=key[1], variant=key[2], config=config,
                    next_attempt_at=demand.now() - timedelta(seconds=20-offset)))
            await db.commit()
        await self.mark(await self.begin(1, 'people-window', 1), [evidence.demand_scope(*key) for key, _ in keys])
        await self.mark(await self.begin(2, 'other-studio-window', 1), [evidence.demand_scope(*studio[0])])
        acquired = []
        async def load(job, key):
            acquired.append(job['native_id'])
            return {'works': [], 'complete': True}
        with patch.object(evidence, 'load_evidence', load), patch.object(evidence.settings_store,
                'get_user_tmdb_key', AsyncMock(return_value='fixture')):
            self.assertEqual(await evidence.run_batch(self.Session), 4)
            self.assertEqual(set(acquired), {201, 202, 203, 204})
            acquired.clear()
            self.assertEqual(await evidence.run_batch(self.Session), 3)
        self.assertEqual(acquired[0], 301)
        self.assertEqual(len(set(acquired)), 3)

    async def test_migration_round_trip(self):
        migration = importlib.import_module('migrations.versions.mt094_search_demand')
        def run(conn):
            with Operations.context(MigrationContext.configure(conn)):
                migration.downgrade()
                migration.upgrade()
        async with self.engine.begin() as conn:
            await conn.run_sync(run)
        await self.mark(await self.begin(1, 'window', 1), ['after-upgrade'])
        self.assertTrue(await self.active('after-upgrade'))
