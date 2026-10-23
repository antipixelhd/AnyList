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

    def test_seventy_percent_boundary(self):
        rows = [viewing('2020-01-01', 2520), viewing('2020-01-02', 3600)]
        self.assertEqual(choose_viewing(rows, 60)['watched_at'], '2020-01-01')
        rows[0]['watched_seconds'] = 2519
        self.assertEqual(choose_viewing(rows, 60)['watched_at'], '2020-01-02')

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
