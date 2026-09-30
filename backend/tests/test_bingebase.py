import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from core import bingebase
from models.base import MediaType
from models.media import Media
from models.show import Show


class TestBingebaseScrobble(unittest.IsolatedAsyncioTestCase):
    async def test_episode_delivery_loads_parent_without_implicit_lazy_io(self):
        settings = SimpleNamespace(bingebase_scrobble=True, bingebase_webhook_url="https://provider.test/hook", bingebase_api_key=None)
        media = Media(media_type=MediaType.episode, show_id=7, title="Episode", tmdb_id=123, season_number=2, episode_number=3)
        db = SimpleNamespace(get=AsyncMock(return_value=Show(id=7, title="Series", tmdb_id=456)))
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as post:
            await bingebase.scrobble(settings, media, "pause", 0.25, db=db)
        db.get.assert_awaited_once_with(Show, 7)
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["Event"], "playback.pause")
        self.assertEqual(payload["Percentage"], 25)
        self.assertEqual(payload["Item"]["SeriesName"], "Series")
        self.assertEqual(payload["Item"]["ProviderIds"]["ShowTmdb"], "456")
        self.assertEqual((payload["Item"]["ParentIndexNumber"], payload["Item"]["IndexNumber"]), (2, 3))
        self.assertNotIn("Authorization", post.call_args.kwargs["headers"])

    async def test_delivery_outage_is_logged_without_failing_local_playback(self):
        settings = SimpleNamespace(bingebase_scrobble=True, bingebase_webhook_url="https://provider.test/hook", bingebase_api_key=None)
        media = SimpleNamespace(media_type=MediaType.movie, title="Movie", tmdb_id=123, imdb_id=None)
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, side_effect=RuntimeError("offline")), \
             self.assertLogs("core.bingebase", level="WARNING") as logs:
            await bingebase.scrobble(settings, media, "stop", 1.0)
        self.assertIn("offline", logs.output[0])

    async def test_maybe_bingebase_scrobble_disabled(self):
        settings = SimpleNamespace(bingebase_scrobble=False, bingebase_webhook_url="https://bingebase.com/api/webhook")
        media = SimpleNamespace(media_type="movie", title="Test", tmdb_id=550, imdb_id=None)
        with patch("httpx.AsyncClient.post") as mock_post:
            await bingebase.scrobble(settings, media, "start", 0.5)
            mock_post.assert_not_called()

    async def test_maybe_bingebase_scrobble_enabled(self):
        settings = SimpleNamespace(
            bingebase_scrobble=True,
            bingebase_webhook_url="https://bingebase.com/api/webhook",
            bingebase_api_key="secret-token"
        )
        media = SimpleNamespace(media_type="movie", title="Fight Club", tmdb_id=550, imdb_id="tt0137523")
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            await bingebase.scrobble(settings, media, "stop", 0.95)
            mock_post.assert_called_once()
            args, kwargs = mock_post.call_args
            self.assertEqual(args[0], "https://bingebase.com/api/webhook")
            self.assertEqual(kwargs["json"]["Event"], "playback.stop")
            self.assertEqual(kwargs["json"]["Item"]["ProviderIds"]["Tmdb"], "550")
            self.assertEqual(kwargs["headers"]["Authorization"], "Bearer secret-token")
