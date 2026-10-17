"""Verified role-image matching, bounded resumable lookups and cache-only first render."""
import unittest
from datetime import datetime,timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch

from sqlalchemy import select,func
import test_statistics_pg as fixture
from core import catalogue
from core.screen_character_art import fill_character_art,KIND
from core.media_details import screen_work,work_details
from core.catalogue_providers import ProviderError
from models import Media,WatchEvent,User
from models.catalogue import CatalogueCredit,CatalogueEntity,MetadataSnapshot


@unittest.skipUnless(fixture.URL,'Requires disposable statistics PostgreSQL')
class CharacterArtTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixture.StatisticsDatabaseTests.asyncSetUp(self)

    async def asyncTearDown(self):
        await fixture.StatisticsDatabaseTests.asyncTearDown(self)

    async def seed(self,db):
        media=await db.get(Media,self.media_id);media.tmdb_id=42;media.imdb_id='tt12345'
        work=await screen_work(db,media)
        credit=await db.scalar(select(CatalogueCredit).where(CatalogueCredit.work_id==work.id,CatalogueCredit.role=='actor'))
        credit.character_image_url=None
        await catalogue.bind_identity(db,work.id,'imdb.title','tt12345','tmdb')
        await catalogue.bind_identity(db,credit.contributor_id,'tmdb.person','100','tmdb')
        other=CatalogueEntity(kind='person',name='Other voice actor',attributes={});db.add(other);await db.flush()
        await catalogue.bind_identity(db,other.id,'tmdb.person','200','tmdb')
        db.add(CatalogueCredit(work_id=work.id,contributor_id=other.id,provider='tmdb',source_key='second',role='actor',character_label='Second character'))
        await db.commit();return media,work

    def replies(self):
        def reply(providers,path):
            if path.startswith('/search/'):
                return [{'movie':{'id':377}}]
            if path.startswith('/movies/'):
                return {'id':377,'name':'Fixture movie','remoteIds':[{'sourceName':'IMDB','id':'tt12345'}],
                        'characters':[{'id':1,'peopleId':10,'personName':'Different provider spelling','name':'Fixture Character','type':3,'image':'https://example.test/first.jpg','sort':0},
                                      {'id':2,'peopleId':20,'personName':'Different spelling again','name':'Second character','type':3,'image':'https://example.test/second.jpg','sort':1}]}
            pid=int(path.split('/')[2])
            return {'id':pid,'remoteIds':[{'sourceName':'TheMovieDB.com','id':str(pid*10)}]}
        return reply

    async def test_cache_only_detail_does_not_wait_for_optional_provider_calls(self):
        async with self.Session() as db:self.viewer=await db.get(User,self.user_id)
        with patch('core.external_scores.refresh_external_scores',AsyncMock(side_effect=AssertionError('Must not fetch'))) as scores, patch(
            'core.media_credits.fill_screen_credits',AsyncMock(side_effect=AssertionError('Must not fetch'))) as credits:
            response=await self.client.get(f'/tracking/title/{self.media_id}')
        self.assertEqual(response.status_code,200,response.text)
        self.assertTrue(response.json()['people'])
        scores.assert_not_awaited();credits.assert_not_awaited()

    async def test_verified_imdb_lookup_resumes_people_batches_and_preserves_history(self):
        async with self.Session() as db:
            media,work=await self.seed(db)
            people_before=await db.scalar(select(func.count()).select_from(CatalogueEntity).where(CatalogueEntity.kind=='person'))
            adapter=SimpleNamespace(tvdb_key='fixture')
            with patch('core.screen_character_art.request_tvdb',AsyncMock(side_effect=self.replies())) as fetch:
                first=await fill_character_art(db,media,providers=adapter,person_limit=1)
                self.assertEqual(first['state'],'partial');self.assertEqual(first['images'],1)
                self.assertEqual(fetch.await_count,3)
                deferred=await fill_character_art(db,media,providers=adapter,person_limit=1)
                self.assertEqual(deferred['state'],'deferred');self.assertEqual(fetch.await_count,3)
                row=await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.kind==KIND))
                row.next_attempt_at=datetime.utcnow()-timedelta(seconds=1);await db.commit()
                second=await fill_character_art(db,media,providers=adapter,person_limit=1)
                self.assertEqual(second['state'],'ready');self.assertEqual(second['images'],2)
                self.assertEqual(fetch.await_count,4)
            details=await work_details(db,work)
            self.assertEqual({p['character_image'] for p in details['people']},{'https://example.test/first.jpg','https://example.test/second.jpg'})
            self.assertEqual(await db.scalar(select(func.count()).select_from(CatalogueEntity).where(CatalogueEntity.kind=='person')),people_before)
            self.assertEqual(await db.scalar(select(func.count()).select_from(WatchEvent)),1)

    async def test_rate_limit_is_visible_durable_and_retried_after_cooldown(self):
        async with self.Session() as db:
            media,_=await self.seed(db);adapter=SimpleNamespace(tvdb_key='fixture')
            with patch('core.screen_character_art.request_tvdb',AsyncMock(side_effect=ProviderError('rate_limited',3600))) as fetch:
                result=await fill_character_art(db,media,providers=adapter)
                self.assertEqual(result['error'],'rate_limited');self.assertGreater(result['retry_after'],3500)
                await fill_character_art(db,media,providers=adapter)
                fetch.assert_awaited_once()
            row=await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.kind==KIND))
            row.next_attempt_at=datetime.utcnow()-timedelta(seconds=1);await db.commit()
            with patch('core.screen_character_art.request_tvdb',AsyncMock(side_effect=self.replies())):
                self.assertEqual((await fill_character_art(db,media,providers=adapter))['state'],'ready')

    async def check_existing_credit_identity(self, *, matching):
        async with self.Session() as db:
            media,work=await self.seed(db)
            actor=await catalogue.resolve_identity(db,'tmdb.person','100')
            other=await catalogue.resolve_identity(db,'tmdb.person','200')
            alias=CatalogueEntity(kind='person',name='Old TVDB actor',attributes={},
                canonical_person_id=actor.entity_id if matching else other.entity_id)
            db.add(alias);await db.flush()
            await catalogue.bind_identity(db,actor.entity_id,'tvdb.person','10','tvdb')
            old=CatalogueCredit(work_id=work.id,contributor_id=alias.id,provider='tvdb',
                source_key='1',role='actor',character_label='Fixture Character',
                character_image_url='https://example.test/cached.jpg')
            db.add(old);await db.commit()
            alias_id,actor_id=alias.id,actor.entity_id
            with patch('core.screen_character_art.request_tvdb',AsyncMock(side_effect=self.replies())):
                result=await fill_character_art(db,media,providers=SimpleNamespace(tvdb_key='fixture'))
            await db.refresh(old)
            if matching:
                self.assertEqual(result['state'],'ready')
                self.assertIsNone(result['error'])
                self.assertEqual(old.contributor_id,actor_id)
                self.assertEqual(old.character_image_url,'https://example.test/first.jpg')
            else:
                self.assertEqual(result['error'],'identity_conflict')
                self.assertEqual(old.contributor_id,alias_id)
                self.assertEqual(old.character_image_url,'https://example.test/cached.jpg')

    async def test_existing_credit_accepts_a_verified_canonical_actor_alias(self):
        await self.check_existing_credit_identity(matching=True)

    async def test_existing_credit_rejects_a_different_canonical_actor(self):
        await self.check_existing_credit_identity(matching=False)

    async def test_wrong_title_identity_never_binds_artwork(self):
        async with self.Session() as db:
            media,work=await self.seed(db)
            reply=self.replies()
            def wrong(providers,path):
                value=reply(providers,path)
                if path.startswith('/movies/'):value['remoteIds'][0]['id']='tt99999'
                return value
            with patch('core.screen_character_art.request_tvdb',AsyncMock(side_effect=wrong)):
                result=await fill_character_art(db,media,providers=SimpleNamespace(tvdb_key='fixture'))
            self.assertEqual(result['error'],'identity_conflict')
            self.assertFalse(any(p['character_image'] for p in (await work_details(db,work))['people']))
            row=await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.kind==KIND))
            row.next_attempt_at=datetime.utcnow()-timedelta(seconds=1);await db.commit()
            with patch('core.screen_character_art.request_tvdb',AsyncMock(side_effect=self.replies())):
                self.assertEqual((await fill_character_art(db,media,providers=SimpleNamespace(tvdb_key='fixture')))['state'],'ready')

    async def test_imdb_person_bridge_upgrades_cached_payload_without_name_matching(self):
        async with self.Session() as db:
            media,work=await self.seed(db)
            raw=self.replies()(None,'/movies/377/extended')
            db.add(MetadataSnapshot(provider='tvdb',kind=KIND,external_id=str(media.id),entity_id=work.id,
                payload={'native':377,'title':raw,'people':{
                    '10':{'id':10,'remoteIds':[{'sourceName':'IMDB','id':'nm100'}]},
                    '20':{'id':20,'remoteIds':[{'sourceName':'IMDB','id':'nm200'}]}}},
                coverage={'version':1},expires_at=datetime.utcnow()+timedelta(days=7)))
            await db.commit()
            adapter=SimpleNamespace(tvdb_key='fixture',tmdb_key='fixture')
            with patch('core.screen_character_art.request_tvdb',AsyncMock()) as tvdb, patch(
                'core.screen_character_art.resolve_imdb_person',AsyncMock(side_effect=[100,200])) as bridge:
                first=await fill_character_art(db,media,providers=adapter,person_limit=1)
                self.assertEqual(first['state'],'partial');self.assertEqual(first['images'],1)
                row=await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.kind==KIND))
                row.next_attempt_at=datetime.utcnow()-timedelta(seconds=1);await db.commit()
                second=await fill_character_art(db,media,providers=adapter,person_limit=1)
                self.assertEqual(second['state'],'ready');self.assertEqual(second['images'],2)
                self.assertEqual(bridge.await_count,2);tvdb.assert_not_awaited()
            self.assertEqual(len((await work_details(db,work))['people']),2)

    async def test_score_quota_keeps_cache_and_blocks_repeat_calls_until_due(self):
        from core import external_scores, mdblist
        async with self.Session() as db:
            media,_=await self.seed(db)
            media.imdb_rating=8.1;await db.commit()
            with patch.object(external_scores,'effective_mdblist_key',AsyncMock(return_value='fixture')), patch.object(
                mdblist,'get_catalog_rating',AsyncMock(side_effect=mdblist.MDBListDailyLimitError('fixture daily limit'))) as fetch:
                first=await external_scores.refresh_score_status(db,media,self.user_id)
                self.assertEqual(first['error'],'daily_budget')
                self.assertGreater(first['retry_after'],0)
                await external_scores.refresh_score_status(db,media,self.user_id)
                fetch.assert_awaited_once()
                self.assertEqual(media.imdb_rating,8.1)
            row=await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.kind=='scores'))
            row.next_attempt_at=datetime.utcnow()-timedelta(seconds=1);await db.commit()
            quota=await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.kind=='score_quota'))
            quota.next_attempt_at=datetime.utcnow()-timedelta(seconds=1);await db.commit()
            with patch.object(external_scores,'effective_mdblist_key',AsyncMock(return_value='fixture')), patch.object(
                mdblist,'get_catalog_rating',AsyncMock(return_value=9)):
                self.assertEqual((await external_scores.refresh_score_status(db,media,self.user_id))['state'],'ready')
            self.assertEqual(media.imdb_rating,9)
