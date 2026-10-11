"""Real catalogue detail projections and route authorization in disposable schemas."""
import importlib
import unittest
from unittest.mock import AsyncMock, patch

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select, text

import test_statistics_pg as fixture
from dependencies import get_current_user
from core.media_details import work_details
from models import Follow, Media, Show, TrackedEntry, User, UserProfileData, WatchEvent
from models.base import MediaType, PrivacyLevel
from models.catalogue import (
    BookEdition, CatalogueCredit, CatalogueEntity, CatalogueIdentity,
    CatalogueRelationship, GameRelease, MetadataSnapshot, SteamPriceSnapshot,
)
from routers.catalogue import router


@unittest.skipUnless(fixture.URL, 'Requires explicit disposable statistics PostgreSQL')
class MediaDetailsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixture.StatisticsDatabaseTests.asyncSetUp(self)
        self.app.include_router(router)

    async def asyncTearDown(self):
        await fixture.StatisticsDatabaseTests.asyncTearDown(self)

    async def login(self):
        async with self.Session() as db:
            self.viewer = await db.get(User, self.user_id)
        self.app.dependency_overrides[get_current_user] = lambda: self.viewer

    async def test_image_cache_is_separate_non_destructive_and_reused(self):
        self.assertEqual((await self.client.get(f'/tracking/title/{self.media_id}/images')).status_code, 401)
        await self.login()
        async with self.Session() as db:
            media = await db.get(Media, self.media_id)
            media.tmdb_id = 99
            media.tmdb_data = {'protected_fixture': 'keep'}
            await db.commit()
        raw = {'id':99, 'posters':[{'file_path':'/poster.jpg','aspect_ratio':.667}],
               'backdrops':[{'file_path':'/scene.jpg','aspect_ratio':1.778}]}
        with (patch('core.settings_store.get_user_tmdb_key', AsyncMock(return_value='fixture')),
              patch('core.tmdb.get_title_images', AsyncMock(return_value=raw)) as fetch):
            data = (await self.client.get(f'/tracking/title/{self.media_id}/images')).json()
            self.assertIn('https://image.tmdb.org/t/p/original/poster.jpg', data['posters'])
            self.assertEqual(data['artwork'], ['https://image.tmdb.org/t/p/original/scene.jpg'])
            await self.client.get(f'/tracking/title/{self.media_id}/images')
            fetch.assert_awaited_once()
        async with self.Session() as db:
            media = await db.get(Media, self.media_id)
            self.assertEqual(media.tmdb_data['protected_fixture'], 'keep')
        projected = (await self.client.get(f'/tracking/title/{self.media_id}')).json()
        self.assertEqual(projected['artwork'], data['artwork'])

    async def test_image_provider_failure_preserves_cache_and_cools_down(self):
        await self.login()
        async with self.Session() as db:
            media = await db.get(Media, self.media_id)
            media.tmdb_id = 99
            media.tmdb_data = {'_anylist_images': {'posters':['https://example.test/old.jpg']}}
            await db.commit()
        with (patch('core.settings_store.get_user_tmdb_key', AsyncMock(return_value='fixture')),
              patch('core.tmdb.get_title_images', AsyncMock(side_effect=RuntimeError('Unavailable'))) as fetch):
            for _ in range(2):
                response = await self.client.get(f'/tracking/title/{self.media_id}/images')
                self.assertEqual(response.status_code, 200)
                self.assertIn('https://example.test/old.jpg', response.json()['posters'])
            fetch.assert_awaited_once()

    async def test_episode_rows_use_canonical_catalogue_and_private_watch_history(self):
        await self.login()
        async with self.Session() as db:
            show=Show(title='Episode fixture',tmdb_id=989)
            db.add(show);await db.flush()
            media=Media(title='Series fixture',media_type=MediaType.series,tmdb_id=989,tmdb_data={
                'seasons':[{'season_number':1,'episode_count':2},{'season_number':2,'episode_count':1}],
                'tracking_episode_ids':[9001,9002,9003],'tracking_catalogue_refreshed_at':'2026-10-11T00:00:00Z'})
            episodes=[Media(title=name,media_type=MediaType.episode,show_id=show.id,tmdb_id=native,
                season_number=number,episode_number=position,release_date=date,runtime=45,poster_path='/still.jpg')
                for native,number,position,name,date in [(9001,1,1,'Watched', '2020-01-01'),(9002,1,2,'Not watched','2020-01-02'),(9003,2,1,'Future','2099-01-01'),(9999,1,3,'Stale identity','2020-01-03')]]
            db.add_all([media,*episodes]);await db.flush()
            other=User(username='other-viewer',email='other-viewer@example.test',api_key='other-fixture')
            db.add(other);await db.flush()
            db.add_all([WatchEvent(user_id=self.user_id,media_id=episodes[0].id,completed=True),
                WatchEvent(user_id=self.user_id,media_id=episodes[0].id,completed=True),
                WatchEvent(user_id=other.id,media_id=episodes[1].id,completed=True)])
            await db.commit();media_id=media.id
        data=(await self.client.get(f'/tracking/title/{media_id}')).json()
        self.assertEqual(data['seasons'][0]['released_count'],2)
        self.assertEqual(data['seasons'][0]['watched_count'],1)
        self.assertEqual([episode['watched'] for episode in data['seasons'][0]['episodes']],[True,False])
        self.assertEqual(data['seasons'][0]['episodes'][0]['image'],'/still.jpg')
        self.assertFalse(data['seasons'][1]['episodes'][0]['released'])
        self.viewer=None
        public=(await self.client.get(f'/tracking/title/{media_id}')).json()
        self.assertFalse(any(episode['watched'] for season in public['seasons'] for episode in season['episodes']))

    async def test_individual_episode_edits_preserve_gaps_and_later_history(self):
        await self.login()
        async with self.Session() as db:
            show=Show(title='Exact episode fixture',tmdb_id=987)
            db.add(show);await db.flush()
            series=Media(title=show.title,media_type=MediaType.series,tmdb_id=987,tmdb_data={
                'seasons':[{'season_number':1,'episode_count':4}],
                'tracking_episode_ids':[8001,8002,8003,8004],'tracking_catalogue_refreshed_at':'2026-10-11T00:00:00Z'})
            episodes=[Media(title=f'Episode {number}',media_type=MediaType.episode,show_id=show.id,
                tmdb_id=8000+number,season_number=1,episode_number=number,release_date='2099-01-01' if number==4 else '2020-01-01') for number in range(1,5)]
            db.add_all([series,*episodes]);await db.commit();series_id=series.id;ids=[episode.id for episode in episodes]
        with patch('core.local_outbound.dispatch_local_tracking_delta',new=AsyncMock()):
            url=f'/tracking/entry/{series_id}/episode/'
            response=await self.client.patch(url+str(ids[2]),json={'watched':True})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.json()['status'],'watching')
            data=(await self.client.get(f'/tracking/title/{series_id}')).json()
            self.assertEqual([row['watched'] for row in data['seasons'][0]['episodes']],[False,False,True,False])
            await self.client.patch(url+str(ids[2]),json={'watched':True})
            async with self.Session() as db:
                self.assertEqual(len(list(await db.scalars(select(WatchEvent).where(WatchEvent.user_id==self.user_id,WatchEvent.media_id==ids[2])))),1)
            for identifier in ids[:2]:
                response=await self.client.patch(url+str(identifier),json={'watched':True})
            self.assertEqual(response.json()['status'],'completed')
            response=await self.client.patch(url+str(ids[0]),json={'watched':False})
            self.assertEqual(response.json()['status'],'watching')
            data=(await self.client.get(f'/tracking/title/{series_id}')).json()
            self.assertEqual(data['seasons'][0]['watched_count'],2)
            self.assertEqual([row['watched'] for row in data['seasons'][0]['episodes']],[False,True,True,False])
            self.assertEqual((await self.client.patch(url+str(ids[3]),json={'watched':True})).status_code,409)
            self.assertEqual((await self.client.patch(url+str(self.media_id),json={'watched':True})).status_code,409)

    async def test_relation_migration_preserves_existing_edges_and_refuses_lossy_downgrade(self):
        def migrate(conn):
            migration = importlib.import_module('migrations.versions.mt043_media_relations')
            with Operations.context(MigrationContext.configure(conn)):
                migration.downgrade()
                source = conn.scalar(text("SELECT id FROM catalogue_entities WHERE kind='movie' LIMIT 1"))
                target = conn.scalar(text("INSERT INTO catalogue_entities(kind,name,attributes) VALUES ('book','Migration source','{}') RETURNING id"))
                params = {'source':source, 'target':target}
                conn.execute(text("INSERT INTO catalogue_relationships(source_id,target_id,relation,provider,attributes) VALUES (:source,:target,'recommendation','tmdb','{}')"), params)
                migration.upgrade()
                conn.execute(text("INSERT INTO catalogue_relationships(source_id,target_id,relation,provider,attributes) VALUES (:source,:target,'based_on','tmdb','{}')"), params)
                with self.assertRaisesRegex(RuntimeError, 'existing data was preserved'):
                    migration.downgrade()
                self.assertEqual(conn.scalar(text('SELECT count(*) FROM catalogue_relationships')), 2)
                conn.execute(text("DELETE FROM catalogue_relationships WHERE relation='based_on'"))
                migration.downgrade()
                self.assertEqual(conn.scalar(text('SELECT count(*) FROM catalogue_relationships')), 1)
                definition = conn.scalar(text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='catalogue_relationships'::regclass AND conname='ck_catalogue_relationship_type'"))
                self.assertNotIn('based_on', definition)
        async with self.engine.begin() as conn:
            await conn.run_sync(migrate)

    async def test_screen_credits_deduplicate_providers_and_retain_multiple_characters(self):
        async with self.Session() as db:
            work = await db.scalar(select(CatalogueEntity).where(CatalogueEntity.kind == 'movie'))
            actor = await db.scalar(select(CatalogueEntity).where(CatalogueEntity.kind == 'person'))
            db.add(CatalogueCredit(work_id=work.id, contributor_id=actor.id, provider='tmdb',
                source_key='extra-role', role='actor', character_label='Second Character'))
            await db.commit()
            details = await work_details(db, work)
            self.assertEqual(len(details['people']), 1)
            self.assertEqual(set(details['people'][0]['characters']), {'Fixture Character', 'Second Character'})
            self.assertEqual(details['people'][0]['url'], f'/actors/catalogue:{actor.id}')
            # The fixture contains two provider credits for this studio.
            self.assertEqual([p['name'] for p in details['organizations']], ['Fixture Studio', 'Fixture Network'])
            self.assertTrue(details['organizations'][0]['url'].startswith('/studio/'))
            self.assertIsNone(details['organizations'][1]['url'])
        response = await self.client.get(f'/tracking/title/{self.media_id}')
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()['entry'])
        self.assertEqual(len(response.json()['people']), 1)

    async def test_game_details_use_stored_releases_relations_prices_and_safe_artwork(self):
        async with self.Session() as db:
            game = CatalogueEntity(kind='game', name='Fixture game', attributes={
                'release_date':'2020-01-01', 'artwork':['javascript:alert(1)', 'https://example.test/game.jpg']})
            other = CatalogueEntity(kind='game', name='Expansion', attributes={})
            release = CatalogueEntity(kind='release', name='PC edition', attributes={})
            db.add_all([game, other, release]); await db.flush()
            db.add(CatalogueIdentity(entity_id=game.id, namespace='steam.app', external_id='123', source='igdb'))
            db.add(GameRelease(entity_id=release.id, work_id=game.id, platform_namespace='igdb.platform',
                platform_id='6', platform_name='PC', region='Worldwide', release_date='2020-01-01'))
            db.add(CatalogueRelationship(source_id=other.id, target_id=game.id, relation='expansion', provider='igdb'))
            db.add(SteamPriceSnapshot(work_id=game.id, steam_app_id='123', country='DE',
                current={'currency':'EUR','amount':7.99,'amountInt':799}, url='https://store.steampowered.com/app/123/', payload={}))
            db.add(MetadataSnapshot(provider='igdb', kind='game', external_id='99', entity_id=game.id,
                payload={'url':'javascript:alert(1)'}, coverage={}))
            await db.commit(); game_id, other_id = game.id, other.id
        self.assertEqual((await self.client.get(f'/catalogue/entities/{game_id}/details')).status_code, 401)
        await self.login()
        response = await self.client.get(f'/catalogue/entities/{game_id}/details')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['artwork'], ['https://example.test/game.jpg'])
        self.assertEqual(data['releases'][0]['platform'], 'PC')
        self.assertEqual(data['relations'][0]['url'], f'/games/{other_id}')
        self.assertTrue(data['relations'][0]['reverse'])
        self.assertEqual(data['prices'][0]['country'], 'DE')
        self.assertEqual(data['sources'], [{'name':'Steam','url':'https://store.steampowered.com/app/123/'}])
        self.assertFalse(data['tracking_available'])

    async def test_following_ratings_respect_privacy_and_country_falls_back_to_screen_metadata(self):
        async with self.Session() as db:
            media = await db.get(Media, self.media_id)
            media.tmdb_data = {'origin_country':['US'], 'production_countries':[{'iso_3166_1':'CA'}]}
            for username, privacy in [('public-friend', PrivacyLevel.public), ('private-friend', PrivacyLevel.private)]:
                friend = User(username=username, email=username+'@example.test', api_key=username+'-fixture-only')
                db.add(friend); await db.flush()
                db.add_all([UserProfileData(user_id=friend.id, privacy_level=privacy,
                    display_name='Display '+username, avatar_path='fixture-avatar.png'),
                    Follow(follower_id=self.user_id, following_id=friend.id),
                    TrackedEntry(user_id=friend.id, media_id=self.media_id, status='completed', manual_score=9)])
            await db.commit()
        await self.login()
        data = (await self.client.get(f'/tracking/title/{self.media_id}')).json()
        self.assertEqual(data['countries'], ['US'])
        self.assertEqual([row['username'] for row in data['friends']], ['public-friend'])
        self.assertEqual(data['friends'][0]['display_name'], 'Display public-friend')
        self.assertTrue(data['friends'][0]['has_avatar'])
        self.assertNotIn('avatar_path', data['friends'][0])
        self.assertEqual(data['friends_average'], 9)

    async def test_book_details_preserve_edition_fields_and_reject_non_work_entities(self):
        async with self.Session() as db:
            book = CatalogueEntity(kind='book', name='Fixture book', attributes={'release_date':'1965','pages':896})
            edition = CatalogueEntity(kind='edition', name='Paperback', image_url='https://example.test/book.jpg', attributes={})
            db.add_all([book, edition]); await db.flush()
            db.add(CatalogueIdentity(entity_id=book.id, namespace='openlibrary.book', external_id='OL123W', source='openlibrary'))
            db.add(BookEdition(entity_id=edition.id, work_id=book.id, pages=896, format='Paperback',
                language='English', release_date='2020-04-01'))
            await db.commit(); book_id, edition_id = book.id, edition.id
        await self.login()
        response = await self.client.get(f'/catalogue/entities/{book_id}/details')
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['year'], '1965')
        self.assertEqual(data['editions'][0]['pages'], 896)
        self.assertEqual(data['sources'][0]['url'], 'https://openlibrary.org/works/OL123W')
        self.assertEqual((await self.client.get(f'/catalogue/entities/{edition_id}/details')).status_code, 404)
        self.assertEqual((await self.client.get('/catalogue/entities/0/details')).status_code, 422)

    async def test_relations_cross_types_while_recommendations_stay_in_the_current_type(self):
        async with self.Session() as db:
            work = await db.scalar(select(CatalogueEntity).where(CatalogueEntity.kind == 'movie'))
            book = CatalogueEntity(kind='book', name='Original novel', attributes={})
            movie = CatalogueEntity(kind='movie', name='Similar movie', attributes={})
            series = CatalogueEntity(kind='series', name='Related series', attributes={})
            game = CatalogueEntity(kind='game', name='A game', attributes={})
            other_game = CatalogueEntity(kind='game', name='Similar game', attributes={})
            db.add_all([book,movie,series,game,other_game]); await db.flush()
            db.add_all([CatalogueIdentity(entity_id=movie.id, namespace='tmdb.movie', external_id='55', source='tmdb'),
                CatalogueIdentity(entity_id=series.id, namespace='tmdb.series', external_id='66', source='tmdb'),
                CatalogueRelationship(source_id=work.id,target_id=book.id,relation='based_on',provider='manual'),
                CatalogueRelationship(source_id=work.id,target_id=movie.id,relation='recommendation',provider='tmdb'),
                CatalogueRelationship(source_id=work.id,target_id=series.id,relation='spin_off',provider='manual'),
                CatalogueRelationship(source_id=game.id,target_id=other_game.id,relation='recommendation',provider='igdb')])
            await db.commit()
            details = await work_details(db,work)
            self.assertEqual({row['type'] for row in details['relations']}, {'book','series'})
            self.assertEqual([row['type'] for row in details['similar']], ['movie'])
            self.assertEqual(details['similar'][0]['url'], '/discover-title?type=movie&id=55')
            self.assertEqual((await work_details(db,game))['similar'][0]['url'], f'/games/{other_game.id}')
            self.assertTrue((await work_details(db,book))['relations'][0]['reverse'])

    async def test_book_series_members_and_author_suggestions_are_separate(self):
        async with self.Session() as db:
            books = [CatalogueEntity(kind='book',name=name,attributes={}) for name in ('First','Second','Other story')]
            series = CatalogueEntity(kind='book_series',name='Fixture trilogy',attributes={})
            author = CatalogueEntity(kind='person',name='Author',attributes={})
            db.add_all([*books,series,author]); await db.flush()
            for book in books:
                db.add(CatalogueCredit(work_id=book.id,contributor_id=author.id,provider='hardcover',source_key=f'author:{book.id}',role='author'))
            for book in books[:2]:
                db.add(CatalogueRelationship(source_id=book.id,target_id=series.id,relation='series_member',provider='hardcover'))
            await db.commit()
            details = await work_details(db,books[0])
            self.assertEqual([row['title'] for row in details['relations']], ['Second'])
            self.assertEqual(details['relations'][0]['label'], 'Fixture trilogy')
            self.assertEqual([row['title'] for row in details['similar']], ['Other story'])

    async def test_collection_cache_is_non_destructive_and_recommendations_exclude_collection_and_other_types(self):
        await self.login()
        async with self.Session() as db:
            media = await db.get(Media,self.media_id)
            media.tmdb_id = 99
            media.tmdb_data = {'belongs_to_collection':{'id':10}, 'protected_fixture':'keep'}
            await db.commit()
        collection = {'id':10,'name':'Fixture collection','parts':[{'id':99,'title':'Current','release_date':'2020-01-01'}, {'id':55,'title':'Earlier','release_date':'2010-01-01'}]}
        recommendations = {'results':[{'id':55,'title':'Earlier'}, {'id':77,'title':'Another movie','poster_path':'/cover.jpg'}, {'id':66,'name':'A series','media_type':'tv'}, {'id':99,'title':'Current'}, {'id':88,'title':'Adult','adult':True}]}
        with (patch('core.settings_store.get_user_tmdb_key',new=AsyncMock(return_value='fixture-only')),
                patch('core.tmdb.get_collection',new=AsyncMock(return_value=collection)) as fetch_collection,
                patch('core.tmdb.get_movie_recommendations',new=AsyncMock(return_value=recommendations)) as fetch_recommendations):
            response = await self.client.get(f'/tracking/title/{self.media_id}/related')
            self.assertEqual(response.status_code,200,response.text)
            data = response.json()
            self.assertEqual([row['title'] for row in data['relations']], ['Earlier'])
            self.assertEqual(data['relations'][0]['label'], 'Previous entry')
            self.assertEqual([row['title'] for row in data['similar']], ['Another movie'])
            self.assertEqual(data['similar'][0]['poster'], 'https://image.tmdb.org/t/p/w500/cover.jpg')
            self.assertFalse(data['related_pending'])
            self.assertEqual((await self.client.get(f'/tracking/title/{self.media_id}/related')).status_code,200)
            fetch_collection.assert_awaited_once(); fetch_recommendations.assert_awaited_once()
        async with self.Session() as db:
            self.assertEqual((await db.get(Media,self.media_id)).tmdb_data['protected_fixture'],'keep')
