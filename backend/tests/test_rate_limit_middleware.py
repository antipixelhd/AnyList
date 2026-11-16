import unittest

import httpx
from fastapi import FastAPI, Request, Response
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.responses import StreamingResponse

from core.limiter import GlobalRateLimitMiddleware


class RateLimitMiddlewareTests(unittest.IsolatedAsyncioTestCase):
    def app(self, **options):
        app = FastAPI()
        limiter = Limiter(key_func=get_remote_address, **options)
        app.state.limiter = limiter
        app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
        app.add_middleware(GlobalRateLimitMiddleware)

        @app.get('/limited')
        @limiter.limit('2/minute')
        async def limited(request: Request, response: Response):
            return {'ok': True}

        @app.get('/stream')
        async def stream():
            async def chunks():
                for value in (b'first\x00', b'second', b'third'):
                    yield value
            return StreamingResponse(chunks(), headers={'Cache-Control': 'private'})

        return app

    async def test_route_limit_and_client_isolation_survive_the_fast_path(self):
        app = self.app()
        for host in ('192.0.2.1', '192.0.2.2'):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, client=(host, 123)),
                                         base_url='http://fixture') as client:
                statuses = [(await client.get('/limited')).status_code for _ in range(3)]
                self.assertEqual(statuses, [200, 200, 429])
                stream = await client.get('/stream')
                self.assertEqual(stream.content, b'first\x00secondthird')
                self.assertEqual(stream.headers['cache-control'], 'private')

    async def test_configured_global_limits_still_apply_to_undecorated_routes(self):
        app = self.app(default_limits=['2/minute'])
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture') as client:
            self.assertEqual([(await client.get('/stream')).status_code for _ in range(3)], [200, 200, 429])

    async def test_route_headers_are_still_owned_by_the_limiter(self):
        app = self.app(headers_enabled=True)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture') as client:
            first, second, blocked = [await client.get('/limited') for _ in range(3)]
            self.assertEqual([first.status_code, second.status_code, blocked.status_code], [200, 200, 429])
            self.assertEqual(first.headers['x-ratelimit-limit'], '2')
            self.assertEqual(second.headers['x-ratelimit-remaining'], '0')
            self.assertIn('retry-after', blocked.headers)
