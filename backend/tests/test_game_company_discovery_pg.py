"""Cold IGDB studios become discoverable without first visiting game pages."""
import asyncio
import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy import func, select

import test_game_contributor_search_pg as fixture
from core import catalogue
from core.game_browse_sources import GameBrowseSources
from core.game_company_discovery import GameCompanyDiscovery
from core.game_company_works import GameCompanyWorks, JOB, snapshot
from core.game_imdb_enrichment import now
from models.catalogue import CatalogueEntity, CatalogueIdentity, MetadataSnapshot


@unittest.skipUnless(fixture.fixture.fixture.fixture.URL, 'Requires disposable PostgreSQL')
class ColdCompanyDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixture.GameContributorSearchTests.asyncSetUp(self)
        self.discovery_patch.stop()
    asyncTearDown = fixture.GameContributorSearchTests.asyncTearDown
    query = fixture.GameContributorSearchTests.query

    async def discovery(self):
        sources = GameBrowseSources(self.Session)
        sources.api.validate = lambda *args: None
        sources.igdb = AsyncMock(return_value=[{'id': 1012, 'name': 'FromSoftware',
            'logo': {'url': 'https://images.igdb.com/igdb/image/upload/t_thumb/company.png'}}])
        # Live IGDB companies full-text search can return[] for a known name;
        # the supported name filter must actually reach the candidate data.
        sources.igdb.side_effect = lambda endpoint, query: [] if query.startswith('search ') else sources.igdb.return_value
        return GameCompanyDiscovery(self.Session, sources=sources), sources

    async def count(self, model, *conditions):
        async with self.Session() as db:
            return await db.scalar(select(func.count()).select_from(model).where(*conditions))

    async def test_cold_search_queues_existing_worker_then_returns_verified_full_portfolio(self):
        service, sources = await self.discovery()
        self.assertEqual(await self.count(CatalogueIdentity, CatalogueIdentity.namespace == 'igdb.company'), 0)
        # Neither game nor company has been opened; no pre-existing game credit.
        with patch('core.game_company_discovery.GameCompanyDiscovery', return_value=service):
            cold = await self.query('FromSoftware', 'studio', 'game')
            self.assertEqual(cold['results']['studio'], [])
            self.assertTrue(cold['verification_pending'])
            self.assertFalse(cold['partial'])
            repeated = await self.query('fromsoftware', 'studio', 'game')
            self.assertTrue(repeated['verification_pending'])
            sources.igdb.assert_awaited_once()
            async with self.Session() as db:
                job = await db.scalar(snapshot('1012'))
                self.assertIsNotNone(job.next_attempt_at)
                mapping = await catalogue.resolve_identity(db, 'igdb.company', '1012')
                company = mapping.entity_id
            from test_game_company_works_pg import item, page
            source = type('CompanySource', (), {})()
            documents = [item(7001, 101), item(7002, 102, 'publisher')]
            for document in documents:
                document['record']['company'] = 1012
            source.page = AsyncMock(return_value=page(documents))
            worker = GameCompanyWorks(self.Session, source=source)
            self.assertEqual(await worker.run('1012'), 'ready')
            ready = await self.query('FromSoftware', 'studio', 'game')
            self.assertFalse(ready['verification_pending'])
            self.assertEqual([r['key'] for r in ready['results']['studio']], [f'catalogue:{company}'])
            self.assertEqual(set(ready['results']['studio'][0]['roles']), {'Developer', 'Publisher'})
            shared = await self.query('FromSoftware', 'studio')
            self.assertEqual([r['key'] for r in shared['results']['studio']], [f'catalogue:{company}'])
            details = await self.client.get(f'/tracking/contributors/studio/catalogue:{company}', params={'media_type': 'game'})
            self.assertEqual(details.status_code, 200, details.text)
            self.assertEqual(details.json()['known_works'], 2)
            self.assertTrue(details.json()['game_company_status']['complete'])
            self.assertEqual(await worker.run('1012'), 'cached')
            self.assertEqual(await self.count(CatalogueIdentity, CatalogueIdentity.namespace == 'igdb.company'), 1)
            sources.igdb.assert_awaited_once()

    async def test_provider_name_search_does_not_merge_namesake_or_imply_eligible_works(self):
        service, sources = await self.discovery()
        sources.igdb.return_value += [{'id': 2222, 'name': 'FromSoftware'}]
        with patch('core.game_company_discovery.GameCompanyDiscovery', return_value=service):
            cold = await self.query('FromSoftware', 'studio', 'game')
            self.assertFalse(cold['results']['studio'])
            self.assertTrue(cold['verification_pending'])
            self.assertEqual(await self.count(CatalogueIdentity, CatalogueIdentity.namespace == 'igdb.company'), 2)
            from test_game_company_works_pg import page
            source = type('CompanySource', (), {})()
            source.page = AsyncMock(return_value=page([]))
            for native in ('1012', '2222'):
                self.assertEqual(await GameCompanyWorks(self.Session, source=source).run(native), 'ready')
            empty = await self.query('FromSoftware', 'studio', 'game')
            self.assertFalse(empty['results']['studio'])
            self.assertFalse(empty['verification_pending'])
            self.assertFalse(empty['partial'])

    async def test_concurrent_queries_share_source_and_candidate_admission(self):
        service, sources = await self.discovery()
        entered, release = asyncio.Event(), asyncio.Event()
        rows = sources.igdb.return_value
        async def load(*args):
            entered.set()
            await release.wait()
            return rows
        sources.igdb.side_effect = load
        first = asyncio.create_task(service.search('FromSoftware'))
        await entered.wait()
        second = asyncio.create_task(service.search('fromsoftware'))
        release.set()
        a, b = await asyncio.gather(first, second)
        self.assertEqual(a['ids'], b['ids'])
        self.assertTrue(a['pending'] and b['pending'])
        sources.igdb.assert_awaited_once()
        self.assertEqual(await self.count(MetadataSnapshot, MetadataSnapshot.kind == JOB), 1)

    async def test_failure_is_partial_and_retried_without_certifying_empty(self):
        service, sources = await self.discovery()
        sources.igdb.side_effect = TimeoutError
        failed = await service.search('FromSoftware')
        self.assertTrue(failed['partial'])
        self.assertFalse(failed['ids'])
        self.assertFalse(failed['pending'])
        await service.search('FromSoftware')
        sources.igdb.assert_awaited_once()
        self.assertEqual(await self.count(CatalogueIdentity, CatalogueIdentity.namespace == 'igdb.company'), 0)

    async def test_denied_and_movie_only_requests_do_not_admit_game_discovery(self):
        service, sources = await self.discovery()
        with patch('core.game_company_discovery.GameCompanyDiscovery', return_value=service):
            self.viewer.games_access = False
            denied = await self.client.get('/tracking/quick-search/contributors', params={
                'q': 'FromSoftware', 'kind': 'studio', 'work_domain': 'game'})
            self.assertEqual(denied.status_code, 403)
            await self.query('FromSoftware', 'studio')
            self.viewer.games_access = True
            await self.query('FromSoftware', 'studio', 'movies_series')
            sources.igdb.assert_not_awaited()

    async def test_refresh_preserves_canonical_identity_and_protected_company_artwork(self):
        service, sources = await self.discovery()
        first = await service.search('FromSoftware')
        async with self.Session() as db:
            company = await db.get(CatalogueEntity, first['ids'][0])
            company.image_url = 'https://example.test/selected-company.jpg'
            company.protected_fields = ['image_url']
            await db.commit()
        await service.load('FromSoftware')
        async with self.Session() as db:
            company = await db.get(CatalogueEntity, first['ids'][0])
            self.assertEqual(company.image_url, 'https://example.test/selected-company.jpg')
        self.assertEqual(await self.count(CatalogueIdentity, CatalogueIdentity.namespace == 'igdb.company'), 1)
        self.assertEqual(await self.count(MetadataSnapshot, MetadataSnapshot.kind == JOB), 1)

    async def test_failed_stale_refresh_retains_candidates_and_marks_partial(self):
        from core.game_source_cache import _flights
        service, sources = await self.discovery()
        first = await service.search('FromSoftware')
        async with self.Session() as db:
            cached = await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.kind == 'game_company_search'))
            cached.expires_at = now() - timedelta(seconds=1)
            await db.commit()
        sources.igdb.side_effect = TimeoutError
        stale = await service.search('FromSoftware')
        self.assertEqual(stale['ids'], first['ids'])
        tasks = [task for key, task in list(_flights.items()) if key[0] == id(self.Session)]
        if tasks:
            await asyncio.gather(*tasks)
        failed = await service.search('FromSoftware')
        self.assertEqual(failed['ids'], first['ids'])
        self.assertTrue(failed['partial'])
        self.assertTrue(failed['pending'])

    async def test_malformed_provider_rows_cannot_create_companies_or_jobs(self):
        service, sources = await self.discovery()
        sources.igdb.return_value = [{'id': True, 'name': 'FromSoftware'}]
        result = await service.search('FromSoftware')
        self.assertTrue(result['partial'])
        self.assertFalse(result['ids'])
        self.assertEqual(await self.count(CatalogueIdentity, CatalogueIdentity.namespace == 'igdb.company'), 0)
        self.assertEqual(await self.count(MetadataSnapshot, MetadataSnapshot.kind == JOB), 0)
