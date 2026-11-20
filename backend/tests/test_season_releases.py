import unittest
from datetime import date, datetime, timezone
from types import SimpleNamespace

from core.season_releases import (
    SEASON_RELEASE_DATE_KIND,
    SEASON_RELEASE_KIND,
    _season_art,
    observation_transition,
    season_availability,
    season_event_dedupe_key,
    season_date_notice_is_current,
    upcoming_season,
)


class UpcomingSeasonTests(unittest.TestCase):
    def test_catalogue_stores_first_released_still_and_isolates_alternate_order(self):
        from core.episode_release import EpisodeRelease
        from core.season_artwork import collect_backdrops, season_backdrop

        release = EpisodeRelease(today=date(2026, 10, 2))
        stored = collect_backdrops([(1, 2, '2025-01-02', '/second.jpg'), (1, 1, '2025-01-01', '/first.jpg'),
            (2, 1, '2026-10-03', '/future.jpg'), (3, 1, None, '/unknown.jpg')], release)
        self.assertEqual(stored, {'1': '/first.jpg'})
        data = {'tracking_season_backdrops': stored}
        alternate = [SimpleNamespace(poster_path='/alternate.jpg')]
        self.assertEqual(season_backdrop(data, 'tmdb:aired', 1, alternate), '/first.jpg')
        self.assertEqual(season_backdrop(data, 'tvdb:dvd', 1, alternate), '/alternate.jpg')

    def test_season_dot_changes_at_the_scheduled_premiere(self):
        from core.episode_release import EpisodeRelease

        premiere = '2026-10-02'
        schedule = {'time': '20:00', 'zone': 'UTC'}
        before = EpisodeRelease(schedule, today=date(2026, 10, 2),
            now=datetime(2026, 10, 2, 19, 59, tzinfo=timezone.utc))
        aired = EpisodeRelease(schedule, today=before.today,
            now=datetime(2026, 10, 2, 20, tzinfo=timezone.utc))
        episodes = [premiere, '2026-10-09']
        self.assertEqual(season_availability(premiere, episodes, 0, before), 'upcoming')
        self.assertEqual(season_availability(premiere, episodes, 1, aired), 'airing')
        self.assertIsNone(season_availability(None, [], 0, before))
        self.assertIsNone(season_availability(premiere, episodes, 1, aired, completed=True))

    def test_home_progress_uses_current_season_until_increment_wraps(self):
        from core.screen_list import project_series

        media = SimpleNamespace(status='Returning Series', tvdb_air_schedule=None, tmdb_data={
            'tracking_catalogue_refreshed_at': '2026-09-23',
            'seasons': [{'season_number': 1, 'episode_count': 1, 'air_date': '2026-09-01', 'name': 'First Campaign'},
                        {'season_number': 2, 'episode_count': 3, 'air_date': '2026-09-21', 'poster_path': '/campaign.jpg'}],
            'next_episode_to_air': {'season_number': 2, 'episode_number': 3, 'air_date': '2026-10-01'},
        })
        episodes = [SimpleNamespace(id=n, season_number=season, episode_number=episode,
                                    release_date=day, watched=watched, imported=False)
                    for n, season, episode, day, watched in [(1, 1, 1, '2026-09-01', True),
                        (2, 2, 1, '2026-09-21', False), (3, 2, 2, '2026-09-22', False),
                        (4, 2, 3, '2026-10-01', False)]]
        entry = {'status': 'watching'}
        today = date(2026, 9, 23)
        project_series(entry, media, episodes, today, True, latest_activity={'episode_id': 1})
        self.assertEqual(entry['progress_season']['name'], 'First Campaign')
        self.assertEqual(entry['progress_season']['next_watch_id'], 2)
        episodes[1].watched = True
        project_series(entry, media, episodes, today, True, latest_activity={'episode_id': 2})
        self.assertEqual(entry['progress_season'], {'number': 2, 'name': 'Season 2',
            'poster': 'https://image.tmdb.org/t/p/original/campaign.jpg', 'watched': 1,
            'released': 2, 'position': 'S2 E1', 'next_watch_id': 3})
        self.assertEqual(entry['next_release_date'], '2026-10-01')

    def test_first_season_is_not_a_new_season_release(self):
        season = {"season_number": 1, "name": "Season 1", "air_date": "2026-10-01", "episode_count": 0}
        self.assertIsNone(upcoming_season({"seasons": [season]}, date(2026, 9, 25)))

    def test_selects_next_confirmed_regular_season_even_without_episode_rows(self):
        metadata = {"seasons": [
            {"season_number": 0, "air_date": "2026-10-01"},
            {"season_number": 1, "air_date": "2024-01-01"},
            {"season_number": 2, "air_date": None},
            {"season_number": 3, "name": "Season 3", "air_date": "2026-10-03", "episode_count": 0},
            {"season_number": 4, "name": "Season 4", "air_date": "2027-01-01", "episode_count": 0},
        ]}
        self.assertEqual(
            upcoming_season(metadata, date(2026, 9, 25)),
            {"season_number": 3, "name": "Season 3", "air_date": "2026-10-03", "episode_count": 0},
        )

    def test_premiere_day_is_still_returned_for_release_day_card(self):
        metadata = {"seasons": [
            {"season_number": 1, "air_date": "2024-01-01"},
            {"season_number": 2, "air_date": "2026-09-25"},
        ]}
        self.assertEqual(upcoming_season(metadata, date(2026, 9, 25))["season_number"], 2)

    def test_season_artwork_fallback_is_explicit_and_retryable(self):
        media = SimpleNamespace(poster_path="show-poster.jpg")
        missing = _season_art(media, {"season_number": 5, "air_date": "2026-10-01"})
        self.assertEqual(missing["poster"], "show-poster.jpg")
        self.assertIsNone(missing["season_poster_path"])
        self.assertTrue(missing["season_artwork_fallback"])
        self.assertTrue(missing["artwork_retry"])

        available = _season_art(media, {
            "season_number": 5, "air_date": "2026-10-01", "poster_path": "season-5.jpg",
        })
        self.assertEqual(available["season_poster_path"], "season-5.jpg")
        self.assertFalse(available["season_artwork_fallback"])
        self.assertFalse(available["artwork_retry"])


