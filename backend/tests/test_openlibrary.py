import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

import httpx
from core import openlibrary
from core.catalogue_providers import CatalogueProviders, ProviderError, ProviderHTTP
from core.config import settings


def sample():
    return {
        "key": "/works/OL1W",
        "title": "Sample book",
        "description": {"value": "Synopsis"},
        "subject_people": ["A fictional character"],
        "subjects": ["Fantasy"],
        "covers": [123],
        "authors": [{"author": {"key": "/authors/OL1A"}}],
        "_authors": {
            "OL1A": {"key": "/authors/OL1A", "name": "Writer", "bio": "Biography"}
        },
        "_availability": {
            "ia": ["sample-book", "https://evil"],
            "ebook_access": "public",
        },
        "_editions": [
            {
                "key": "/books/OL1M",
                "title": "Edition",
                "works": [{"key": "/works/OL1W"}],
                "isbn_13": ["9783551354013", "9780000000000"],
                "isbn_10": ["3551354014"],
                "publish_date": "2000",
                "number_of_pages": 334,
                "physical_format": "Paperback",
                "languages": [{"key": "/languages/ger"}],
                "publishers": ["Carlsen"],
                "lccn": ["1234"],
                "table_of_contents": [{"title": "Chapter"}],
            }
        ],
    }


class OpenLibraryTests(unittest.IsolatedAsyncioTestCase):
    def test_metadata_projection_keeps_work_edition_and_subject_boundaries(self):
        doc = openlibrary.normalize(sample())
        self.assertEqual(doc["work"]["identities"][0]["external_id"], "OL1W")
        self.assertEqual(doc["characters"], [])
        self.assertEqual(
            doc["work"]["attributes"]["reading_links"],
            ["https://archive.org/details/sample-book"],
        )
        self.assertEqual(len(doc["editions"][0]["entity"]["identities"]), 3)
        self.assertIsNone(doc["editions"][0]["release_date"])
        self.assertEqual(doc["editions"][0]["entity"]["attributes"]["lccn"], ["1234"])

    async def test_identified_search_needs_no_key_and_makes_no_hardcover_calls(self):
        requests = []

        async def handle(request):
            requests.append(request)
            return httpx.Response(
                200, json={"docs": [{"key": "/works/OL1W", "title": "Sample"}]}
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with patch.object(
                settings, "openlibrary_contact_email", "contact@example.org"
            ):
                adapter = CatalogueProviders(ProviderHTTP(client))
                rows = await adapter.search("openlibrary", "book", "Sample")
        self.assertEqual(rows[0]["external_id"], "OL1W")
        self.assertEqual(len(requests), 1)
        self.assertEqual(
            requests[0].headers["user-agent"],
            "AnyList catalogue/1 (contact@example.org)",
        )
        self.assertNotIn("authorization", requests[0].headers)

    async def test_isbn_redirect_is_validated_and_checksum_verified(self):
        raw = sample()["_editions"][0]
        http = AsyncMock()
        http.request.side_effect = [
            {"_redirect": "https://openlibrary.org/books/OL1M.json"},
            raw,
        ]
        self.assertEqual(
            await openlibrary.lookup_isbn(http, "978-3-551-35401-3"), "OL1M"
        )
        http.request.side_effect = [
            {"_redirect": "https://evil.example/books/OL1M.json"}
        ]
        with self.assertRaises(ProviderError):
            await openlibrary.lookup_isbn(http, "9783551354013")
        http.request.side_effect = [{**raw, "isbn_13": [], "isbn_10": []}]
        with self.assertRaisesRegex(ProviderError, "identity_conflict"):
            await openlibrary.lookup_isbn(http, "9783551354013")

    async def test_real_http_redirect_handling_is_bounded(self):
        async def handle(request):
            if request.url.path.startswith("/isbn/"):
                return httpx.Response(302, headers={"Location": "/books/OL1M.json"})
            return httpx.Response(200, json=sample()["_editions"][0])

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            self.assertEqual(
                await openlibrary.lookup_isbn(ProviderHTTP(client), "3551354014"),
                "OL1M",
            )

    async def test_selected_isbn_edition_is_retained_outside_first_page(self):
        raw = sample()
        http = AsyncMock()
        http.request.side_effect = [
            raw["_editions"][0],
            raw,
            {"docs": []},
            {"entries": [], "size": 40},
            raw["_authors"]["OL1A"],
        ]
        doc, payload = await CatalogueProviders(http).detail(
            "openlibrary", "book", "OL1M"
        )
        self.assertEqual(
            doc["editions"][0]["entity"]["identities"][0]["external_id"], "OL1M"
        )
        self.assertTrue(payload["_coverage"]["has_more_editions"])
        self.assertEqual(http.request.await_count, 5)

    async def test_edition_pagination_and_path_injection_rejection(self):
        raw = sample()
        http = AsyncMock()
        http.request.side_effect = [
            raw,
            {"docs": []},
            {"entries": raw["_editions"], "size": 40},
            raw["_authors"]["OL1A"],
        ]
        doc, _ = await CatalogueProviders(http).openlibrary_editions_page("OL1W", 2)
        self.assertFalse(doc["coverage"]["editions_complete"])
        self.assertEqual(http.request.await_args_list[2].kwargs["params"]["offset"], 20)
        with self.assertRaises(ProviderError):
            await CatalogueProviders(http).detail("openlibrary", "book", "OL1W?x=/evil")

    async def test_ambiguous_edition_work_mapping_refuses_import(self):
        edition = sample()["_editions"][0]
        http = AsyncMock()
        http.request.return_value = {
            **edition,
            "works": [{"key": "/works/OL1W"}, {"key": "/works/OL2W"}],
        }
        with self.assertRaisesRegex(ProviderError, "identity_conflict"):
            await CatalogueProviders(http).detail("openlibrary", "book", "OL1M")
