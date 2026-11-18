"""Fresh game-only actors/crew are found through exact source identities."""
import asyncio
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import func, select

import test_game_contributor_search_pg as fixture
from test_game_imdb_person_pg import credit, page
from test_game_imdb_discovery_pg import outcome
from core import catalogue
from core.game_imdb_discovery import GamePersonDiscovery, JOB as DISCOVERY_JOB
from core.game_imdb_enrichment import now, snapshot
from core.game_imdb_person import GameIMDbPersonEnrichment, COMPONENT, JOB
from core.game_imdb_source import IMDbGameSource
from core.game_people_discovery import GamePeopleDiscovery
from models.catalogue import CatalogueCredit, CatalogueEntity, CatalogueIdentity, MetadataSnapshot


@unittest.skipUnless(fixture.fixture.fixture.fixture.URL, 'Requires disposable PostgreSQL')
class ColdPeopleDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixture.GameContributorSearchTests.asyncSetUp(self)
        self.people_discovery_patch.stop()
        self.source = SimpleNamespace(people_candidates=AsyncMock(return_value=[
            {'id': 'nm1', 'name': 'Cold performer', 'image': None}]))
        self.service = GamePeopleDiscovery(self.Session, source=self.source)
        mock = patch('core.game_people_discovery.GamePeopleDiscovery', return_value=self.service)
        mock.start(); self.addCleanup(mock.stop)

    asyncTearDown = fixture.GameContributorSearchTests.asyncTearDown
    query = fixture.GameContributorSearchTests.query

    async def count(self, model, *conditions):
        async with self.Session() as db:
            return await db.scalar(select(func.count()).select_from(model).where(*conditions))

    async def portfolio(self, rows):
        raw = page(rows, len(rows))
        raw['header']['nameText']['text'] = 'Cold performer'
        source = SimpleNamespace(credits=AsyncMock(return_value=raw))
        worker = GameIMDbPersonEnrichment(self.Session, source=source)
        self.assertEqual(await worker.run('nm1'), 'ready')
        return worker, source

    async def test_fresh_actor_and_crew_search_discovers_games_without_any_detail_visit(self):
        self.assertEqual(await self.count(CatalogueIdentity, CatalogueIdentity.namespace == 'imdb.person'), 0)
        cold = await self.query('Cold performer', 'people', 'game')
        self.assertTrue(cold['verification_pending'])
        self.assertFalse(cold['results']['actor'])
        self.assertFalse(cold['results']['staff'])
        worker, source = await self.portfolio([credit('tt2'), credit('tt3', role='composer')])
        pending = await self.query('Cold performer', 'people', 'game')
        self.assertTrue(pending['verification_pending'])
        self.assertTrue(pending['section_pending']['staff'])
        self.assertFalse(pending['results']['actor'])
        discovery = GamePersonDiscovery(self.Session, source=SimpleNamespace(page=AsyncMock(return_value={
            'outcomes': [outcome(), outcome('tt3', 30)], 'stale': False})))
        self.assertEqual(await discovery.run('nm1'), 'ready')
        self.assertEqual(await worker.run('nm1'), 'ready')
        ready = await self.query('Cold performer', 'people', 'game')
        self.assertFalse(ready['verification_pending'])
        self.assertFalse(ready['partial'])
        self.assertEqual(len(ready['results']['actor']), 1)
        self.assertEqual(len(ready['results']['staff']), 1)
        self.assertEqual(ready['results']['actor'][0]['key'], ready['results']['staff'][0]['key'])
        self.assertEqual(ready['results']['staff'][0]['roles'], ['Composer'])
        actor = await self.client.get('/tracking/contributors/actor/' + ready['results']['actor'][0]['key'],
            params={'media_type': 'game'})
        self.assertEqual(actor.status_code, 200, actor.text)
        self.assertEqual(actor.json()['known_works'], 1)
        shared = await self.query('Cold performer', 'people')
        self.assertEqual(shared['results']['actor'][0]['key'], ready['results']['actor'][0]['key'])
        self.source.people_candidates.assert_awaited_once()
        source.credits.assert_awaited_once()
        self.assertEqual(await self.count(CatalogueIdentity, CatalogueIdentity.namespace == 'imdb.person'), 1)

    async def test_complete_acting_source_stops_crew_polling_during_game_mapping(self):
        async with self.Session() as db:
            game = CatalogueEntity(kind='game', name='Saved game', attributes={'game_class': 'game', 'igdb_id': '10'})
            composer = CatalogueEntity(kind='person', name='Cold performer composer')
            db.add_all([game, composer])
            await db.flush()
            db.add_all([
                CatalogueIdentity(entity_id=game.id, namespace='igdb.game', external_id='10', source='igdb'),
                CatalogueIdentity(entity_id=game.id, namespace='imdb.title', external_id='tt1', source='reviewed',
                    evidence={'title_type': 'videoGame', 'igdb_id': '10'}),
                CatalogueCredit(work_id=game.id, contributor_id=composer.id, provider='igdb',
                    source_key='saved-composer', role='composer'),
            ])
            await db.commit()

        self.service.source = IMDbGameSource()
        self.service.source.people_candidates = self.source.people_candidates
        sample = [credit(f'tt{i}') for i in range(1, 5)]
        with patch('core.game_people_presence.presence', AsyncMock(
                return_value={'total': 6, 'rows': sample, 'complete': False})):
            cold = await self.query('Cold performer', 'people', 'game')
        self.assertTrue(cold['section_pending']['staff'])
        self.assertEqual([row['name'] for row in cold['results']['staff']], ['Cold performer composer'])

        await self.portfolio([*sample, credit('tt5', role='archive_footage'), credit('tt6', role='thanks')])
        async with self.Session() as db:
            discovery = await db.scalar(snapshot(DISCOVERY_JOB, 'nm1'))
            due = now().replace(microsecond=0) + timedelta(minutes=3)
            discovery.next_attempt_at = due
            await db.commit()

        ready = await self.query('Cold performer', 'people')
        self.assertEqual(ready['results']['staff'], cold['results']['staff'])
        self.assertEqual([row['name'] for row in ready['results']['actor']], ['Cold performer'])
        self.assertFalse(ready['section_pending']['staff'])
        self.assertFalse(ready['verification_pending'])
        self.assertFalse(ready['partial'])
        self.assertNotIn('retry_after', ready)

        async with self.Session() as db:
            self.assertEqual(await db.scalar(snapshot(DISCOVERY_JOB, 'nm1').with_only_columns(
                MetadataSnapshot.next_attempt_at)), due)
            component = await db.scalar(snapshot(COMPONENT, 'nm1'))
            component.expires_at = now() - timedelta(seconds=1)
            await db.commit()
        stale = await self.query('Cold performer', 'people')
        self.assertTrue(stale['section_pending']['staff'])
        self.assertTrue(stale['verification_pending'])

    async def test_suggestions_alone_do_not_prove_game_work_or_merge_namesakes(self):
        self.source.people_candidates.return_value += [{'id': 'nm2', 'name': 'Cold performer', 'image': None}]
        cold = await self.query('Cold performer', 'actor', 'game')
        self.assertTrue(cold['verification_pending'])
        self.assertFalse(cold['results']['actor'])
        self.assertEqual(await self.count(CatalogueIdentity, CatalogueIdentity.namespace == 'imdb.person'), 2)
        await self.portfolio([credit('tt4', kind='movie')])
        pending = await self.query('Cold performer', 'actor', 'game')
        self.assertFalse(pending['results']['actor'])
        self.assertTrue(pending['verification_pending'])  # second namesake unknown
        raw = page([], 0); raw['header']['id'] = 'nm2'
        second = GameIMDbPersonEnrichment(self.Session, source=SimpleNamespace(credits=AsyncMock(return_value=raw)))
        self.assertEqual(await second.run('nm2'), 'ready')
        ready = await self.query('Cold performer', 'actor', 'game')
        self.assertFalse(ready['verification_pending'])
        self.assertFalse(ready['partial'])
        self.assertFalse(ready['results']['actor'])

    async def test_thanks_only_and_excluded_dlc_do_not_become_people_results(self):
        await self.query('Cold performer', 'people', 'game')
        worker, _ = await self.portfolio([credit('tt2', role='thanks'), credit('tt3', role='composer')])
        discovery = GamePersonDiscovery(self.Session, source=SimpleNamespace(page=AsyncMock(return_value={
            'outcomes': [{'id': 'tt3', 'status': 'excluded', 'reason': 'dlc', 'igdb_id': 30}], 'stale': False})))
        self.assertEqual(await discovery.run('nm1'), 'ready')
        self.assertEqual(await worker.run('nm1'), 'ready')
        result = await self.query('Cold performer', 'people', 'game')
        self.assertFalse(result['results']['actor'])
        self.assertFalse(result['results']['staff'])
        self.assertFalse(result['verification_pending'])

    async def test_concurrent_searches_share_provider_and_admission(self):
        entered, release = asyncio.Event(), asyncio.Event()
        rows = self.source.people_candidates.return_value
        async def load(*args):
            entered.set(); await release.wait(); return rows
        self.source.people_candidates.side_effect = load
        first = asyncio.create_task(self.service.search('Cold performer'))
        await entered.wait()
        second = asyncio.create_task(self.service.search('cold performer'))
        release.set()
        a, b = await asyncio.gather(first, second)
        self.assertEqual(a['ids'], b['ids'])
        self.source.people_candidates.assert_awaited_once()
        self.assertEqual(await self.count(MetadataSnapshot, MetadataSnapshot.kind == JOB), 1)

    async def test_progressive_search_returns_pending_then_reuses_the_shared_discovery(self):
        entered, release = asyncio.Event(), asyncio.Event()
        rows = self.source.people_candidates.return_value
        async def load(*args):
            entered.set(); await release.wait(); return rows
        self.source.people_candidates.side_effect = load
        try:
            cold = await asyncio.wait_for(self.service.search('Cold performer', progressive=True), 2)
            await asyncio.wait_for(entered.wait(), 2)
            self.assertEqual(cold, {'ids': [], 'pending': True, 'partial': False, 'retry_after': 2})
            again = await asyncio.wait_for(self.service.search('cold performer', progressive=True), 2)
            self.assertTrue(again['pending'])
            self.source.people_candidates.assert_awaited_once()
        finally:
            release.set()
            ready = await self.service.search('Cold performer')
        self.assertEqual(len(ready['ids']), 1)
        self.assertTrue(ready['pending'])  # Admission is not a verified career.
        self.source.people_candidates.assert_awaited_once()
        self.assertEqual(await self.count(MetadataSnapshot, MetadataSnapshot.kind == JOB), 1)

    async def test_failed_source_is_partial_and_does_not_admit_or_certify_empty(self):
        self.source.people_candidates.side_effect = TimeoutError
        result = await self.query('Cold performer', 'people', 'game')
        self.assertTrue(result['partial'])
        self.assertTrue(result['unavailable'])
        self.assertFalse(result['verification_pending'])
        self.assertFalse(result['results']['actor'])
        await self.query('Cold performer', 'people', 'game')
        self.source.people_candidates.assert_awaited_once()
        self.assertEqual(await self.count(CatalogueIdentity, CatalogueIdentity.namespace == 'imdb.person'), 0)

    async def test_unmapped_game_identity_remains_partial_after_source_discovery_finishes(self):
        await self.query('Cold performer', 'people', 'game')
        worker, _ = await self.portfolio([credit('tt2')])
        discovery = GamePersonDiscovery(self.Session, source=SimpleNamespace(page=AsyncMock(return_value={
            'outcomes': [{'id': 'tt2', 'status': 'unmapped'}], 'stale': False})))
        self.assertEqual(await discovery.run('nm1'), 'ready')
        self.assertEqual(await worker.run('nm1'), 'ready')
        result = await self.query('Cold performer', 'people', 'game')
        self.assertFalse(result['verification_pending'])
        self.assertTrue(result['partial'])
        self.assertFalse(result['unavailable'])
        self.assertFalse(result['results']['actor'])

    async def test_long_provider_cooldown_is_returned_without_refetching_names(self):
        await self.query('Cold performer', 'people', 'game')
        async with self.Session() as db:
            job = await db.scalar(snapshot(JOB, 'nm1'))
            job.next_attempt_at = now() + timedelta(minutes=5)
            job.error_code = 'rate_limited'
            await db.commit()
        result = await self.query('Cold performer', 'people', 'game')
        self.assertTrue(result['verification_pending'])
        self.assertTrue(result['partial'])
        self.assertGreater(result['retry_after'], 30)
        self.source.people_candidates.assert_awaited_once()

    async def test_existing_explicit_identity_is_reused_without_replacing_protected_metadata(self):
        async with self.Session() as db:
            person = await db.get(CatalogueEntity, self.person_id)
            person.image_url = 'https://example.test/selected.jpg'
            person.protected_fields = ['image_url']
            db.add(CatalogueIdentity(entity_id=person.id, namespace='imdb.person', external_id='nm1', source='tmdb'))
            await db.commit()
        self.source.people_candidates.return_value[0]['image'] = 'https://m.media-amazon.com/images/M/new._V1_.jpg'
        await self.query('Cold performer', 'people', 'game')
        async with self.Session() as db:
            mapping = await catalogue.resolve_identity(db, 'imdb.person', 'nm1')
            self.assertEqual(mapping.entity_id, self.person_id)
            self.assertEqual((await db.get(CatalogueEntity, self.person_id)).image_url, 'https://example.test/selected.jpg')

    async def test_denied_and_movie_only_searches_do_not_fetch_or_queue_game_people(self):
        self.viewer.games_access = False
        denied = await self.client.get('/tracking/quick-search/contributors', params={
            'q': 'Cold performer', 'kind': 'people', 'work_domain': 'game'})
        self.assertEqual(denied.status_code, 403)
        await self.query('Cold performer', 'people')
        self.viewer.games_access = True
        await self.query('Cold performer', 'people', 'movies_series')
        self.source.people_candidates.assert_not_awaited()