class SeasonObservationTests(unittest.TestCase):
    def test_future_date_notifies_once_and_is_remembered_for_release(self):
        future = observation_transition(
            date(2026, 10, 1), None, False, False, date(2026, 9, 25),
        )
        self.assertTrue(future["send_date_notice"])
        self.assertFalse(future["send_release_notice"])
        self.assertTrue(future["seen_upcoming"])
        self.assertFalse(future["release_processed"])

        release = observation_transition(
            date(2026, 10, 1), "2026-10-01", future["seen_upcoming"],
            future["release_processed"], date(2026, 10, 1),
        )
        self.assertFalse(release["send_date_notice"])
        self.assertTrue(release["send_release_notice"])
        self.assertTrue(release["release_processed"])

        repeated = observation_transition(
            date(2026, 10, 1), "2026-10-01", release["seen_upcoming"],
            release["release_processed"], date(2026, 10, 2),
        )
        self.assertFalse(repeated["send_release_notice"])

    def test_first_scan_on_premiere_day_only_sends_release_notice(self):
        transition = observation_transition(
            date(2026, 9, 25), None, False, False, date(2026, 9, 25),
        )
        self.assertFalse(transition["send_date_notice"])
        self.assertTrue(transition["send_release_notice"])
        self.assertTrue(transition["release_processed"])

    def test_first_scan_after_a_past_premiere_is_quiet(self):
        transition = observation_transition(
            date(2026, 9, 20), None, False, False, date(2026, 9, 25),
        )
        self.assertFalse(transition["send_date_notice"])
        self.assertFalse(transition["send_release_notice"])
        self.assertTrue(transition["release_processed"])

    def test_corrected_past_date_reopens_release_tracking_for_future_date(self):
        corrected = observation_transition(
            date(2026, 10, 15), "2026-09-20", False, True, date(2026, 9, 25),
        )
        self.assertTrue(corrected["send_date_notice"])
        self.assertFalse(corrected["release_processed"])
        premiere = observation_transition(
            date(2026, 10, 15), "2026-10-15", corrected["seen_upcoming"],
            corrected["release_processed"], date(2026, 10, 15),
        )
        self.assertTrue(premiere["send_release_notice"])

    def test_delayed_scan_releases_only_a_previously_seen_upcoming_season(self):
        seen = observation_transition(
            date(2026, 9, 20), "2026-09-20", True, False, date(2026, 9, 25),
        )
        self.assertTrue(seen["send_release_notice"])

    def test_date_dedupe_changes_with_date_but_release_dedupe_does_not(self):
        old = season_event_dedupe_key(SEASON_RELEASE_DATE_KIND, 12, 5, "2026-10-01")
        revised = season_event_dedupe_key(SEASON_RELEASE_DATE_KIND, 12, 5, "2026-10-15")
        first_release = season_event_dedupe_key(SEASON_RELEASE_KIND, 12, 5, "2026-10-01")
        revised_release = season_event_dedupe_key(SEASON_RELEASE_KIND, 12, 5, "2026-10-15")
        self.assertNotEqual(old, revised)
        self.assertEqual(first_release, revised_release)

    def test_date_notice_is_stale_when_provider_retracts_or_changes_date(self):
        current_key = season_event_dedupe_key(SEASON_RELEASE_DATE_KIND, 12, 5, "2026-10-01")
        confirmed = {"season_number": 5, "air_date": "2026-10-01"}
        self.assertTrue(season_date_notice_is_current(12, 5, confirmed, current_key))
        self.assertFalse(season_date_notice_is_current(12, 5, None, current_key))
        self.assertFalse(season_date_notice_is_current(
            12, 5, {"season_number": 5, "air_date": None}, current_key,
        ))
        self.assertFalse(season_date_notice_is_current(
            12, 5, {"season_number": 5, "air_date": "2026-10-15"}, current_key,
        ))


