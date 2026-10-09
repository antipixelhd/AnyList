"""Use only the explicit disposable catalogue DB, with a private schema per test."""

import asyncio
import importlib
import os
import unittest
import uuid
from copy import deepcopy
from datetime import datetime, timedelta
from unittest.mock import AsyncMock

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from models import Base
from models.catalogue import (
    CatalogueEntity,
    CatalogueCredit,
    CatalogueLegacyLink,
    MetadataSnapshot,
    BookEdition,
    CharacterAppearance,
    SteamPriceSnapshot,
    CatalogueRelationship,
    CharacterPerformance,
)
from models.media import Media
from models.show import Show
from models.base import MediaType
from core import catalogue
from core.catalogue_providers import ProviderError
from core.catalogue_normalize import (
    entity,
    normalize_igdb,
    normalize_rawg,
    normalize_hardcover,
    normalize_tvdb,
    normalize_tmdb,
)
from test_catalogue_providers import fixture

URL = os.getenv("CATALOGUE_TEST_DATABASE_URL")


@unittest.skipUnless(URL, "Requires disposable catalogue PostgreSQL")
class CatalogueDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if ":55449/catalogue_test" not in URL:
            raise RuntimeError("Refusing non-disposable catalogue DB")
        self.schema = "catalogue_test_" + uuid.uuid4().hex
        self.admin = create_async_engine(URL)
        async with self.admin.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.engine = create_async_engine(
            URL, connect_args={"server_settings": {"search_path": self.schema}}
        )
        wanted = [
            t
            for t in Base.metadata.sorted_tables
            if t.name
            in (
                "users",
                "media",
                "shows",
                "title_credits",
                "watch_events",
                "lists",
                "list_items",
                "tracked_entries",
            )
        ]

        def setup(conn):
            Base.metadata.create_all(conn, tables=wanted)
            with Operations.context(MigrationContext.configure(conn)):
                importlib.import_module(
                    "migrations.versions.mt036_catalogue_foundation"
                ).upgrade()
                importlib.import_module(
                    "migrations.versions.mt037_catalogue_reference_integrity"
                ).upgrade()
                importlib.import_module(
                    "migrations.versions.mt038_openlibrary_identities"
                ).upgrade()

        async with self.engine.begin() as conn:
            await conn.run_sync(setup)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()
        async with self.admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
        await self.admin.dispose()

    async def test_book_providers_share_verified_isbn_work_and_edition_in_both_orders(
        self,
    ):
        from core.openlibrary import normalize
        from test_openlibrary import sample
        from core.catalogue_normalize import normalize_hardcover

        for order in [("hardcover", "openlibrary"), ("openlibrary", "hardcover")]:
            async with self.Session() as db:
                documents = {
                    "hardcover": normalize_hardcover(fixture("hardcover")),
                    "openlibrary": normalize(sample()),
                }
                first = await catalogue.ingest_document(
                    db, documents[order[0]], order[0]
                )
                second = await catalogue.ingest_document(
                    db, documents[order[1]], order[1]
                )
                self.assertEqual(first.id, second.id)
                isbn = await catalogue.resolve_identity(db, "isbn.13", "9783551354013")
                native = await catalogue.resolve_identity(
                    db, "openlibrary.edition", "OL1M"
                )
                self.assertEqual(isbn.entity_id, native.entity_id)
                self.assertEqual(
                    (await db.get(BookEdition, isbn.entity_id)).work_id, first.id
                )
                self.assertEqual(first.name, fixture("hardcover")["title"])
                self.assertEqual(
                    (await db.get(BookEdition, isbn.entity_id)).language, "de"
                )
                self.assertTrue(
                    await db.scalar(
                        select(func.count()).select_from(CharacterAppearance)
                    )
                )
                await db.rollback()

    async def test_book_native_work_and_isbn_conflict_do_not_silently_merge(self):
        from core.openlibrary import normalize
        from test_openlibrary import sample

        async with self.Session() as db:
            await catalogue.ingest_document(
                db, normalize_hardcover(fixture("hardcover")), "hardcover"
            )
            other = sample()
            other["_editions"] = []
            await catalogue.ingest_document(db, normalize(other), "openlibrary")
            await db.commit()
            with self.assertRaises(catalogue.IdentityConflict):
                async with db.begin_nested():
                    await catalogue.ingest_document(
                        db, normalize(sample()), "openlibrary"
                    )
            self.assertEqual(
                await db.scalar(
                    select(func.count())
                    .select_from(CatalogueEntity)
                    .where(CatalogueEntity.kind == "book")
                ),
                2,
            )

    async def test_hardcover_durable_daily_budget_counts_failed_requests_and_resets(
        self,
    ):
        import httpx
        from unittest.mock import patch
        from core.catalogue_providers import ProviderHTTP
        from core.config import settings
        from models.catalogue import MetadataProviderBudget

        async def handle(request):
            return httpx.Response(400)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            http = ProviderHTTP(client, self.Session)
            with patch.object(settings, "hardcover_daily_budget", 1):
                with self.assertRaisesRegex(ProviderError, "bad_response"):
                    await http.request(
                        "hardcover",
                        "GET",
                        "https://api.hardcover.app/test",
                        cache=False,
                    )
                with self.assertRaisesRegex(ProviderError, "daily_budget"):
                    await http.request(
                        "hardcover",
                        "GET",
                        "https://api.hardcover.app/test",
                        cache=False,
                    )
                async with self.Session() as db:
                    row = await db.get(MetadataProviderBudget, "hardcover")
                    self.assertEqual(row.daily_request_count, 1)
                    row.day = "1900-01-01"
                    await db.commit()
                with self.assertRaisesRegex(ProviderError, "bad_response"):
                    await http.request(
                        "hardcover",
                        "GET",
                        "https://api.hardcover.app/test",
                        cache=False,
                    )

    async def test_repeated_imports_namespace_collisions_and_partial_fallback(self):
        async with self.Session() as db:
            primary = await catalogue.ingest_document(
                db, normalize_igdb(fixture("igdb")), "igdb"
            )
            id = primary.id
            name = primary.name
            await db.commit()
            again = await catalogue.ingest_document(
                db, normalize_igdb(fixture("igdb")), "igdb"
            )
            fallback = await catalogue.ingest_document(
                db, normalize_rawg(fixture("rawg")), "rawg"
            )
            self.assertEqual((again.id, fallback.id), (id, id))
            self.assertEqual(fallback.name, name)
            empty = normalize_igdb({"id": 1942, "name": name, "summary": None})
            await catalogue.ingest_document(db, empty, "igdb")
            self.assertEqual(primary.description, "Sample game synopsis.")
            await db.commit()
            credits = (
                (
                    await db.execute(
                        select(CatalogueCredit).where(CatalogueCredit.work_id == id)
                    )
                )
                .scalars()
                .all()
            )
            self.assertEqual(
                len(credits), len({(c.provider, c.source_key) for c in credits})
            )
            org1 = await catalogue.upsert_entity(
                db,
                entity("organization", "tmdb.company", {"id": 7, "name": "Same name"}),
                "tmdb",
            )
            org2 = await catalogue.upsert_entity(
                db,
                entity("organization", "tmdb.network", {"id": 7, "name": "Same name"}),
                "tmdb",
            )
            self.assertNotEqual(org1.id, org2.id)
            # The DB enforces kind boundaries even outside repository helpers.
            async with db.begin_nested() as savepoint:
                with self.assertRaises(IntegrityError):
                    await db.execute(
                        text(
                            "INSERT INTO catalogue_identities(entity_id,namespace,external_id,source) VALUES (:id,'hardcover.author','777','test')"
                        ),
                        {"id": org1.id},
                    )
                await savepoint.rollback()

    async def test_conflicting_mapping_rolls_back_and_manual_fields_survive(self):
        async with self.Session() as db:
            work = await catalogue.ingest_document(
                db, normalize_igdb(fixture("igdb")), "igdb"
            )
            work.name = "Corrected"
            work.protected_fields = ["name"]
            other = await catalogue.ingest_document(
                db, normalize_rawg({"id": 3328, "name": "Another work"}), "rawg"
            )
            await db.commit()
            async with db.begin_nested():
                with self.assertRaises(catalogue.IdentityConflict):
                    await catalogue.ingest_document(
                        db, normalize_rawg(fixture("rawg")), "rawg"
                    )
            refreshed = await catalogue.ingest_document(
                db, normalize_igdb(fixture("igdb")), "igdb"
            )
            self.assertEqual(refreshed.name, "Corrected")
            self.assertNotEqual(work.id, other.id)
            with self.assertRaises(catalogue.IdentityConflict):
                await catalogue.bind_identity(
                    db, work.id, "rawg.game", "3328", "reviewed"
                )
            with self.assertRaises(catalogue.IdentityConflict):
                await catalogue.bind_identity(
                    db, work.id, "isbn.13", "9780747532699", "reviewed"
                )

    async def test_editions_do_not_duplicate_works_and_isbn_collision_stops_import(
        self,
    ):
        async with self.Session() as db:
            doc = normalize_hardcover(fixture("hardcover"))
            work = await catalogue.ingest_document(db, doc, "hardcover")
            await catalogue.ingest_document(db, doc, "hardcover")
            count = (
                await db.execute(
                    select(func.count())
                    .select_from(CatalogueEntity)
                    .where(CatalogueEntity.kind == "book")
                )
            ).scalar_one()
            self.assertEqual(count, 1)
            editions = (
                (
                    await db.execute(
                        select(BookEdition).where(BookEdition.work_id == work.id)
                    )
                )
                .scalars()
                .all()
            )
            self.assertEqual(len(editions), len(doc["editions"]))
            edition_id = editions[0].entity_id
            await catalogue.bind_identity(
                db, edition_id, "isbn.13", "9780747532699", "reviewed"
            )
            other = entity(
                "edition", "hardcover.edition", {"id": 9999999, "name": "Other"}
            )
            other["identities"].append(
                {"namespace": "isbn.13", "external_id": "9780747532699"}
            )
            linked = await catalogue.upsert_entity(db, other, "hardcover")
            # A trusted edition ISBN links to that edition; it never maps a work.
            self.assertEqual(linked.id, edition_id)

    async def test_refresh_cache_failure_last_good_and_single_worker_claim(self):
        adapter = AsyncMock()
        adapter.validate = lambda *a: None

        async def details(*args):
            await asyncio.sleep(0.1)
            return normalize_igdb(fixture("igdb")), {"public": "payload"}

        adapter.detail.side_effect = details

        async def refresh():
            async with self.Session() as db:
                return await catalogue.refresh_metadata(
                    db, adapter, "igdb", "game", "1942"
                )

        results = await asyncio.gather(refresh(), refresh())
        self.assertEqual(adapter.detail.await_count, 1)
        self.assertIn("updated", [r["status"] for r in results])
        self.assertEqual((await refresh())["status"], "cached")
        async with self.Session() as db:
            state = (await db.execute(select(MetadataSnapshot))).scalar_one()
            state.expires_at = datetime.utcnow() - timedelta(seconds=1)
            await db.commit()
        adapter.detail.side_effect = ProviderError("unavailable")
        result = await refresh()
        self.assertEqual(result["status"], "last_good")
        async with self.Session() as db:
            state = (await db.execute(select(MetadataSnapshot))).scalar_one()
            self.assertEqual(state.payload, {"public": "payload"})
            self.assertIsNone(state.lease_token)
            self.assertIsNotNone(state.next_attempt_at)
        self.assertEqual((await refresh())["status"], "last_good")

    async def test_performance_cross_work_foreign_keys_and_restrict_deletion(self):
        async with self.Session() as db:
            doc = normalize_tvdb(fixture("tvdb"))
            first = await catalogue.ingest_document(db, doc, "tvdb")
            other = await catalogue.ingest_document(
                db, normalize_igdb(fixture("igdb")), "igdb"
            )
            credit = (
                (
                    await db.execute(
                        select(CatalogueCredit).where(
                            CatalogueCredit.work_id == first.id
                        )
                    )
                )
                .scalars()
                .first()
            )
            character = await catalogue.upsert_entity(
                db,
                entity("character", "igdb.character", {"id": 444, "name": "Character"}),
                "igdb",
            )
            appearance = await catalogue.upsert(
                db,
                CharacterAppearance,
                {
                    "work_id": first.id,
                    "character_id": character.id,
                    "provider": "reviewed",
                },
                ["work_id", "character_id", "provider"],
            )
            performance = await catalogue.link_performance(
                db, credit.id, appearance.id, "en"
            )
            self.assertEqual(performance.work_id, first.id)
            foreign = await catalogue.upsert(
                db,
                CharacterAppearance,
                {"work_id": other.id, "character_id": character.id, "provider": "igdb"},
                ["work_id", "character_id", "provider"],
            )
            with self.assertRaises(catalogue.IdentityConflict):
                await catalogue.link_performance(db, credit.id, foreign.id)
            async with db.begin_nested() as savepoint:
                with self.assertRaises(IntegrityError):
                    await db.execute(
                        text("DELETE FROM catalogue_entities WHERE id=:id"),
                        {"id": first.id},
                    )
                await savepoint.rollback()

    async def test_prices_verified_identity_cache_no_offer_and_last_good_low(self):
        async with self.Session() as db:
            work = await catalogue.ingest_document(
                db, normalize_igdb(fixture("igdb")), "igdb"
            )
            await db.commit()
            client = AsyncMock()
            client.steam_prices.return_value = {
                "itad_id": "018d937f-1212-7232-b23f-a046f6fd4a57",
                "current": {"amountInt": 4999, "currency": "EUR"},
                "historical_low": {"amountInt": 299, "currency": "EUR"},
                "historical_low_at": "2024-06-27",
                "url": "https://itad.link/example",
                "payload": {},
            }
            with self.assertRaises(catalogue.IdentityConflict):
                await catalogue.refresh_prices(db, client, work.id, "111", "DE")
            result = await catalogue.refresh_prices(db, client, work.id, "292030", "DE")
            self.assertEqual(result["status"], "updated")
            self.assertEqual(
                (await catalogue.refresh_prices(db, client, work.id, "292030", "DE"))[
                    "status"
                ],
                "cached",
            )
            state = (
                await db.execute(
                    select(MetadataSnapshot).where(MetadataSnapshot.provider == "itad")
                )
            ).scalar_one()
            state.expires_at = datetime.utcnow() - timedelta(seconds=1)
            await db.commit()
            client.steam_prices.return_value = {
                **client.steam_prices.return_value,
                "current": None,
                "historical_low": None,
                "historical_low_at": None,
                "url": None,
            }
            await catalogue.refresh_prices(db, client, work.id, "292030", "DE")
            row = (await db.execute(select(SteamPriceSnapshot))).scalar_one()
            await db.refresh(row)
            self.assertIsNone(row.current)
            self.assertEqual(row.historical_low["amountInt"], 299)
            state.expires_at = datetime.utcnow() - timedelta(seconds=1)
            await db.commit()
            client.steam_prices.return_value["historical_low"] = {
                "amountInt": 199,
                "currency": "EUR",
            }
            await catalogue.refresh_prices(db, client, work.id, "292030", "DE")
            await db.refresh(row)
            self.assertEqual(row.historical_low["amountInt"], 299)
            self.assertEqual(row.historical_low_at, "2024-06-27")
            state.expires_at = datetime.utcnow() - timedelta(seconds=1)
            await db.commit()
            client.steam_prices.side_effect = RuntimeError("private provider error")
            result = await catalogue.refresh_prices(db, client, work.id, "292030", "DE")
            self.assertEqual(result["status"], "last_good")
            self.assertEqual(result["error_code"], "ingestion_failed")
            self.assertEqual(
                (await catalogue.refresh_prices(db, client, work.id, "292030", "DE"))[
                    "status"
                ],
                "last_good",
            )
            state.lease_token = "other-worker"
            state.lease_until = datetime.utcnow() + timedelta(minutes=1)
            await db.commit()
            self.assertEqual(
                (await catalogue.refresh_prices(db, client, work.id, "292030", "DE"))[
                    "status"
                ],
                "refreshing",
            )

    async def test_placeholder_names_and_unexpected_failures_retain_last_good(self):
        async with self.Session() as db:
            doc = normalize_igdb(fixture("igdb"))
            work = await catalogue.ingest_document(db, doc, "igdb")
            name = work.name
            partial = normalize_igdb(
                {"id": 99999, "name": "Other", "similar_games": [{"id": 1942}]}
            )
            await catalogue.ingest_document(db, partial, "igdb")
            self.assertEqual(work.name, name)
            stub = await catalogue.upsert_entity(
                db, entity("person", "tmdb.person", {"id": 777}), "legacy"
            )
            self.assertNotIn("name", stub.field_sources)
            await catalogue.upsert_entity(
                db,
                entity("person", "tmdb.person", {"id": 777, "name": "Known"}),
                "tmdb",
            )
            self.assertEqual(stub.name, "Known")
            await db.commit()
            adapter = AsyncMock()
            adapter.validate = lambda *args: None
            adapter.detail.return_value = (doc, {"known": "payload"})
            await catalogue.refresh_metadata(db, adapter, "igdb", "game", "1942")
            state = (await db.execute(select(MetadataSnapshot))).scalar_one()
            state.expires_at = datetime.utcnow() - timedelta(seconds=1)
            await db.commit()
            adapter.detail.side_effect = RuntimeError("private provider error")
            result = await catalogue.refresh_metadata(
                db, adapter, "igdb", "game", "1942"
            )
            self.assertEqual(result["status"], "last_good")
            self.assertEqual(result["error_code"], "ingestion_failed")
            self.assertNotIn("private", str(result))
            await db.refresh(state)
            self.assertEqual(state.payload, {"known": "payload"})
            self.assertIsNone(state.lease_token)
            self.assertIsNotNone(state.next_attempt_at)
            # An unexpected failure after canonical writes must roll them back.
            state.next_attempt_at = None
            await db.commit()
            broken = deepcopy(doc)
            broken["work"]["name"] = "Faulty overwrite"
            broken["credits"][0].pop("source_key")
            adapter.detail.side_effect = None
            adapter.detail.return_value = (broken, {"faulty": "payload"})
            result = await catalogue.refresh_metadata(
                db, adapter, "igdb", "game", "1942"
            )
            self.assertEqual(result["status"], "last_good")
            self.assertEqual(result["error_code"], "ingestion_failed")
            await db.refresh(work)
            await db.refresh(state)
            self.assertEqual(work.name, name)
            self.assertEqual(state.payload, {"known": "payload"})

    async def test_partial_relations_preserve_known_labels_and_attributes(self):
        async with self.Session() as db:
            raw = {
                "id": 550,
                "title": "Film",
                "credits": {
                    "cast": [
                        {
                            "id": 7,
                            "name": "Actor",
                            "credit_id": "stable-credit",
                            "character": "Hero",
                            "order": 0,
                        }
                    ]
                },
            }
            film = await catalogue.ingest_document(
                db, normalize_tmdb(raw, "movie"), "tmdb"
            )
            raw["credits"]["cast"][0].pop("character")
            raw["credits"]["cast"][0].pop("order")
            await catalogue.ingest_document(db, normalize_tmdb(raw, "movie"), "tmdb")
            credit = (
                await db.execute(
                    select(CatalogueCredit).where(CatalogueCredit.work_id == film.id)
                )
            ).scalar_one()
            self.assertEqual(credit.character_label, "Hero")
            self.assertEqual(credit.position, 0)
            book = {
                "id": 1,
                "title": "Book",
                "_characters": [
                    {
                        "id": 2,
                        "character": {"id": 3, "name": "Character"},
                        "spoiler": True,
                        "only_mentioned": False,
                    }
                ],
                "_series": [
                    {
                        "id": 4,
                        "series": {"id": 5, "name": "Series"},
                        "position": 1,
                        "compilation": False,
                    }
                ],
            }
            work = await catalogue.ingest_document(
                db, normalize_hardcover(book), "hardcover"
            )
            for key in ("spoiler", "only_mentioned"):
                book["_characters"][0].pop(key)
            for key in ("position", "compilation"):
                book["_series"][0].pop(key)
            await catalogue.ingest_document(db, normalize_hardcover(book), "hardcover")
            appearance = (
                await db.execute(
                    select(CharacterAppearance).where(
                        CharacterAppearance.work_id == work.id
                    )
                )
            ).scalar_one()
            relation = (
                await db.execute(
                    select(CatalogueRelationship).where(
                        CatalogueRelationship.source_id == work.id
                    )
                )
            ).scalar_one()
            self.assertEqual(
                appearance.attributes, {"spoiler": True, "only_mentioned": False}
            )
            self.assertEqual(relation.attributes, {"position": 1, "compilation": False})

    async def test_steam_price_identity_cannot_be_removed_or_reassigned(self):
        async with self.Session() as db:
            work = await catalogue.ingest_document(
                db, normalize_igdb(fixture("igdb")), "igdb"
            )
            other = await catalogue.upsert_entity(
                db, entity("game", "igdb.game", {"id": 99999, "name": "Other"}), "igdb"
            )
            db.add(
                SteamPriceSnapshot(
                    work_id=work.id, steam_app_id="292030", country="DE", payload={}
                )
            )
            await db.commit()
            for sql in (
                "DELETE FROM catalogue_identities WHERE namespace='steam.app' AND external_id='292030'",
                "UPDATE catalogue_identities SET entity_id=:other WHERE namespace='steam.app' AND external_id='292030'",
            ):
                async with db.begin_nested() as savepoint:
                    with self.assertRaises(IntegrityError):
                        await db.execute(text(sql), {"other": other.id})
                    await savepoint.rollback()

    async def test_linked_performer_credit_cannot_become_nonperformer(self):
        async with self.Session() as db:
            work = await catalogue.ingest_document(
                db, normalize_tvdb(fixture("tvdb")), "tvdb"
            )
            credit = (
                (
                    await db.execute(
                        select(CatalogueCredit).where(
                            CatalogueCredit.work_id == work.id,
                            CatalogueCredit.role == "actor",
                        )
                    )
                )
                .scalars()
                .first()
            )
            character = await catalogue.upsert_entity(
                db,
                entity("character", "igdb.character", {"id": 999, "name": "Character"}),
                "igdb",
            )
            appearance = await catalogue.upsert(
                db,
                CharacterAppearance,
                {
                    "work_id": work.id,
                    "character_id": character.id,
                    "provider": "reviewed",
                },
                ["work_id", "character_id", "provider"],
            )
            performance = await catalogue.link_performance(db, credit.id, appearance.id)
            await db.commit()
            async with db.begin_nested() as savepoint:
                with self.assertRaises(IntegrityError):
                    await db.execute(
                        text("UPDATE catalogue_credits SET role='writer' WHERE id=:id"),
                        {"id": credit.id},
                    )
                await savepoint.rollback()
            self.assertTrue(await catalogue.unlink_performance(db, performance.id))
            await db.flush()
            self.assertFalse(await catalogue.unlink_performance(db, performance.id))
            await db.execute(
                text("UPDATE catalogue_credits SET role='writer' WHERE id=:id"),
                {"id": credit.id},
            )
            await db.refresh(credit)
            self.assertEqual(credit.role, "writer")
            with self.assertRaises(catalogue.IdentityConflict):
                await catalogue.link_performance(db, credit.id, appearance.id)

    async def test_edition_with_credits_cannot_move_to_another_work(self):
        async with self.Session() as db:
            doc = normalize_hardcover(fixture("hardcover"))
            doc["credits"].append(
                {
                    "contributor": entity(
                        "person", "hardcover.author", {"id": 999, "name": "Translator"}
                    ),
                    "role": "translator",
                    "source_key": "edition-translator",
                    "edition_identity": doc["editions"][0]["entity"]["identities"][0],
                }
            )
            work = await catalogue.ingest_document(db, doc, "hardcover")
            other = await catalogue.upsert_entity(
                db,
                entity("book", "hardcover.book", {"id": 99999, "name": "Other"}),
                "hardcover",
            )
            edition = (
                (
                    await db.execute(
                        select(BookEdition).where(BookEdition.work_id == work.id)
                    )
                )
                .scalars()
                .first()
            )
            await db.commit()
            async with db.begin_nested() as savepoint:
                with self.assertRaises(IntegrityError):
                    await db.execute(
                        text(
                            "UPDATE catalogue_book_editions SET work_id=:other WHERE entity_id=:id"
                        ),
                        {"id": edition.entity_id, "other": other.id},
                    )
                await savepoint.rollback()

    async def test_concurrent_performer_insert_and_role_change_are_serialized(self):
        async with self.Session() as db:
            work = await catalogue.ingest_document(
                db, normalize_tvdb(fixture("tvdb")), "tvdb"
            )
            credit = (
                (
                    await db.execute(
                        select(CatalogueCredit).where(
                            CatalogueCredit.work_id == work.id,
                            CatalogueCredit.role == "actor",
                        )
                    )
                )
                .scalars()
                .first()
            )
            char = await catalogue.upsert_entity(
                db,
                entity("character", "igdb.character", {"id": 999, "name": "Character"}),
                "igdb",
            )
            appearance = await catalogue.upsert(
                db,
                CharacterAppearance,
                {"work_id": work.id, "character_id": char.id, "provider": "reviewed"},
                ["work_id", "character_id", "provider"],
            )
            ids = {
                "work_id": work.id,
                "credit_id": credit.id,
                "appearance_id": appearance.id,
            }
            await db.commit()
        for performance_first in (False, True):
            ready, release = asyncio.Event(), asyncio.Event()

            async def first():
                async with self.Session() as db:
                    if performance_first:
                        db.add(CharacterPerformance(**ids, language="en"))
                        await db.flush()
                    else:
                        await db.execute(
                            text(
                                "UPDATE catalogue_credits SET role='writer' WHERE id=:id"
                            ),
                            {"id": ids["credit_id"]},
                        )
                    ready.set()
                    await release.wait()
                    await db.commit()

            async def second():
                async with self.Session() as db:
                    with self.assertRaises(IntegrityError):
                        if performance_first:
                            await db.execute(
                                text(
                                    "UPDATE catalogue_credits SET role='writer' WHERE id=:id"
                                ),
                                {"id": ids["credit_id"]},
                            )
                        else:
                            db.add(CharacterPerformance(**ids, language="en"))
                            await db.flush()
                        await db.commit()
                    await db.rollback()

            task1 = asyncio.create_task(first())
            try:
                await asyncio.wait_for(ready.wait(), 3)
                task2 = asyncio.create_task(second())
                await asyncio.sleep(0.1)
                self.assertFalse(task2.done())
            finally:
                release.set()
            await asyncio.wait_for(asyncio.gather(task1, task2), 5)
            async with self.Session() as db:
                await db.execute(
                    text(
                        "DELETE FROM catalogue_character_performances WHERE credit_id=:id"
                    ),
                    {"id": ids["credit_id"]},
                )
                await db.execute(
                    text("UPDATE catalogue_credits SET role='actor' WHERE id=:id"),
                    {"id": ids["credit_id"]},
                )
                await db.commit()

    async def test_legacy_backfill_preserves_media_ids_and_person_links(self):
        from models.users import User
        from models.events import WatchEvent
        from models.lists import List, ListItem
        from models.tracking import TrackedEntry

        async with self.Session() as db:
            db.add(
                User(
                    id=1,
                    email="test@example.com",
                    username="test",
                    api_key="disposable-key",
                )
            )
            await db.flush()
            movie = Media(id=40, tmdb_id=550, media_type=MediaType.movie, title="Movie")
            person = Media(
                id=41,
                tmdb_id=7,
                media_type=MediaType.person,
                title="Person",
                poster_path="https://image.tmdb.org/t/p/w185/portrait.jpg",
            )
            show = Show(id=42, tvdb_id=121361, title="Show", canonical_source="tvdb")
            db.add_all([movie, person, show])
            await db.commit()
            db.add(List(id=1, user_id=1, name="People"))
            await db.flush()
            db.add_all(
                [
                    ListItem(id=1, list_id=1, media_id=41),
                    WatchEvent(
                        id=1,
                        user_id=1,
                        media_id=40,
                        completed=True,
                        play_count=2,
                        watched_at=datetime(2020, 1, 1),
                    ),
                    TrackedEntry(
                        id=1,
                        user_id=1,
                        media_id=40,
                        status="completed",
                        manual_score=8.5,
                        progress=12,
                    ),
                ]
            )
            await db.commit()
            result = await catalogue.backfill_legacy(db, limit=1)
            self.assertEqual(result["media_cursor"], 40)
            self.assertTrue(result["has_more"])
            result = await catalogue.backfill_legacy(
                db, result["media_cursor"], result["show_cursor"], 1
            )
            self.assertEqual(result["media_cursor"], 41)
            await catalogue.backfill_legacy(db)
            links = (
                (
                    await db.execute(
                        select(CatalogueLegacyLink).order_by(
                            CatalogueLegacyLink.media_id
                        )
                    )
                )
                .scalars()
                .all()
            )
            self.assertEqual([r.media_id for r in links], [40, 41])
            canonical_person = await db.get(CatalogueEntity, links[1].entity_id)
            self.assertEqual(canonical_person.image_url, person.poster_path)
            self.assertEqual((await db.get(Show, 42)).canonical_source, "tvdb")
            self.assertEqual((await db.get(Media, 41)).media_type, MediaType.person)
            self.assertEqual((await db.get(ListItem, 1)).media_id, 41)
            self.assertEqual((await db.get(WatchEvent, 1)).play_count, 2)
            self.assertEqual(
                (await db.get(WatchEvent, 1)).watched_at, datetime(2020, 1, 1)
            )
            self.assertEqual((await db.get(TrackedEntry, 1)).manual_score, 8.5)
            self.assertEqual((await db.get(TrackedEntry, 1)).progress, 12)


