"""Game search summaries, invalidation and cross-domain discovery in real PG."""
import asyncio
import importlib
import unittest
from unittest.mock import AsyncMock, patch

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import event, select

import test_game_contributor_works_pg as fixture
from core import contributor_search as search
from dependencies import get_current_user
from models.catalogue import CatalogueCredit, CatalogueEntity, CatalogueRelationship, ContributorSearch


@unittest.skipUnless(fixture.fixture.fixture.URL, 'Requires explicit disposable statistics PostgreSQL')
class GameContributorSearchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixture.GameContributorTests.asyncSetUp(self)
        self.app.dependency_overrides[get_current_user] = lambda: self.viewer
        search._candidate_cache._store.clear()
        self.discovery_patch = patch.object(search, 'discover_companies', AsyncMock(
            return_value={'ids': [], 'pending': False, 'partial': False}))
        self.discovery_patch.start()
        self.addCleanup(self.discovery_patch.stop)

    asyncTearDown = fixture.GameContributorTests.asyncTearDown
    seed = fixture.GameContributorTests.seed

    async def summary(self, identifier=None):
        async with self.Session() as db:
            return await db.get(ContributorSearch, identifier or self.person_id)

    async def query(self, term, kind='all', domain='all'):
        response = await self.client.get('/tracking/quick-search/contributors',
            params={'q':term,'kind':kind,'work_domain':domain})
        self.assertEqual(response.status_code,200,response.text)
        return response.json()

    async def test_roles_are_separate_and_new_game_credits_are_immediately_searchable(self):
        # Establish a reusable movie/provider shortlist before Games exists.
        await self.query('Game company','studio')
        _, alias, company = await self.seed(2)
        row = await self.summary()
        self.assertEqual((row.work_count,row.actor_count,row.screen_count),(1,1,1))
        self.assertEqual((row.game_work_count,row.game_actor_count,row.game_staff_count),(2,2,1))
        actors = (await self.query('Verified alias','actor','game'))['results']['actor']
        self.assertEqual([r['key'] for r in actors],[f'catalogue:{self.person_id}'])
        self.assertEqual(actors[0]['url'],f'/actors/catalogue:{self.person_id}?media_type=game')
        self.assertIsNone(await self.summary(alias))
        staff = (await self.query('Fixture','staff','game'))['results']['staff']
        self.assertEqual(staff[0]['roles'],['Composer'])
        studios = (await self.query('Game company','studio'))['results']['studio']
        self.assertEqual(studios[0]['key'],f'catalogue:{company}')
        self.assertEqual(studios[0]['roles'],['Developer'])
        self.assertFalse((await self.query('Game company','studio','movies_series'))['results']['studio'])
        self.assertFalse((await self.query('%','actor','game'))['results']['actor'])

    async def test_retired_cast_and_work_metadata_invalidate_only_game_qualification(self):
        games, _, _ = await self.seed(1)
        async with self.Session() as db:
            credit = await db.scalar(select(CatalogueCredit).where(CatalogueCredit.work_id==games[0].id,CatalogueCredit.role=='voice_actor'))
            credit.attributes={**credit.attributes,'source_active':False}
            await db.commit()
        row=await self.summary()
        self.assertEqual((row.actor_count,row.game_actor_count,row.game_staff_count),(1,0,1))
        self.assertFalse((await self.query('Fixture','actor','game'))['results']['actor'])
        async with self.Session() as db:
            work=await db.get(CatalogueEntity,games[0].id)
            work.attributes={**work.attributes,'game_class':'pack'}
            await db.commit()
        self.assertEqual((await self.summary()).game_work_count,0)
        async with self.Session() as db:
            work=await db.get(CatalogueEntity,games[0].id)
            work.attributes={**work.attributes,'game_class':'game'}
            await db.commit()
        self.assertEqual((await self.summary()).game_work_count,1)
        self.assertEqual((await self.summary()).work_count,1)

    async def test_verified_parent_changes_reclassify_expansion_and_port_credits(self):
        games, _, _ = await self.seed(1)
        async with self.Session() as db:
            expansion=CatalogueEntity(kind='game',name='Expansion',attributes={'game_class':'expansion'})
            port=CatalogueEntity(kind='game',name='Platform port',attributes={'game_class':'port'})
            performer=CatalogueEntity(kind='person',name='Port-only performer')
            db.add_all([expansion,port,performer]);await db.flush()
            db.add_all([CatalogueCredit(work_id=expansion.id,contributor_id=self.person_id,provider='wikidata',source_key='expansion',role='actor'),
                CatalogueCredit(work_id=port.id,contributor_id=performer.id,provider='wikidata',source_key='port',role='voice_actor')])
            await db.commit()
        self.assertEqual((await self.summary()).game_work_count,1)
        self.assertEqual((await self.summary(performer.id)).game_work_count,0)
        async with self.Session() as db:
            db.add_all([CatalogueRelationship(source_id=expansion.id,target_id=games[0].id,relation='expansion',provider='igdb'),
                CatalogueRelationship(source_id=port.id,target_id=games[0].id,relation='port',provider='igdb')])
            await db.commit()
        self.assertEqual((await self.summary()).game_work_count,2)
        self.assertEqual((await self.summary(performer.id)).game_work_count,1)
        async with self.Session() as db:
            base=await db.get(CatalogueEntity,games[0].id)
            base.attributes={**base.attributes,'game_class':'unknown'}
            await db.commit()
        self.assertEqual((await self.summary()).game_work_count,0)
        self.assertEqual((await self.summary(performer.id)).game_work_count,0)

    async def test_migration_round_trip_preserves_movie_counts_and_backfills_games(self):
        await self.seed(2)
        async with self.engine.begin() as conn:
            def run(sync):
                migration=importlib.import_module('migrations.versions.mt061_game_contributor_search')
                participation=importlib.import_module('migrations.versions.mt064_game_credit_participation')
                with Operations.context(MigrationContext.configure(sync)):
                    participation.downgrade();migration.downgrade();migration.upgrade();participation.upgrade()
            await conn.run_sync(run)
        row=await self.summary()
        self.assertEqual((row.work_count,row.actor_count,row.game_work_count,row.game_actor_count),(1,1,2,2))

    async def test_acknowledgements_are_stored_without_becoming_work_and_uncredited_cast_still_counts(self):
        games,_,_=await self.seed(1)
        async with self.Session() as db:
            person=CatalogueEntity(kind='person',name='Only thanked person')
            db.add(person);await db.flush();native=person.id
            db.add(CatalogueCredit(work_id=games[0].id,contributor_id=native,provider='imdb',
                source_key='thanks',role='thanks',role_label='Special thanks',attributes={'source_active':True}))
            await db.commit()
        summary=await self.summary(native)
        self.assertEqual((summary.game_work_count,summary.game_staff_count),(0,0))
        self.assertFalse((await self.query('Only thanked','staff','game'))['results']['staff'])
        async with self.Session() as db:
            self.assertEqual(len(list(await db.scalars(select(CatalogueCredit).where(CatalogueCredit.contributor_id==native)))),1)
            db.add(CatalogueCredit(work_id=games[0].id,contributor_id=native,provider='imdb',
                source_key='voice',role='voice_actor',role_label='Voice actor',attributes={'uncredited':True}))
            await db.commit()
        summary=await self.summary(native)
        self.assertEqual((summary.game_work_count,summary.game_actor_count,summary.game_staff_count),(1,1,0))
        actors=(await self.query('Only thanked','actor','game'))['results']['actor']
        self.assertEqual(actors[0]['key'],f'catalogue:{native}')

    async def test_simultaneous_game_credit_writers_do_not_lose_counts(self):
        async with self.Session() as db:
            games=[CatalogueEntity(kind='game',name=f'Concurrent {i}',attributes={'game_class':'game'}) for i in range(2)]
            db.add_all(games);await db.commit()
        async def write(game):
            async with self.Session() as db, db.begin():
                db.add(CatalogueCredit(work_id=game.id,contributor_id=self.person_id,
                    provider='wikidata',source_key=f'concurrent-{game.id}',role='voice_actor'))
                await db.flush();await asyncio.sleep(.02)
        await asyncio.gather(*(write(game) for game in games))
        self.assertEqual((await self.summary()).game_actor_count,2)
        self.assertEqual((await self.summary()).actor_count,1)

    async def test_game_search_reads_only_the_compact_index_and_skips_movie_provider(self):
        await self.seed(3)
        statements=[]
        def record(conn,cursor,statement,parameters,context,many):
            if statement.lstrip().upper().startswith(('SELECT','WITH')):statements.append(statement)
        event.listen(self.engine.sync_engine,'before_cursor_execute',record)
        try:
            with patch.object(search.tmdb,'search_people',AsyncMock(side_effect=AssertionError('movie fan-out'))), \
                 patch.object(search.settings_store,'get_user_tmdb_key',AsyncMock(side_effect=AssertionError('credential fan-out'))):
                data=await self.query('Fixture','actor','game')
            self.assertEqual(data['game_coverage'],'saved_credits')
            self.assertEqual(len(statements),1)
            self.assertIn('catalogue_contributor_search',statements[0])
            self.assertNotIn('catalogue_credits',statements[0])
            self.assertNotIn('.attributes',statements[0])
        finally:event.remove(self.engine.sync_engine,'before_cursor_execute',record)
