"""Admin health and repair safety against an explicitly disposable PostgreSQL DB."""
import json
import os
import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI, HTTPException
from sqlalchemy import func, select, text, update

import test_statistics_pg as statistics_fixture
from core import catalogue, data_health, statistics_snapshots as snapshots
from core.catalogue_additive import fill_document
from core.catalogue_normalize import normalize_tmdb
from core.data_health_repairs import fill_screen_metadata, refresh_statistics, run_job
from db import get_db
from dependencies import get_current_user
from models import Media, User, WatchEvent
from models.base import MediaType
from models.catalogue import CatalogueCredit, CatalogueEntity, CatalogueIdentity, MetadataSnapshot
from models.statistics import StatsMetadataRevision, UserStatsState
from models.sync import SyncJob, SyncStatus
from models.tracking import TrackedEntry
from routers.admin_data_health import router

URL = os.getenv('STATISTICS_TEST_DATABASE_URL') or os.getenv('TRACKING_TEST_DATABASE_URL')


@unittest.skipUnless(URL, 'Requires explicit disposable statistics PostgreSQL')
class DataHealthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # Reuse the neighbouring migration-backed fixture, without inheriting tests.
        await statistics_fixture.StatisticsDatabaseTests.asyncSetUp(self)
        await self.client.aclose()
        app = FastAPI()
        app.include_router(router)

        async def db_override():
            async with self.Session() as db:
                yield db

        async def caller():
            if self.viewer is None:
                raise HTTPException(status_code=401, detail='Sign in required')
            return self.viewer

        app.dependency_overrides[get_db] = db_override
        app.dependency_overrides[get_current_user] = caller
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')
        async with self.Session() as db:
            self.viewer = await db.get(User, self.user_id)
            self.viewer.is_admin = True
            await db.commit()

    async def asyncTearDown(self):
        await statistics_fixture.StatisticsDatabaseTests.asyncTearDown(self)

    async def test_report_is_read_only_counts_titles_once_and_redacts_secrets(self):
        async with self.Session() as db:
            user = User(email='private@example.test', username='other', api_key='PRIVATE_KEY')
            db.add(user)
            await db.flush()
            db.add(TrackedEntry(user_id=user.id, media_id=self.media_id))
            db.add(MetadataSnapshot(provider='tmdb', kind='movie', external_id='1', error_code='PRIVATE_KEY', payload={'key':'PRIVATE_KEY'}))
            await db.commit()
            revision = await db.scalar(select(StatsMetadataRevision.revision))
        response = await self.client.get('/admin/data-health')
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data['tracked_titles'], 1)
        checks = {c['code']:c for c in data['checks']}
        self.assertEqual(checks['genres']['count'], 0)
        self.assertEqual(checks['country']['count'], 1)
        self.assertEqual(checks['character_links']['count'], 1)
        self.assertEqual(checks['character_images']['count'], 0)
        self.assertEqual(checks['provider_failures']['count'], 1)
        self.assertNotIn('PRIVATE_KEY', response.text)
        self.assertNotIn('private@example.test', response.text)
        async with self.Session() as db:
            self.assertEqual(await db.scalar(select(StatsMetadataRevision.revision)), revision)
            self.assertEqual(await db.scalar(select(func.count()).select_from(WatchEvent)), 1)

    async def test_all_routes_require_admin_and_reject_unknown_operations(self):
        for path in ('/admin/data-health','/admin/data-health/job'):
            self.viewer = None
            self.assertEqual((await self.client.get(path)).status_code,401)
            async with self.Session() as db:
                self.viewer = await db.get(User,self.user_id)
            self.viewer.is_admin = False
            self.assertEqual((await self.client.get(path)).status_code,403)
        self.assertEqual((await self.client.post('/admin/data-health/repair',json={'action':'characters'})).status_code,403)
        self.viewer.is_admin = True
        self.assertEqual((await self.client.post('/admin/data-health/repair',json={'action':'delete_duplicates'})).status_code,422)
        self.assertEqual((await self.client.post('/admin/data-health/repair',json={'action':'characters','force':True})).status_code,422)

    async def test_duplicate_candidates_and_conflicting_roots_are_not_merged(self):
        async with self.Session() as db:
            movie = await db.get(Media,self.media_id)
            movie.tmdb_id=42; movie.tvdb_id=43
            extra = Media(title='Duplicate',media_type=MediaType.movie,tmdb_id=42)
            other_kind = Media(title='Same number, different type',media_type=MediaType.series,tmdb_id=42)
            work = await db.scalar(select(CatalogueEntity).where(CatalogueEntity.kind=='movie'))
            conflicting = CatalogueEntity(kind='movie',name='Conflicting root',attributes={})
            names = [CatalogueEntity(kind='person',name=n,attributes={}) for n in ('Shared Name','shared name')]
            db.add_all([extra,other_kind,conflicting,*names]); await db.flush()
            db.add_all([CatalogueIdentity(entity_id=work.id,namespace='tmdb.movie',external_id='42',source='tmdb'),
                        CatalogueIdentity(entity_id=conflicting.id,namespace='tvdb.movie',external_id='43',source='tvdb')])
            await db.commit()
            count=await db.scalar(select(func.count()).select_from(CatalogueEntity))
        checks={c['code']:c for c in (await self.client.get('/admin/data-health')).json()['checks']}
        self.assertEqual(checks['duplicate_media']['count'],1)
        self.assertEqual(checks['duplicate_people']['count'],1)
        self.assertEqual(checks['identity_conflicts']['count'],1)
        self.assertIsNone(checks['duplicate_media']['action'])
        async with self.Session() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(CatalogueEntity)),count)

    async def test_additive_projection_preserves_known_and_protected_data_and_credits(self):
        async with self.Session() as db:
            work=await db.scalar(select(CatalogueEntity).where(CatalogueEntity.kind=='movie'))
            work.description='Keep biography'; work.attributes={'genres':['Comedy'],'countries':[],'runtime':110}
            work.protected_fields=['attributes.country_basis']
            db.add(CatalogueIdentity(entity_id=work.id,namespace='tmdb.movie',external_id='42',source='tmdb'))
            old=await db.scalar(select(CatalogueCredit).where(CatalogueCredit.role=='actor'))
            old.provider='tmdb'; old.source_key='old-credit'
            db.add(CatalogueIdentity(entity_id=old.contributor_id,namespace='tmdb.person',external_id='10',source='tmdb'))
            await db.commit()
            raw={'id':42,'title':'Replace known title','overview':'Replace biography','origin_country':['DE'],'runtime':150,
                 'genres':[{'id':28,'name':'Action'}],'credits':{'cast':[{'id':10,'name':'Replace person','credit_id':'new-credit','character':'New Role'}],'crew':[]}}
            doc=normalize_tmdb(raw,'movie'); doc['coverage']['credits_complete']=True
            await fill_document(db,doc,'tmdb'); await db.commit()
            await db.refresh(work)
            self.assertEqual(work.name,'Fixture movie')
            self.assertEqual(work.description,'Keep biography')
            self.assertEqual(work.attributes['genres'],['Comedy'])
            self.assertEqual(work.attributes['runtime'],110)
            self.assertEqual(work.attributes['countries'],['DE'])
            self.assertNotIn('country_basis',work.attributes)
            credits=list(await db.scalars(select(CatalogueCredit).where(CatalogueCredit.role=='actor')))
            self.assertEqual(len(credits),2)
            self.assertIn('old-credit',{c.source_key for c in credits})
            person=await db.get(CatalogueEntity,old.contributor_id)
            self.assertEqual(person.name,'Fixture Actor')
            self.assertEqual(await db.scalar(select(func.count()).select_from(WatchEvent)),1)

    async def test_job_lock_duplicate_enqueue_restart_recovery_and_durable_results(self):
        async with self.Session() as db:
            job=await data_health.enqueue(db,self.user_id,'characters')
            job_id=job.id
        async with self.Session() as db:
            self.assertIsNone(await data_health.enqueue(db,self.user_id,'metadata'))
        await run_job(job_id,factory=self.Session,bind=self.engine)
        async with self.Session() as db:
            job=await db.get(SyncJob,job_id)
            self.assertEqual(job.status,SyncStatus.completed)
            self.assertEqual(job.processed_items,2)
            self.assertEqual(job.total_items,2)
            self.assertIn('Character links',job.stats['results'])
            await db.execute(update(SyncJob).where(SyncJob.id==job_id).values(status=SyncStatus.running,updated_at=snapshots.utcnow()-timedelta(hours=1)))
            await db.commit()
        async with self.Session() as db:
            self.assertEqual(data_health.job_view(await data_health.latest_job(db))['status'],'interrupted')
            replacement=await data_health.enqueue(db,self.user_id,'characters')
            self.assertIsNotNone(replacement)
            await db.refresh(await db.get(SyncJob,job_id))
            self.assertEqual((await db.get(SyncJob,job_id)).status,SyncStatus.failed)
        async with self.engine.connect() as lock:
            await lock.execute(text('SELECT pg_advisory_lock(:key)'),{'key':data_health.LOCK_KEY}); await lock.commit()
            async with self.Session() as db:
                self.assertIsNone(await data_health.enqueue(db,self.user_id,'characters'))
            await lock.execute(text('SELECT pg_advisory_unlock(:key)'),{'key':data_health.LOCK_KEY}); await lock.commit()

    async def test_failed_job_retains_completed_steps_and_hides_exception(self):
        async with self.Session() as db:
            job=await data_health.enqueue(db,self.user_id,'characters'); job_id=job.id
        with patch('core.data_health_repairs.repair_unmapped_roles',AsyncMock(side_effect=RuntimeError('SECRET_URL'))):
            await run_job(job_id,factory=self.Session,bind=self.engine)
        async with self.Session() as db:
            job=await db.get(SyncJob,job_id)
            self.assertEqual(job.status,SyncStatus.failed)
            self.assertEqual(job.processed_items,1)
            self.assertNotIn('SECRET_URL',json.dumps(data_health.job_view(job),default=str))

    async def test_statistics_refresh_preserves_personal_facts_and_daily_schedule(self):
        result=await refresh_statistics(self.Session)
        self.assertEqual(result['published'],1)
        async with self.Session() as db:
            state=await db.get(UserStatsState,self.user_id)
            self.assertGreater(state.next_due_at,snapshots.utcnow()+timedelta(hours=23))
            self.assertEqual(await db.scalar(select(func.count()).select_from(WatchEvent)),1)
            self.assertEqual((await db.scalar(select(TrackedEntry))).manual_score,8)
        checks={c['code']:c for c in (await self.client.get('/admin/data-health')).json()['checks']}
        self.assertEqual(checks['statistics_missing']['count'],0)
        self.assertEqual(checks['statistics_scheduled']['count'],0)
        async with self.Session() as db:
            await db.execute(update(UserStatsState).values(dirty=True)); await db.commit()
        checks={c['code']:c for c in (await self.client.get('/admin/data-health')).json()['checks']}
        self.assertEqual(checks['statistics_scheduled']['severity'],'info')
        self.assertEqual(checks['statistics_scheduled']['count'],1)

    async def test_cached_metadata_avoids_network_and_provider_backoff_is_respected(self):
        async with self.Session() as db:
            movie=await db.get(Media,self.media_id); movie.tmdb_id=42
            db.add(MetadataSnapshot(provider='tmdb',kind='movie',external_id='42',error_code='rate_limited',next_attempt_at=snapshots.utcnow()+timedelta(hours=1)))
            await db.commit()
            providers=type('Providers',(),{'tmdb_key':'PRIVATE_KEY','tvdb_key':None,'detail':AsyncMock()})()
            result=await fill_screen_metadata(db,providers)
            self.assertEqual(result['deferred'],1)
            providers.detail.assert_not_awaited()

    async def test_additive_refresh_uses_leases_rejects_wrong_reply_and_records_retry(self):
        providers=AsyncMock()
        providers.validate=lambda *args: None
        providers.detail.return_value=(normalize_tmdb({'id':999,'title':'Wrong movie'},'movie'),{'id':999,'title':'Wrong movie'})
        async with self.Session() as db:
            receipt=await catalogue.refresh_metadata(db,providers,'tmdb','movie','42',additive=True)
            self.assertEqual(receipt['error_code'],'identity_conflict')
            snapshot=await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.external_id=='42'))
            self.assertIsNone(snapshot.lease_token)
            self.assertIsNotNone(snapshot.next_attempt_at)
            self.assertEqual(await db.scalar(select(func.count()).select_from(CatalogueEntity).where(CatalogueEntity.name=='Wrong movie')),0)

    async def test_cached_projection_fills_fields_without_provider_requests(self):
        async with self.Session() as db:
            movie=await db.get(Media,self.media_id); movie.tmdb_id=42
            work=await db.scalar(select(CatalogueEntity).where(CatalogueEntity.kind=='movie'))
            db.add(CatalogueIdentity(entity_id=work.id,namespace='tmdb.movie',external_id='42',source='tmdb'))
            db.add(MetadataSnapshot(provider='tmdb',kind='movie',external_id='42',entity_id=work.id,
                payload={'id':42,'title':'Fixture movie','poster_path':'/poster.jpg','origin_country':['US'],'genres':[{'id':18,'name':'Drama'}]}))
            await db.commit()
            providers=type('Providers',(),{'tmdb_key':None,'tvdb_key':None,'detail':AsyncMock()})()
            result=await fill_screen_metadata(db,providers)
            self.assertEqual(result['updated'],1)
            providers.detail.assert_not_awaited()
            await db.refresh(work)
            self.assertEqual(work.attributes['countries'],['US'])
            self.assertEqual(work.image_url,'https://image.tmdb.org/t/p/original/poster.jpg')

    async def test_metadata_job_is_bounded_and_partial_failures_are_reported(self):
        async with self.Session() as db:
            job=await data_health.enqueue(db,self.user_id,'metadata'); job_id=job.id
        with patch('core.data_health_repairs.fill_screen_metadata',AsyncMock(return_value={'examined':1,'failed':1})), patch(
            'core.data_health_repairs.backfill_history_runtimes',AsyncMock(return_value={'examined':0,'failed':0})) as runtimes, patch(
            'core.data_health_repairs.backfill_legacy_countries',AsyncMock(return_value={'examined':0,'failed':0})), patch(
            'core.data_health_repairs.backfill_catalogue_countries',AsyncMock(return_value={'examined':0,'failed':0})) as countries, patch(
            'core.data_health_repairs.backfill_tvmaze_images',AsyncMock(return_value={'examined':0,'failed':0})) as images:
            await run_job(job_id,factory=self.Session,bind=self.engine)
        self.assertEqual(runtimes.await_args.kwargs['limit'],50)
        self.assertEqual(countries.await_args.kwargs['missing_only'],True)
        self.assertEqual(images.await_args.kwargs['limit'],10)
        async with self.Session() as db:
            job=await db.get(SyncJob,job_id)
            self.assertEqual(job.status,SyncStatus.completed)
            self.assertEqual(job.processed_items,5)
            self.assertEqual(job.errors,1)

    async def test_unnamed_voice_credits_are_unavailable_metadata_not_broken_links(self):
        async with self.Session() as db:
            await db.execute(update(CatalogueCredit).where(CatalogueCredit.role=='actor').values(character_label='(voice)'))
            await db.commit()
        checks={c['code']:c for c in (await self.client.get('/admin/data-health')).json()['checks']}
        self.assertEqual(checks['character_links']['count'],0)
        self.assertEqual(checks['character_names']['count'],1)
        self.assertEqual(checks['character_names']['severity'],'info')

    async def test_statistics_batch_refreshes_only_selected_accounts(self):
        async with self.Session() as db:
            other=User(email='other@example.test',username='second',api_key='second-test-key')
            db.add(other); await db.flush()
            db.add(TrackedEntry(user_id=other.id,media_id=self.media_id)); await db.flush()
            await db.execute(update(UserStatsState).where(UserStatsState.user_id==self.user_id).values(next_due_at=snapshots.utcnow()-timedelta(days=2)))
            await db.execute(update(UserStatsState).where(UserStatsState.user_id==other.id).values(next_due_at=snapshots.utcnow()-timedelta(days=1)))
            await db.commit(); other_id=other.id
        with patch('core.data_health_repairs.STATS_BATCH',1):
            result=await refresh_statistics(self.Session)
        self.assertEqual(result,{'requested':1,'published':1,'pending':0})
        async with self.Session() as db:
            self.assertIsNotNone((await db.get(UserStatsState,self.user_id)).active_snapshot_id)
            self.assertIsNone((await db.get(UserStatsState,other_id)).active_snapshot_id)


if __name__=='__main__':
    unittest.main()
