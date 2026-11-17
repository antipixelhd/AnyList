import asyncio
import unittest
from types import SimpleNamespace
from datetime import datetime,timedelta
from unittest.mock import AsyncMock
from sqlalchemy import select,func,text
import test_statistics_pg as fixture
from core.cinema_releases import backfill_cinema_releases
from core.catalogue_providers import ProviderError
from models import Media,WatchEvent
from models.catalogue import MetadataSnapshot


@unittest.skipUnless(fixture.URL,'Requires disposable PostgreSQL')
class CinemaBackfillTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):await fixture.StatisticsDatabaseTests.asyncSetUp(self)
    async def asyncTearDown(self):await fixture.StatisticsDatabaseTests.asyncTearDown(self)

    async def test_publication_proceeds_during_catalogue_work_and_collection_foreign_key_checks(self):
        async with self.Session() as db:
            movie=await db.get(Media,self.media_id);movie.tmdb_id=42
            await db.commit()
        # A collection insert holds KEY SHARE on its referenced media until its
        # transaction ends. Neither it nor unrelated identity publication should
        # stop this independent evidence cache from publishing JSON metadata.
        async with self.Session() as holder, self.Session() as publisher:
            await holder.execute(text('SELECT pg_advisory_xact_lock(735903601)'))
            await holder.execute(select(Media.id).where(Media.id==self.media_id)
                .with_for_update(read=True,key_share=True))
            adapter=SimpleNamespace(tmdb_key='fixture',http=SimpleNamespace(request=AsyncMock(return_value={'results':[]})))
            result=await asyncio.wait_for(backfill_cinema_releases(publisher,adapter),3)
            self.assertEqual(result['updated'],1)
            self.assertEqual(result['failed'],0)

    async def test_competing_refreshers_share_one_live_lease(self):
        async with self.Session() as db:
            (await db.get(Media,self.media_id)).tmdb_id=42
            await db.commit()
        started=asyncio.Event();release=asyncio.Event()
        async def request(*args,**kwargs):
            started.set();await release.wait()
            return {'results':[]}
        adapter=SimpleNamespace(tmdb_key='fixture',http=SimpleNamespace(request=AsyncMock(side_effect=request)))
        async with self.Session() as first,self.Session() as second:
            pending=asyncio.create_task(backfill_cinema_releases(first,adapter))
            try:
                await asyncio.wait_for(started.wait(),3)
                other=await asyncio.wait_for(backfill_cinema_releases(second,adapter),3)
                self.assertEqual(other['examined'],0)
            finally:
                release.set()
                result=await asyncio.wait_for(pending,3)
            self.assertEqual(result['updated'],1)
            self.assertEqual(adapter.http.request.await_count,1)

    async def test_cached_release_evidence_is_reused_without_changing_history(self):
        async with self.Session() as db:
            movie=await db.get(Media,self.media_id);movie.tmdb_id=42
            db.add(MetadataSnapshot(provider='tmdb',kind='movie',external_id='42',
                expires_at=datetime.utcnow()+timedelta(days=1),payload={'release_dates':{'results':[
                    {'iso_3166_1':'DE','release_dates':[{'type':2,'release_date':'2020-01-01'}]}]}}))
            await db.commit()
            adapter=SimpleNamespace(tmdb_key='fixture',http=SimpleNamespace(request=AsyncMock()))
            result=await backfill_cinema_releases(db,adapter)
            self.assertEqual(result['updated'],1);self.assertEqual(result['cinema'],1)
            self.assertEqual(movie.tmdb_data['theatrical_release_dates'],['2020-01-01'])
            self.assertEqual(movie.tmdb_data['theatrical_release_dates_by_country'],{'DE':['2020-01-01']})
            self.assertEqual(movie.tmdb_data['genres'],['Drama'])
            self.assertEqual(await db.scalar(select(func.count()).select_from(WatchEvent)),1)
            adapter.http.request.assert_not_awaited()

    async def test_existing_worldwide_dates_are_backfilled_from_release_snapshot(self):
        async with self.Session() as db:
            movie=await db.get(Media,self.media_id);movie.tmdb_id=42
            movie.tmdb_data={**movie.tmdb_data,'theatrical_release_dates':['2020-01-01']}
            db.add(MetadataSnapshot(provider='tmdb',kind='cinema_releases',external_id='42',
                expires_at=datetime.utcnow()+timedelta(days=1),payload={'results':[
                    {'iso_3166_1':'DE','release_dates':[{'type':4,'release_date':'2020-01-01'}]},
                    {'iso_3166_1':'RU','release_dates':[{'type':3,'release_date':'2020-01-01'}]}]}))
            await db.commit()
            adapter=SimpleNamespace(tmdb_key='fixture',http=SimpleNamespace(request=AsyncMock()))
            result=await backfill_cinema_releases(db,adapter)
            self.assertEqual(result['updated'],1)
            self.assertEqual(movie.tmdb_data['theatrical_release_dates_by_country'],{'DE':[],'RU':['2020-01-01']})
            adapter.http.request.assert_not_awaited()
            self.assertEqual((await backfill_cinema_releases(db,adapter))['examined'],0)

    async def test_provider_failure_is_durable_and_other_titles_still_backfill(self):
        async with self.Session() as db:
            movie=await db.get(Media,self.media_id);movie.tmdb_id=42
            other=Media(title='Streaming only',tmdb_id=43,media_type='movie',tmdb_data={'genres':['Drama']})
            db.add(other);await db.commit()
            adapter=SimpleNamespace(tmdb_key='fixture',http=SimpleNamespace(request=AsyncMock(side_effect=[
                ProviderError('rate_limited',3600),{'results':[{'release_dates':[{'type':4,'release_date':'2020-01-01'}]}]}])))
            result=await backfill_cinema_releases(db,adapter)
            self.assertEqual(result['failed'],1);self.assertEqual(result['updated'],1)
            await db.refresh(other);self.assertEqual(other.tmdb_data['theatrical_release_dates'],[])
            row=await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.kind=='cinema_releases',MetadataSnapshot.external_id=='42'))
            self.assertEqual(row.error_code,'rate_limited');self.assertGreater(row.next_attempt_at,datetime.utcnow()+timedelta(minutes=59))
            self.assertEqual((await backfill_cinema_releases(db,adapter))['examined'],0)
            self.assertEqual(adapter.http.request.await_count,2)
