"""Isolate catalogue and list reads while one API owns account workflows."""
import asyncio
import logging
import multiprocessing
import socket
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx
from starlette.responses import JSONResponse, StreamingResponse

from core.api_roles import ApiRole, ReaderRoutes

log = logging.getLogger(__name__)
_HOP_HEADERS = {
    b"connection", b"keep-alive", b"proxy-authenticate", b"proxy-authorization",
    b"te", b"trailer", b"transfer-encoding", b"upgrade",
}


def proxy_headers(headers):
    blocked = set(_HOP_HEADERS)
    for name, value in headers:
        if name.lower() == b"connection":
            blocked.update(part.strip().lower() for part in value.split(b","))
    return [(name, value) for name, value in headers if name.lower() not in blocked]


class ReadOnlyApp:
    def __init__(self, app):
        self.app = app
        self.routes = ReaderRoutes(app.routes) if hasattr(app, 'routes') else None

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        routes = self.routes or scope['app'].state.reader_routes
        if not ((scope['path'], scope['method']) == ('/health', 'GET')
                or routes.allows(scope['path'], scope['method'])):
            return await JSONResponse({"detail": "Route belongs to the primary API"}, status_code=405)(
                scope, receive, send)
        return await self.app(scope, receive, send)


def serve_reader(listener, factory):
    import uvicorn
    from uvicorn.importer import import_from_string
    app = import_from_string(factory)(role=ApiRole.READER)
    config = uvicorn.Config(
        app, host="127.0.0.1", port=listener.getsockname()[1],
        access_log=False, log_level="warning", timeout_graceful_shutdown=10,
        proxy_headers=True, forwarded_allow_ips='127.0.0.1',
    )
    uvicorn.Server(config).run(sockets=[listener])


@dataclass
class Reader:
    process: multiprocessing.Process
    origin: str


class ReadWorkerPool:
    def __init__(self, count, factory="application:create_app"):
        self.count = count
        self.factory = factory
        self.readers = []
        self.cursor = 0
        self.context = multiprocessing.get_context("spawn")
        self.client = httpx.AsyncClient(
            trust_env=False, timeout=httpx.Timeout(60, connect=1, pool=5),
            limits=httpx.Limits(max_connections=200, max_keepalive_connections=100),
        )

    async def start_reader(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen(2048)
            origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
            process = self.context.Process(target=serve_reader, args=(listener, self.factory))
            process.start()
            reader = Reader(process, origin)
            try:
                async with asyncio.timeout(30):
                    while process.is_alive():
                        try:
                            if (await self.client.get(origin + "/health", timeout=1)).status_code == 200:
                                log.info("Started API reader %s", process.pid)
                                return reader
                        except httpx.HTTPError:
                            pass
                        await asyncio.sleep(.1)
                    raise RuntimeError("API reader exited during startup")
            except BaseException:
                await self.stop_reader(reader)
                raise

    async def start(self):
        try:
            for _ in range(self.count):
                self.readers.append(await self.start_reader())
        except BaseException:
            await self.close()
            raise

    async def maintain(self):
        while True:
            await asyncio.sleep(1)
            for index, reader in enumerate(self.readers):
                if reader.process.is_alive():
                    continue
                await self.stop_reader(reader)
                try:
                    self.readers[index] = await self.start_reader()
                except Exception:
                    log.exception("API reader restart failed; primary API continues serving")

    @staticmethod
    async def stop_reader(reader):
        process = reader.process
        if process.is_alive():
            process.terminate()
        await asyncio.to_thread(process.join, 12)
        if process.is_alive():
            process.kill()
            await asyncio.to_thread(process.join)

    async def close(self):
        await asyncio.gather(*(self.stop_reader(reader) for reader in self.readers))
        await self.client.aclose()

    def choose(self):
        available = [reader for reader in self.readers if reader.process.is_alive()]
        if not available:
            return None
        reader = available[self.cursor % len(available)]
        self.cursor += 1
        return reader


@asynccontextmanager
async def read_workers(app, count):
    if not count:
        yield
        return
    pool = ReadWorkerPool(count, getattr(app.state, "read_worker_factory", "application:create_app"))
    await pool.start()
    app.state.read_workers = pool
    watcher = asyncio.create_task(pool.maintain())
    try:
        yield
    finally:
        app.state.read_workers = None
        watcher.cancel()
        await asyncio.gather(watcher, return_exceptions=True)
        await pool.close()


class ReadWorkerMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        application = scope['app']
        pool = getattr(application.state, 'read_workers', None)
        # Global/default limits stay on one owner, preserving aggregate buckets.
        limiter = application.state.limiter
        if not pool or (limiter.enabled and limiter.has_global_limits):
            return await self.app(scope, receive, send)
        if not application.state.reader_routes.allows(scope['path'], scope['method']):
            return await self.app(scope, receive, send)
        if any(name.lower() == b"transfer-encoding" or name.lower() == b"content-length" and value != b"0"
               for name, value in scope["headers"]):
            return await self.app(scope, receive, send)
        reader = pool.choose()
        if reader is None:
            return await self.app(scope, receive, send)
        raw_path = scope.get("raw_path", scope["path"].encode())
        if scope.get("query_string"):
            raw_path += b"?" + scope["query_string"]
        url = httpx.URL(reader.origin).copy_with(raw_path=raw_path)
        headers = [(name, value) for name, value in proxy_headers(scope['headers'])
                   if name.lower() not in (b'forwarded', b'x-forwarded-for', b'x-forwarded-proto')]
        if scope.get('client'):
            headers.append((b'x-forwarded-for', scope['client'][0].encode('ascii')))
        headers.append((b'x-forwarded-proto', scope.get('scheme', 'http').encode('ascii')))
        request = httpx.Request("GET", url, headers=headers)
        try:
            upstream = await pool.client.send(request, stream=True)
        except (httpx.NetworkError, httpx.RemoteProtocolError):
            return await self.app(scope, receive, send)
        except httpx.TimeoutException:
            return await JSONResponse({"detail": "API read timed out"}, status_code=504)(scope, receive, send)
        try:
            response = StreamingResponse(upstream.aiter_raw(), status_code=upstream.status_code)
            response.raw_headers = proxy_headers(upstream.headers.raw)
            await response(scope, receive, send)
        finally:
            await upstream.aclose()
