import unittest
from datetime import datetime
from types import SimpleNamespace
from core.streaming_evidence import choose_viewing, completion_evidence, activity_plan
from core.streaming_activity import record_streaming_activity, reschedule_date_activity


def viewing(day, seconds=None, completed=False):
    return {'watched_at': day, 'watched_seconds': seconds, 'completed_evidence': completed}


def show():
    return {'kind': 'show', 'source_dates': ['2020-01-01', '2020-01-03'],
            'outcome': {'status': 'completed', 'latest_season': 1, 'latest_episode': 4},
            'seasons': [{'season_number': 1, 'total_released': 8}, {'season_number': 2, 'total_released': 8}],
            'episodes': [{'season_number': 1, 'episode_number': 1, 'resolution': 'exact', 'dates': ['2020-01-01']},
                         {'season_number': 1, 'episode_number': 4, 'resolution': 'exact', 'dates': ['2020-01-03']}]}


class EvidenceTests(unittest.TestCase):
    def test_movie_and_same_name_series_keep_separate_evidence(self):
        from core.streaming_evidence import prepare_evidence
        from core.streaming_history import WatchObservation
        movie = {'kind': 'movie', 'source_title': 'Fargo', 'source_dates': ['2022-01-01'],
                 'outcome': {'status': 'completed'}}
        prepare_evidence([movie], [
            WatchObservation(row=1, source_title='Fargo', watched_at='2022-01-01'),
            WatchObservation(row=2, source_title='Fargo: Season 1: Pilot', show_title='Fargo',
                             episode_title='Pilot', season_number=1, watched_at='2020-01-01'),
        ])
        self.assertEqual(movie['source_dates'], ['2022-01-01'])
        self.assertEqual([row['row'] for row in movie['source_evidence']['observations']], [1])

    def test_stronger_incomplete_viewing_wins_but_first_completed_viewing_beats_rewatch(self):
        rows = [viewing('2020-01-01', 600), viewing('2020-01-03', 2700)]
        self.assertEqual(choose_viewing(rows, 60)['watched_at'], '2020-01-03')
        rows += [viewing('2020-01-05', 3600)]
        self.assertEqual(choose_viewing(rows, 60)['watched_at'], '2020-01-03')

    def test_explicit_completion_and_unknown_strength(self):
        self.assertEqual(choose_viewing([viewing('2020-01-03'), viewing('2020-01-01')])['watched_at'], '2020-01-01')
        self.assertEqual(choose_viewing([viewing('2020-01-01', 600, True), viewing('2020-01-03', 3600)], 60)['watched_at'], '2020-01-01')

    def test_partial_status_requires_both_recent_evidence_and_season_finished(self):
        from datetime import date
        from core.streaming_evidence import partial_tracking_status
        item = show()
        item['episodes'] = [{'season_number': 1, 'episode_number': 8, 'dates': ['2026-06-23']}]
        self.assertEqual(partial_tracking_status(item, date(2026, 10, 23)), 'watching')
        item['episodes'][0]['dates'] = ['2026-06-22']
        self.assertEqual(partial_tracking_status(item, date(2026, 10, 23)), 'dropped')
        item['episodes'][0].update(episode_number=4, dates=['2026-10-22'])
        self.assertEqual(partial_tracking_status(item, date(2026, 10, 23)), 'dropped')

    def test_repeated_episode_names_keep_their_season_identity(self):
        from core.streaming_evidence import prepare_evidence
        from core.streaming_history import WatchObservation
        item = show()
        item['source_title'] = 'Show'
        item['episodes'] = [
            {'season_number': sn, 'episode_number': 1, 'source_title': f'Show: Season {sn}: Episode 1',
             'source_episode_title': 'Episode 1', 'resolution': 'exact', 'dates': []}
            for sn in (1, 2)
        ]
        rows = [WatchObservation(row=sn, source_title=f'Show: Season {sn}: Episode 1',
                                 episode_title='Episode 1', show_title='Show', season_number=sn,
                                 watched_at=f'202{sn}-01-01') for sn in (1, 2)]
        prepare_evidence([item], rows)
        self.assertEqual([ep['dates'] for ep in item['episodes']], [['2021-01-01'], ['2022-01-01']])

    def test_sparse_skip_is_preserved_while_preparing_a_partial_status_suggestion(self):
        from core.streaming_evidence import prepare_evidence
        from core.streaming_history import WatchObservation
        item = show(); item['source_title'] = 'Show'; item['outcome']['status'] = 'skip'
        item['episodes'] = [{'season_number': 1, 'episode_number': 1, 'source_title': 'Pilot', 'dates': ['2020-01-01']}]
        prepare_evidence([item], [WatchObservation(row=2, source_title='Pilot', episode_title='Pilot', show_title='Show',
                                                 season_number=1, watched_at='2020-01-01')])
        self.assertEqual(item['outcome']['status'], 'skip')
        self.assertEqual(item['outcome']['tracking_status'], 'dropped')

    def test_seventy_percent_boundary(self):
        rows = [viewing('2020-01-01', 2520), viewing('2020-01-02', 3600)]
        self.assertEqual(choose_viewing(rows, 60)['watched_at'], '2020-01-01')
        rows[0]['watched_seconds'] = 2519
        self.assertEqual(choose_viewing(rows, 60)['watched_at'], '2020-01-02')

    def test_duration_needs_known_runtime_but_explicit_file_completion_is_enough(self):
        from core.streaming_evidence import completed_viewing
        self.assertTrue(completed_viewing(viewing('2020-01-01', 2520), 60))
        self.assertFalse(completed_viewing(viewing('2020-01-01', 2519), 60))
        self.assertFalse(completed_viewing(viewing('2020-01-01', 7200)))
        self.assertTrue(completed_viewing(viewing('2020-01-01', 600, completed=True)))
        self.assertTrue(completed_viewing(viewing('2020-01-01')))

    def test_weak_season_finale_does_not_suggest_watching(self):
        from datetime import date
        from core.streaming_evidence import partial_tracking_status
        item = show()
        item['episodes'] = [
            {'season_number': 1, 'episode_number': 7, 'dates': ['2026-10-22'], 'watch_qualified': True},
            {'season_number': 1, 'episode_number': 8, 'dates': ['2026-10-23'], 'watch_qualified': False},
        ]
        self.assertEqual(partial_tracking_status(item, date(2026, 10, 23)), 'dropped')

    def test_finish_requires_tail_of_final_released_season(self):
        item = show()
        self.assertIsNone(completion_evidence(item)['finish_date'])
        item['episodes'].append({'season_number': 2, 'episode_number': 6, 'dates': ['2020-02-01']})
        self.assertEqual(completion_evidence(item)['finish_date'], '2020-02-01')

    def test_season_card_uses_latest_season_evidence_even_when_not_final_episode(self):
        events = activity_plan(show())
        self.assertEqual([e['date'] for e in events if e['key'] == 'season:1'], ['2020-01-03'])
        self.assertFalse(any(e['key'] == 'season:2' for e in events))
        self.assertFalse(any(e['key'] == 'finish' for e in events))
        self.assertTrue(any(e['key'] == 'episode:1:1' for e in events))
        self.assertFalse(any(e['key'] == 'episode:1:4' for e in events))

    def test_partial_does_not_finish_unwatched_season_or_use_inferred_episodes(self):
        item = show(); item['outcome']['status'] = 'partial'
        item['episodes'].append({'season_number': 1, 'episode_number': 3, 'resolution': 'covered', 'dates': ['2020-01-02']})
        events = activity_plan(item)
        self.assertFalse(any(e.get('finished_seasons') for e in events))
        self.assertFalse(any(e['key'] == 'episode:1:3' for e in events))

    def test_user_finish_date_and_dropped_date(self):
        item = show()
        self.assertEqual([e['date'] for e in activity_plan(item, '2020-03-01') if e['key'] == 'finish'], ['2020-03-01'])
        item['outcome'].update(status='partial', tracking_status='dropped')
        self.assertEqual([e['date'] for e in activity_plan(item) if e['key'] == 'status'], ['2020-01-03'])


