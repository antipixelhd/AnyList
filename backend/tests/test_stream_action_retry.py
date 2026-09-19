import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://test:test@localhost/test",
)

import main
from core import cloud_actions, stream_actions


class _Scalars:
    def __init__(self, values):
        self.values = values

    def all(self):
        return self.values


class _Result:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return _Scalars(self.values)


class _Session:
    def __init__(self, user_ids=()):
        self.execute = AsyncMock(return_value=_Result(list(user_ids)))

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _Factory:
    def __init__(self, user_ids):
        self.sessions = [_Session(user_ids)] + [_Session() for _ in user_ids]

    def __call__(self):
        return self.sessions.pop(0)


class StreamActionRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_retries_every_pending_user_in_a_separate_session(self):
        factory = _Factory([4, 9])
        dispatch = AsyncMock()
        cloud_dispatch = AsyncMock()
        with patch.object(stream_actions, "dispatch_stream_actions", dispatch), \
             patch.object(cloud_actions, "dispatch_cloud_actions", cloud_dispatch):
            await main._dispatch_pending_stream_actions_once(factory)

        self.assertEqual([call.args[1] for call in dispatch.await_args_list], [4, 9])
        self.assertEqual([call.args[1] for call in cloud_dispatch.await_args_list], [4, 9])
        self.assertIsNot(dispatch.await_args_list[0].args[0], dispatch.await_args_list[1].args[0])

    async def test_one_user_failure_does_not_block_the_next(self):
        factory = _Factory([4, 9])
        dispatch = AsyncMock(side_effect=[RuntimeError("remote body"), None])
        cloud_dispatch = AsyncMock()
        with patch.object(stream_actions, "dispatch_stream_actions", dispatch), \
             patch.object(cloud_actions, "dispatch_cloud_actions", cloud_dispatch):
            await main._dispatch_pending_stream_actions_once(factory)

        self.assertEqual([call.args[1] for call in dispatch.await_args_list], [4, 9])
        self.assertEqual([call.args[1] for call in cloud_dispatch.await_args_list], [9])
