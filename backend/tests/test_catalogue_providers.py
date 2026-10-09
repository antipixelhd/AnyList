import json
import logging
import os
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

import httpx
from core import catalogue_normalize as normalize
from core import catalogue_providers as providers
from core.config import settings

FIXTURES = Path(__file__).parent / "fixtures" / "catalogue"


def fixture(name):
    return json.loads((FIXTURES / (name + ".json")).read_text(encoding="utf-8"))


class NormalizationTests(unittest.TestCase):
    def test_game_cross_reference_and_separate_character_art(self):
        doc = normalize.normalize_igdb(fixture("igdb"))
        self.assertIn(
            normalize.identity("steam.app", "292030"), doc["work"]["identities"]
        )
        self.assertTrue(any(c["character"]["image_url"] for c in doc["characters"]))
        self.assertTrue(all(r["entity"]["kind"] == "release" for r in doc["releases"]))
        self.assertTrue(any(c["role"] == "developer" for c in doc["credits"]))

    def test_book_editions_isbns_and_association_ids(self):
        doc = normalize.normalize_hardcover(fixture("hardcover"))
        self.assertEqual(doc["work"]["kind"], "book")
        self.assertTrue(doc["editions"])
        self.assertEqual(
            doc["characters"][0]["character"]["identities"][0]["external_id"], "1"
        )
        self.assertEqual(doc["relationships"][0]["target"]["kind"], "book_series")
        self.assertTrue(any(c["role"] == "author" for c in doc["credits"]))
        self.assertIsNone(normalize.isbn("9780000000000", 13))
        self.assertEqual(normalize.isbn("978-0-7475-3269-9", 13), "9780747532699")

    def test_tvdb_associations_never_become_fictional_identity(self):
        doc = normalize.normalize_tvdb(fixture("tvdb"))
        self.assertEqual(doc["characters"], [])
        self.assertEqual(
            doc["credits"][0]["contributor"]["identities"][0]["external_id"], "247831"
        )
        self.assertIn("/photo/", doc["credits"][0]["contributor"]["image_url"])
        self.assertIn(
            normalize.identity("tmdb.series", "1399"), doc["work"]["identities"]
        )

    def test_tmdb_multiple_roles_share_one_person(self):
        doc = normalize.normalize_tmdb(
            {
                "id": 1,
                "name": "Show",
                "aggregate_credits": {
                    "cast": [
                        {
                            "id": 2,
                            "name": "Actor",
                            "roles": [
                                {"credit_id": "a", "character": "One"},
                                {"credit_id": "b", "character": "Two"},
                            ],
                        }
                    ]
                },
            },
            "series",
        )
        self.assertEqual(len(doc["credits"]), 2)
        self.assertEqual(
            doc["credits"][0]["contributor"]["identities"],
            doc["credits"][1]["contributor"]["identities"],
        )
        self.assertEqual(doc["characters"], [])

    def test_rawg_steam_mapping_requires_exact_store_url(self):
        doc = normalize.normalize_rawg(fixture("rawg"))
        self.assertIn(
            normalize.identity("steam.app", "292030"), doc["work"]["identities"]
        )
        for url in (
            "https://store.steampowered.com.evil/app/292030",
            "https://evil/app/292030",
            "http://store.steampowered.com/app/292030",
            "javascript:alert(1)",
        ):
            self.assertIsNone(normalize.steam_app(url))
        self.assertIsNone(normalize.image("javascript:alert(1)"))


class ClientTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.keys = patch.multiple(
            settings,
            igdb_client_id="fake-client",
            igdb_client_secret="fake-secret",
            hardcover_api_key="fake-hc",
            rawg_api_key="fake-rawg",
            itad_api_key="fake-itad",
        )
        self.keys.start()
        self.addCleanup(self.keys.stop)
        self.intervals = patch.dict(
            providers.INTERVALS, {k: 0 for k in providers.INTERVALS}
        )
        self.intervals.start()
        self.addCleanup(self.intervals.stop)
        providers._cache.clear()
        providers._local_next.clear()
        providers._local_locks.clear()
        providers._tokens.clear()
        providers._token_locks.clear()

    async def test_steam_only_low_date_and_unchanged_link(self):
        requests = []

        def handle(request):
            requests.append(request)
            names = {
                "/games/lookup/v1": "itad-lookup",
                "/games/prices/v3": "itad-prices",
                "/games/storelow/v2": "itad-storelow",
            }
            data = fixture(names[request.url.path])
            if request.url.path == "/games/prices/v3":
                data[0]["deals"].append(
                    {"shop": {"id": 35}, "price": {"amount": 0.01}, "url": "wrong"}
                )
                self.assertEqual(request.url.params["shops"], "61")
                self.assertIsInstance(json.loads(request.content), list)
            return httpx.Response(200, json=data)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            adapter = providers.CatalogueProviders(providers.ProviderHTTP(client))
            result = await adapter.steam_prices("292030", "DE")
        self.assertEqual(result["historical_low"]["amountInt"], 299)
        self.assertEqual(result["historical_low_at"], "2024-06-27T19:30:27+02:00")
        self.assertEqual(result["url"], fixture("itad-prices")[0]["deals"][0]["url"])
        self.assertEqual(requests[0].url.params["appid"], "292030")

    async def test_oversized_response_stops_stream_and_closes_without_caching(self):
        class Body(httpx.AsyncByteStream):
            consumed = 0
            closed = False

            async def __aiter__(self):
                for _ in range(20):
                    self.consumed += 1
                    yield b"x" * 65536

            async def aclose(self):
                self.closed = True

        for advertised in (False, True):
            body = Body()
            headers = {"Content-Length": "9999999"} if advertised else {}
            with patch.object(providers, "MAX_RESPONSE_BYTES", 100000):
                async with httpx.AsyncClient(
                    transport=httpx.MockTransport(
                        lambda r: httpx.Response(200, headers=headers, stream=body)
                    )
                ) as client:
                    with self.assertRaisesRegex(
                        providers.ProviderError, "^payload_too_large$"
                    ):
                        await providers.ProviderHTTP(client).request(
                            "rawg", "GET", "https://api.rawg.io/test"
                        )
            self.assertEqual(body.consumed, 0 if advertised else 2)
            self.assertTrue(body.closed)
            self.assertEqual(len(providers._cache), 0)

    async def test_cache_has_a_total_byte_bound_and_date_retry_after_is_respected(self):
        calls = []

        def handle(request):
            calls.append(request)
            return httpx.Response(200, json={"text": "x" * 200})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            api = providers.ProviderHTTP(client)
            with (
                patch.object(providers, "MAX_CACHE_BYTES", 300),
                patch.object(providers.asyncio, "sleep", new=AsyncMock()),
            ):
                await api.request("rawg", "GET", "https://api.rawg.io/one")
                await api.request("rawg", "GET", "https://api.rawg.io/two")
                self.assertLessEqual(
                    sum(len(v[1]) for v in providers._cache.values()), 300
                )
                await api.request("rawg", "GET", "https://api.rawg.io/one")
                self.assertEqual(len(calls), 3)
        retry = format_datetime(
            datetime.now(timezone.utc) + timedelta(minutes=10), usegmt=True
        )
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(429, headers={"Retry-After": retry}, json={})
            )
        ) as client:
            with self.assertRaises(providers.ProviderError) as ctx:
                await providers.ProviderHTTP(client).request(
                    "itad", "GET", "https://api.isthereanydeal.com/throttled"
                )
            self.assertGreater(ctx.exception.retry_after, 590)
            self.assertLessEqual(ctx.exception.retry_after, 600)

    async def test_game_edition_retains_native_and_steam_aliases_on_parent_work(self):
        adapter = providers.CatalogueProviders()
        version = {
            "id": 43,
            "name": "Edition",
            "version_parent": 42,
            "external_games": [
                {
                    "uid": "123",
                    "url": "https://store.steampowered.com/app/123/",
                    "external_game_source": {"name": "Steam"},
                }
            ],
        }
        adapter.igdb = AsyncMock(side_effect=[[version], [{"id": 42, "name": "Base"}]])
        adapter._igdb_pages = AsyncMock(return_value=([], True))
        doc, raw = await adapter.detail("igdb", "game", "43")
        self.assertEqual(doc["work"]["name"], "Base")
        self.assertIn(normalize.identity("igdb.game", 43), doc["work"]["identities"])
        self.assertIn(normalize.identity("steam.app", 123), doc["work"]["identities"])
        self.assertEqual(
            doc["releases"][0]["entity"]["identities"],
            [normalize.identity("igdb.game_version", 43)],
        )
        related = normalize.normalize_igdb(
            {
                "id": 1,
                "name": "Other",
                "similar_games": [
                    {
                        "id": 43,
                        "name": "Edition",
                        "version_parent": {"id": 42, "name": "Base"},
                    }
                ],
            }
        )
        self.assertEqual(
            related["relationships"][0]["target"]["identities"],
            [normalize.identity("igdb.game", 42), normalize.identity("igdb.game", 43)],
        )

    async def test_igdb_expired_token_exchanges_once_and_does_not_recurse(self):
        calls = []

        def handle(request):
            calls.append(request.url.host)
            if request.url.host == "id.twitch.tv":
                return httpx.Response(
                    200, json={"access_token": "fake-token", "expires_in": 3600}
                )
            return httpx.Response(401, json={"secret": "must not escape"})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            adapter = providers.CatalogueProviders(providers.ProviderHTTP(client))
            with self.assertRaises(providers.ProviderError) as context:
                await adapter.igdb("games", "fields id; limit 1;")
        self.assertEqual(str(context.exception), "unauthorized")
        self.assertEqual(len(calls), 4)

    async def test_hardcover_errors_are_not_empty_success_and_not_logged(self):
        def handle(request):
            return httpx.Response(
                200, json={"errors": [{"message": "private-token-value"}]}
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            adapter = providers.CatalogueProviders(providers.ProviderHTTP(client))
            with self.assertRaisesRegex(providers.ProviderError, "^graphql_error$"):
                await adapter.hardcover("query { books {id} }")
        record = logging.LogRecord(
            "httpx",
            20,
            "",
            1,
            "GET https://api.rawg.io/api/games?key=private-token-value",
            (),
            None,
        )
        self.assertFalse(providers._PrivateRequests().filter(record))

    async def test_retry_budget_cache_and_large_retry_after(self):
        calls = []

        def handle(request):
            calls.append(request)
            return httpx.Response(503 if len(calls) < 3 else 200, json={"results": []})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            api = providers.ProviderHTTP(client)
            with patch.object(providers.asyncio, "sleep", new=AsyncMock()):
                await api.request("rawg", "GET", "https://api.rawg.io/api/games")
                await api.request("rawg", "GET", "https://api.rawg.io/api/games")
            self.assertEqual(len(calls), 3)
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(429, headers={"Retry-After": "300"}, json={})
            )
        ) as client:
            with self.assertRaises(providers.ProviderError) as ctx:
                await providers.ProviderHTTP(client).request(
                    "itad", "GET", "https://api.isthereanydeal.com/test"
                )
            self.assertEqual(ctx.exception.retry_after, 300)

    async def test_pagination_is_bounded_and_coverage_explicit(self):
        adapter = providers.CatalogueProviders()
        adapter.igdb = AsyncMock(return_value=[{"id": i} for i in range(50)])
        rows, complete = await adapter._igdb_pages("characters", "fields id;")
        self.assertEqual(len(rows), 250)
        self.assertFalse(complete)
        self.assertEqual(adapter.igdb.await_count, 5)
        adapter.hardcover = AsyncMock(
            return_value={"editions": [{"id": i} for i in range(50)]}
        )
        rows, complete = await adapter._hc_pages("editions", "book_id:{_eq:1}", "id")
        self.assertEqual(len(rows), 250)
        self.assertFalse(complete)
        self.assertEqual(adapter.hardcover.await_count, 5)

    async def test_hardcover_editions_resume_without_claiming_full_coverage(self):
        adapter = providers.CatalogueProviders()
        adapter.hardcover = AsyncMock(
            side_effect=[
                {"books": [{"id": 2, "canonical_id": 3, "title": "Alias"}]},
                {"books": [{"id": 3, "title": "Canonical"}]},
                {"editions": [{"id": i, "title": "Edition"} for i in range(50)]},
            ]
        )
        adapter._hc_pages = AsyncMock(return_value=([], True))
        doc, raw = await adapter.book_editions_page(2, 6)
        self.assertEqual(doc["work"]["identities"][-1]["external_id"], "2")
        self.assertEqual(raw["_coverage"]["next_edition_page"], 7)
        self.assertEqual(len(doc["editions"]), 50)
        self.assertIn("offset:250", adapter.hardcover.call_args.args[0])
        self.assertIn("book_id:{_eq:3}", adapter.hardcover.call_args.args[0])

    async def test_rejects_unsupported_provider_kind_and_query_injection(self):
        adapter = providers.CatalogueProviders()
        with self.assertRaisesRegex(providers.ProviderError, "unsupported"):
            await adapter.detail("rawg", "book", "1")
        with self.assertRaisesRegex(providers.ProviderError, "invalid_request"):
            await adapter.detail("igdb", "game", "1; drop")
        adapter.igdb = AsyncMock(return_value=[])
        await adapter.search("igdb", "game", 'x"; fields *; //', 1, 2)
        self.assertIn('search "x\\"; fields *; //";', adapter.igdb.call_args.args[1])


if __name__ == "__main__":
    unittest.main()
