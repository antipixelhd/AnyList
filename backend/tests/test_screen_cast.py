"""Provider priority and title-local artwork matching must not merge people."""
import unittest
from types import SimpleNamespace
from core.screen_cast import preferred_cast


def row(person, provider, character='Priya', *, work=55, name='Dimple Kapadia', role='actor'):
    return (SimpleNamespace(work_id=work, provider=provider, role=role, character_label=character),
            SimpleNamespace(id=person, name=name))


class ScreenCastTests(unittest.TestCase):
    def test_unlinked_tvdb_role_projects_to_tmdb_person_without_mutating_sources(self):
        credits = [row(10458, 'tmdb'), row(16964, 'tvdb')]
        result = preferred_cast(credits)
        self.assertEqual([person.id for _, person in result], [10458, 10458])
        self.assertIs(result[1][0], credits[1][0])
        self.assertEqual(credits[1][1].id, 16964)

    def test_fallback_cast_and_crew_remain_complete(self):
        credits = [row(1, 'tvdb'), row(2, 'tvdb', role='contributor')]
        self.assertEqual(preferred_cast(credits), credits)

    def test_unverified_names_cannot_bridge_different_characters_or_titles(self):
        credits = [row(1, 'tmdb'), row(2, 'tvdb', 'Different role'),
                   row(3, 'tvdb', ''), row(4, 'tvdb', work=99)]
        self.assertEqual([p.id for _, p in preferred_cast(credits, {55})], [1, 4])

    def test_ambiguous_people_do_not_attach_artwork(self):
        for credits in ([row(1, 'tmdb'), row(2, 'tmdb'), row(3, 'tvdb')],
                        [row(1, 'tmdb'), row(2, 'tvdb'), row(3, 'tvdb')]):
            self.assertTrue(all(c.provider == 'tmdb' for c, _ in preferred_cast(credits, {55})))

    def test_verified_person_keeps_differently_spelled_roles(self):
        credits = [row(1, 'tmdb'), row(1, 'tvdb', 'Translated role')]
        self.assertEqual(preferred_cast(credits), credits)

    def test_matching_uses_case_and_spacing_but_not_fuzzy_names(self):
        credits = [row(1, 'tmdb'), row(2, 'tvdb', ' PRIYA ', name=' DIMPLE  KAPADIA '),
                   row(3, 'tvdb', name='Dimple Kapadi')]
        self.assertEqual([p.id for _, p in preferred_cast(credits, {55})], [1, 1])

    def test_partial_tmdb_cast_does_not_hide_unmatched_fallback_actors(self):
        credits = [row(1, 'tmdb'), row(2, 'tvdb', 'Another role', name='Other person')]
        self.assertEqual(preferred_cast(credits), credits)
