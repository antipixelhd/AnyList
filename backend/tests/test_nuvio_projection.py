"""Focused Nuvio projection behavior, independent of HTTP sync orchestration."""

import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from core import nuvio, nuvio_payloads, nuvio_projection
from models.base import MediaType
from models.media import Media
from models.playback_progress import PlaybackProgress
from models.show import Show


class _Result:
    def __init__(self, *, scalars=None, rows=None):
        self._scalars = scalars or []
        self._rows = rows or []

    def scalars(self):
        return _Result(rows=self._scalars)

    def all(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar_one_or_none(self):
        return self._scalars[0] if self._scalars else None

    def scalar_one(self):
        if len(self._scalars) != 1:
            raise AssertionError(f"expected one scalar result, got {len(self._scalars)}")
        return self._scalars[0]



class NuvioMetadataProjectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_imdb_ids_are_resolved_and_cached(self) -> None:
        movie = Media(
            id=10,
            tmdb_id=550,
            media_type=MediaType.movie,
            title="Fight Club",
            tmdb_data={},
        )
        episode = Media(
            id=11,
            media_type=MediaType.episode,
            title="It's All Good",
            show_id=5,
            season_number=3,
            episode_number=2,
        )
        show = Show(id=5, tmdb_id=125988, title="Silo", tmdb_data={})

        async def external_ids(tmdb_id: int, media_type: str, api_key: str | None = None) -> dict:
            self.assertEqual(api_key, "tmdb-token")
            return {
                "imdb_id": {
                    ("movie", 550): "tt0137523",
                    ("tv", 125988): "tt14688458",
                }[(media_type, tmdb_id)]
            }

        with patch("core.nuvio_projection.tmdb.get_external_ids", side_effect=external_ids) as get_external_ids:
            await nuvio_projection.ensure_imdb_ids(
                [movie, episode],
                {show.id: show},
                "tmdb-token",
            )

        self.assertEqual(get_external_ids.await_count, 2)
        self.assertEqual(movie.tmdb_data["external_ids"]["imdb_id"], "tt0137523")
        self.assertEqual(show.tmdb_data["external_ids"]["imdb_id"], "tt14688458")



class NuvioSyntheticProgressTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_push_clears_only_old_resume_for_same_watching_title(self) -> None:
        desired=[{"content_id":"tt1234567","progress_key":"tt1234567_s1e4"}]
        remote=[
            {"content_id":"tt1234567","progress_key":"tt1234567_s1e3"},
            {"content_id":"tt1234567","progress_key":"tt1234567_s1e4"},
            {"content_id":"tt9999999","progress_key":"tt9999999_s1e1"},
        ]
        self.assertEqual(nuvio_payloads.obsolete_progress_keys(remote, desired),
            ["tt1234567_s1e3"])

    async def test_full_push_history_omits_deleted_titles_but_keeps_listed_episodes(self) -> None:
        now = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
        old_show = Show(id=5, tmdb_id=1396, title="Removed",
            tmdb_data={"external_ids": {"imdb_id": "tt0903747"}})
        kept_show = Show(id=6, tmdb_id=1400, title="Listed",
            tmdb_data={"external_ids": {"imdb_id": "tt1234567"}})
        old_episode = Media(id=21, media_type=MediaType.episode, title="Old", show_id=5,
            season_number=1, episode_number=1)
        kept_episode = Media(id=22, media_type=MediaType.episode, title="Kept", show_id=6,
            season_number=1, episode_number=1)
        db = SimpleNamespace(execute=AsyncMock(side_effect=[
            _Result(rows=[(21, now, now), (22, now, now)]),
            _Result(scalars=[old_episode, kept_episode]),
            _Result(scalars=[old_show, kept_show]),
            _Result(rows=[(10, 1400, None, MediaType.series)]),
        ]))

        with patch("core.nuvio_projection.ensure_imdb_ids", AsyncMock()):
            items = await nuvio_projection.build_watched_items(db, user_id=7, tracked_only=True)

        self.assertEqual([(item["content_id"], item["episode"]) for item in items],
            [("tt1234567", 1)])

    async def test_outbound_progress_uses_observed_nuvio_content_id(self) -> None:
        movie = Media(id=10, tmdb_id=550, media_type=MediaType.movie,
            title="Fight Club", imdb_id="tt0137523")
        baseline = SimpleNamespace(snapshot={"mappings": {"tmdb:550": 550}})
        payload = nuvio_payloads.remap_payload({
            "content_id": "tt0137523", "content_type": "movie",
            "video_id": "tt0137523", "progress_key": "tt0137523",
            "position": 1000, "duration": 120000,
        }, movie, None, baseline)

        self.assertEqual(payload["content_id"], "tmdb:550")
        self.assertEqual(payload["video_id"], "tmdb:550")
        self.assertEqual(payload["progress_key"], "tmdb:550")

    async def test_watching_movie_without_fresh_local_progress_gets_one_second_resume(self) -> None:
        status_changed_at = datetime(2026, 9, 20, 12, 0)
        entry = SimpleNamespace(status="watching", status_changed_at=status_changed_at, updated_at=status_changed_at)
        movie = Media(id=10, tmdb_id=550, media_type=MediaType.movie, title="Fight Club", runtime=120,
            imdb_id="tt0137523")
        stale = PlaybackProgress(user_id=7, media_id=10, progress_seconds=900, progress_percent=0.1,
            updated_at=datetime(2026, 9, 19, 12, 0))
        db = SimpleNamespace(execute=AsyncMock(side_effect=[
            _Result(rows=[(entry, movie)]),
            _Result(rows=[(stale, movie)]),
        ]))

        with patch("core.nuvio_projection.ensure_imdb_ids", AsyncMock()):
            items = await nuvio_projection.build_progress_items(
                db, user_id=7, next_up_for_watched_series=True,
            )

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["content_id"], "tt0137523")
        self.assertEqual(items[0]["position"], 1000)
        self.assertEqual(items[0]["duration"], 7_200_000)
        self.assertEqual(items[0]["progress_key"], "tt0137523")
        self.assertEqual(items[0]["last_watched"], int(status_changed_at.replace(tzinfo=timezone.utc).timestamp() * 1000))

    async def test_watching_series_uses_next_released_unwatched_episode_for_synthetic_resume(self) -> None:
        status_changed_at = datetime(2026, 9, 20, 12, 0)
        entry = SimpleNamespace(status="watching", status_changed_at=status_changed_at, updated_at=status_changed_at)
        series = Media(id=10, tmdb_id=1396, media_type=MediaType.series, title="Breaking Bad")
        show = Show(id=5, tmdb_id=1396, title="Breaking Bad", tmdb_data={"external_ids": {"imdb_id": "tt0903747"}})
        episodes = [
            Media(id=21, media_type=MediaType.episode, title="Pilot", show_id=5, season_number=1,
                episode_number=1, runtime=45, release_date="2008-01-20"),
            Media(id=22, media_type=MediaType.episode, title="Upcoming", show_id=5, season_number=1,
                episode_number=2, runtime=45, release_date="2099-01-01"),
            Media(id=23, media_type=MediaType.episode, title="Next", show_id=5, season_number=1,
                episode_number=3, runtime=45, release_date="2008-01-27"),
        ]
        db = SimpleNamespace(execute=AsyncMock(side_effect=[
            _Result(rows=[(entry, series)]),
            _Result(scalars=[show]),
            _Result(scalars=episodes),
            _Result(rows=[]),
            _Result(scalars=[]),
            _Result(scalars=[show]),
        ]))

        with patch("core.nuvio_projection.ensure_imdb_ids", AsyncMock()):
            items = await nuvio_projection.build_progress_items(
                db, user_id=7, next_up_for_watched_series=True,
            )

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["season"], 1)
        self.assertEqual(items[0]["episode"], 1)
        self.assertEqual(items[0]["position"], 1000)
        self.assertEqual(items[0]["progress_key"], "tt0903747_s1e1")

    async def test_watching_series_with_episode_history_uses_next_up_without_synthetic_resume(self) -> None:
        changed_at = datetime(2026, 9, 20, 12, 0)
        entry = SimpleNamespace(status="watching", status_changed_at=changed_at, updated_at=changed_at)
        series = Media(id=10, tmdb_id=1396, media_type=MediaType.series, title="Breaking Bad")
        show = Show(id=5, tmdb_id=1396, title="Breaking Bad",
            tmdb_data={"external_ids": {"imdb_id": "tt0903747"}})
        episodes = [
            Media(id=21, media_type=MediaType.episode, title="Watched", show_id=5,
                season_number=1, episode_number=1, runtime=45, release_date="2008-01-20"),
            Media(id=22, media_type=MediaType.episode, title="Next", show_id=5,
                season_number=1, episode_number=2, runtime=45, release_date="2008-01-27"),
        ]
        db = SimpleNamespace(execute=AsyncMock(side_effect=[
            _Result(rows=[(entry, series)]),
            _Result(scalars=[show]),
            _Result(scalars=episodes),
            _Result(rows=[]),
            _Result(scalars=[21]),
        ]))

        with patch("core.nuvio_projection.ensure_imdb_ids", AsyncMock()):
            items = await nuvio_projection.build_progress_items(
                db, user_id=7, next_up_for_watched_series=True,
            )

        self.assertEqual(items, [])

    async def test_watching_series_with_history_preserves_real_fractional_playback_position(self) -> None:
        changed_at = datetime(2026, 9, 20, 12, 0)
        entry = SimpleNamespace(status="watching", status_changed_at=changed_at, updated_at=changed_at)
        series = Media(id=10, tmdb_id=1396, media_type=MediaType.series, title="Breaking Bad")
        show = Show(id=5, tmdb_id=1396, title="Breaking Bad",
            tmdb_data={"external_ids": {"imdb_id": "tt0903747"}})
        watched_episode = Media(id=21, media_type=MediaType.episode, title="Watched", show_id=5,
            season_number=1, episode_number=1, runtime=45, release_date="2008-01-20")
        active_episode = Media(id=22, media_type=MediaType.episode, title="Current", show_id=5,
            season_number=1, episode_number=2, runtime=45, release_date="2008-01-27")
        progress = SimpleNamespace(progress_seconds=12.75, progress_percent=0.25,
            updated_at=datetime(2026, 9, 21, 12, 0))
        db = SimpleNamespace(execute=AsyncMock(side_effect=[
            _Result(rows=[(entry, series)]),
            _Result(scalars=[show]),
            _Result(scalars=[watched_episode, active_episode]),
            _Result(rows=[(progress, active_episode)]),
            _Result(scalars=[21]),
            _Result(scalars=[show]),
        ]))

        with patch("core.nuvio_projection.ensure_imdb_ids", AsyncMock()):
            items = await nuvio_projection.build_progress_items(
                db, user_id=7, next_up_for_watched_series=True,
            )

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["progress_key"], "tt0903747_s1e2")
        self.assertEqual(items[0]["position"], 12_750)
        self.assertNotIn("synthetic_resume", items[0])

    async def test_completed_episode_progress_does_not_hide_next_episode(self) -> None:
        changed_at = datetime(2026, 9, 20, 12, 0)
        entry = SimpleNamespace(status="watching", status_changed_at=changed_at, updated_at=changed_at)
        series = Media(id=10, tmdb_id=1396, media_type=MediaType.series, title="Breaking Bad")
        show = Show(id=5, tmdb_id=1396, title="Breaking Bad",
            tmdb_data={"external_ids": {"imdb_id": "tt0903747"}})
        episodes = [
            Media(id=21 + index, media_type=MediaType.episode, title=f"Episode {index + 1}",
                show_id=5, season_number=1, episode_number=index + 1, runtime=45,
                release_date="2008-01-20")
            for index in range(4)
        ]
        completed = PlaybackProgress(user_id=7, media_id=23, progress_seconds=2700,
            progress_percent=1.0, updated_at=datetime(2026, 9, 21, 12, 0))
        db = SimpleNamespace(execute=AsyncMock(side_effect=[
            _Result(rows=[(entry, series)]),
            _Result(scalars=[show]),
            _Result(scalars=episodes),
            _Result(rows=[(completed, episodes[2])]),
            _Result(scalars=[21, 22, 23]),
            _Result(scalars=[show]),
        ]))

        with patch("core.nuvio_projection.ensure_imdb_ids", AsyncMock()):
            items = await nuvio_projection.build_progress_items(db, user_id=7)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["progress_key"], "tt0903747_s1e4")
        self.assertEqual(items[0]["position"], 1000)

    async def test_watching_series_without_episode_catalogue_is_not_silent_success(self) -> None:
        entry = SimpleNamespace(status="watching", status_changed_at=None, updated_at=None)
        series = Media(id=10, tmdb_id=1396, media_type=MediaType.series, title="Breaking Bad")
        db = SimpleNamespace(execute=AsyncMock(side_effect=[
            _Result(rows=[(entry, series)]),
            _Result(scalars=[]),
            _Result(rows=[]),
        ]))

        with self.assertRaisesRegex(nuvio.NuvioAPIError, "Breaking Bad"):
            await nuvio_projection.build_progress_items(db, user_id=7)

    async def test_full_push_clear_resolves_manually_added_imdb_ids_and_keeps_remote_only_rows(self) -> None:
        paused = Media(id=10, tmdb_id=550, media_type=MediaType.movie, title="Fight Club", imdb_id="tt0137523")
        watching = Media(id=11, tmdb_id=603, media_type=MediaType.movie, title="The Matrix", imdb_id="tt0133093")
        db = SimpleNamespace(
            get=AsyncMock(return_value=None),
            execute=AsyncMock(side_effect=[
                _Result(rows=[(paused, "paused"), (watching, "watching")]),
                _Result(rows=[]),
            ]),
        )
        remote = [
            {"content_id": "tt0137523", "content_type": "movie", "progress_key": "tt0137523"},
            {"content_id": "tt0133093", "content_type": "movie", "progress_key": "tt0133093"},
            {"content_id": "tt9999999", "content_type": "movie", "progress_key": "tt9999999"},
        ]

        keys = await nuvio_projection.progress_keys_to_clear(db, user_id=7, connection_id=44, remote_rows=remote)

        self.assertEqual(keys, ["tt0137523"])

    async def test_legacy_synthetic_series_resume_cleanup_normalizes_tv_and_checks_echo_time(self) -> None:
        series = Media(id=10, tmdb_id=1396, media_type=MediaType.series,
            title="Breaking Bad", imdb_id="tt0903747")
        progress_key = "tt0903747_s1e2"
        outbound = {
            "action": "upsert", "synthetic_resume": True,
            "progress_key": progress_key, "position": 1000, "duration": 2_700_000,
            "season": 1, "episode": 2, "last_watched": 1_790_000_000_000,
        }
        baseline = SimpleNamespace(snapshot={
            "mappings": {"tt0903747": "1396"},
            "outbound": {"tt0903747": outbound},
        })

        async def clear_keys(remote_row):
            db = SimpleNamespace(
                get=AsyncMock(return_value=baseline),
                execute=AsyncMock(side_effect=[
                    _Result(rows=[(series, "watching")]),
                    _Result(rows=[]),
                    _Result(scalars=[Show(id=5, tmdb_id=1396)]),
                    _Result(scalars=[5]),
                ]),
            )
            return await nuvio_projection.progress_keys_to_clear(
                db, user_id=7, connection_id=44, remote_rows=[remote_row],
            )

        echoed = {
            "content_id": "tt0903747", "content_type": "tv", "progress_key": progress_key,
            "position": 1000, "duration": 2_700_000, "season": 1, "episode": 2,
            "last_watched": 1_790_000_000_000,
        }
        self.assertEqual(await clear_keys(echoed), [progress_key])
        self.assertEqual(await clear_keys({**echoed, "last_watched": 1_790_000_000_001}), [])
        self.assertEqual(await clear_keys({**echoed, "position": 1500}), [])



class NuvioWatchedStateProjectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_date_episode_uses_event_creation_time_for_nuvio(self) -> None:
        episode = Media(
            id=10,
            media_type=MediaType.episode,
            title="The Rescue",
            show_id=5,
            season_number=1,
            episode_number=2,
        )
        show = Show(
            id=5,
            tmdb_id=123,
            title="Fixture Show",
            tmdb_data={"external_ids": {"imdb_id": "tt1234567"}},
        )
        created_at = datetime(2026, 9, 1, 12, 30)
        db = SimpleNamespace(execute=AsyncMock(return_value=_Result(rows=[(episode.id, None, created_at)])))

        with (
            patch("core.nuvio_projection.select_in_chunks", AsyncMock(side_effect=[[episode], [show]])),
            patch("core.nuvio_projection.ensure_imdb_ids", AsyncMock()),
        ):
            items = await nuvio_projection.build_watched_items(db, user_id=7, media_ids={episode.id})

        self.assertEqual(items, [{
            "content_id": "tt1234567",
            "content_type": "series",
            "title": "The Rescue",
            "season": 1,
            "episode": 2,
            "watched_at": int(created_at.replace(tzinfo=timezone.utc).timestamp() * 1000),
        }])
