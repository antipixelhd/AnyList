import unittest
from datetime import date
from core.tracking_rules import normalize_score, effective_score, project_score, observed_status, default_dates, removed_keys


class TrackingRulesTests(unittest.TestCase):
    def test_zero_and_missing_are_unrated(self):
        self.assertIsNone(normalize_score(0))
        self.assertIsNone(normalize_score(None))

    def test_half_point_boundaries_and_invalid_scores(self):
        for score in (0.5, 7.5, 10):
            self.assertEqual(normalize_score(score), score)
        for score in (-0.5, 0.25, 7.3, 10.5, float('inf'), float('nan')):
            with self.subTest(score=score), self.assertRaises(ValueError):
                normalize_score(score)

    def test_manual_tenth_point_scores_and_unrated_zero(self):
        for score in (0.1, 5.8, 7.3, 10):
            self.assertEqual(normalize_score(score, 0.1), score)
        self.assertIsNone(normalize_score(0, 0.1))
        for score in (0.05, 7.35, 10.1):
            with self.subTest(score=score), self.assertRaises(ValueError):
                normalize_score(score, 0.1)

    def test_manual_score_does_not_inherit_or_average_seasons(self):
        self.assertEqual(effective_score('manual', 8, {'1': 6, '2': 10}), 8)
        self.assertIsNone(effective_score('manual', None, {'1': 8}))

    def test_average_excludes_specials_unrated_and_saved_manual(self):
        self.assertAlmostEqual(effective_score('average', 10, {'0': 10, '1': 8, '2': 6, '3': 8, '4': None, '5': 0}), 22/3)

    def test_empty_average_never_restores_manual_score(self):
        self.assertIsNone(effective_score('average', 9, {}))
        self.assertIsNone(effective_score('average', 9, {'0': 8, '1': None}))

    def test_provider_projection_rounds_midpoints_up(self):
        self.assertEqual(project_score(7.5), 8)
        self.assertEqual(project_score(7.49), 7)
        self.assertEqual(project_score(7.25, 0.5), 7.5)
        self.assertIsNone(project_score(None))

    def test_completed_has_priority_over_new_playback(self):
        self.assertEqual(observed_status('completed', False, True), 'completed')
        self.assertEqual(observed_status('dropped', True, False), 'completed')
        self.assertEqual(observed_status('paused', False, True), 'watching')
        self.assertEqual(observed_status('dropped', False, False), 'dropped')

    def test_completed_manual_add_sets_only_finish_date(self):
        today = date(2026, 9, 18)
        self.assertEqual(default_dates(None, 'completed', None, None, today), (None, today))
        self.assertEqual(default_dates(None, 'planning', None, None, today), (None, None))
        self.assertEqual(default_dates('planning', 'watching', None, None, today), (today, None))

    def test_polling_does_not_restore_cleared_dates(self):
        self.assertEqual(default_dates('completed', 'completed', None, None, date(2026,9,18)), (None,None))

    def test_new_or_incomplete_connection_cannot_remove_entries(self):
        self.assertEqual(removed_keys(None, set(), True), set())
        self.assertEqual(removed_keys({'a','b'}, set(), False), set())
        self.assertEqual(removed_keys({'a','b'}, {'b','c'}, True), {'a'})


if __name__ == '__main__':
    unittest.main()
