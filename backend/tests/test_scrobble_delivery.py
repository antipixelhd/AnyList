import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from core import scrobble_delivery
from models.base import MediaType
from models.media import Media
from models.show import Show


class PlaybackForwardingTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_order_and_pause_capability_are_preserved(self):
        for action in ("start", "pause", "stop"):
            with self.subTest(action=action):
                calls = []
                providers = {
                    name: AsyncMock(side_effect=lambda *a, name=name, **kw: calls.append(name))
                    for name in ("trakt", "mdblist", "simkl", "bingebase")
                }
                settings, media, db = object(), object(), object()
                with (
                    patch.object(scrobble_delivery, "trakt_scrobble", providers["trakt"]),
                    patch.object(scrobble_delivery, "mdblist_scrobble", providers["mdblist"]),
                    patch.object(scrobble_delivery, "simkl_scrobble", providers["simkl"]),
                    patch.object(scrobble_delivery.bingebase, "scrobble", providers["bingebase"]),
                ):
                    await scrobble_delivery.forward(settings, media, action, 0.5, db=db)
                expected = ["trakt", "mdblist", "bingebase"] if action == "pause" else list(providers)
                self.assertEqual(calls, expected)
                for name in expected:
                    providers[name].assert_awaited_once_with(settings, media, action, 0.5, db=db)

    async def test_trakt_episode_resolves_unloaded_parent_and_uses_refreshed_token(self):
        settings = SimpleNamespace(user_id=7, trakt_scrobble=True, trakt_access_token="old",
                                   trakt_client_id="client")
        media = Media(media_type=MediaType.episode, show_id=8, tmdb_id=90,
                      season_number=2, episode_number=3)
        show = Show(tmdb_id=80, title="Series")
        db = SimpleNamespace(get=AsyncMock(return_value=show))
        with (
            patch.object(scrobble_delivery.trakt_auth, "ensure_valid_trakt_token_for_user",
                         AsyncMock(return_value="fresh")) as refresh,
            patch.object(scrobble_delivery.trakt_client, "scrobble_episode", AsyncMock()) as send,
        ):
            await scrobble_delivery.trakt_scrobble(settings, media, "stop", 1.5, db=db)
        refresh.assert_awaited_once_with(7)
        db.get.assert_awaited_once_with(Show, 8)
        send.assert_awaited_once_with("client", "fresh", action="stop", season_number=2,
                                     episode_number=3, progress=100.0, show_tmdb_id=80,
                                     show_title="Series", episode_tmdb_id=90)

    async def test_trakt_refresh_outage_does_not_fail_local_playback(self):
        settings = SimpleNamespace(user_id=7, trakt_scrobble=True, trakt_access_token="old",
                                   trakt_client_id="client")
        with (
            patch.object(scrobble_delivery.trakt_auth, "ensure_valid_trakt_token_for_user",
                         AsyncMock(side_effect=RuntimeError("offline"))),
            patch.object(scrobble_delivery.trakt_client, "scrobble_movie", AsyncMock()) as send,
            self.assertLogs("core.scrobble_delivery", level="WARNING"),
        ):
            await scrobble_delivery.trakt_scrobble(settings, Media(media_type=MediaType.movie), "start", 0.2)
        send.assert_not_awaited()

    async def test_mdblist_clears_only_barely_started_stops(self):
        settings = SimpleNamespace(mdblist_scrobble=True, mdblist_api_key="key")
        media = Media(media_type=MediaType.movie, tmdb_id=90)
        for progress, actions in ((0.05, ["stop", "clear"]), (0.051, ["stop"])):
            with self.subTest(progress=progress), patch.object(
                scrobble_delivery.mdblist_client, "scrobble_movie", AsyncMock()
            ) as send:
                await scrobble_delivery.mdblist_scrobble(settings, media, "stop", progress)
            self.assertEqual([call.kwargs["action"] for call in send.await_args_list], actions)
            if len(actions) > 1:
                self.assertIsNone(send.await_args_list[-1].kwargs["progress"])

    async def test_mdblist_outage_does_not_fail_local_playback(self):
        settings = SimpleNamespace(mdblist_scrobble=True, mdblist_api_key="key")
        with (
            patch.object(scrobble_delivery.mdblist_client, "scrobble_movie",
                         AsyncMock(side_effect=RuntimeError("offline"))),
            self.assertLogs("core.scrobble_delivery", level="WARNING"),
        ):
            await scrobble_delivery.mdblist_scrobble(settings, Media(media_type=MediaType.movie, tmdb_id=90),
                                                    "start", 0.2)


