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
                (people['actor'], 'popular', 'actor'), (alias, 'popular', 'director'),
                (people['crew'], 'rated', 'director'), (people['partial'], 'rated', 'actor'),
                (people['unranked'], 'unranked', 'actor'), (people['game'], 'game', 'writer'),
                (people['adult'], 'adult', 'actor'), (people['studio'], 'popular', 'producer'),
                (people['studio'], 'game', 'developer'),
            ]
            db.add_all([CatalogueCredit(work_id=works[work].id, contributor_id=person.id, role=role,
                provider='fixture', source_key=str(index), attributes={'principal': True})
                for index, (person, work, role) in enumerate(credits)])
            await db.commit()

    async def asyncTearDown(self):
        await fixture.StatisticsDatabaseTests.asyncTearDown(self)

    async def browse(self, kind='staff', **params):
        return await self.client.get(f'/tracking/browse/contributors/{kind}', params=params)

    @staticmethod
    def names(response):
        return [item['name'] for item in response.json()['results']]

    async def test_shared_people_ranking_and_birthday_coverage(self):
        self.assertEqual(self.names(await self.browse(scope='movie')),
            ['Screen Actor', 'Partial Date', 'Screen Director'])
        self.assertEqual(self.names(await self.browse(scope='movie', sort='score')),
            ['Partial Date', 'Screen Director', 'Screen Actor'])
        self.assertEqual(self.names(await self.browse(sort='birthdays', day='11-27')),
            ['Screen Actor', 'Screen Director', 'Unranked Actor'])
        self.assertEqual(self.names(await self.browse(sort='birthdays', day='11-28')), [])

    async def test_games_scope_and_canonical_studio_across_domains(self):
        self.viewer = None
        self.assertEqual((await self.browse(scope='game')).status_code, 401)
        self.assertNotIn('Game Developer', self.names(await self.browse()))
        self.viewer = SimpleNamespace(id=self.user_id, games_access=True)
        game = await self.browse(scope='game')
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
