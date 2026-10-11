"""Real catalogue detail projections and route authorization in disposable schemas."""
import unittest

from sqlalchemy import select

import test_statistics_pg as fixture
from dependencies import get_current_user
from core.media_details import work_details
from models import Follow, Media, TrackedEntry, User, UserProfileData
from models.base import PrivacyLevel
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
