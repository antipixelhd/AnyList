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
from core.game_imdb_discovery import GamePersonDiscovery
from core.game_imdb_enrichment import now, snapshot
from core.game_imdb_person import GameIMDbPersonEnrichment, JOB
from core.game_people_discovery import GamePeopleDiscovery
from models.catalogue import CatalogueEntity, CatalogueIdentity, MetadataSnapshot


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

    async def test_failed_source_is_partial_and_does_not_admit_or_certify_empty(self):
        self.source.people_candidates.side_effect = TimeoutError
        result = await self.query('Cold performer', 'people', 'game')
        self.assertTrue(result['partial'])
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
