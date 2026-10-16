import unittest
from core.credit_roles import work_credit


class WorkCreditTests(unittest.TestCase):
    def test_acknowledgements_and_idea_only_roles_are_not_works(self):
        for role in ('Thanks', 'Special Thanks', 'Thank You', 'Idea', 'Original Idea', 'Story Idea',
                     'Original Concept', 'Acknowledgements', 'In Memory Of', 'Contributor', None):
            with self.subTest(role=role):
                self.assertFalse(work_credit(role))

    def test_actual_writing_and_design_roles_are_retained(self):
        for role in ('Director', 'Producer', 'Creator', 'Story', 'Original Story', 'Screenplay',
                     'Writer', 'Concept Artist', 'Actor', 'Voice Actor', 'Self'):
            with self.subTest(role=role):
                self.assertTrue(work_credit(role))

    def test_uncredited_and_archive_appearances_are_not_credited_works(self):
        for character in ('Man (uncredited)', 'Self (archive footage)', 'Voice (archive sound)'):
            self.assertFalse(work_credit('Actor', character))
        self.assertTrue(work_credit('Actor', 'Batman (voice)'))
