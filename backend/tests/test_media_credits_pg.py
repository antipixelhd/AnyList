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
    async def test_cold_movie_and_series_publish_primary_information_without_another_fetch(self):
        async with self.Session() as db:
            self.viewer=await db.get(User,self.user_id)
        self.app.dependency_overrides[get_current_user]=lambda:self.viewer
        for kind,native in [('movie',1492640),('series',1492641)]:
            raw=movie_payload(native)
            raw.update(name='Cold series',overview='Recovered synopsis',release_date='2026-01-01',first_air_date='2026-01-01',
                status='Released' if kind=='movie' else 'Returning Series',original_language='en',runtime=100,
                episode_run_time=[42],number_of_seasons=2,number_of_episodes=12,
                created_by=[{'id':7,'name':'Creator'}],networks=[{'id':8,'name':'Network'}],
                production_companies=[{'id':9,'name':'Studio'}],genres=[{'id':18,'name':'Drama'}],
                production_countries=[{'iso_3166_1':'US','name':'United States'}],budget=1000000,revenue=2000000)
            self.providers.detail.return_value=(normalize_tmdb(raw,kind),raw)
            async with self.Session() as db:
                media=Media(title='Saved title',media_type=MediaType(kind),tmdb_id=native,tmdb_data={})
                db.add(media);await db.commit();identifier=media.id
            with patch('core.media_credits.CatalogueProviders',return_value=self.providers):
                first=(await self.client.get(f'/tracking/title/{identifier}')).json()
                self.assertTrue(first['primary_status']['pending'])
                prior=self.providers.detail.await_count
                late=(await self.client.get(f'/tracking/title/{identifier}/people')).json()
                self.assertEqual(late['primary_status'],{'state':'ready','pending':False})
                primary=late['primary']
                self.assertEqual(primary['id'],identifier)
                self.assertEqual(primary['type'],kind)
                self.assertEqual(primary['overview'],'Recovered synopsis')
                self.assertEqual(primary['countries'],['US'])
                self.assertEqual(primary['genres'],['Drama'])
                self.assertEqual(primary['release_date'],'2026-01-01')
                self.assertEqual(primary['release_status'],raw['status'])
                self.assertTrue(late['organizations'])
                self.assertFalse({'entry','favorite','progress','seasons','poster','artwork'} & primary.keys())
                if kind=='series':
                    self.assertEqual((primary['season_count'],primary['episode_count'],primary['episode_runtime']),(2,12,42))
                    self.assertEqual(primary['networks'][0]['name'],'Network')
                else:self.assertEqual(primary['budget'],1000000)
                warm=(await self.client.get(f'/tracking/title/{identifier}')).json()
                self.assertEqual(warm['primary_status'],{'state':'ready','pending':False})
                self.assertEqual(warm['overview'],'Recovered synopsis')
                await self.client.get(f'/tracking/title/{identifier}/people')
                self.assertEqual(self.providers.detail.await_count,prior+1)
            async with self.Session() as db:
                saved=await db.get(Media,identifier)
                self.assertEqual(saved.title,'Saved title')
                self.assertIsNone(saved.runtime)

    async def test_legacy_bridge_success_does_not_finish_failed_primary_acquisition(self):
        from core.catalogue import ingest_document
        async with self.Session() as db:
            media = await self.new_movie(db)
            await ingest_document(db, normalize_tmdb(movie_payload(), 'movie'), 'tmdb')
            await db.commit()
            identifier = media.id
            self.viewer = await db.get(User, self.user_id)
        self.app.dependency_overrides[get_current_user] = lambda: self.viewer
        self.providers.detail.side_effect = RuntimeError('Provider unavailable')
        with patch('core.media_credits.CatalogueProviders', return_value=self.providers):
            late = (await self.client.get(f'/tracking/title/{identifier}/people')).json()
            self.assertEqual(late['credits_status'], 'updated')
            self.assertTrue(late['primary_status']['pending'])
            self.assertEqual(late['primary_status']['retry_after'], 60)
            retry = (await self.client.get(f'/tracking/title/{identifier}/people')).json()
            self.assertTrue(retry['primary_status']['pending'])
        self.providers.detail.assert_awaited_once()

    async def test_complete_tmdb_title_never_fetches_secondary_provider(self):
        raw = movie_payload()
        raw.update(overview='Complete overview', poster_path='/poster.jpg', release_date='2026-01-01',
                   genres=[{'id': 18, 'name': 'Drama'}], production_companies=[{'id': 3, 'name': 'Studio'}])
        self.reply(raw)
        self.providers.tvdb_key = 'fixture'
        async with self.Session() as db:
            media = await self.new_movie(db)
            media.tvdb_id = 42
            await db.commit()
            self.assertEqual(await fill_screen_credits(db, media, providers=self.providers), 'updated')
            self.assertEqual(await fill_screen_credits(db, media, providers=self.providers), 'cached')
            work = await screen_work(db, media)
            actor = await db.scalar(select(CatalogueCredit.contributor_id).where(
                CatalogueCredit.work_id == work.id, CatalogueCredit.role == 'actor'))
            db.add_all([CatalogueCredit(work_id=work.id, contributor_id=actor, provider=provider,
                                       source_key='old-credit', role='actor') for provider in ('tmdb', 'tvdb')])
            await db.commit()
            # Periodic authoritative refresh still removes obsolete primary
            # credits, while preserving other source data and avoiding downloads.
            await fill_screen_credits(db, media, providers=self.providers, authoritative_primary=True)
            remaining = list(await db.scalars(select(CatalogueCredit.provider).where(
                CatalogueCredit.work_id == work.id, CatalogueCredit.source_key == 'old-credit')))
            self.assertEqual(remaining, ['tvdb'])
        self.assertEqual([call.args for call in self.providers.detail.await_args_list], [('tmdb', 'movie', '1492640')])

    async def test_secondary_fills_only_missing_crew_and_retains_full_payload(self):
        from core.catalogue_normalize import normalize_tvdb
        raw = movie_payload()
        raw['credits']['crew'] = []
        raw.update(overview='Primary overview', poster_path='/poster.jpg', release_date='2026-01-01',
                   genres=[{'id': 18, 'name': 'Drama'}], production_companies=[{'id': 3, 'name': 'Studio'}])
        secondary = {'id': 42, 'name': 'Secondary title', 'overview': 'Secondary overview',
                     'remoteIds': [{'sourceName': 'TheMovieDB.com', 'id': '1492640'}],
                     'characters': [
                         {'id': 1, 'peopleId': 901, 'personName': 'Secondary actor', 'type': 3, 'name': 'Role'},
                         {'id': 2, 'peopleId': 902, 'personName': 'Missing director', 'type': 1, 'peopleType': 'Director'}]}
        self.providers.tvdb_key = 'fixture'
        self.providers.detail.side_effect = [(normalize_tmdb(raw, 'movie'), raw),
                                            (normalize_tvdb(secondary, 'movie'), secondary)]
        async with self.Session() as db:
            media = await self.new_movie(db)
            media.tvdb_id = 42
            await db.commit()
            self.assertEqual(await fill_screen_credits(db, media, providers=self.providers), 'updated')
            work = await screen_work(db, media)
            self.assertEqual(work.description, 'Primary overview')
            credits = list(await db.scalars(select(CatalogueCredit).where(
                CatalogueCredit.work_id == work.id, CatalogueCredit.provider == 'tvdb')))
            self.assertEqual([credit.source_key for credit in credits], ['2'])
            snapshot = await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.provider == 'tvdb'))
            self.assertEqual(len(snapshot.payload['characters']), 2)
            self.assertEqual(snapshot.coverage['detail_credit_groups'], ['crew'])
            await fill_screen_credits(db, media, providers=self.providers)
        self.assertEqual(self.providers.detail.await_count, 2)
        self.assertEqual(self.providers.detail.await_args.kwargs, {'credit_groups': {'crew'}})

    async def test_tvdb_episode_catalogue_remains_primary_with_both_ids(self):
        from core.screen_metadata_policy import screen_sources
        from core.catalogue_normalize import normalize_tvdb
        from models.show import Show
        raw = {'id': 91, 'name': 'TVDB series', 'overview': 'Primary series overview',
               'image': 'https://artworks.thetvdb.com/poster.jpg', 'firstAired': '2026-01-01',
               'genres': [{'id': 18, 'name': 'Drama'}],
               'remoteIds': [{'sourceName': 'TheMovieDB.com', 'id': '90'}],
               'companies': [{'id': 3, 'name': 'Studio', 'companyType': {'companyTypeName': 'Studio'}}],
               'characters': [{'id': 1, 'peopleId': 5, 'personName': 'Actor', 'type': 3},
                              {'id': 2, 'peopleId': 6, 'personName': 'Director', 'type': 1, 'peopleType': 'Director'}]}
        self.providers.tvdb_key = 'fixture'
        self.providers.detail.return_value = (normalize_tvdb(raw, 'series'), raw)
        async with self.Session() as db:
            media = Media(title='TVDB series', media_type=MediaType.series, tmdb_id=90, tvdb_id=91,
                          tmdb_data={'tracking_catalogue_provider': 'tmdb'})
            db.add_all([media, Show(title='TVDB series', tmdb_id=90, tvdb_id=91, canonical_source='tvdb')])
            await db.commit()
            self.assertEqual(await screen_sources(db, media), [('tvdb', 91), ('tmdb', 90)])
            self.assertEqual(await fill_screen_credits(db, media, providers=self.providers), 'updated')
            self.assertEqual((await screen_work(db, media)).description, 'Primary series overview')
            self.assertEqual((await db.scalar(select(Show).where(Show.tvdb_id == 91))).canonical_source, 'tvdb')
        self.providers.detail.assert_awaited_once_with('tvdb', 'series', '91')

    async def test_primary_refresh_lease_does_not_start_secondary_import(self):
        self.providers.tvdb_key = 'fixture'
        async with self.Session() as db:
            media = await self.new_movie(db)
            media.tvdb_id = 42
            db.add(MetadataSnapshot(provider='tmdb', kind='movie', external_id=str(media.tmdb_id),
                                    lease_until=datetime.utcnow() + timedelta(minutes=3)))
            await db.commit()
            self.assertEqual(await fill_screen_credits(db, media, providers=self.providers), 'deferred')
        self.providers.detail.assert_not_awaited()

    async def test_cached_portrait_backfill_is_identity_matched_bounded_and_idempotent(self):
        from scripts.backfill_person_portraits import backfill
        from core import catalogue
        async with self.Session() as db:
            raw=movie_payload()
            raw['credits']['cast'][0]['profile_path']='/portrait.jpg'
            work=await catalogue.ingest_document(db,normalize_tmdb(raw,'movie'),'tmdb')
            actor=await db.scalar(select(CatalogueEntity).join(CatalogueIdentity).where(
                CatalogueIdentity.namespace=='tmdb.person',CatalogueIdentity.external_id=='123'))
            actor.image_url='https://artworks.thetvdb.com/banners/person/portrait.jpg'
            actor.field_sources={**actor.field_sources,'image_url':'tvdb'}
            db.add(MetadataSnapshot(provider='tmdb',kind='movie',external_id=str(raw['id']),entity_id=work.id,payload=raw))
            await db.commit()
            dry=await backfill(db,apply=False)
            self.assertEqual(dry['updated'],1)
            self.assertIn('thetvdb.com',actor.image_url)
            applied=await backfill(db,apply=True)
            self.assertEqual(applied['updated'],1)
            self.assertEqual(actor.image_url,'https://image.tmdb.org/t/p/original/portrait.jpg')
            self.assertEqual((await backfill(db,apply=True))['updated'],0)
            from core.catalogue_additive import fill_document
            actor.image_url='https://artworks.thetvdb.com/banners/person/portrait.jpg'
            actor.field_sources={**actor.field_sources,'image_url':'tvdb'};await db.commit()
            await fill_document(db,normalize_tmdb(raw,'movie'),'tmdb');await db.commit()
            self.assertEqual(actor.image_url,'https://image.tmdb.org/t/p/original/portrait.jpg')
            actor.image_url='https://artworks.thetvdb.com/banners/person/selected.jpg'
            actor.field_sources={**actor.field_sources,'image_url':'tvdb'}
            actor.protected_fields=['image_url'];await db.commit()
            self.assertEqual((await backfill(db,apply=True))['updated'],0)
            self.assertTrue(actor.image_url.endswith('/selected.jpg'))

    async def test_large_crews_do_not_truncate_the_director_from_movie_details(self):
        async with self.Session() as db:
            work=await db.scalar(select(CatalogueEntity).where(CatalogueEntity.kind=='movie'))
            person=await db.scalar(select(CatalogueEntity).where(CatalogueEntity.kind=='person'))
            db.add_all([CatalogueCredit(work_id=work.id,contributor_id=person.id,provider='tmdb',source_key=f'crew-{index}',role='contributor',role_label='Crew') for index in range(550)])
            director=CatalogueEntity(kind='person',name='Christopher Nolan',attributes={})
            db.add(director);await db.flush()
            db.add(CatalogueCredit(work_id=work.id,contributor_id=director.id,provider='tmdb',source_key='late-director',role='director',role_label='Director'))
            await db.commit()
            people=(await work_details(db,work))['people']
            nolan=next(person for person in people if person['name']=='Christopher Nolan')
            self.assertEqual(nolan['roles'],['Director'])

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
            self.assertEqual((await self.client.get(f'/tracking/title/{media_id}')).status_code, 401)
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
