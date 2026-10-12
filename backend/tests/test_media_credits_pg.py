"""Real-database credit ingestion, cache reuse and tracking preservation."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from datetime import datetime, timedelta

from sqlalchemy import func, select

import test_statistics_pg as fixture
from core.media_credits import backfill_screen_credits, fill_screen_credits
from core.media_details import screen_work, work_details
from core.catalogue_normalize import normalize_tmdb
from dependencies import get_current_user
from models import Media, TrackedEntry, User
from models.base import MediaType
from models.catalogue import CatalogueCredit, CatalogueEntity, CatalogueIdentity, CatalogueLegacyLink, MetadataSnapshot


def movie_payload(native=1492640, *, empty=False):
    return {'id': native, 'title': 'UNABOMBER', 'production_companies': [], 'credits': {
        'cast': [] if empty else [{'id': 123, 'name': 'Lead Actor', 'character': 'Ted', 'order': 0, 'credit_id': 'cast'}],
        'crew': [] if empty else [{'id': 124, 'name': 'Director', 'job': 'Director', 'credit_id': 'crew'}]}}


@unittest.skipUnless(fixture.URL, 'Requires explicit disposable statistics PostgreSQL')
class MediaCreditTests(unittest.IsolatedAsyncioTestCase):
    async def test_person_filmography_backfill_filters_jobs_and_never_imports_personal_media(self):
        from core.person_works_backfill import backfill_person_works
        profile={'id':124,'combined_credits':{'crew':[
            {'id':10001,'media_type':'movie','job':'Director'},
            {'id':10001,'media_type':'movie','job':'Producer'},
            {'id':10002,'media_type':'movie','job':'Special Thanks'},
            {'id':10003,'media_type':'movie','job':'Idea'}]}}
        self.reply(movie_payload(10001))
        with patch('core.person_works_backfill.tmdb.get_person',AsyncMock(return_value=profile)):
            async with self.Session() as db:
                before=await db.scalar(select(func.count()).select_from(Media))
                result=await backfill_person_works(db,self.providers,124)
                self.assertEqual((result['examined'],result['updated'],result['failed']),(1,1,0))
                self.assertEqual(await db.scalar(select(func.count()).select_from(Media)),before)
                second=await backfill_person_works(db,self.providers,124)
                self.assertEqual(second['failed'],0)
        self.providers.detail.assert_awaited_once()

    async def test_existing_cast_and_one_crew_member_do_not_hide_missing_cached_director(self):
        from core.media_credits import VERSION
        from core import catalogue
        async with self.Session() as db:
            media = await self.new_movie(db)
            raw = movie_payload()
            raw['credits']['crew'].append({'id':125,'name':'Christopher Nolan','job':'Director','credit_id':'missing-nolan'})
            partial = {**raw, 'credits':{**raw['credits'], 'crew':raw['credits']['crew'][:1]}}
            work = await catalogue.ingest_document(db, normalize_tmdb(partial,'movie'),'tmdb')
            db.add(CatalogueLegacyLink(media_id=media.id,entity_id=work.id))
            db.add(MetadataSnapshot(provider='tmdb',kind='movie',external_id=str(media.tmdb_id),entity_id=work.id,
                payload=raw,coverage={'detail_credits_version':VERSION-1},fetched_at=datetime.utcnow(),expires_at=datetime.utcnow()+timedelta(days=7)))
            await db.commit()
            self.assertEqual(await fill_screen_credits(db,media,providers=self.providers),'updated')
            details = await work_details(db,work)
            self.assertIn('Christopher Nolan',[person['name'] for person in details['people']])
            self.assertEqual(await fill_screen_credits(db,media,providers=self.providers),'cached')
        self.providers.detail.assert_not_awaited()

    async def asyncSetUp(self):
        await fixture.StatisticsDatabaseTests.asyncSetUp(self)
        self.providers = SimpleNamespace(tmdb_key='fixture', tvdb_key=None,
            validate=lambda *args: None, detail=AsyncMock())

    async def asyncTearDown(self):
        await fixture.StatisticsDatabaseTests.asyncTearDown(self)

    async def new_movie(self, db):
        media = Media(title='UNABOMBER', media_type=MediaType.movie, tmdb_id=1492640)
        db.add(media)
        await db.commit()
        return media

    def reply(self, raw):
        self.providers.detail.return_value = (normalize_tmdb(raw, 'movie'), raw)

    async def test_unlisted_title_repairs_cast_crew_and_bridge_once(self):
        self.reply(movie_payload())
        async with self.Session() as db:
            media = await self.new_movie(db)
            result = await backfill_screen_credits(db, self.providers)
            self.assertEqual(result['updated'], 1)
            details = await work_details(db, await screen_work(db, media))
            self.assertEqual({p['name'] for p in details['people']}, {'Lead Actor', 'Director'})
            self.assertEqual(details['people'][0]['characters'], ['Ted'])
            self.assertIsNotNone(await db.get(CatalogueLegacyLink, media.id))
            self.assertEqual(await fill_screen_credits(db, media, providers=self.providers), 'cached')
            self.assertIsNone(await db.scalar(select(TrackedEntry).where(TrackedEntry.media_id == media.id)))
        self.providers.detail.assert_awaited_once()

    async def test_cached_payload_fills_missing_crew_preserving_personal_facts(self):
        async with self.Session() as db:
            media = await db.get(Media, self.media_id)
            media.tmdb_id = 1492640
            work = await screen_work(db, media)
            db.add(CatalogueIdentity(entity_id=work.id, namespace='tmdb.movie', external_id='1492640', source='tmdb'))
            raw = movie_payload()
            db.add(MetadataSnapshot(provider='tmdb', kind='movie', external_id='1492640', entity_id=work.id,
                payload=raw, fetched_at=datetime.utcnow(), expires_at=datetime.utcnow()+timedelta(days=7)))
            await db.commit()
            entry = await db.scalar(select(TrackedEntry).where(TrackedEntry.media_id == media.id))
            before = (entry.status, entry.manual_score, entry.updated_at)
            self.assertEqual(await fill_screen_credits(db, media, providers=self.providers), 'updated')
            await db.refresh(entry)
            self.assertEqual(before, (entry.status, entry.manual_score, entry.updated_at))
            self.assertEqual(await db.scalar(select(func.count()).select_from(CatalogueCredit).where(
                CatalogueCredit.work_id == work.id, CatalogueCredit.provider == 'tvdb')), 2)
            details = await work_details(db, work)
            self.assertIn('Director', [p['name'] for p in details['people']])
        self.providers.detail.assert_not_awaited()

    async def test_empty_provider_credits_do_not_refetch_on_every_visit(self):
        self.reply(movie_payload(empty=True))
        async with self.Session() as db:
            media = await self.new_movie(db)
            self.assertEqual(await fill_screen_credits(db, media, providers=self.providers), 'updated')
            self.assertEqual(await fill_screen_credits(db, media, providers=self.providers), 'cached')
        self.providers.detail.assert_awaited_once()

    async def test_provider_failure_retains_retry_backoff_and_title(self):
        self.providers.detail.side_effect = RuntimeError('Private request detail')
        async with self.Session() as db:
            media = await self.new_movie(db)
            self.assertEqual(await fill_screen_credits(db, media, providers=self.providers), 'failed')
            self.assertEqual(await fill_screen_credits(db, media, providers=self.providers), 'deferred')
            self.assertEqual(media.title, 'UNABOMBER')
            self.assertIsNone(await screen_work(db, media))
        self.providers.detail.assert_awaited_once()

    async def test_conflicting_cached_identity_does_not_replace_existing_bridge(self):
        async with self.Session() as db:
            media = await db.get(Media, self.media_id)
            media.tmdb_id = 1492640
            original = await screen_work(db, media)
            other = CatalogueEntity(kind='movie', name='Different movie', attributes={})
            db.add(other)
            await db.flush()
            db.add(CatalogueIdentity(entity_id=other.id, namespace='tmdb.movie', external_id='1492640', source='tmdb'))
            db.add(MetadataSnapshot(provider='tmdb', kind='movie', external_id='1492640', entity_id=other.id,
                payload=movie_payload(), fetched_at=datetime.utcnow(), expires_at=datetime.utcnow()+timedelta(days=7)))
            await db.commit()
            original_id, other_id = original.id, other.id
            self.assertEqual(await fill_screen_credits(db, media, providers=self.providers), 'failed')
            self.assertEqual((await db.get(CatalogueLegacyLink, media.id)).entity_id, original_id)
            self.assertEqual(await db.scalar(select(func.count()).select_from(CatalogueCredit).where(
                CatalogueCredit.work_id == other_id)), 0)

    async def test_first_render_is_cached_and_signed_in_background_repairs_missing_credits(self):
        self.reply(movie_payload())
        async with self.Session() as db:
            media = await self.new_movie(db)
            media_id = media.id
            self.viewer = await db.get(User, self.user_id)
        with patch('core.media_credits.CatalogueProviders', return_value=self.providers), patch(
            'core.screen_character_art.fill_character_art',AsyncMock(return_value={'state':'ready','pending':False})):
            self.viewer = None
            self.assertEqual((await self.client.get(f'/tracking/title/{media_id}')).json()['people'], [])
            self.providers.detail.assert_not_awaited()
            async with self.Session() as db:
                self.viewer = await db.get(User, self.user_id)
            data = (await self.client.get(f'/tracking/title/{media_id}')).json()
            self.assertEqual(data['people'], [])
            self.providers.detail.assert_not_awaited()
            self.app.dependency_overrides[get_current_user] = lambda: self.viewer
            data = (await self.client.get(f'/tracking/title/{media_id}/people')).json()
            self.assertEqual(len(data['people']), 2)
        self.providers.detail.assert_awaited_once()

    async def test_import_returns_new_title_before_optional_credits_and_background_fills_them(self):
        self.reply(movie_payload())
        async with self.Session() as db:
            self.viewer = await db.get(User, self.user_id)
        self.app.dependency_overrides[get_current_user] = lambda: self.viewer

        async def enrich(media, **kwargs):
            media.title = 'UNABOMBER'

        with (patch('core.settings_store.get_user_tmdb_key', AsyncMock(return_value='fixture')),
              patch('core.enrichment.enrich_media', AsyncMock(side_effect=enrich)),
              patch('core.media_credits.CatalogueProviders', return_value=self.providers)):
            response = await self.client.post('/tracking/catalog/movie/1492640')
            self.assertEqual(response.status_code, 200)
            self.providers.detail.assert_not_awaited()
            extra = await self.client.get(f'/tracking/title/{response.json()["id"]}/people')
            self.assertEqual(extra.status_code,200,extra.text)
        async with self.Session() as db:
            media = await db.get(Media, response.json()['id'])
            self.assertEqual(len((await work_details(db, await screen_work(db, media)))['people']), 2)
        self.providers.detail.assert_awaited_once()

    async def test_complete_identity_match_restores_missing_bridge_without_fetching(self):
        from core.catalogue import ingest_document
        from core.media_credits import VERSION
        async with self.Session() as db:
            media = await self.new_movie(db)
            work = await ingest_document(db, normalize_tmdb(movie_payload(), 'movie'), 'tmdb')
            db.add(MetadataSnapshot(provider='tmdb',kind='movie',external_id=str(media.tmdb_id),entity_id=work.id,
                payload=movie_payload(),coverage={'detail_credits_version':VERSION},
                fetched_at=datetime.utcnow(),expires_at=datetime.utcnow()+timedelta(days=7)))
            await db.commit()
            self.assertIsNone(await db.get(CatalogueLegacyLink, media.id))
            self.assertEqual(await fill_screen_credits(db, media, providers=self.providers), 'updated')
            self.assertEqual((await db.get(CatalogueLegacyLink, media.id)).entity_id, work.id)
        self.providers.detail.assert_not_awaited()
