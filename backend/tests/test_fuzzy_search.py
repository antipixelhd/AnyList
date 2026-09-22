"""Fast, database-free coverage for remote catalogue typo recovery."""
import os
import unittest

os.environ.setdefault('SECRET_KEY', 'local-tests-only')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

from routers.tracking import fuzzy_remote_terms, is_close_title_match


class FuzzyRemoteSearchTests(unittest.TestCase):
    def test_common_typos_produce_the_intended_remote_query(self):
        self.assertIn('Mutiny', fuzzy_remote_terms('Mutany'))
        self.assertIn('The Odyssey', fuzzy_remote_terms('Thee Odyssey'))

    def test_generated_result_must_still_resemble_the_original_query(self):
        self.assertTrue(is_close_title_match('Mutany', 'Mutiny'))
        self.assertTrue(is_close_title_match('Thee Odyssey', 'The Odyssey'))
        self.assertFalse(is_close_title_match('Mutany', 'Unrelated Film'))


if __name__ == '__main__':
    unittest.main()