class MemoryDb:
    def __init__(self):
        self.rows = []
    async def execute(self, query):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: list(self.rows)))
    def add(self, row):
        self.rows.append(row)
    async def delete(self, row):
        if row in self.rows:
            self.rows.remove(row)
    async def flush(self):
        pass


class ActivityTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_pause_survives_rescheduling_imported_start(self):
        from unittest.mock import AsyncMock, patch
        from datetime import date
        from core.activity import record_daily_activity
        db = MemoryDb()
        await record_streaming_activity(db, 1, 2, [
            {'key': 'start', 'date': '2020-01-01', 'status': 'watching', 'started_watching': True, 'status_changed': True},
            {'key': 'episode:1:1', 'date': '2020-01-01', 'status': 'watching', 'episodes_watched': 1, 'position': 'S1E1'},
        ])
        original = db.rows[0]
        with patch('core.activity._active_activity_card', new=AsyncMock(return_value=original)):
            await record_daily_activity(db, user_id=1, media_id=2, status='paused', score=None,
                                        status_changed=True, previous_status='watching', now=datetime(2020, 1, 1, 12))
        await reschedule_date_activity(db, 1, 2, start_date=date(2019, 12, 1), finish_date=None, changed={'start_date'})
        self.assertEqual(original.status, 'paused')
        self.assertEqual(original.created_at, datetime(2020, 1, 1, 12))
        self.assertEqual(original.payload['episodes_watched'], 1)
        self.assertNotIn('started_watching', original.payload)

    async def test_local_episode_progress_survives_rescheduling_imported_start(self):
        from unittest.mock import AsyncMock, patch
        from datetime import date
        from core.activity import record_progress_activity, clear_recent_rating_activity
        db = MemoryDb()
        await record_streaming_activity(db, 1, 2, [
            {'key': 'start', 'date': '2020-01-01', 'status': 'watching', 'started_watching': True, 'status_changed': True},
            {'key': 'episode:1:1', 'date': '2020-01-01', 'status': 'watching', 'episodes_watched': 1, 'position': 'S1E1'},
        ])
        original = db.rows[0]
        episodes = [SimpleNamespace(id=i, season_number=1, episode_number=i) for i in (1, 2, 3)]
        with (patch('core.activity._active_activity_card', new=AsyncMock(return_value=original)),
              patch('core.activity._series_activity_catalog', new=AsyncMock(return_value=(episodes, {1: 3})))):
            await record_progress_activity(db, user_id=1, media=SimpleNamespace(id=2), previous_progress=1,
                                           progress=2, status='watching', score=8, rating_changed=True,
                                           watched_ids={1, 2}, previous_watched_ids={1}, now=datetime(2020, 1, 1, 12))
        await clear_recent_rating_activity(db, user_id=1, media_id=2, now=datetime(2020, 1, 1, 13))
        await reschedule_date_activity(db, 1, 2, start_date=date(2019, 12, 1), finish_date=None, changed={'start_date'})
        self.assertEqual(original.created_at, datetime(2020, 1, 1, 12))
        self.assertEqual(original.payload['episodes_watched'], 2)
        self.assertEqual((original.payload['position_start'], original.payload['position']), ('S1E1', 'S1E2'))
        self.assertEqual(original.payload['progress'], 2)
        self.assertFalse(original.payload.get('rating_changed'))

    async def test_imported_episode_cards_keep_numbered_ranges_in_fixed_windows(self):
        db = MemoryDb()
        await record_streaming_activity(db, 1, 2, [
            {'key': 'episode:3:6', 'date': '2020-01-01', 'status': 'watching', 'episodes_watched': 1, 'position': 'S3E6'},
            {'key': 'episode:3:5', 'date': '2020-01-01', 'status': 'watching', 'episodes_watched': 1, 'position': 'S3E5'},
            {'key': 'episode:3:7', 'date': '2020-01-02', 'status': 'watching', 'episodes_watched': 1, 'position': 'S3E7'},
        ])
        self.assertEqual(len(db.rows), 2)
        self.assertEqual((db.rows[0].payload['position_start'], db.rows[0].payload['position']), ('S3E5', 'S3E6'))
        self.assertEqual(db.rows[0].payload['episodes_watched'], 2)
        self.assertEqual((db.rows[1].payload['position_start'], db.rows[1].payload['position']), ('S3E7', 'S3E7'))

    async def test_movie_runtime_enrichment_is_shared_and_used_for_seventy_percent(self):
        from unittest.mock import AsyncMock, patch
        from core.netflix_sessions import _prepare_movie_runtime
        from core.streaming_history import WatchObservation
        from core.streaming_evidence import prepare_evidence
        items = [{'kind': 'movie', 'source_title': 'Movie', 'match': {'candidate': {'tmdb_id': 1}},
                  'outcome': {'status': 'skip'}} for _ in range(2)]
        observations = [WatchObservation(row=2, source_title='Movie', watched_at='2020-01-01', watched_seconds=2520)]
        with patch('core.netflix_sessions.tmdb.get_movie_light', new=AsyncMock(return_value={'id': 1, 'runtime': 60})) as fetch:
            await _prepare_movie_runtime(items, observations, 'test-key', 'en-US')
        fetch.assert_awaited_once_with(1, api_key='test-key', language='en-US')
        prepare_evidence(items, observations)
        self.assertTrue(all(item['suggested_movie_status'] == 'completed' for item in items))
        with patch('core.netflix_sessions.tmdb.get_movie_light', new=AsyncMock()) as fetch:
            await _prepare_movie_runtime(items, [WatchObservation(row=2, source_title='Movie', watched_at='2020-01-01')], 'test-key', None)
        fetch.assert_not_awaited()

    async def test_runtime_failure_retains_movie_for_dropped_review(self):
        from unittest.mock import AsyncMock, patch
        from core.netflix_sessions import _prepare_movie_runtime
        from core.streaming_history import WatchObservation
        from core.streaming_evidence import prepare_evidence
        items = [{'kind': 'movie', 'source_title': 'Movie', 'match': {'candidate': {'tmdb_id': 1}}, 'outcome': {'status': 'skip'}}]
        observations = [WatchObservation(row=2, source_title='Movie', watched_at='2020-01-01', watched_seconds=600)]
        with patch('core.netflix_sessions.tmdb.get_movie_light', new=AsyncMock(side_effect=RuntimeError('offline'))):
            await _prepare_movie_runtime(items, observations, 'test-key', None)
        prepare_evidence(items, observations)
        self.assertEqual(items[0]['suggested_movie_status'], 'dropped')
        self.assertEqual(items[0]['source_dates'], ['2020-01-01'])

    async def test_same_day_season_card_preserves_final_dropped_status(self):
        db = MemoryDb()
        item = show()
        item['outcome'].update(status='partial', tracking_status='dropped', latest_episode=8)
        item['episodes'][-1]['episode_number'] = 8
        events = activity_plan(item)
        self.assertEqual(events[-1]['key'], 'status')
        latest = await record_streaming_activity(db, 1, 2, events, score=7)
        self.assertEqual(latest.status, 'dropped')
        self.assertEqual(latest.payload['finished_seasons'], [1])
        self.assertTrue(latest.payload['status_changed'])
        self.assertEqual(latest.score, 7)

    async def test_existing_card_keeps_its_timestamp_status_and_fixed_window(self):
        from models.tracking import TrackingActivity
        from core.activity import activity_window_start
        db = MemoryDb()
        row = TrackingActivity(user_id=1, media_id=2, status='paused',
                               created_at=datetime(2020, 1, 3, 12), payload={
                                   'window_started_at': '2020-01-02T20:00:00',
                                   'status_changed': True, 'position': 'S1E4', 'episodes_watched': 1,
                               })
        db.rows.append(row)
        await record_streaming_activity(db, 1, 2, [
            {'key': 'episode:1:3', 'date': '2020-01-03', 'status': 'watching', 'episodes_watched': 1, 'position': 'S1E3'},
        ])
        self.assertEqual(len(db.rows), 1)
        self.assertEqual(row.created_at, datetime(2020, 1, 3, 12))
        self.assertEqual(row.status, 'paused')
        self.assertEqual(row.payload['position'], 'S1E4')
        self.assertEqual(activity_window_start(row), datetime(2020, 1, 2, 20))
        await record_streaming_activity(db, 1, 2, [
            {'key': 'episode:1:5', 'date': '2020-01-04', 'status': 'watching', 'episodes_watched': 1, 'position': 'S1E5'},
        ])
        self.assertEqual(len(db.rows), 2)

    async def test_historical_windows_repeat_import_and_rating_attachment(self):
        db = MemoryDb(); events = activity_plan(show(), '2020-01-03')
        latest = await record_streaming_activity(db, 1, 2, events, score=8)
        self.assertEqual(len(db.rows), 2)
        self.assertEqual(latest.created_at, datetime(2020, 1, 3))
        self.assertEqual(latest.status, 'completed')
        self.assertEqual(latest.score, 8)
        await record_streaming_activity(db, 1, 2, events)
        self.assertEqual(len(db.rows), 2)
        self.assertEqual(latest.score, 8)
        self.assertTrue(latest.payload['rating_changed'])
        self.assertEqual(db.rows[0].payload['episodes_watched'], 1)
        original_payload = dict(latest.payload)
        await record_streaming_activity(db, 1, 2, events, score=8, previous_score=8)
        self.assertEqual(latest.payload, original_payload)
        self.assertEqual(latest.score, 8)

    async def test_finish_date_moves_card_and_keeps_rating_attached(self):
        from datetime import date
        db = MemoryDb()
        await record_streaming_activity(db, 1, 2, activity_plan(show(), '2020-01-03'), score=8)
        await reschedule_date_activity(db, 1, 2, start_date=date(2020, 1, 1), finish_date=date(2020, 2, 1), changed={'finish_date'})
        completed = [r for r in db.rows if r.status == 'completed' and r.payload.get('status_changed')]
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0].created_at, datetime(2020, 2, 1))
        self.assertEqual(completed[0].score, 8)

    async def test_clearing_finish_date_preserves_season_history_and_moves_rating(self):
        from datetime import date
        db = MemoryDb()
        await record_streaming_activity(db, 1, 2, activity_plan(show(), '2020-01-03'), score=8)
        await reschedule_date_activity(db, 1, 2, start_date=date(2020, 1, 1), finish_date=None, changed={'finish_date'})
        self.assertFalse(any(r.status == 'completed' and r.payload.get('status_changed') for r in db.rows))
        self.assertTrue(any(r.payload.get('finished_seasons') == [1] for r in db.rows))
        self.assertEqual(sum(r.score == 8 for r in db.rows), 1)

    async def test_restoring_a_cleared_finish_date_recreates_card_and_moves_rating(self):
        from datetime import date
        db = MemoryDb()
        await record_streaming_activity(db, 1, 2, activity_plan(show(), '2020-01-03'), score=8)
        await reschedule_date_activity(db, 1, 2, start_date=date(2020, 1, 1), finish_date=None,
                                       changed={'finish_date'}, status='completed')
        await reschedule_date_activity(db, 1, 2, start_date=date(2020, 1, 1), finish_date=date(2020, 2, 1),
                                       changed={'finish_date'}, status='completed')
        completed = [row for row in db.rows if row.status == 'completed' and row.payload.get('status_changed')]
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0].created_at, datetime(2020, 2, 1))
        self.assertEqual(completed[0].score, 8)
        self.assertEqual(sum(bool(row.payload.get('rating_changed')) for row in db.rows), 1)

    async def test_adding_start_date_creates_history_but_finish_on_partial_does_not(self):
        from datetime import date
        db = MemoryDb()
        await reschedule_date_activity(db, 1, 2, start_date=date(2020, 1, 1), finish_date=date(2020, 2, 1),
                                       changed={'start_date', 'finish_date'}, status='watching')
        self.assertEqual(len(db.rows), 1)
        self.assertEqual(db.rows[0].status, 'watching')
        self.assertTrue(db.rows[0].payload['started_watching'])
        self.assertEqual(db.rows[0].created_at, datetime(2020, 1, 1))

    async def test_date_correction_preserves_unrelated_rating_cards(self):
        from datetime import date
        from models.tracking import TrackingActivity
        db = MemoryDb()
        old_rating = TrackingActivity(user_id=1, media_id=2, status='watching', score=6,
                                      created_at=datetime(2019, 1, 1), payload={'rating_changed': True, 'rating_first': True})
        db.rows.append(old_rating)
        await record_streaming_activity(db, 1, 2, activity_plan(show(), '2020-01-03'), score=8)
        await reschedule_date_activity(db, 1, 2, start_date=date(2020, 1, 1), finish_date=date(2020, 2, 1), changed={'finish_date'})
        self.assertIn(old_rating, db.rows)
        self.assertEqual(old_rating.score, 6)
        self.assertTrue(old_rating.payload['rating_changed'])
        self.assertEqual(old_rating.created_at, datetime(2019, 1, 1))

    async def test_missing_finish_evidence_preserves_existing_date(self):
        from datetime import date
        from core.netflix_sessions import _track_imported_root
        entry = SimpleNamespace(status='completed', progress=8, start_date=date(2020, 1, 1),
                                finish_date=date(2020, 2, 1), initial_import_completed_at=None)
        class Db:
            async def execute(self, query):
                return SimpleNamespace(scalar_one_or_none=lambda: entry)
        tracked, is_new = await _track_imported_root(Db(), 1, SimpleNamespace(id=2), {'status': 'completed'},
                                                     [date(2020, 1, 1)], 8, False)
        self.assertFalse(is_new)
        self.assertEqual(tracked.finish_date, date(2020, 2, 1))

    async def test_clearing_manual_completion_keeps_an_existing_rating_event(self):
        from models.tracking import TrackingActivity
        db = MemoryDb()
        row = TrackingActivity(user_id=1, media_id=2, status='completed', score=8,
                               created_at=datetime(2020, 1, 3), payload={'status_changed': True, 'rating_changed': True})
        db.rows.append(row)
        await reschedule_date_activity(db, 1, 2, start_date=None, finish_date=None, changed={'finish_date'})
        self.assertIn(row, db.rows)
        self.assertFalse(row.payload['status_changed'])
        self.assertTrue(row.payload['rating_changed'])
        self.assertEqual(row.score, 8)
