"""Admin review uses cached explicit identities, not automatic title matches."""
import unittest

from sqlalchemy import delete,select

import test_catalogue_pg as fixture
import test_game_imdb_person_pg as person_fixture
from test_game_imdb_person_pg import credit,page
from core.game_imdb_enrichment import snapshot,now
from datetime import timedelta
from core.game_imdb_person import COMPONENT
from core.game_imdb_review import review_titles,ReviewChanged,INDEX
from models.catalogue import CatalogueIdentity,MetadataSnapshot


@unittest.skipUnless(fixture.URL,'Requires disposable PostgreSQL')
class ReviewTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=person_fixture.PersonEnrichmentTests.asyncSetUp
    asyncTearDown=fixture.CatalogueDatabaseTests.asyncTearDown

    async def test_current_excluded_matches_are_classified_and_do_not_need_a_manual_binding(self):
        from core.game_imdb_discovery import MAP,VERSION
        await self.service.run('nm1')
        async with self.Session() as db:
            row=MetadataSnapshot(provider='wikidata',kind=MAP,external_id='tt2',expires_at=now()+timedelta(days=1),
                payload={'status':'excluded','reason':'dlc','igdb_id':30,'game_title':'Known parent: Known DLC','matcher_version':VERSION})
            db.add(row);await db.commit()
            self.assertEqual((await review_titles(db,'nm1'))['results'][0]['status'],'excluded')
            classified=(await review_titles(db,'nm1',scope='all',title_id='tt2'))['results'][0]
            self.assertEqual((classified['status'],classified['igdb_id'],classified['reason']),('excluded','30','dlc'))
            self.assertIsNone(classified['work_id']);self.assertNotIn('match_status',classified)
            row.expires_at=now()-timedelta(seconds=1);await db.commit()
            self.assertEqual((await review_titles(db,'nm1'))['results'][0]['status'],'unmapped')

    async def test_unmapped_titles_preserve_roles_and_exclude_movies(self):
        rows=[credit(),credit('tt2'),credit('tt2',role='writer'),credit('tt3','movie')]
        rows[1]['title']['titleText']['text']='Game 100%_complete'
        rows[1]['title']['releaseYear']={'year':2024}
        self.source.credits.return_value=page(rows,4)
        self.assertEqual(await self.service.run('nm1'),'ready')
        async with self.Session() as db:
            result=await review_titles(db,'nm1')
            self.assertTrue(result['ready']);self.assertEqual(result['total'],1)
            row=result['results'][0]
            self.assertEqual((row['imdb_id'],row['roles'],row['year']),('tt2',['actor','writer'],2024))
            self.assertEqual(row['status'],'unmapped')
            self.assertEqual((await review_titles(db,'nm1',scope='all'))['total'],2)
            self.assertEqual((await review_titles(db,'nm1',query='%_'))['total'],1)
            self.assertEqual((await review_titles(db,'nm1',query='missing%_'))['total'],0)
            self.assertEqual((await review_titles(db,'nm1',title_id='tt1'))['results'][0]['work_id'],self.game_id)
            self.assertEqual((await review_titles(db,'nm1',title_id='tt3'))['results'],[])
        self.source.credits.assert_awaited_once()

    async def test_paging_and_version_guards(self):
        rows=[credit(f'tt{n}') for n in range(1,77)]
        self.source.credits.side_effect=[page(rows[:50],76,True,'next'),page(rows[50:],76)]
        self.assertEqual(await self.service.run('nm1'),'ready')
        async with self.Session() as db:
            first=await review_titles(db,'nm1');second=await review_titles(db,'nm1',page=2,
                generation=first['generation'],bindings=first['bindings'])
            self.assertEqual((first['total'],len(first['results']),len(second['results'])),(75,50,25))
            self.assertTrue(first['has_more']);self.assertFalse(second['has_more'])
            ids=[row['imdb_id'] for row in first['results']+second['results']]
            self.assertEqual(ids,[f'tt{n}' for n in range(2,77)])
            self.assertEqual((await review_titles(db,'nm1',page=3))['total'],75)
            with self.assertRaises(ReviewChanged):await review_titles(db,'nm1',generation='0'*32)
            with self.assertRaises(ReviewChanged):await review_titles(db,'nm1',bindings='0'*32)

    async def test_invalid_current_binding_needs_review(self):
        await self.service.run('nm1')
        async with self.Session() as db:
            identity=await db.scalar(select(CatalogueIdentity).where(CatalogueIdentity.namespace=='igdb.game'))
            identity.external_id='99';await db.commit()
            result=await review_titles(db,'nm1')
            self.assertEqual(result['total'],2)
            row=next(row for row in result['results'] if row['imdb_id']=='tt1')
            self.assertEqual(row['status'],'review_required');self.assertIsNone(row['work_id'])
            self.assertEqual(row['review_work_id'],self.game_id)

    async def test_old_complete_connection_builds_index_from_cache_without_http(self):
        await self.service.run('nm1')
        async with self.Session() as db:
            component=await db.scalar(snapshot(COMPONENT,'nm1'))
            component.payload={key:value for key,value in component.payload.items() if not key.startswith('index_')}
            await db.execute(delete(MetadataSnapshot).where(MetadataSnapshot.kind==INDEX));await db.commit()
            state=await review_titles(db,'nm1')
            self.assertFalse(state['ready']);self.assertIsNone(state['total']);self.assertTrue(state['status']['pending'])
        self.assertEqual(await self.service.run('nm1'),'ready')
        async with self.Session() as db:self.assertTrue((await review_titles(db,'nm1'))['ready'])
        self.source.credits.assert_awaited_once()

    async def test_failed_refresh_keeps_usable_review_index(self):
        from core.catalogue_providers import ProviderError
        await self.service.run('nm1')
        async with self.Session() as db:first=await review_titles(db,'nm1')
        await person_fixture.PersonEnrichmentTests.due(self)
        self.source.credits.side_effect=[page([credit('tt4')],2,True,'next'),ProviderError('unavailable')]
        self.assertEqual(await self.service.run('nm1'),'retry')
        async with self.Session() as db:
            current=await review_titles(db,'nm1',generation=first['generation'])
            self.assertTrue(current['ready']);self.assertTrue(current['status']['pending'])
            self.assertEqual(current['results'],first['results'])
