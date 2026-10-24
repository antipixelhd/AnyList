import unittest

from core.streaming_duplicates import evidence_covered


class StreamingDuplicateEvidenceTests(unittest.TestCase):
    def test_movie_earlier_watch_covers_repeat_but_not_earlier_new_evidence(self):
        item = {'kind': 'movie', 'source_dates': ['2020-02-02']}
        self.assertTrue(evidence_covered(item, {None: '2020-02-02'}))
        self.assertTrue(evidence_covered(item, {None: '2020-01-01'}))
        self.assertFalse(evidence_covered(item, {None: '2020-03-03'}))
        self.assertFalse(evidence_covered(item, {}))

    def test_series_requires_every_episode_and_real_date(self):
        item = {'kind': 'show', 'episodes': [
            {'matched': True, 'season_number': 1, 'episode_number': n, 'dates': ['2020-02-02']}
            for n in (1, 2, 3)
        ]}
        watches = {(1, n): '2020-02-02' for n in (1, 2, 3)}
        self.assertTrue(evidence_covered(item, watches))
        self.assertFalse(evidence_covered(item, {(1, 1): '2020-02-02'}))
        watches[(1, 3)] = '2020-03-03'
        self.assertFalse(evidence_covered(item, watches))
        item['episodes'][2]['matched'] = False
        watches[(1, 3)] = '2020-02-02'
        self.assertFalse(evidence_covered(item, watches))

    def test_partial_movie_receipt_does_not_hide_new_completion_evidence(self):
        item = {'kind': 'movie', 'source_dates': ['2020-02-02'], 'suggested_movie_status': 'dropped'}
        events = [{'key': 'status', 'status': 'dropped', 'date': '2020-02-02'}]
        self.assertTrue(evidence_covered(item, {}, events))
        item['suggested_movie_status'] = 'completed'
        self.assertFalse(evidence_covered(item, {}, events))
        item['suggested_movie_status'] = 'dropped'
        item['source_dates'] = ['2020-02-01']
        self.assertFalse(evidence_covered(item, {}, events))