class SimklScrobbleFallbackTests(unittest.IsolatedAsyncioTestCase):
    """#328: a Simkl /scrobble/stop that 404s (absolute-numbered anime past
    the first cour) must not silently lose the watch - fall back to
    /sync/history, which maps TMDB numbering onto Simkl's split entries."""

    def _settings(self):
        return SimpleNamespace(
            simkl_scrobble=True, simkl_access_token="tok", simkl_client_id="cid",
        )

    def _episode(self):
        return SimpleNamespace(
            media_type=MediaType.episode, season_number=1, episode_number=25,
            tmdb_id=4562708, show_id=1,
            show=SimpleNamespace(tmdb_id=95479, title="JUJUTSU KAISEN"),
        )

    async def test_successful_stop_does_not_touch_history(self):
        with (
            patch.object(scrobble_delivery.simkl_client, "stop_scrobble_episode", AsyncMock()),
            patch.object(scrobble_delivery.simkl_client, "add_episode_to_history", AsyncMock()) as add_hist,
        ):
            await scrobble_delivery.simkl_scrobble(self._settings(), self._episode(), "stop", 1.0)
        add_hist.assert_not_awaited()

    async def test_failed_stop_at_watched_progress_falls_back_to_history(self):
        with (
            patch.object(scrobble_delivery.simkl_client, "stop_scrobble_episode",
                         AsyncMock(side_effect=RuntimeError("404 Not Found"))),
            patch.object(scrobble_delivery.simkl_client, "add_episode_to_history", AsyncMock()) as add_hist,
        ):
            await scrobble_delivery.simkl_scrobble(self._settings(), self._episode(), "stop", 1.0)
        add_hist.assert_awaited_once_with("cid", "tok", 95479, 1, 25)

    async def test_failed_stop_below_watched_progress_does_not_fall_back(self):
        with (
            patch.object(scrobble_delivery.simkl_client, "stop_scrobble_episode",
                         AsyncMock(side_effect=RuntimeError("404"))),
            patch.object(scrobble_delivery.simkl_client, "add_episode_to_history", AsyncMock()) as add_hist,
        ):
            await scrobble_delivery.simkl_scrobble(self._settings(), self._episode(), "stop", 0.30)
        add_hist.assert_not_awaited()

    async def test_failed_start_does_not_fall_back(self):
        with (
            patch.object(scrobble_delivery.simkl_client, "checkin_episode",
                         AsyncMock(side_effect=RuntimeError("404"))),
            patch.object(scrobble_delivery.simkl_client, "add_episode_to_history", AsyncMock()) as add_hist,
        ):
            await scrobble_delivery.simkl_scrobble(self._settings(), self._episode(), "start", 0.05)
        add_hist.assert_not_awaited()

    async def test_failed_movie_stop_falls_back_to_movie_history(self):
        movie = SimpleNamespace(
            media_type=MediaType.movie, tmdb_id=550, title="Fight Club", release_date="1999-10-15",
        )
        with (
            patch.object(scrobble_delivery.simkl_client, "stop_scrobble_movie",
                         AsyncMock(side_effect=RuntimeError("500"))),
            patch.object(scrobble_delivery.simkl_client, "add_movie_to_history", AsyncMock()) as add_hist,
        ):
            await scrobble_delivery.simkl_scrobble(self._settings(), movie, "stop", 0.95)
        add_hist.assert_awaited_once_with("cid", "tok", 550)

    async def test_disabled_does_nothing(self):
        s = SimpleNamespace(simkl_scrobble=False, simkl_access_token="tok", simkl_client_id="cid")
        with patch.object(scrobble_delivery.simkl_client, "stop_scrobble_episode", AsyncMock()) as stop:
            await scrobble_delivery.simkl_scrobble(s, self._episode(), "stop", 1.0)
        stop.assert_not_awaited()

    async def test_history_fallback_that_simkl_also_rejects_is_swallowed(self):
        # #328 follow-up: /sync/history resolves the tmdb id to Simkl's own
        # layout too, so the season-split mismatch comes back as not_found
        # inside a 201. add_episode_to_history now raises on that; the webhook
        # must log it, not crash and not claim success.
        with (
            patch.object(scrobble_delivery.simkl_client, "stop_scrobble_episode",
                         AsyncMock(side_effect=RuntimeError("404 Not Found"))),
            patch.object(scrobble_delivery.simkl_client, "add_episode_to_history",
                         AsyncMock(side_effect=scrobble_delivery.simkl_client.SimklHistoryRejected("not_found"))),
        ):
            with self.assertLogs("core.scrobble_delivery", level="WARNING") as logs:
                await scrobble_delivery.simkl_scrobble(self._settings(), self._episode(), "stop", 1.0)
        self.assertTrue(any("fallback also failed" in m for m in logs.output))
