"""Actual migration, private generations and full-collection group paging."""
import asyncio
import importlib
import unittest
from datetime import date
from unittest.mock import AsyncMock,patch

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select,func,event,text

import test_statistics_pg as fixture
from core import game_statistics as stats
from models import User,UserProfileData
from models.base import PrivacyLevel
from models.games import GameEntry,GameChildState,GameAchievement,GameAchievementUnlock
from models.catalogue import CatalogueEntity,CatalogueCredit,CatalogueIdentity
from models.game_statistics import GameStatsState
from models.statistics import UserStatsState
from routers.games import router


@unittest.skipUnless(fixture.URL,'Requires explicit disposable statistics PostgreSQL')
class GameStatisticsDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixture.StatisticsDatabaseTests.asyncSetUp(self)
        self.app.include_router(router)
        async with self.Session() as db:
            user=await db.get(User,self.user_id);user.games_access=True
            game=CatalogueEntity(kind='game',name='Statistics game',attributes={'game_class':'game','genres':['RPG'],
                'release_date':'2020-01-01','source_payload':'RAW_METADATA_SENTINEL '*5000,
                'completion_estimates':{'main_story_seconds':18000,'main_sides_seconds':36000,'completionist_seconds':72000}})
            db.add(game);await db.flush();self.game_id=game.id
            db.add(GameEntry(user_id=user.id,work_id=game.id,status='completed',score=8,finish_date=date(2024,1,1),
                total_seconds=72000,completion_seconds=36000,notes='PRIVATE_NOTE_SENTINEL',replay_count=2))
            await db.commit();self.viewer=user

    asyncTearDown=fixture.StatisticsDatabaseTests.asyncTearDown

    async def request(self,**params):
        return await self.client.get('/tracking/games/users/statistics-fixture/statistics',params=params)

    async def ready(self):
        for _ in range(3):
            await stats.run_due(self.Session)
            if (await self.request()).json()['status']=='ready':return
        self.fail('Games statistics were not published')

    async def test_real_route_cached_generation_parent_only_and_screen_scope_stays_separate(self):
        await self.ready();response=await self.request();self.assertEqual(response.status_code,200,response.text)
        data=response.json();self.assertEqual(data['domain'],'game');self.assertEqual(data['data']['totals']['recorded_seconds'],72000)
        self.assertEqual(data['data']['totals']['listed_games'],1)
        self.assertEqual(response.headers['cache-control'],'private, no-store')
        self.assertNotIn('PRIVATE_NOTE_SENTINEL',response.text);self.assertNotIn('RAW_METADATA_SENTINEL',response.text)
        async with self.Session() as db:
            before=(await db.get(GameStatsState,self.user_id)).source_revision
            base=await db.scalar(select(GameEntry).where(GameEntry.work_id==self.game_id))
            expansion=CatalogueEntity(kind='game',name='Expansion',attributes={'game_class':'expansion'})
            db.add(expansion);await db.flush()
            db.add(GameChildState(user_id=self.user_id,work_id=expansion.id,base_work_id=self.game_id,base_entry_id=base.id,status='completed',score=10))
            await db.commit()
            self.assertEqual((await db.get(GameStatsState,self.user_id)).source_revision,before)
        await self.ready();after=(await self.request()).json()
        self.assertEqual(after['data']['totals'],data['data']['totals'])
        self.assertEqual((await self.client.get('/tracking/profile/statistics-fixture/stats/overview?media_type=game')).status_code,422)
        async with self.Session() as db:
            self.assertIsNone((await db.get(UserStatsState,self.user_id)).active_snapshot_id)

    async def test_semantic_writes_invalidate_immediately_notes_do_not_and_stale_publication_is_rejected(self):
        await self.ready()
        async with self.Session() as db:
            row=await db.scalar(select(GameEntry).where(GameEntry.work_id==self.game_id))
            row.notes='Another private note';row.revision+=1;await db.commit()
        self.assertEqual((await self.request()).json()['status'],'ready')
        async with self.Session() as db:
            row=await db.scalar(select(GameEntry).where(GameEntry.work_id==self.game_id));row.total_seconds=108000;await db.commit()
        self.assertEqual((await self.request()).json()['status'],'pending')
        job=await stats.claim(self.Session);result=await stats.compute(job,self.Session)
        async with self.Session() as db:
            row=await db.scalar(select(GameEntry).where(GameEntry.work_id==self.game_id));row.score=9;await db.commit()
        self.assertFalse(await stats.publish(job,result,self.Session))
        async with self.Session() as db:
            state=await db.get(GameStatsState,self.user_id);state.next_due_at=stats.now();await db.commit()
        await self.ready();data=(await self.request()).json()['data']
        self.assertEqual(data['totals']['recorded_seconds'],108000)
        self.assertEqual(data['comparisons'][1]['mean_difference_seconds'],0)

    async def test_principal_cast_and_hard_cap_have_no_continuation_and_reject_changed_generations(self):
        async with self.Session() as db:
            for index in range(30):
                person=CatalogueEntity(kind='person',name=f'Actor {index:02}');db.add(person);await db.flush()
                db.add(CatalogueCredit(work_id=self.game_id,contributor_id=person.id,provider='imdb',source_key=str(index),role='voice_actor',
                    attributes={'large_evidence':'RAW_CREDIT_SENTINEL '*5000}))
            await db.commit()
        await self.ready();first=(await self.request(section='actors')).json()
        self.assertEqual((first['total'],len(first['data']),first['has_more']),(24,24,False))
        second=(await self.request(section='actors',page=2,generation=first['generation'])).json()
        self.assertEqual(len(second['data']),24)
        self.assertEqual(second['page'],1)
        self.assertEqual(len({row['key'] for row in first['data']+second['data']}),24)
        self.assertNotIn('RAW_',str(first))
        async with self.Session() as db:
            row=await db.scalar(select(GameEntry).where(GameEntry.work_id==self.game_id));row.score=9;await db.commit()
        await self.ready()
        self.assertEqual((await self.request(section='actors',page=2,generation=first['generation'])).status_code,409)

    async def test_all_group_sorts_read_thirty_from_the_complete_snapshot(self):
        await self.ready()
        async with self.Session() as db:
            state=await db.get(GameStatsState,self.user_id)
            rows=[{'key':str(i),'label':f'Company {i:02}','games':i+1,'recorded_seconds':40-i,
                'known_time_games':1,'mean_score':i/4,'rated_games':1,'href':None,'image':None,'roles':[], 'top_titles':[]} for i in range(40)]
            state.payload={**state.payload,'developers':rows};await db.commit()
        count=(await self.request(section='developers')).json()
        time=(await self.request(section='developers',sort='time')).json()
        self.assertEqual((len(count['data']),count['total'],count['has_more']),(30,30,False))
        self.assertEqual(count['data'][0]['key'],'39');self.assertEqual(time['data'][0]['key'],'0')
        async with self.Session() as db:
            self.assertEqual(len((await db.get(GameStatsState,self.user_id)).payload['developers']),40)

    async def test_private_profile_revocation_and_anonymous_paths_do_not_expose_cached_statistics(self):
        await self.ready()
        async with self.Session() as db:
            other=User(username='game-stats-viewer',email='game-stats-viewer@example.test',api_key='other-stats-key',games_access=True)
            db.add(other);await db.commit();self.viewer=other
            profile=await db.scalar(select(UserProfileData).where(UserProfileData.user_id==self.user_id));profile.privacy_level=PrivacyLevel.private
            await db.commit()
        self.assertEqual((await self.request()).status_code,403)
        self.viewer.games_access=False;self.assertEqual((await self.request()).status_code,403)
        self.viewer=None;self.assertIn((await self.request()).status_code,(401,403))

    async def test_metadata_invalidation_is_scoped_and_follows_verified_contributor_aliases(self):
        async with self.Session() as db:
            person=CatalogueEntity(kind='person',name='Canonical actor');db.add(person);await db.flush()
            alias=CatalogueEntity(kind='person',name='Alias',canonical_person_id=person.id);db.add(alias);await db.flush()
            db.add(CatalogueCredit(work_id=self.game_id,contributor_id=alias.id,provider='imdb',source_key='alias',role='actor'))
            unrelated=CatalogueEntity(kind='game',name='Unlisted game',attributes={'game_class':'game'})
            db.add(unrelated);await db.commit();person_id,unrelated_id=person.id,unrelated.id
        await self.ready();before=(await self.request()).json()['generation']
        async with self.Session() as db:
            unrelated=await db.get(CatalogueEntity,unrelated_id);unrelated.name='Unrelated correction';await db.commit()
        self.assertEqual((await self.request()).json()['generation'],before)
        async with self.Session() as db:
            person=await db.get(CatalogueEntity,person_id);person.name='Verified corrected actor';await db.commit()
        self.assertEqual((await self.request()).json()['status'],'pending')
        await self.ready();actors=(await self.request(section='actors')).json()['data']
        self.assertEqual(actors[0]['label'],'Verified corrected actor')
        async with self.Session() as db:
            credit=await db.scalar(select(CatalogueCredit).where(CatalogueCredit.source_key=='alias'))
            credit.attributes={'source_active':False};await db.commit()
        self.assertEqual((await self.request()).json()['status'],'pending')
        await self.ready();self.assertEqual((await self.request(section='actors')).json()['data'],[])

    async def test_migration_round_trip_preserves_personal_entries_and_warm_reads_select_only_the_section(self):
        await self.ready();statements=[]
        def record(conn,cursor,statement,parameters,context,many):statements.append(statement)
        event.listen(self.engine.sync_engine,'before_cursor_execute',record)
        try:response=await self.request(section='genres')
        finally:event.remove(self.engine.sync_engine,'before_cursor_execute',record)
        self.assertEqual(response.status_code,200,response.text)
        self.assertFalse(any('game_stats_state.payload AS' in statement or 'game_entries.notes' in statement for statement in statements))
        self.assertFalse(any(statement.lstrip().startswith(('INSERT','UPDATE','DELETE')) for statement in statements))
        migration=importlib.import_module('migrations.versions.mt066_game_statistics')
        def apply(conn,operation):
            with Operations.context(MigrationContext.configure(conn)):operation()
        async with self.engine.begin() as conn:
            await conn.run_sync(lambda conn:apply(conn,migration.downgrade))
            self.assertEqual(await conn.scalar(select(func.count()).select_from(GameEntry)),1)
            await conn.run_sync(lambda conn:apply(conn,migration.upgrade))
            precision=await conn.scalars(text("SELECT DISTINCT datetime_precision FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='game_stats_state' AND data_type LIKE 'timestamp%'"))
            self.assertEqual(list(precision),[3])

    async def test_unchanged_daily_probe_skips_work_and_failed_or_cancelled_jobs_release_the_lease(self):
        await self.ready();before=(await self.request()).json()['generation']
        async with self.Session() as db:
            state=await db.get(GameStatsState,self.user_id);state.next_due_at=stats.now();await db.commit()
        self.assertEqual(await stats.claim(self.Session),{'skipped':True})
        self.assertEqual((await self.request()).json()['generation'],before)
        async with self.Session() as db:
            row=await db.scalar(select(GameEntry).where(GameEntry.work_id==self.game_id));row.total_seconds=90000;await db.commit()
        job=await stats.claim(self.Session);await stats.release(job,self.Session,failed=True)
        data=(await self.request()).json();self.assertEqual(data['status'],'error');self.assertIsNone(data['data'])
        self.assertGreater(data['retry_after'],3)
        async with self.Session() as db:
            state=await db.get(GameStatsState,self.user_id);self.assertIsNone(state.lease_token)
            state.next_due_at=stats.now();await db.commit()
        job=await stats.claim(self.Session);await stats.release(job,self.Session)
        async with self.Session() as db:
            state=await db.get(GameStatsState,self.user_id);self.assertIsNone(state.lease_token)
            state.next_due_at=stats.now();await db.commit()
        await self.ready();self.assertEqual((await self.request()).json()['data']['totals']['recorded_seconds'],90000)

    async def test_empty_profile_is_computed_once_and_explicit_zero_progress_is_preserved(self):
        async with self.Session() as db:
            row=await db.scalar(select(GameEntry).where(GameEntry.work_id==self.game_id));await db.delete(row);await db.commit()
        await self.ready();data=(await self.request()).json()['data']
        self.assertEqual(data['totals']['listed_games'],0)
        self.assertIsNone(data['totals']['achievement_percentage'])
        async with self.Session() as db:
            db.add(GameEntry(user_id=self.user_id,work_id=self.game_id,status='playing',total_seconds=0,
                achievement_mode='percentage',achievement_percentage=0));await db.commit()
        await self.ready();data=(await self.request()).json()['data']
        self.assertEqual(data['totals']['recorded_seconds'],0)
        self.assertEqual(data['totals']['achievement_percentage'],0)

    async def test_targeted_unlock_changes_invalidate_but_import_evidence_timestamp_does_not(self):
        async with self.Session() as db:
            game=await db.get(CatalogueEntity,self.game_id);game.attributes={**game.attributes,'achievement_catalogue':{'total':2,'achievements':'LARGE_ACHIEVEMENT_SENTINEL '*5000}}
            db.add(CatalogueIdentity(entity_id=game.id,namespace='steam.app',external_id='9000000',source='steam'))
            await db.flush()
            achievement=GameAchievement(work_id=game.id,app_id='9000000',api_name='FIRST',name='First')
            db.add(achievement);await db.flush();achievement_id=achievement.id
            entry=await db.scalar(select(GameEntry).where(GameEntry.work_id==game.id));entry.achievement_mode='targeted';await db.commit()
            from core.game_statistics_facts import load_facts
            facts,_=await load_facts(db,self.user_id)
            self.assertEqual(facts[0]['achievements'],{'total':2})
        await self.ready();before=(await self.request()).json()['data']['totals'];self.assertEqual(before['achievement_count'],0)
        async with self.Session() as db:
            db.add(GameAchievementUnlock(user_id=self.user_id,work_id=self.game_id,achievement_id=achievement_id));await db.commit()
        self.assertEqual((await self.request()).json()['status'],'pending');await self.ready()
        after=(await self.request()).json();self.assertEqual(after['data']['totals']['achievement_count'],1)
        self.assertEqual(after['data']['totals']['achievement_percentage'],50)
        async with self.Session() as db:
            unlock=await db.get(GameAchievementUnlock,(self.user_id,achievement_id));unlock.source='steam';unlock.observed_at=stats.now();await db.commit()
        self.assertEqual((await self.request()).json()['generation'],after['generation'])

    async def test_concurrent_workers_share_one_lease_and_reject_a_metadata_race(self):
        jobs=await asyncio.gather(stats.claim(self.Session),stats.claim(self.Session))
        claimed=[job for job in jobs if job]
        self.assertEqual(len(claimed),1)
        job=claimed[0];result=await stats.compute(job,self.Session)
        async with self.Session() as db:
            game=await db.get(CatalogueEntity,self.game_id);game.attributes={**game.attributes,'release_date':'2021-01-01'};await db.commit()
        self.assertFalse(await stats.publish(job,result,self.Session))
        async with self.Session() as db:
            state=await db.get(GameStatsState,self.user_id);state.next_due_at=stats.now();await db.commit()
        await self.ready();self.assertEqual((await self.request()).json()['data']['release_years'][0]['key'],'2021')

    async def test_scheduler_cancellation_and_failure_release_claims_without_publishing(self):
        with patch.object(stats,'compute',AsyncMock(side_effect=asyncio.CancelledError)):
            with self.assertRaises(asyncio.CancelledError):await stats.run_due(self.Session)
        async with self.Session() as db:
            state=await db.get(GameStatsState,self.user_id);self.assertIsNone(state.lease_token);self.assertIsNone(state.payload)
            state.next_due_at=stats.now();await db.commit()
        with patch.object(stats,'compute',AsyncMock(side_effect=RuntimeError('Fixture failure'))):
            self.assertEqual(await stats.run_due(self.Session),0)
        async with self.Session() as db:
            state=await db.get(GameStatsState,self.user_id);self.assertIsNone(state.lease_token);self.assertIsNone(state.payload)
            self.assertEqual(state.error_code,'calculation_failed')
