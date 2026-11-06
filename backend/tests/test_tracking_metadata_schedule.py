import os
import asyncio
import gc
import weakref
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://test:test@localhost/test",
)

from core.tracking_metadata import fetch_tmdb_seasons, retain_episode_runtime, tracking_catalogue_is_fresh


class SeasonLoopLifetimeTests(unittest.TestCase):
    def test_shared_slots_do_not_retain_closed_event_loops(self):
        async def season(*args, **kwargs):
            await asyncio.sleep(.001)
            return {'episodes': []}

        async def acquire():
            reference = weakref.ref(asyncio.get_running_loop())
            await fetch_tmdb_seasons(1, [{'season_number': n, 'episode_count': 0}
                for n in range(1, 9)], 'fixture')
            return reference

        with patch('core.tracking_metadata.tmdb.get_season', side_effect=season):
            reference = asyncio.run(acquire())
        gc.collect()
        self.assertIsNone(reference())


class SeasonAcquisitionTests(unittest.IsolatedAsyncioTestCase):
    async def test_overlapping_catalogues_share_four_slots_and_keep_complete_order(self):
        active = maximum = 0
        started = asyncio.Event()
        release = asyncio.Event()

        async def season(series, number, **kwargs):
            nonlocal active, maximum
            active += 1
            maximum = max(maximum, active)
            if active == 4:
                started.set()
            try:
                await release.wait()
                await asyncio.sleep((4 - number % 4) / 1000)
                return {'episodes': [{'id': series * 100 + number, 'episode_number': 1}]}
            finally:
                active -= 1

        seasons = [{'season_number': n, 'episode_count': 1} for n in range(1, 13)]
        with patch('core.tracking_metadata.tmdb.get_season', side_effect=season):
            tasks = [asyncio.create_task(fetch_tmdb_seasons(series, seasons, 'fixture')) for series in (1, 2)]
            try:
                await asyncio.wait_for(started.wait(), 1)
                await asyncio.sleep(0)
                self.assertEqual(active, 4)
            finally:
                release.set()
            results = await asyncio.gather(*tasks)
        self.assertEqual(maximum, 4)
        self.assertEqual(active, 0)
        for series, rows in enumerate(results, 1):
            self.assertEqual([(number, episode['id']) for number, episode in rows],
                [(n, series * 100 + n) for n in range(1, 13)])

    async def test_incomplete_or_unidentified_season_cancels_siblings(self):
        for bad in ({'episodes': []}, {'episodes': [{'episode_number': 1}]}):
            active = 0
            started = asyncio.Event()

            async def season(series, number, **kwargs):
                nonlocal active
                active += 1
                if active == 4:
                    started.set()
                try:
                    await started.wait()
                    if number == 1:
                        return bad
                    await asyncio.Event().wait()
                finally:
                    active -= 1

            with patch('core.tracking_metadata.tmdb.get_season', side_effect=season):
                with self.assertRaisesRegex(ValueError, 'Episode'):
                    await asyncio.wait_for(fetch_tmdb_seasons(1,
                        [{'season_number': n, 'episode_count': 1} for n in range(1, 9)], 'fixture'), 1)
            self.assertEqual(active, 0)

    async def test_departing_caller_cancels_its_seasons_and_releases_slots(self):
        active = 0
        started = asyncio.Event()

        async def season(*args, **kwargs):
            nonlocal active
            active += 1
            if active == 4:
                started.set()
            try:
                await asyncio.Event().wait()
            finally:
                active -= 1

        with patch('core.tracking_metadata.tmdb.get_season', side_effect=season):
            task = asyncio.create_task(fetch_tmdb_seasons(1,
                [{'season_number': n, 'episode_count': 1} for n in range(1, 9)], 'fixture'))
            await asyncio.wait_for(started.wait(), 1)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(active, 0)
        with patch('core.tracking_metadata.tmdb.get_season', return_value={'episodes': []}):
            self.assertEqual(await fetch_tmdb_seasons(1,
                [{'season_number': 1, 'episode_count': 0}], 'fixture'), [])


class TrackingCatalogueScheduleTests(unittest.TestCase):
    def test_catalogue_refresh_keeps_recovered_runtime_when_provider_omits_it(self):
        episode = SimpleNamespace(runtime=45)
        for value in (None, 0, -1, True, 'unknown'):
            retain_episode_runtime(episode, {'runtime': value})
            self.assertEqual(episode.runtime, 45)
        retain_episode_runtime(episode, {'runtime': 48})
        self.assertEqual(episode.runtime, 48)

    def _media(self, refreshed_at):
        return SimpleNamespace(
            tmdb_data={"tracking_catalogue_refreshed_at": refreshed_at,'tracking_episode_details_version':1}
        )

    def test_older_catalogues_are_refreshed_once_for_episode_artwork(self):
        now=datetime(2026,9,19,12,tzinfo=timezone.utc)
        media=self._media(now.isoformat())
        media.tmdb_data.pop('tracking_episode_details_version')
        self.assertFalse(tracking_catalogue_is_fresh(media,'Ended',now))
        media.tmdb_data['tracking_episode_details_version']=1
        self.assertTrue(tracking_catalogue_is_fresh(media,'Ended',now))

    def test_active_show_refreshes_daily(self):
        now = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
        self.assertTrue(tracking_catalogue_is_fresh(
            self._media((now - timedelta(hours=19)).isoformat()),
            "Returning Series",
            now,
        ))
        self.assertFalse(tracking_catalogue_is_fresh(
            self._media((now - timedelta(hours=21)).isoformat()),
            "Returning Series",
            now,
        ))

    def test_finished_show_refreshes_monthly(self):
        now = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)
        self.assertTrue(tracking_catalogue_is_fresh(
            self._media((now - timedelta(days=29)).isoformat()),
            "Ended",
            now,
        ))
        self.assertFalse(tracking_catalogue_is_fresh(
            self._media((now - timedelta(days=31)).isoformat()),
            "Canceled",
            now,
        ))

    def test_missing_or_invalid_timestamp_is_stale(self):
        self.assertFalse(tracking_catalogue_is_fresh(SimpleNamespace(tmdb_data={}), None))
        self.assertFalse(tracking_catalogue_is_fresh(self._media("not-a-date"), None))

