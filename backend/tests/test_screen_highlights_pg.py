"""Screen shelf ingestion and career hints share the real catalogue tables."""

import asyncio
from datetime import datetime, timedelta, timezone
import unittest
from unittest.mock import AsyncMock, patch

from sqlalchemy import select

from core import tmdb
from core.catalogue_normalize import normalize_tmdb
from core.catalogue_providers import CatalogueProviders, ProviderHTTP
from core.game_work import Priority, work_context
from core.screen_appearance_backfill import KIND, backfill_screen_appearances, career_candidates
from core.screen_highlights import cached_highlights, ingest_highlights
from models import Media
from models.base import MediaType
from models.catalogue import CatalogueCredit, CatalogueEntity, CatalogueIdentity, CatalogueLegacyLink, ContributorEvidence, MetadataProviderBudget, MetadataSnapshot
from models.game_runner import GameProviderTicket
from models.tracking import TrackedEntry
import test_statistics_pg as fixture


@unittest.skipUnless(fixture.URL, 'Requires explicit disposable statistics PostgreSQL')
class ScreenHighlightsTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.StatisticsDatabaseTests.asyncSetUp
    asyncTearDown = fixture.StatisticsDatabaseTests.asyncTearDown

    async def test_cached_shelves_take_ten_each_and_use_local_fallback(self):
        pages = AsyncMock(side_effect=lambda *args, **opts: {'results': [
            {'type':opts['media_type'], 'tmdb_id':i} for i in range(1,13)]})
        with (
            patch('core.screen_highlights.browse.browse_page', pages),
            patch('core.screen_highlights.browse.imdb_page', AsyncMock(return_value=({}, []))) as ranked,
            patch('core.screen_highlights.chronological_discovery.page', AsyncMock(return_value=None)) as newest,
        ):
            async with self.Session() as db:
                sections = await cached_highlights(db, key='fixture', mdblist_key='fixture', show_anime=False)
        self.assertEqual(len(sections), 8)
        self.assertTrue(all([card['tmdb_id'] for card in shelf] == list(range(1,11)) for shelf in sections.values()))
        self.assertEqual(ranked.await_count, 6)
        self.assertEqual(newest.await_count, 2)
        self.assertTrue(all(call.kwargs['cached_only'] for call in [*ranked.await_args_list,*newest.await_args_list]))
        self.assertTrue(all(call.kwargs['source'] == ('local' if call.kwargs['sort']=='newest' else '')
                            for call in pages.await_args_list))

    async def test_titles_import_once_and_keep_cast_crew_studios(self):
        raw = {'id':81111, 'title':'Shelf title', 'genres':[], 'production_companies':[{'id':5, 'name':'Test Studio'}],
               'credits':{'cast':[{'id':4,'name':'Actor','character':'Lead','credit_id':'cast'}],
                          'crew':[{'id':6,'name':'Director','job':'Director','credit_id':'crew'}]}}
        providers = CatalogueProviders(tmdb_key='fixture')
        card = {'type':'movie', 'tmdb_id':81111}
        direct = {'type':'movie','tmdb_id':81112}
        alias = {'type':'series','tmdb_id':81113}
        shelves = {('movie','trending'):[card, direct], ('movie','score'):[card, direct], ('series','trending'):[alias]}
        with patch.object(providers, 'detail', AsyncMock(return_value=(normalize_tmdb(raw, 'movie'), raw))) as detail:
            async with self.Session() as db:
                linked = CatalogueEntity(kind='series',name='Listed series')
                tvdb_media = Media(title='Listed series',media_type='series',tvdb_id=12345)
                tmdb_media = Media(title='Listed movie',media_type='movie',tmdb_id=81112)
                db.add_all([linked,tvdb_media,tmdb_media])
                await db.flush()
                db.add_all([
                    CatalogueIdentity(entity_id=linked.id,namespace='tmdb.series',external_id='81113',source='tmdb'),
                    CatalogueIdentity(entity_id=linked.id,namespace='tvdb.series',external_id='12345',source='tvdb'),
                    TrackedEntry(user_id=self.user_id,media_id=tvdb_media.id,status='planning'),
                    TrackedEntry(user_id=self.user_id,media_id=tmdb_media.id,status='planning'),
                ])
                await db.commit()
                first = await ingest_highlights(db, providers, shelves, show_anime=True)
                second = await ingest_highlights(db, providers, shelves, show_anime=True)
                snapshot = await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.external_id=='81111'))
                roles = set(await db.scalars(select(CatalogueCredit.role).where(CatalogueCredit.work_id==snapshot.entity_id)))
        self.assertEqual((first['candidates'], first['listed'], first['updated'], second['cached']), (3, 2, 1, 1))
        self.assertEqual(roles, {'actor','director','producer'})
        detail.assert_awaited_once()

    async def test_old_filmography_hints_admit_unknown_series_and_reuse_saved_evidence(self):
        current = datetime.now(timezone.utc).replace(tzinfo=None)
        base = {'media_type':'tv','episode_count':1,'first_air_date':'1985-01-01','character':'Guest'}
        async with self.Session() as db:
            db.add(ContributorEvidence(kind='person', native_id=5, variant='test', config={},
                expires_at=current + timedelta(days=1), payload={'complete':True, 'cast':[
                    dict(base,id=10,adult=True), dict(base,id=11,genre_ids=[16],original_language='ja'),
                    dict(base,id=12), dict(base,id=13), dict(base,id=14,episode_count=40),
                ]}))
            db.add(MetadataSnapshot(provider='tmdb',kind=KIND,external_id='12',fetched_at=current))
            await db.commit()
            ids = await career_candidates(db, show_anime=False)
            self.assertEqual(ids,[12,13,14])
            self.assertEqual(await career_candidates(db, show_anime=False), ids)
            with patch('core.screen_appearance_backfill.acquire', AsyncMock()) as acquire:
                status = await backfill_screen_appearances(db, CatalogueProviders(tmdb_key='fixture'),
                    limit=1, priority_ids=ids)
            self.assertEqual(status['examined'], 1)
            self.assertEqual(acquire.await_args.args[2],13)

    async def test_fresh_partial_snapshot_repairs_unlisted_title_without_provider(self):
        raw = {'id':81117, 'title':'Cold legacy title', 'production_companies':[{'id':8,'name':'Test Studio'}],
               'credits': {'cast':[], 'crew':[{'id':9,'name':'Director','job':'Director','credit_id':'crew'}]}}
        stamp = datetime.now(timezone.utc).replace(tzinfo=None)
        providers = CatalogueProviders(tmdb_key='fixture')
        async with self.Session() as db:
            work = CatalogueEntity(kind='movie',name=raw['title'])
            media = Media(title=raw['title'],media_type=MediaType.movie,tmdb_id=raw['id'])
            db.add_all([work,media])
            await db.flush()
            db.add_all([
                CatalogueIdentity(entity_id=work.id,namespace='tmdb.movie',external_id=str(raw['id']),source='tmdb'),
                MetadataSnapshot(provider='tmdb',kind='movie',external_id=str(raw['id']),entity_id=work.id,
                    payload=raw,coverage={'detail_credits_version':3,'detail_credit_groups':['cast']},
                    fetched_at=stamp,expires_at=stamp + timedelta(days=7)),
            ])
            await db.commit()
            shelves = {('movie','trending'):[{'type':'movie','id':media.id,'tmdb_id':raw['id']}]}
            with patch.object(providers, 'detail', AsyncMock(side_effect=AssertionError('Fresh snapshot should be reused'))) as detail:
                first = await ingest_highlights(db, providers, shelves, show_anime=True)
                second = await ingest_highlights(db, providers, shelves, show_anime=True)
                detail.assert_not_awaited()
            self.assertEqual((first['updated'],second['cached']), (1,1))
            self.assertEqual((await db.get(CatalogueLegacyLink,media.id)).entity_id,work.id)
            self.assertEqual(set(await db.scalars(select(CatalogueCredit.role).where(CatalogueCredit.work_id==work.id))),
                             {'director','producer'})

    async def test_tmdb_direct_request_can_start_during_background_request(self):
        http = ProviderHTTP(session_factory=self.Session)
        started, release, interactive = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def background():
            with work_context(Priority.RETRY, 'screen-highlights'):
                async with http.lane('tmdb'):
                    started.set()
                    await release.wait()

        async def foreground():
            with work_context(Priority.DIRECT, 'viewer:fixture'):
                async with http.lane('tmdb'):
                    interactive.set()

        async with asyncio.TaskGroup() as group:
            group.create_task(background())
            await asyncio.wait_for(started.wait(), timeout=3)
            group.create_task(foreground())
            try:
                await asyncio.wait_for(interactive.wait(), timeout=3)
            finally:
                release.set()

    async def test_pending_foreground_leaves_background_request_budget_untouched(self):
        http = ProviderHTTP(session_factory=self.Session)
        started = asyncio.Event()

        async def background():
            with work_context(Priority.RETRY, 'screen-highlights'):
                async with http.lane('tmdb'):
                    started.set()

        async def wait_for_ticket():
            while True:
                async with self.Session() as db:
                    if await db.scalar(select(GameProviderTicket.id).where(GameProviderTicket.provider=='tmdb').limit(1)):
                        return
                await asyncio.sleep(.01)

        async with asyncio.TaskGroup() as group:
            with tmdb.foreground_read(), patch.object(tmdb, 'wait_for_foreground', AsyncMock()):
                group.create_task(background())
                await asyncio.wait_for(wait_for_ticket(), timeout=3)
                with self.assertRaises(TimeoutError):
                    await asyncio.wait_for(started.wait(), timeout=.2)
                async with self.Session() as db:
                    budget = await db.get(MetadataProviderBudget, 'tmdb')
                    self.assertEqual(budget.request_count if budget else 0, 0)
            await asyncio.wait_for(started.wait(), timeout=3)
