import unittest
from types import SimpleNamespace

from sqlalchemy import update

import test_statistics_pg as fixture
from dependencies import get_optional_user_or_api_key
from models import GlobalSettings
from models.catalogue import CatalogueCredit, CatalogueEntity


@unittest.skipUnless(fixture.URL, 'Requires disposable PostgreSQL')
class ContributorBrowseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixture.StatisticsDatabaseTests.asyncSetUp(self)
        self.app.dependency_overrides[get_optional_user_or_api_key] = lambda: self.viewer
        async with self.Session() as db:
            people = {
                'actor': CatalogueEntity(kind='person', name='Screen Actor', attributes={'birthday': '1980-11-27'}),
                'crew': CatalogueEntity(kind='person', name='Screen Director', attributes={'birthday': '1980-11-27'}),
                'unranked': CatalogueEntity(kind='person', name='Unranked Actor', attributes={'birthday': '1990-11-27'}),
                'partial': CatalogueEntity(kind='person', name='Partial Date', attributes={'birthday': '1980-11'}),
                'composer': CatalogueEntity(kind='person', name='Composer', attributes={'known_for_department': 'Sound'}),
                'sound': CatalogueEntity(kind='person', name='Sound Designer', attributes={'known_for_department': 'Sound'}),
                'game': CatalogueEntity(kind='person', name='Game Developer', attributes={'birthday': '1980-11-27'}),
                'adult': CatalogueEntity(kind='person', name='Restricted Actor', attributes={'birthday': '1980-11-27'}),
                'studio': CatalogueEntity(kind='organization', name='Shared Studio Inc.'),
            }
            works = {
                'popular': CatalogueEntity(kind='movie', name='Popular Film', attributes={'vote_count': 1000, 'vote_average': 6}),
                'rated': CatalogueEntity(kind='movie', name='Acclaimed Film', attributes={'vote_count': 100, 'vote_average': 9}),
                'unranked': CatalogueEntity(kind='series', name='Unranked Series'),
                'game': CatalogueEntity(kind='game', name='Game', attributes={
                    'game_class': 'game', 'igdb_rating_count': 100, 'igdb_rating': 85}),
                'adult': CatalogueEntity(kind='movie', name='Restricted Film', attributes={'adult': True, 'vote_count': 2000}),
            }
            db.add_all([*people.values(), *works.values()])
            await db.flush()
            alias = CatalogueEntity(kind='person', name='Alias', canonical_person_id=people['actor'].id)
            db.add(alias)
            await db.flush()
            credits = [
                (people['actor'], 'popular', 'actor', None, 199),
                (alias, 'popular', 'director', None, 50),
                (people['actor'], 'rated', 'soundtrack', 'Soundtrack', 50),
                (people['crew'], 'rated', 'director', None, 75),
                (people['partial'], 'rated', 'actor', None, 35),
                (people['composer'], 'rated', 'contributor', 'Original Music Composer', 15),
                (people['sound'], 'rated', 'contributor', 'Sound Designer', 25),
                (people['unranked'], 'unranked', 'actor', None, None),
                (people['game'], 'game', 'writer', None, None),
                (people['adult'], 'adult', 'actor', None, 500),
                (people['studio'], 'popular', 'producer', None, None),
                (people['studio'], 'game', 'developer', None, None),
            ]
            db.add_all([CatalogueCredit(work_id=works[work].id, contributor_id=person.id, role=role, role_label=label,
                provider='tmdb' if popularity is not None else 'fixture', source_key=str(index),
                attributes={'popularity': popularity} if popularity is not None else {})
                for index, (person, work, role, label, popularity) in enumerate(credits)])
            await db.commit()

    async def asyncTearDown(self):
        await fixture.StatisticsDatabaseTests.asyncTearDown(self)

    async def browse(self, kind='staff', **params):
        return await self.client.get(f'/tracking/browse/contributors/{kind}', params=params)

    @staticmethod
    def names(response):
        return [item['name'] for item in response.json()['results']]

    async def test_shared_people_ranking_and_birthday_coverage(self):
        self.assertEqual(self.names(await self.browse()),
            ['Screen Director', 'Screen Actor', 'Partial Date', 'Sound Designer', 'Composer'])
        self.assertEqual((await self.browse(sort='score')).status_code, 422)
        self.assertEqual(self.names(await self.browse(sort='birthdays', day='11-27')),
            ['Screen Director', 'Screen Actor', 'Unranked Actor'])
        self.assertEqual(self.names(await self.browse(sort='birthdays', day='11-28')), [])
        self.assertEqual(self.names(await self.browse(job='music')), ['Composer'])
        self.assertEqual(self.names(await self.browse(job='directing')), ['Screen Director', 'Screen Actor'])
        self.assertEqual(self.names(await self.browse(job='acting', sort='birthdays', day='11-27')),
            ['Screen Actor', 'Unranked Actor'])

    async def test_games_scope_and_canonical_studio_across_domains(self):
        self.viewer = None
        self.assertEqual((await self.browse(scope='game')).status_code, 401)
        self.assertNotIn('Game Developer', self.names(await self.browse(sort='birthdays', day='11-27')))
        self.viewer = SimpleNamespace(id=self.user_id, games_access=True)
        game = await self.browse(scope='game', sort='birthdays', day='11-27', job='writing')
        self.assertEqual(self.names(game), ['Game Developer'])
        self.assertEqual(game.json()['results'][0]['domain'], 'game')
        self.assertEqual(game.headers['cache-control'], 'private, no-store')
        self.assertEqual(self.names(await self.browse('studio')), ['Shared Studio'])
        self.assertEqual(self.names(await self.browse('studio', scope='game')), ['Shared Studio'])
        self.viewer = None
        async with self.Session() as db:
            await db.execute(update(GlobalSettings).values(enable_logged_out_navigation=False))
            await db.commit()
        self.assertEqual((await self.browse()).status_code, 401)
