import unittest

from core.game_contributor_works import coverage_notice
from core.game_imdb_person import VERSION


class PortfolioCoverageTests(unittest.TestCase):
    def person(self, **changes):
        return {'pending': False, 'mapping': {'person_version': VERSION,
            'source_complete': True, 'omitted_rows': 0,
            'role_coverage': {'actor': {'source': 2, 'unmapped': 0},
                'staff': {'source': 3, 'unmapped': 1}}, **changes}}

    def test_complete_selected_role_does_not_inherit_another_roles_gap(self):
        self.assertIsNone(coverage_notice('actor', person=self.person()))
        self.assertEqual(coverage_notice('staff', person=self.person()),
            'Some game credits could not be matched.')

    def test_unknown_old_and_incomplete_sources_keep_notice(self):
        for person in (None, self.person(person_version=VERSION - 1),
                self.person(source_complete=False), self.person(omitted_rows=1),
                self.person(role_coverage={})):
            with self.subTest(person=person):
                self.assertEqual(coverage_notice('actor', person=person),
                    'Game credits may be incomplete.')

    def test_other_role_discovery_does_not_invalidate_complete_cached_role(self):
        person = {**self.person(), 'pending': True, 'complete': False}
        self.assertIsNone(coverage_notice('actor', person=person))
        self.assertEqual(coverage_notice('staff', person=person),
            'Some game credits could not be matched.')

    def test_verified_empty_role_and_complete_company_need_no_notice(self):
        self.assertIsNone(coverage_notice('actor', person=self.person(
            role_coverage={'actor': {'source': 0, 'unmapped': 0}})))
        self.assertIsNone(coverage_notice('studio', company={'coverage': 'complete'}))
        for company in (None, {'coverage': 'partial'}, {'coverage': 'unknown'}):
            self.assertEqual(coverage_notice('studio', company=company),
                'Game credits may be incomplete.')
