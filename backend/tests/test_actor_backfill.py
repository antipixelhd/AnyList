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
    async def test_movie_matches_tvdb_by_imdb_and_refreshes_both_providers(self):
        row = fixtures.media(1, "movie", tmdb_id=10, imdb_id="tt123", tmdb_data={})
        db = AsyncMock(); db.scalars.return_value = MagicMock(); db.scalars.return_value.all.return_value = [row]
        providers = Row(tmdb_key="private", tvdb_key="private", http=Row(lane=lane))
        with patch("core.actor_backfill.tvdb.find_movie", AsyncMock(return_value=42)) as match, patch(
            "core.actor_backfill.catalogue.refresh_metadata", AsyncMock(return_value={"status": "updated", "entity_id": 9})) as refresh:
            result = await backfill_actors(db, providers, limit=3)
        match.assert_awaited_once_with("tt123", "private")
        self.assertEqual([call.args[2:5] for call in refresh.await_args_list], [("tmdb", "movie", "10"), ("tvdb", "movie", "42")])
        self.assertEqual(result, {"examined": 1, "updated": 1, "failed": 0})
        self.assertEqual(row.tmdb_data["actor_metadata_version"], 3)
        self.assertEqual(db.scalars.await_args.args[0]._limit_clause.value, 3)

    async def test_tvdb_detail_preserves_all_credits_but_bounds_people_enrichment(self):
        raw = {"id": 42, "name": "Film", "characters": [{"id": i, "peopleId": i, "type": 3, "personName": str(i), "name": "Role", "sort": i} for i in range(1, 31)]}
        adapter = CatalogueProviders(http=Row(lane=lane), tvdb_key="private")
        with patch("core.tvdb.get_movie", AsyncMock(return_value=raw)), patch("core.tvdb.get_person", AsyncMock(return_value={"remoteIds": []})) as people:
            doc, retained = await adapter.detail("tvdb", "movie", "42")
        self.assertEqual(len(doc["credits"]), 30)
        self.assertEqual(people.await_count, 12)
        self.assertEqual(len(retained["_people"]), 12)

    async def test_remote_id_lookup_rejects_ambiguous_and_invalid_matches(self):
        with patch("core.tvdb._get", AsyncMock(return_value={"data": [{"movie": {"id": 1}}, {"movie": {"id": 2}}]})) as request:
            self.assertIsNone(await tvdb.find_movie("tt123", "private"))
            self.assertIsNone(await tvdb.find_movie("../../bad", "private"))
        request.assert_awaited_once()
