import os
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI, Request, Response
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("SECRET_KEY", "read-worker-tests")

from core.api_roles import ApiRole, ReaderRoutes, reader
from core.limiter import ApiLimiter, GlobalRateLimitMiddleware
from core.read_workers import ReadOnlyApp, ReadWorkerMiddleware


def proxy(app, worker, **limits):
    app.state.reader_routes = ReaderRoutes(app.routes)
    app.state.limiter = ApiLimiter(key_func=get_remote_address, **limits)
    app.state.read_workers = SimpleNamespace(
        choose=lambda: SimpleNamespace(origin="http://reader"), client=worker
    )
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(GlobalRateLimitMiddleware)
    app.add_middleware(ReadWorkerMiddleware)


class ReadWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_routes_opt_in_and_respect_primary_precedence_and_parameter_constraints(
        self,
    ):
        app = FastAPI()

        async def primary():
            return {}

        @reader
        async def detail():
            return {}

        @reader(type="movie")
        async def media():
            return {}

        app.add_api_route("/works/settings", primary)
        app.add_api_route("/works/{id}", detail)
        app.add_api_route("/media/{type}/{id}", media)
        policy = ReaderRoutes(app.routes)
        self.assertTrue(policy.allows("/works/1", "GET"))
        self.assertTrue(policy.allows("/media/movie/1", "GET"))
        for path, method in (
            ("/works/settings", "GET"),
            ("/media/episode/1", "GET"),
            ("/works/1", "PATCH"),
            ("/works/1", "HEAD"),
        ):
            self.assertFalse(policy.allows(path, method))
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=ReadOnlyApp(app)),
            base_url="http://reader",
        ) as client:
            self.assertEqual((await client.get("/works/1")).status_code, 200)
            self.assertEqual((await client.get("/works/settings")).status_code, 405)
            self.assertEqual((await client.patch("/works/1")).status_code, 405)

        @reader(typo="movie")
        async def invalid():
            return {}

        app.add_api_route("/invalid/{type}", invalid)
        with self.assertRaisesRegex(ValueError, "Invalid reader path constraints"):
            ReaderRoutes(app.routes)

    async def test_proxy_preserves_auth_bytes_and_replaces_untrusted_forwarding_headers(
        self,
    ):
        seen = []

        async def upstream(request):
            seen.append(
                (
                    request.headers.get("authorization"),
                    request.headers.get("cookie"),
                    request.url.query,
                    request.headers.get("x-forwarded-for"),
                    request.headers.get("x-forwarded-proto"),
                    request.headers.get("forwarded"),
                )
            )
            return httpx.Response(
                206,
                stream=httpx.ByteStream(b"\x00\xff"),
                headers={
                    "Set-Cookie": "private=first",
                    "ETag": '"current"',
                    "Cache-Control": "private",
                    "Connection": "x-hop",
                    "X-Hop": "remove",
                },
            )

        app = FastAPI()

        @app.get("/tracking/title/{id}")
        @reader
        async def detail():
            raise AssertionError("Should proxy")

        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as worker:
            proxy(app, worker)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, client=("192.0.2.1", 123)),
                base_url="https://primary",
            ) as client:
                for token in ("first", "second"):
                    response = await client.get(
                        "/tracking/title/1?a=one%20two",
                        headers={
                            "Authorization": token,
                            "X-Forwarded-For": "spoofed",
                            "X-Forwarded-Proto": "spoofed",
                            "Forwarded": "for=spoofed",
                        },
                    )
                    self.assertEqual(
                        (response.status_code, response.content), (206, b"\x00\xff")
                    )
                    self.assertEqual(response.headers["etag"], '"current"')
                    self.assertEqual(response.headers["cache-control"], "private")
                    self.assertNotIn("x-hop", response.headers)
                    client.cookies.clear()
        self.assertEqual(
            seen,
            [
                (token, None, b"a=one%20two", "192.0.2.1", "https", None)
                for token in ("first", "second")
            ],
        )

    async def test_failed_reader_falls_back_without_replaying_a_write_or_body(self):
        app = FastAPI()
        calls = []

        @app.api_route("/tracking/title/1", methods=["GET", "PATCH"])
        @reader
        async def detail(request: Request):
            calls.append(request.method)
            return Response("primary")

        worker = SimpleNamespace(
            send=AsyncMock(
                side_effect=[httpx.ConnectError("stopped"), httpx.ReadError("reset")]
            )
        )
        proxy(app, worker)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://primary"
        ) as client:
            for options in ({}, {}, {"content": b"body"}):
                self.assertEqual(
                    (await client.request("GET", "/tracking/title/1", **options)).text,
                    "primary",
                )
            self.assertEqual((await client.patch("/tracking/title/1")).text, "primary")
        self.assertEqual(calls, ["GET", "GET", "GET", "PATCH"])
        self.assertEqual(worker.send.await_count, 2)

    async def test_global_limits_keep_one_owner_and_separate_clients(self):
        app = FastAPI()

        @app.get("/works/{id}")
        @reader
        async def detail():
            return {}

        worker = SimpleNamespace(
            send=AsyncMock(
                side_effect=AssertionError("Global limits must stay on the primary")
            )
        )
        proxy(app, worker, default_limits=["2/minute"])
        for host in ("192.0.2.1", "192.0.2.2"):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, client=(host, 123)),
                base_url="http://primary",
            ) as client:
                self.assertEqual(
                    [(await client.get("/works/1")).status_code for _ in range(3)],
                    [200, 200, 429],
                )
        worker.send.assert_not_awaited()

    async def test_wrapped_rate_limited_routes_automatically_keep_their_owner(self):
        app = FastAPI()
        worker = SimpleNamespace(send=AsyncMock())
        proxy(app, worker)

        @app.get("/limited")
        @app.state.limiter.limit("1/minute")
        @reader
        async def detail(request: Request, response: Response):
            return {}

        app.state.reader_routes = ReaderRoutes(app.routes)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://primary"
        ) as client:
            self.assertEqual(
                [(await client.get("/limited")).status_code for _ in range(2)],
                [200, 429],
            )
        worker.send.assert_not_awaited()

    async def test_factory_roles_share_cleanup_and_only_primary_starts_recovery_and_jobs(
        self,
    ):
        import application as main

        calls = []

        @asynccontextmanager
        async def jobs(*args):
            calls.append("start")
            yield
            calls.append("stop")

        with (
            patch.object(main, "initialize_primary", AsyncMock()) as initialize,
            patch.object(main, "close_clients", AsyncMock()) as close,
            patch.object(main.scheduler, "background_jobs", jobs),
            patch.object(main, "read_workers", jobs),
        ):
            for role in ApiRole:
                app = main.create_app(role=role)
                async with app.router.lifespan_context(app):
                    self.assertEqual(app.state.role, role)
            initialize.assert_awaited_once()
            self.assertEqual(close.await_count, 2)
            self.assertEqual(calls, ["start", "start", "stop", "stop"])
            initialize.side_effect = RuntimeError("startup")
            app = main.create_app()
            with self.assertRaisesRegex(RuntimeError, "startup"):
                async with app.router.lifespan_context(app):
                    pass
            self.assertEqual(close.await_count, 3)