@unittest.skipUnless(URL, "Requires disposable catalogue PostgreSQL")
class MigrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_additive_upgrade_existing_rows_and_empty_parents(self):
        if ":55449/catalogue_test" not in URL:
            raise RuntimeError("Refusing non-disposable catalogue DB")
        engine = create_async_engine(URL)
        try:
            for populated in (False, True, "invalid"):
                schema = "catalogue_migration_" + uuid.uuid4().hex
                async with engine.connect() as conn:
                    tx = await conn.begin()
                    await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
                    await conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                    await conn.execute(
                        text("CREATE TABLE media (id integer PRIMARY KEY, title text)")
                    )
                    await conn.execute(
                        text(
                            "CREATE TABLE shows (id integer PRIMARY KEY, canonical_source text)"
                        )
                    )
                    if populated:
                        await conn.execute(
                            text("INSERT INTO media VALUES (17,'Legacy person')")
                        )
                        await conn.execute(text("INSERT INTO shows VALUES (8,'tvdb')"))

                    def upgrade(sync_conn):
                        with Operations.context(MigrationContext.configure(sync_conn)):
                            importlib.import_module(
                                "migrations.versions.mt036_catalogue_foundation"
                            ).upgrade()

                    await conn.run_sync(upgrade)
                    self.assertEqual(
                        (
                            await conn.execute(
                                text("SELECT count(*) FROM catalogue_entities")
                            )
                        ).scalar_one(),
                        0,
                    )
                    if populated:
                        for sql in (
                            "INSERT INTO catalogue_entities(id,kind,name) VALUES(1,'game','Game'),(2,'book','Book'),(3,'edition','Edition'),(4,'person','Narrator'),(5,'character','Character')",
                            "INSERT INTO catalogue_identities(entity_id,namespace,external_id,source) VALUES(1,'steam.app','123','igdb')",
                            "INSERT INTO catalogue_steam_prices(work_id,steam_app_id,country,payload,historical_low,historical_low_at) VALUES(1,'123','DE','{}',jsonb_build_object('amountInt',299),'2024-06-27')",
                            "INSERT INTO catalogue_book_editions(entity_id,work_id,pages) VALUES(3,2,300)",
                            "INSERT INTO catalogue_credits(id,work_id,contributor_id,edition_id,role,provider,source_key,scope) VALUES(1,2,4,3,'narrator','hardcover','one','edition')",
                            "INSERT INTO catalogue_character_appearances(id,work_id,character_id,provider) VALUES(1,2,5,'hardcover')",
                            "INSERT INTO catalogue_character_performances(work_id,credit_id,appearance_id,language) VALUES(2,1,1,'en')",
                        ):
                            await conn.execute(text(sql))

                    def upgrade_integrity(sync_conn):
                        with Operations.context(MigrationContext.configure(sync_conn)):
                            importlib.import_module(
                                "migrations.versions.mt037_catalogue_reference_integrity"
                            ).upgrade()

                    if populated == "invalid":
                        await conn.execute(
                            text(
                                "DELETE FROM catalogue_identities WHERE namespace='steam.app'"
                            )
                        )
                        async with conn.begin_nested() as savepoint:
                            with self.assertRaises(IntegrityError):
                                await conn.run_sync(upgrade_integrity)
                            await savepoint.rollback()
                        self.assertEqual(
                            (
                                await conn.execute(
                                    text("SELECT count(*) FROM catalogue_steam_prices")
                                )
                            ).scalar_one(),
                            1,
                        )
                        self.assertFalse(
                            (
                                await conn.execute(
                                    text(
                                        "SELECT EXISTS(SELECT 1 FROM pg_attribute WHERE attrelid='catalogue_steam_prices'::regclass AND attname='identity_namespace' AND NOT attisdropped)"
                                    )
                                )
                            ).scalar_one()
                        )
                    else:
                        await conn.run_sync(upgrade_integrity)
                    if populated and populated != "invalid":
                        self.assertEqual(
                            (
                                await conn.execute(
                                    text(
                                        "SELECT identity_namespace,historical_low_at FROM catalogue_steam_prices"
                                    )
                                )
                            ).one(),
                            ("steam.app", "2024-06-27"),
                        )
                    if populated:
                        self.assertEqual(
                            (
                                await conn.execute(
                                    text("SELECT pages FROM catalogue_book_editions")
                                )
                            ).scalar_one(),
                            300,
                        )
                        self.assertEqual(
                            (
                                await conn.execute(
                                    text(
                                        "SELECT count(*) FROM catalogue_character_performances"
                                    )
                                )
                            ).scalar_one(),
                            1,
                        )
                        self.assertEqual(
                            (
                                await conn.execute(text("SELECT id,title FROM media"))
                            ).one(),
                            (17, "Legacy person"),
                        )
                        self.assertEqual(
                            (
                                await conn.execute(
                                    text("SELECT canonical_source FROM shows")
                                )
                            ).scalar_one(),
                            "tvdb",
                        )
                    await tx.rollback()
        finally:
            await engine.dispose()


if __name__ == "__main__":
    unittest.main()
