import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
from fastapi import FastAPI, Request
from starlette.responses import Response

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")
os.environ.setdefault("SECRET_KEY", "read-worker-tests")

from core.read_workers import ReadOnlyApp, ReadWorkerMiddleware, read_route


class ReadWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_reviewed_reads_can_enter_a_reader(self):
        app = FastAPI()

        @app.api_route("/{path:path}", methods=["GET", "PATCH", "POST"])
        async def detail(path: str):
            return {"path": path}

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=ReadOnlyApp(app)),
                                     base_url="http://fixture") as client:
            for method, path, expected in (
                ("GET", "/tracking/games/works/12", 200),
                ("GET", "/tracking/title/12/people", 200),
                ("PATCH", "/tracking/games/entries/12", 405),
                ("GET", "/auth/api-key", 405),
                ("GET", "/catalogue/search", 405),
                ("GET", "/imports/netflix/session", 405),
                ("GET", "/sync/jobs", 405),
            ):
                self.assertEqual((await client.request(method, path)).status_code, expected)
        self.assertFalse(read_route("/tracking/games/works/1", "HEAD"))

    async def test_proxy_preserves_auth_bytes_and_does_not_reuse_response_cookies(self):
        seen = []

        async def upstream(request):
            seen.append((request.headers.get("authorization"), request.headers.get("cookie"), request.url.query))
            return httpx.Response(206, stream=httpx.ByteStream(b"\x00\xff"), headers={
                "Set-Cookie": "private=first", "ETag": '"current"', "Cache-Control": "private",
                "Connection": "x-hop", "X-Hop": "remove",
            })

        app = FastAPI()
        transport = httpx.MockTransport(upstream)
        async with httpx.AsyncClient(transport=transport) as worker:
            app.state.read_workers = SimpleNamespace(
                choose=lambda: SimpleNamespace(origin="http://reader"), client=worker)
            app.add_middleware(ReadWorkerMiddleware)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                         base_url="http://primary") as client:
                for token in ("first", "second"):
                    response = await client.get("/tracking/title/1?a=one%20two", headers={"Authorization": token})
                    self.assertEqual(response.status_code, 206)
                    self.assertEqual(response.content, b"\x00\xff")
                    self.assertEqual(response.headers["etag"], '"current"')
                    self.assertEqual(response.headers["cache-control"], "private")
                    self.assertNotIn("x-hop", response.headers)
                    client.cookies.clear()
        self.assertEqual(seen, [("first", None, b"a=one%20two"), ("second", None, b"a=one%20two")])

    async def test_failed_reader_falls_back_without_replaying_an_account_write(self):
        app = FastAPI()
        calls = []

        @app.api_route("/tracking/title/1", methods=["GET", "PATCH"])
        async def detail(request: Request):
            calls.append(request.method)
            return Response("primary")

        worker = SimpleNamespace(send=AsyncMock(side_effect=[
            httpx.ConnectError("stopped"), httpx.ReadError("connection reset before headers")]))
        app.state.read_workers = SimpleNamespace(
            choose=lambda: SimpleNamespace(origin="http://reader"), client=worker)
        app.add_middleware(ReadWorkerMiddleware)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://primary") as client:
            self.assertEqual((await client.get("/tracking/title/1")).text, "primary")
            self.assertEqual((await client.get("/tracking/title/1")).text, "primary")
            self.assertEqual((await client.request("GET", "/tracking/title/1", content=b"body")).text, "primary")
            self.assertEqual((await client.patch("/tracking/title/1", json={"score": 8})).text, "primary")
        self.assertEqual(calls, ["GET", "GET", "GET", "PATCH"])
        self.assertEqual(worker.send.await_count, 2)
