import os
import asyncio
import gc
import weakref
import unittest
from unittest.mock import AsyncMock, patch
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from types import SimpleNamespace

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+asyncpg://test:test@localhost/test",
)

from core.tracking_metadata import fetch_tmdb_seasons, retain_episode_runtime, tracking_catalogue_is_fresh
from core.tracking_metadata import refresh_air_schedule
from core.episode_release import EpisodeRelease, tvdb_schedule


class EpisodeReleaseTests(unittest.IsolatedAsyncioTestCase):
    raw = {'airsTime': '22:00', 'originalCountry': 'usa', 'airsDays': {'thursday': True}}

    def test_airtime_is_the_same_instant_across_midnight_and_imports_stay_released(self):
        schedule = tvdb_schedule(296861, self.raw)
        air_at = datetime(2026, 11, 20, 3, tzinfo=timezone.utc)
        before = EpisodeRelease(schedule, today=date(2026, 11, 20), now=air_at - timedelta(seconds=1))
        self.assertEqual(before.air_at('2026-11-19').astimezone(ZoneInfo('Europe/Berlin')).isoformat(),
                         '2026-11-20T04:00:00+01:00')
        self.assertFalse(before.is_released('2026-11-19'))
        self.assertTrue(before.is_released('2026-11-19', imported=True))
        self.assertTrue(before.is_released('2026-11-18'))  # Off the usual weekday.
        self.assertTrue(EpisodeRelease(schedule, today=date(2026, 11, 19), now=air_at).is_released('2026-11-19'))

    def test_daylight_saving_uses_the_origin_zone_and_ambiguous_clocks_fall_back(self):
        schedule = tvdb_schedule(1, self.raw)
        self.assertEqual(EpisodeRelease(schedule).air_at('2026-07-16').isoformat(), '2026-07-17T02:00:00+00:00')
        ambiguous = tvdb_schedule(1, {**self.raw, 'airsTime': '01:30', 'airsDays': {'sunday': True}})
        self.assertIsNone(EpisodeRelease(ambiguous).air_at('2026-11-01'))

    async def test_only_tmdb_supplied_ids_are_fetched_and_removal_clears_the_schedule(self):
        with patch('core.tvdb.get_series_airtime', AsyncMock(return_value=self.raw)) as fetch:
            self.assertIsNone(await refresh_air_schedule(None, {'tvdb_id': 296861}, 'key'))
            fetch.assert_not_called()
            details = {'external_ids': {'tvdb_id': 296861}}
            schedule = await refresh_air_schedule(None, details, 'key')
            fetch.assert_awaited_once_with(296861, 'key')
            self.assertEqual(schedule, tvdb_schedule(296861, self.raw))
            self.assertIsNone(await refresh_air_schedule(schedule, {}, 'key'))
            fetch.assert_awaited_once()

    async def test_provider_failure_keeps_the_schedule_for_the_same_id_and_empty_success_clears_it(self):
        schedule = tvdb_schedule(296861, self.raw)
        with patch('core.tvdb.get_series_airtime', AsyncMock(side_effect=TimeoutError)):
            self.assertEqual(await refresh_air_schedule(schedule, {'external_ids': {'tvdb_id': 296861}}, 'key'), schedule)
            self.assertIsNone(await refresh_air_schedule(schedule, {'external_ids': {'tvdb_id': 3}}, 'key'))
        with patch('core.tvdb.get_series_airtime', AsyncMock(return_value={'airsTime': ''})):
            self.assertIsNone(await refresh_air_schedule(schedule, {'external_ids': {'tvdb_id': 296861}}, 'key'))


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

    def test_legacy_catalogue_refreshes_for_an_imminent_episode_with_a_tmdb_tvdb_id(self):
        now = datetime(2026, 11, 19, 4, tzinfo=timezone.utc)
        media = self._media((now - timedelta(hours=2)).isoformat())
        media.tmdb_data.update(external_ids={'tvdb_id': 296861},
                              next_episode_to_air={'air_date': '2026-11-19'})
        self.assertFalse(tracking_catalogue_is_fresh(media, 'Returning Series', now))
        self.assertTrue(tracking_catalogue_is_fresh(media, 'Ended', now))
        media.tmdb_data['next_episode_to_air']['air_date'] = '2026-11-26'
        self.assertTrue(tracking_catalogue_is_fresh(media, 'Returning Series', now))
        media.tmdb_data['last_episode_to_air'] = {'air_date': '2026-11-19'}
        self.assertFalse(tracking_catalogue_is_fresh(media, 'Returning Series', now))
        media.tmdb_data['tracking_tvdb_airtime_checked'] = True
        self.assertTrue(tracking_catalogue_is_fresh(media, 'Returning Series', now))
        media.tmdb_data.pop('tracking_tvdb_airtime_checked')
        media.tmdb_data['external_ids'] = {}
        self.assertTrue(tracking_catalogue_is_fresh(media, 'Returning Series', now))

    def test_missing_or_invalid_timestamp_is_stale(self):
        self.assertFalse(tracking_catalogue_is_fresh(SimpleNamespace(tmdb_data={}), None))
        self.assertFalse(tracking_catalogue_is_fresh(self._media("not-a-date"), None))

