"""Destructive account controls tested only in an explicit disposable schema."""
import os
import unittest
import uuid
from urllib.parse import urlsplit

os.environ.setdefault('SECRET_KEY', 'list-deletion-test')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

import httpx
from fastapi import FastAPI, HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from db import get_db
from dependencies import get_current_user
from models import (
    Base, CollectionSource, Comment, GameAchievement, GameAchievementUnlock,
    GameActivity, GameChildState, GameEntry, GameFavorite, List, ListItem,
    Media, MediaFavorite, MediaServerConnection, MediaType, PlaybackProgress,
    Rating, SteamConnection, SteamPlayerGame, SyncJob, SyncStatus,
    TrackedEntry, TrackingActivity, TrackingPreferences, User, UserProfileData, WatchEvent,
)
from models.catalogue import CatalogueEntity, CatalogueIdentity
from routers.auth import router

URL = os.getenv('LIST_DELETION_TEST_DATABASE_URL') or os.getenv('TRACKING_TEST_DATABASE_URL')


@unittest.skipUnless(URL, 'Requires explicit disposable PostgreSQL')
class ListDeletionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        target = urlsplit(URL)
        if target.hostname not in ('localhost', '127.0.0.1') or target.port not in (5432, 55449) or target.path != '/anylist_test':
            raise RuntimeError('Refusing non-disposable list-deletion database')
        self.schema = 'list_delete_' + uuid.uuid4().hex
        self.admin = create_async_engine(URL)
        async with self.admin.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.engine = create_async_engine(URL, connect_args={'server_settings': {'search_path': self.schema}})
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as db:
            self.owner = User(email='owner@test.local', username='owner', api_key='owner-key')
            self.other = User(email='other@test.local', username='other', api_key='other-key')
            db.add_all([self.owner, self.other]); await db.flush()
            self.movie = Media(title='Movie', media_type=MediaType.movie)
            self.series = Media(title='Series', media_type=MediaType.series)
            self.episode = Media(title='Episode', media_type=MediaType.episode)
            self.game = CatalogueEntity(kind='game', name='Game', attributes={'game_class': 'game'})
            self.child = CatalogueEntity(kind='game', name='DLC', attributes={'game_class': 'dlc'})
            db.add_all([self.movie, self.series, self.episode, self.game, self.child]); await db.flush()
            for user in (self.owner, self.other):
                for media in (self.movie, self.series, self.episode):
                    db.add_all([
                        TrackedEntry(user_id=user.id, media_id=media.id),
                        WatchEvent(user_id=user.id, media_id=media.id, completed=True),
                        Rating(user_id=user.id, media_id=media.id, rating=8),
                        MediaFavorite(user_id=user.id, media_id=media.id),
                        PlaybackProgress(user_id=user.id, media_id=media.id),
                        TrackingActivity(user_id=user.id, media_id=media.id, status='completed'),
                    ])
                db.add_all([
                    GameEntry(user_id=user.id, work_id=self.game.id),
                    GameFavorite(user_id=user.id, work_id=self.game.id),
                    GameActivity(user_id=user.id, work_id=self.game.id, status='completed'),
                    GameChildState(user_id=user.id, work_id=self.child.id, base_work_id=self.game.id),
                    SteamPlayerGame(user_id=user.id, steam_id='123', app_id='42', achievement_count=4),
                    UserProfileData(user_id=user.id, game_banners={str(self.game.id): 'https://example.com/image.jpg'}),
                    TrackingPreferences(user_id=user.id, favorite_order=[self.movie.id, self.series.id]),
                ])
                for kind in ('movie', 'series', 'episode', 'person'):
                    db.add(Comment(user_id=user.id, media_type=kind, tmdb_id=1, content='Private review'))
            listing = List(user_id=self.owner.id, name='Mixed list')
            other_list = List(user_id=self.other.id, name='Other list')
            db.add_all([listing, other_list]); await db.flush()
            db.add_all([ListItem(list_id=listing.id, media_id=self.movie.id),
                        ListItem(list_id=listing.id, media_id=self.series.id),
                        ListItem(list_id=other_list.id, media_id=self.movie.id)])
            connection = MediaServerConnection(user_id=self.owner.id, type='steam', name='Steam', url='https://steamcommunity.com', token='private')
            db.add(connection); await db.flush()
            self.connection_id = connection.id
            db.add(SteamConnection(connection_id=connection.id, user_id=self.owner.id, steam_id='123',
                imported=True, library=[{'app_id': '42'}], selection=['42'], checkpoints={'42': {'work_id': self.game.id}},
                draft={'overrides': {'42': True}}, auto_add_games=True))
            db.add(CatalogueIdentity(entity_id=self.game.id, namespace='steam.app', external_id='42', source='steam'))
            await db.flush()
            achievement = GameAchievement(work_id=self.game.id, app_id='42', api_name='WIN', name='Win')
            db.add(achievement); await db.flush()
            for user in (self.owner, self.other):
                db.add(GameAchievementUnlock(user_id=user.id, achievement_id=achievement.id, work_id=self.game.id))
            await db.commit()
        app = FastAPI(); app.include_router(router, prefix='/auth')
        self.viewer = self.owner

        async def viewer():
            if self.viewer is None:
                raise HTTPException(401)
            return self.viewer

        async def database():
            async with self.Session() as db:
                yield db

        app.dependency_overrides[get_db] = database
        app.dependency_overrides[get_current_user] = viewer
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')

    async def asyncTearDown(self):
        await self.client.aclose(); await self.engine.dispose()
        async with self.admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
        await self.admin.dispose()

    async def erase(self, category):
        return await self.client.delete(f'/auth/me/lists/{category}?confirm=delete')

    async def test_movies_preserve_series_games_other_users_and_shared_metadata(self):
        response = await self.erase('movies')
        self.assertEqual(response.status_code, 200, response.text)
        async with self.Session() as db:
            for model in (TrackedEntry, WatchEvent, Rating, MediaFavorite, PlaybackProgress, TrackingActivity):
                own = list(await db.scalars(select(model.media_id).where(model.user_id == self.owner.id)))
                self.assertEqual(set(own), {self.series.id, self.episode.id}, model.__name__)
                self.assertEqual(await db.scalar(select(func.count()).select_from(model).where(model.user_id == self.other.id)), 3)
            self.assertEqual(await db.scalar(select(func.count()).select_from(Media)), 3)
            self.assertEqual(await db.scalar(select(func.count()).select_from(GameEntry)), 2)
            self.assertEqual(await db.scalar(select(func.count()).select_from(ListItem)), 2)
            self.assertEqual((await db.get(TrackingPreferences, self.owner.id)).favorite_order, [self.series.id])
            self.assertEqual(set(await db.scalars(select(Comment.media_type).where(Comment.user_id == self.owner.id))), {'series', 'episode', 'person'})

    async def test_series_also_erases_episode_history(self):
        response = await self.erase('series')
        self.assertEqual(response.status_code, 200, response.text)
        async with self.Session() as db:
            for model in (TrackedEntry, WatchEvent, Rating, MediaFavorite, PlaybackProgress, TrackingActivity):
                self.assertEqual(list(await db.scalars(select(model.media_id).where(model.user_id == self.owner.id))), [self.movie.id])
            self.assertEqual(await db.scalar(select(func.count()).select_from(GameEntry)), 2)

    async def test_games_erases_private_progress_and_invalidates_import_drafts(self):
        response = await self.erase('games')
        self.assertEqual(response.status_code, 200, response.text)
        async with self.Session() as db:
            for model in (GameEntry, GameChildState, GameActivity, GameFavorite, GameAchievementUnlock, SteamPlayerGame):
                self.assertEqual(await db.scalar(select(func.count()).select_from(model).where(model.user_id == self.owner.id)), 0, model.__name__)
                self.assertEqual(await db.scalar(select(func.count()).select_from(model).where(model.user_id == self.other.id)), 1, model.__name__)
            state = await db.get(SteamConnection, self.connection_id)
            self.assertEqual((state.selection, state.library, state.checkpoints, state.draft, state.known_apps), ([], [], {}, {}, []))
            self.assertEqual(state.generation, 2)
            self.assertFalse(state.imported or state.auto_add_games)
            self.assertEqual(await db.scalar(select(func.count()).select_from(GameAchievement)), 1)
            self.assertEqual(await db.scalar(select(func.count()).select_from(TrackedEntry)), 6)
            profile = await db.scalar(select(UserProfileData).where(UserProfileData.user_id == self.owner.id))
            self.assertEqual(profile.game_banners, {})

    async def test_requires_authentication_confirmation_and_valid_category(self):
        self.assertEqual((await self.client.delete('/auth/me/lists/games')).status_code, 422)
        self.assertEqual((await self.erase('everything')).status_code, 422)
        self.viewer = None
        self.assertEqual((await self.erase('games')).status_code, 401)

    async def test_active_sync_refuses_without_partial_deletion(self):
        async with self.Session() as db:
            db.add(SyncJob(user_id=self.owner.id, source=CollectionSource.steam, status=SyncStatus.running))
            await db.commit()
        self.assertEqual((await self.erase('games')).status_code, 409)
        async with self.Session() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(GameEntry)), 2)
            self.assertTrue((await db.get(SteamConnection, self.connection_id)).imported)

    async def test_repeated_deletion_is_safe(self):
        for category in ('movies', 'series', 'games'):
            for _ in range(2):
                response = await self.erase(category)
                self.assertEqual(response.status_code, 200, response.text)

    async def test_screen_deletion_clears_queues_snapshots_and_collection_files(self):
        from models import (
            CloudBaseline, Collection, CollectionFile, MediaRequest, StreamAction,
            StreamBaseline, StreamingLibraryIntent, SyncReview, TrackingDeletion,
            TrackingDeliveryJob, UserCalendarCache, UserStatsSnapshot, UserStatsState, WatchIntent,
        )
        async with self.Session() as db:
            for media in (self.movie, self.series):
                db.add_all([
                    WatchIntent(user_id=self.owner.id, connection_id=self.connection_id, media_id=media.id, desired_watched=True),
                    StreamAction(user_id=self.owner.id, connection_id=self.connection_id, media_id=media.id, action='watch'),
                    StreamingLibraryIntent(user_id=self.owner.id, media_id=media.id, desired=True),
                    SyncReview(user_id=self.owner.id, media_id=media.id, kind='status', message='Review'),
                    TrackingDeletion(user_id=self.owner.id, media_id=media.id),
                    TrackingDeliveryJob(user_id=self.owner.id, media_id=media.id),
                    MediaRequest(user_id=self.owner.id, media_type=media.media_type.value, tmdb_id=media.id),
                ])
                collection = Collection(user_id=self.owner.id, media_id=media.id)
                db.add(collection); await db.flush()
                db.add(CollectionFile(collection_id=collection.id, source=CollectionSource.manual))
            db.add_all([
                StreamBaseline(user_id=self.owner.id, connection_id=self.connection_id, snapshot={'history': 'private'}),
                CloudBaseline(user_id=self.owner.id, provider='trakt', snapshot={'history': 'private'}),
                UserCalendarCache(user_id=self.owner.id, payload={'history': 'private'}),
            ])
            snapshot = UserStatsSnapshot(user_id=self.owner.id, source_revision=1, metadata_revision=1, contract_version=1, payload={'history': 'private'})
            db.add(snapshot); await db.flush()
            db.add(UserStatsState(user_id=self.owner.id, active_snapshot_id=snapshot.id))
            await db.commit()
        response = await self.erase('movies')
        self.assertEqual(response.status_code, 200, response.text)
        async with self.Session() as db:
            for model in (WatchIntent, StreamAction, StreamingLibraryIntent, SyncReview, TrackingDeletion, TrackingDeliveryJob, Collection):
                self.assertEqual(list(await db.scalars(select(model.media_id).where(model.user_id == self.owner.id))), [self.series.id], model.__name__)
            self.assertEqual(await db.scalar(select(func.count()).select_from(CollectionFile)), 1)
            for model in (StreamBaseline, CloudBaseline, UserCalendarCache, UserStatsState, UserStatsSnapshot):
                self.assertEqual(await db.scalar(select(func.count()).select_from(model)), 0, model.__name__)

    async def test_database_failure_rolls_back_entire_category(self):
        from sqlalchemy.exc import IntegrityError
        async with self.engine.begin() as conn:
            await conn.execute(text('CREATE TABLE deletion_blocker (work_id integer REFERENCES game_entries(id))'))
            await conn.execute(text('INSERT INTO deletion_blocker SELECT id FROM game_entries WHERE user_id=:owner'), {'owner': self.owner.id})
        with self.assertRaises(IntegrityError):
            await self.erase('games')
        async with self.Session() as db:
            # Child deletion precedes entry deletion; it must be rolled back.
            self.assertEqual(await db.scalar(select(func.count()).select_from(GameChildState)), 2)
            self.assertEqual(await db.scalar(select(func.count()).select_from(GameEntry)), 2)
            self.assertTrue((await db.get(SteamConnection, self.connection_id)).imported)