if __name__ == "__main__":
    unittest.main()


class AvailabilityDotTests(unittest.TestCase):
    def test_airing_requires_started_season_with_future_scheduled_episode(self):
        from core.season_releases import availability_dot
        today = date(2026, 9, 23)
        metadata = {'seasons': [{'season_number': 3, 'episode_count': 8, 'air_date': '2026-09-01'}]}
        episodes = [(3, '2026-09-01', 1), (3, '2026-09-08', 2), (3, '2026-10-01', 3)]
        self.assertEqual(availability_dot(metadata, episodes, {1, 2}, 'completed', today), (True, 'airing'))

        pointer_only = {**metadata, 'next_episode_to_air': {
            'season_number': 3, 'episode_number': 3, 'air_date': '2026-10-01',
        }}
        self.assertEqual(availability_dot(pointer_only, episodes[:2], {1, 2}, 'completed', today), (True, 'airing'))

    def test_fully_released_batch_is_not_airing(self):
        from core.season_releases import availability_dot
        today = date(2026, 9, 23)
        metadata = {'seasons': [{'season_number': 2, 'episode_count': 2, 'air_date': '2026-09-01'}]}
        episodes = [(2, '2026-09-01', 1), (2, '2026-09-01', 2)]
        self.assertEqual(availability_dot(metadata, episodes, set(), 'planning', today), (True, 'new_season'))

    def test_recent_unwatched_season_respects_status_watch_history_and_expiry(self):
        from core.season_releases import availability_dot
        today = date(2026, 9, 23)
        metadata = {'seasons': [{'season_number': 4, 'episode_count': 3, 'air_date': '2026-09-01'}]}
        episodes = [(4, '2026-09-01', 1), (4, '2026-09-02', 2)]
        self.assertEqual(availability_dot(metadata, episodes, {1}, 'paused', today), (True, 'new_season'))
        self.assertEqual(availability_dot(metadata, episodes, {1}, 'watching', today), (False, None))
        self.assertEqual(availability_dot(metadata, episodes, {1, 2}, 'paused', today), (False, None))
        expired = {'seasons': [{'season_number': 4, 'episode_count': 2, 'air_date': '2026-08-23'}]}
        self.assertEqual(availability_dot(expired, episodes, set(), 'planning', today), (False, None))

    def test_future_and_special_seasons_are_ignored(self):
        from core.season_releases import availability_dot
        today = date(2026, 9, 23)
        metadata = {'seasons': [
            {'season_number': 0, 'episode_count': 1, 'air_date': '2026-09-01'},
            {'season_number': 5, 'episode_count': 3, 'air_date': '2026-10-01'},
        ]}
        self.assertEqual(availability_dot(metadata, [(5, '2026-10-01', 1)], set(), 'planning', today), (False, None))

    def test_import_confirmed_release_counts_without_fabricating_an_air_date(self):
        from core.season_releases import availability_dot
        metadata = {'seasons': [{'season_number': 2, 'episode_count': 1, 'air_date': '2024-11-09'}]}
        self.assertEqual(availability_dot(metadata, [(2, None, 55, True)], {55}, 'completed', date(2026, 9, 24)),
                         (False, None))


