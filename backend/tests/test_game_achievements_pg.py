"""Live-shaped catalogue ingestion, durable components and preserved user facts."""
import asyncio
import unittest
from datetime import timedelta,date
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch

from sqlalchemy import delete,select,func,text
from alembic.migration import MigrationContext
from alembic.operations import Operations

import test_games_pg as fixture
from core.catalogue_providers import ProviderError
from core.config import settings
from core.game_achievements import GameAchievements,JOB,PREFIX,view
from core.game_imdb_enrichment import now
from core.game_ratings import snapshot
from core.game_tracking import states
from models.catalogue import CatalogueEntity,CatalogueIdentity
from models.games import GameAchievement,GameAchievementUnlock,GameEntry
from models.game_statistics import GameStatsState


def globals_part():return {'available':True,'total':3,'percentages':{'A0':75.5,'A1':0,'A2':5}}


def schema_part(names=('A0','A1','A2')):
    return {'available':True,'total':len(names),'language':'english','definitions':[
        {'api_name':name,'name':f'Name {name}','description':None,'hidden':False,'icon_url':None,'locked_icon_url':None} for name in names]}


@unittest.skipUnless(fixture.URL,'Requires disposable Games PostgreSQL')
class AchievementDatabaseTests(unittest.IsolatedAsyncioTestCase):
    cleanup=fixture.GamesDatabaseTests.cleanup

    async def asyncSetUp(self):
        await fixture.GamesDatabaseTests.asyncSetUp(self)
        async with self.Session() as db:
            await db.execute(delete(GameAchievement))
            work=await db.get(CatalogueEntity,self.base)
            work.attributes={'game_class':'game','selected_artwork':'keep'}
            db.add(GameEntry(user_id=self.user.id,work_id=self.base,status='completed',score=8,total_seconds=198000,
                completion_seconds=144000,finish_date=date(2026,10,25),replay_count=2,notes='private draft preserved'))
            await view(db,work,enqueue=True);await db.commit()
        self.source=SimpleNamespace(key='test-only-server-key',fetch=AsyncMock(side_effect=
            lambda app,part,stamp:globals_part() if part=='globals' else schema_part()))
        self.service=GameAchievements(self.Session,self.source)

    async def due(self,*parts):
        async with self.Session() as db:
            job=await db.scalar(snapshot(JOB,self.base));job.next_attempt_at=now()-timedelta(seconds=1);job.expires_at=None
            for part in parts:
                row=await db.scalar(snapshot(PREFIX+part,self.base));row.expires_at=now()-timedelta(seconds=1)
            await db.commit()

    async def test_viewer_membership_selects_schema_or_global_order_before_pagination(self):
        await self.service.run(self.base)
        # Existing IDs must not dictate ordering after a provider schema reorder.
        await self.due('schema')
        self.source.fetch.side_effect=lambda app,part,stamp:globals_part() if part=='globals' else schema_part(('A2','A1','A0'))
        await self.service.run(self.base)
        path=f'/tracking/games/works/{self.base}/achievements'
        async def names():
            result=[]
            for offset in range(3):
                page=await self.client.get(path,params={'offset':offset,'limit':1})
                self.assertEqual(page.status_code,200,page.text)
                result.extend(row['name'] for row in page.json()['results'])
            return result
        self.assertEqual(await names(),['Name A2','Name A1','Name A0'])
        self.viewer=self.other
        self.assertEqual(await names(),['Name A0','Name A2','Name A1'])
        self.viewer=self.user
        async with self.Session() as db:
            await db.execute(delete(GameEntry).where(GameEntry.user_id==self.user.id,GameEntry.work_id==self.base))
            await db.commit()
        self.assertEqual(await names(),['Name A0','Name A2','Name A1'])

    async def test_migration_backfills_cached_steam_schema_order(self):
        await self.service.run(self.base)
        from migrations.versions import mt071_achievement_order as migration
        def migrate(conn):
            with Operations.context(MigrationContext.configure(conn)):
                migration.downgrade()
                migration.upgrade()
        async with self.engine.begin() as conn:
            await conn.run_sync(migrate)
        async with self.Session() as db:
            rows=list(await db.scalars(select(GameAchievement).order_by(GameAchievement.sort_order)))
            self.assertEqual([(row.api_name,row.sort_order) for row in rows],[('A0',0),('A1',1),('A2',2)])

    async def test_complete_idempotent_source_catalogue_and_unknown_personal_progress(self):
        self.assertEqual(await self.service.run(self.base),'ready')
        self.assertEqual(await self.service.run(self.base),'cached');self.assertEqual(self.source.fetch.await_count,2)
        async with self.Session() as db:
            work=await db.get(CatalogueEntity,self.base);entry=await db.scalar(select(GameEntry).where(GameEntry.work_id==self.base))
            summary=(await states(db,self.user.id,[self.base]))[0].achievements
            self.assertEqual((summary.total,summary.definitions_available,summary.mode,summary.percentage),(3,True,None,None))
            self.assertEqual((entry.status,entry.score,entry.total_seconds,entry.completion_seconds,entry.replay_count),('completed',8,198000,144000,2))
            self.assertEqual(entry.notes,'private draft preserved');self.assertEqual(work.attributes['selected_artwork'],'keep')
            rows=list(await db.scalars(select(GameAchievement).order_by(GameAchievement.api_name)))
            self.assertEqual(len(rows),3);self.assertEqual([row.global_percentage for row in rows],[75.5,0,5])
            ids=[row.id for row in rows]
        await self.due('schema');await self.service.run(self.base)
        async with self.Session() as db:self.assertEqual(list(await db.scalars(select(GameAchievement.id).order_by(GameAchievement.api_name))),ids)

    async def test_keyless_count_only_and_source_failure_never_certifies_zero(self):
        self.source.key='';self.service=GameAchievements(self.Session,self.source)
        self.assertEqual(await self.service.run(self.base),'ready');self.source.fetch.assert_awaited_once()
        async with self.Session() as db:
            summary=(await states(db,self.user.id,[self.base]))[0].achievements
            self.assertEqual((summary.total,summary.definitions_available,summary.mode),(3,False,None))
            self.assertEqual(await db.scalar(select(func.count()).select_from(GameAchievement)),0)
        await self.due('globals');self.source.fetch.side_effect=ProviderError('unavailable')
        self.assertEqual(await self.service.run(self.base),'partial')
        async with self.Session() as db:
            data=await view(db,await db.get(CatalogueEntity,self.base))
            self.assertEqual(data['catalogue']['total'],3);self.assertFalse(data['status']['complete'])
            self.assertTrue(data['status']['pending']);self.assertEqual(data['status']['error'],'unavailable')

    async def test_enabling_a_key_fetches_only_the_missing_schema_and_keeps_personal_percentage(self):
        self.source.key=''
        # Simulate an unconfigured server independently of the developer's private key.
        with patch.object(settings,'steam_web_api_key',''):
            await GameAchievements(self.Session,self.source).run(self.base)
        async with self.Session() as db:
            entry=await db.scalar(select(GameEntry).where(GameEntry.work_id==self.base));entry.achievement_mode='percentage';entry.achievement_percentage=37.5
            await db.commit()
        self.source.key='test-only-server-key';self.source.fetch.reset_mock()
        with patch.object(settings,'steam_web_api_key','test-only-server-key'):
            async with self.Session() as db:
                await view(db,await db.get(CatalogueEntity,self.base),enqueue=True);await db.commit()
            self.assertEqual(await GameAchievements(self.Session,self.source).run(self.base),'ready')
        self.source.fetch.assert_awaited_once();self.assertEqual(self.source.fetch.call_args.args[1],'schema')
        async with self.Session() as db:
            summary=(await states(db,self.user.id,[self.base]))[0].achievements
            self.assertEqual((summary.mode,summary.percentage,summary.definitions_available),('percentage',37.5,True))

    async def test_withdrawn_definitions_keep_evidence_but_leave_active_counts_and_dirty_statistics(self):
        await self.service.run(self.base)
        async with self.Session() as db:
            definition=await db.scalar(select(GameAchievement).where(GameAchievement.api_name=='A1'))
            db.add(GameAchievementUnlock(user_id=self.user.id,work_id=self.base,achievement_id=definition.id))
            entry=await db.scalar(select(GameEntry).where(GameEntry.work_id==self.base));entry.achievement_mode='targeted'
            await db.commit();self.retired_id=definition.id
            before=(await db.get(GameStatsState,self.user.id)).metadata_source_revision
        await self.due('schema')
        self.source.fetch.side_effect=lambda app,part,stamp:globals_part() if part=='globals' else schema_part(('A0','A2','A3'))
        await self.service.run(self.base)
        async with self.Session() as db:
            self.assertFalse((await db.get(GameAchievement,self.retired_id)).active)
            self.assertEqual(await db.scalar(select(func.count()).select_from(GameAchievementUnlock)),1)
            summary=(await states(db,self.user.id,[self.base]))[0].achievements
            self.assertEqual((summary.mode,summary.count,summary.exact_count),('targeted',0,0))
            self.assertGreater((await db.get(GameStatsState,self.user.id)).metadata_source_revision,before)
        response=await self.client.get(f'/tracking/games/works/{self.base}/achievements')
        self.assertEqual(response.status_code,200,response.text);self.assertEqual(response.json()['total'],3)
        self.assertNotIn(self.retired_id,[row['id'] for row in response.json()['results']])
        response=await self.client.patch(f'/tracking/games/entries/{self.base}',json={
            'revision':1,'achievement_mode':'targeted','expected_achievement_total':3,'unlocked_ids':[]})
        self.assertEqual(response.status_code,200,response.text)
        async with self.Session() as db:self.assertEqual(await db.scalar(select(func.count()).select_from(GameAchievementUnlock)),1)

    async def test_concurrent_callers_and_cancellation_share_two_component_reads(self):
        entered,release=asyncio.Event(),asyncio.Event()
        async def fetch(app,part,stamp):
            entered.set();await release.wait()
            return globals_part() if part=='globals' else schema_part()
        self.source.fetch.side_effect=fetch
        first=asyncio.create_task(self.service.run(self.base));await entered.wait()
        second=asyncio.create_task(self.service.run(self.base));first.cancel()
        with self.assertRaises(asyncio.CancelledError):await first
        release.set();self.assertEqual(await second,'ready');self.assertEqual(self.source.fetch.await_count,2)

    async def test_identity_changed_during_fetch_cannot_publish_a_wrong_app(self):
        async def change(app,part,stamp):
            async with self.Session() as db:
                db.add(CatalogueIdentity(entity_id=self.base,namespace='steam.app',external_id='99999',source='reviewed'))
                await db.commit()
            return globals_part()
        self.source.fetch.side_effect=change
        self.assertEqual(await self.service.run(self.base),'retry')
        async with self.Session() as db:
            self.assertNotIn('achievement_catalogue',(await db.get(CatalogueEntity,self.base)).attributes)
            self.assertEqual(await db.scalar(select(func.count()).select_from(GameAchievement)),0)

    async def test_api_is_private_and_child_or_unapproved_requests_do_not_admit_catalogues(self):
        response=await self.client.get(f'/tracking/games/works/{self.base}/achievement-catalogue')
        self.assertEqual(response.status_code,200,response.text);self.assertIn('private',response.headers['cache-control'])
        child=await self.client.get(f'/tracking/games/works/{self.child}/achievement-catalogue')
        self.assertEqual(child.json()['status']['state'],'unavailable')
        self.viewer.games_access=False
        self.assertEqual((await self.client.get(f'/tracking/games/works/{self.base}/achievement-catalogue')).status_code,403)
        self.viewer=None
        self.assertEqual((await self.client.get(f'/tracking/games/works/{self.base}/achievement-catalogue')).status_code,401)
