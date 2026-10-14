"""Actor backfill is bounded, refreshes complete cast and respects last-good data."""
import unittest
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace as Row
from contextlib import asynccontextmanager

import test_statistics_facts as fixtures
from core.actor_backfill import backfill_actors
from core.catalogue_providers import CatalogueProviders
from core import tvdb


@asynccontextmanager
async def lane(*args):
    yield


class ActorBackfillTests(unittest.IsolatedAsyncioTestCase):
    def test_secondary_projection_keeps_source_document_and_episode_fields_intact(self):
        from core.screen_metadata_policy import gap_document
        document = {'work': {'attributes': {'episode_count': 100, 'regular_episode_count': 90,
                                           'episode_run_time': [40], 'genres': ['Drama']}},
                    'credits': [{'contributor': {'kind': 'person'}, 'role': 'actor'},
                                {'contributor': {'kind': 'person'}, 'role': 'director'}]}
        projected = gap_document(document, {'crew'})
        self.assertEqual([credit['role'] for credit in projected['credits']], ['director'])
        self.assertEqual(projected['work']['attributes'], {'genres': ['Drama']})
        self.assertEqual(document['work']['attributes']['episode_count'], 100)
        self.assertEqual(len(document['credits']), 2)

    async def test_unmatched_imdb_lookup_consumes_retry_without_a_refresh_source(self):
        row = fixtures.media(1, 'movie', tmdb_id=None, imdb_id='tt123', tmdb_data={})
        db = AsyncMock(); db.scalars.return_value = MagicMock(); db.scalars.return_value.all.return_value = [row]
        providers = Row(tmdb_key=None, tvdb_key='private', http=Row(lane=lane))
        with patch('core.actor_backfill.fill_screen_credits', AsyncMock(return_value='unavailable')) as refresh:
            result = await backfill_actors(db, providers)
        refresh.assert_awaited_once_with(db, row, providers=providers, authoritative_primary=True)
        self.assertEqual(result['updated'], 0)
        self.assertIn('actor_backfill_attempted_at', row.tmdb_data)
        db.commit.assert_awaited_once()

    async def test_background_uses_shared_source_policy_and_bounded_batch(self):
        row = fixtures.media(1, "movie", tmdb_id=10, imdb_id="tt123", tmdb_data={})
        db = AsyncMock(); db.scalars.return_value = MagicMock(); db.scalars.return_value.all.return_value = [row]
        providers = Row(tmdb_key="private", tvdb_key="private", http=Row(lane=lane))
        with patch('core.actor_backfill.fill_screen_credits', AsyncMock(return_value='updated')) as refresh:
            result = await backfill_actors(db, providers, limit=3)
        refresh.assert_awaited_once_with(db, row, providers=providers, authoritative_primary=True)
        self.assertEqual(result, {"examined": 1, "updated": 1, "failed": 0})
        self.assertEqual(row.tmdb_data["actor_metadata_version"], 4)
        self.assertEqual(db.scalars.await_args.args[0]._limit_clause.value, 3)

    async def test_tvdb_detail_preserves_all_credits_but_bounds_people_enrichment(self):
        raw = {"id": 42, "name": "Film", "characters": [{"id": i, "peopleId": i, "type": 3, "personName": str(i), "name": "Role", "sort": i} for i in range(1, 31)]}
        adapter = CatalogueProviders(http=Row(lane=lane), tvdb_key="private")
        with patch("core.tvdb.get_movie", AsyncMock(return_value=raw)), patch("core.tvdb.get_person", AsyncMock(return_value={"remoteIds": []})) as people:
            doc, retained = await adapter.detail("tvdb", "movie", "42")
        self.assertEqual(len(doc["credits"]), 30)
        self.assertEqual(people.await_count, 12)
        self.assertEqual(len(retained["_people"]), 12)

    async def test_metadata_only_tvdb_enrichment_skips_people_and_company_requests(self):
        raw = {'id': 42, 'name': 'Film', 'overview': 'Missing description',
               'characters': [{'id': 1, 'peopleId': 5, 'type': 3, 'personName': 'Actor', 'name': 'Role'}],
               'companies': [{'id': 3, 'name': 'Studio', 'companyType': {'companyTypeName': 'Studio'}}]}
        adapter = CatalogueProviders(http=Row(lane=lane), tvdb_key='private')
        with patch('core.tvdb.get_movie', AsyncMock(return_value=raw)), patch(
                'core.tvdb.get_person', AsyncMock()) as people, patch('core.tvdb.get_company', AsyncMock()) as companies:
            doc, retained = await adapter.detail('tvdb', 'movie', '42', credit_groups=set())
        people.assert_not_awaited()
        companies.assert_not_awaited()
        self.assertEqual(len(doc['credits']), 2)
        self.assertEqual(len(retained['characters']), 1)

    async def test_remote_id_lookup_rejects_ambiguous_and_invalid_matches(self):
        with patch("core.tvdb._get", AsyncMock(return_value={"data": [{"movie": {"id": 1}}, {"movie": {"id": 2}}]})) as request:
            self.assertIsNone(await tvdb.find_movie("tt123", "private"))
            self.assertIsNone(await tvdb.find_movie("../../bad", "private"))
        request.assert_awaited_once()
