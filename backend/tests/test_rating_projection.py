import unittest

from core.rating_projection import integer_provider_score, is_projected_echo, project_score


class RatingProjectionTests(unittest.TestCase):
    def test_exact_midpoints_round_up(self):
        self.assertEqual(integer_provider_score(7.5), 8)
        self.assertEqual(integer_provider_score(8.5), 9)
        self.assertEqual(project_score(7.25, 0.5), 7.5)

    def test_converted_echo_preserves_local_precision(self):
        self.assertTrue(is_projected_echo(7.5, 8))
        self.assertTrue(is_projected_echo(8.5, 9))
        self.assertFalse(is_projected_echo(7.5, 7))
