"""Diagnostic profiling separates request CPU, jobs and child tasks."""
import asyncio
import importlib.util
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy.util import greenlet_spawn

spec = importlib.util.spec_from_file_location('stress_origins', Path(__file__).resolve().parents[2]/'scripts/stress/cpu_profile.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class StressOriginsTests(unittest.IsolatedAsyncioTestCase):
    async def test_request_greenlet_post_response_and_spawned_task_are_distinct(self):
        observed = []
        async def app(scope, receive, send):
            observed.append(module.task_origin())
            observed.append(await greenlet_spawn(module.task_origin))
            async def child():
                return module.task_origin()
            observed.append(await asyncio.create_task(child()))
            await send({'type': 'http.response.body', 'body': b'first', 'more_body': True})
            observed.append(module.task_origin())
            await send({'type': 'http.response.body', 'body': b'last'})
            observed.append(module.task_origin())
        async def unused(*args):
            pass
        with patch.dict(os.environ, {'STRESS_PROFILE': '1'}):
            middleware = module.ProcessTelemetry(app, lambda: None)
        await middleware({'type': 'http', 'method': 'GET', 'path': '/media/movie/123'}, unused, unused)
        self.assertEqual(observed, ['http:GET /media/movie/:id', 'http:GET /media/movie/:id',
            'spawned:GET /media/movie/:id', 'http:GET /media/movie/:id', 'after_response:GET /media/movie/:id'])
        self.assertIsNone(module._request_work.get())

    async def test_failed_request_resets_context(self):
        async def broken(*args):
            raise ValueError('fixture')
        with patch.dict(os.environ, {'STRESS_PROFILE': '1'}):
            middleware = module.ProcessTelemetry(broken, lambda: None)
        with self.assertRaises(ValueError):
            await middleware({'type': 'http', 'method': 'GET', 'path': '/fixture'}, None, None)
        self.assertIsNone(module._request_work.get())