class CurrentlyAiringTests(unittest.TestCase):
    def test_requires_returning_status_and_future_episode_in_a_started_season(self):
        from core.season_releases import current_airing_details

        today = date(2026, 9, 23)
        metadata = {
            'status': 'Returning Series',
            'seasons': [{'season_number': 3, 'episode_count': 8, 'air_date': '2026-09-01'}],
            'last_episode_to_air': {'season_number': 3, 'episode_number': 2, 'air_date': '2026-09-15'},
            'next_episode_to_air': {'season_number': 3, 'episode_number': 3, 'air_date': '2026-10-01'},
        }
        episodes = [(3, '2026-09-01', 1), (3, '2026-09-15', 2), (3, '2026-10-01', 3)]
        self.assertTrue(current_airing_details(metadata, episodes, today)[0])
        self.assertEqual(current_airing_details(metadata, episodes, today), (True, '2026-10-01'))

        no_pointer = {key: value for key, value in metadata.items() if key != 'next_episode_to_air'}
        self.assertEqual(current_airing_details(no_pointer, episodes, today), (True, '2026-10-01'))

        # A stale Returning Series label alone must not keep a show in the
        # airing state after a batch has fully released.
        batch_release = {
            'status': 'Returning Series',
            'seasons': [{'season_number': 1, 'episode_count': 2, 'air_date': '2026-09-01'}],
        }
        self.assertFalse(current_airing_details(
            batch_release, [(1, '2026-09-01', 1), (1, '2026-09-01', 2)], today,
        )[0])
        stale_pointer = {
            **batch_release,
            'last_episode_to_air': {'season_number': 1, 'episode_number': 2},
            'next_episode_to_air': {'season_number': 1, 'episode_number': 2, 'air_date': '2026-10-01'},
        }
        self.assertFalse(current_airing_details(
            stale_pointer, [(1, '2026-09-01', 1), (1, '2026-09-01', 2)], today,
        )[0])

    def test_ended_or_not_yet_started_series_are_not_currently_airing(self):
        from core.season_releases import current_airing_details

        today = date(2026, 9, 23)
        future_episode = [(1, '2026-10-01', 1)]
        scheduled = {
            'seasons': [{'season_number': 1, 'episode_count': 8, 'air_date': '2026-09-01'}],
            'next_episode_to_air': {'season_number': 1, 'episode_number': 2, 'air_date': '2026-10-01'},
        }
        self.assertFalse(current_airing_details({'status': 'Ended', **scheduled}, future_episode, today)[0])
        unstarted = {
            'status': 'Returning Series',
            'seasons': [{'season_number': 2, 'episode_count': 8, 'air_date': '2026-10-01'}],
            'next_episode_to_air': {'season_number': 2, 'episode_number': 1, 'air_date': '2026-10-01'},
        }
        self.assertFalse(current_airing_details(unstarted, future_episode, today)[0])

        between_seasons = {
            'status': 'Returning Series',
            'seasons': [
                {'season_number': 1, 'episode_count': 2, 'air_date': '2026-01-01'},
                {'season_number': 2, 'episode_count': 8, 'air_date': '2026-10-01'},
            ],
            'last_episode_to_air': {'season_number': 1, 'episode_number': 2, 'air_date': '2026-02-01'},
            'next_episode_to_air': {'season_number': 2, 'episode_number': 1, 'air_date': '2026-10-01'},
        }
        finished_first_season = [(1, '2026-01-01', 1), (1, '2026-02-01', 2)]
        self.assertFalse(current_airing_details(between_seasons, finished_first_season, today)[0])
