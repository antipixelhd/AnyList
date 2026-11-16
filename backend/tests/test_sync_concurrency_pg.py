"""Concurrent public identities and private memberships in disposable PostgreSQL."""
import asyncio
import contextlib
import io
import os
import unittest
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, patch

os.environ.setdefault('SECRET_KEY', 'sync-concurrency-tests')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core import server_sync
from core.sync_admission import SyncAdmission
from models import Base, Collection, CollectionFile, Media, MediaServerConnection, Show, User, UserSettings, WatchEvent
from models.base import CollectionSource, MediaType
from models.sync import SyncJob, SyncStatus

URL = os.getenv('SYNC_TEST_DATABASE_URL')


@unittest.skipUnless(URL, 'Requires disposable sync PostgreSQL')
class SyncConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if ':55449/anylist_test_sync' not in URL:
            raise RuntimeError('Refusing non-disposable sync database')
        self.schema = 'sync_test_' + uuid.uuid4().hex
        self.admin = create_async_engine(URL)
        async with self.admin.begin() as db:
            await db.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.engine = create_async_engine(URL, connect_args={'server_settings': {'search_path': self.schema}})
        async with self.engine.begin() as db:
            await db.run_sync(Base.metadata.create_all)
            await db.execute(text('CREATE UNIQUE INDEX uq_media_tmdb_type ON media (tmdb_id,media_type) WHERE tmdb_id IS NOT NULL'))
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.sessions() as db:
            users = [User(username=f'concurrent-{i}', email=f'concurrent-{i}@example.test',
                          api_key=f'concurrent-{i}') for i in range(2)]
            db.add_all(users)
            await db.flush()
            db.add_all([UserSettings(user_id=u.id) for u in users])
            connections = [MediaServerConnection(user_id=u.id, type='jellyfin', name='Test',
                url='http://fixture', token='synthetic', server_user_id=str(u.id)) for u in users]
            db.add_all(connections)
            await db.commit()
            self.actors = [(u.id, c.id) for u, c in zip(users, connections)]

    async def asyncTearDown(self):
        await self.engine.dispose()
        async with self.admin.begin() as db:
            await db.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
        await self.admin.dispose()

    async def cached_movies(self, count, first=70000):
        async with self.sessions() as db:
            movies = [Media(tmdb_id=first+i, media_type=MediaType.movie, title=f'Cached {i}') for i in range(count)]
            db.add_all(movies)
            await db.commit()
            return {movie.tmdb_id: movie.id for movie in movies}

    async def pull_cached(self, items):
        stats = {'movies': 0, 'episodes': 0, 'skipped': 0, 'errors': 0}
        collected = set()
        user, connection = self.actors[0]
        async with self.sessions() as db:
            with patch.object(server_sync.tmdb, 'get_movie', AsyncMock(side_effect=AssertionError('Cached movie fetched'))), contextlib.redirect_stdout(io.StringIO()):
                await server_sync.sync_items(items, MediaType.movie, CollectionSource.jellyfin, db,
                    stats, user, connection_id=connection, sync_watched=True, sync_ratings=False,
                    new_collected_ids=collected)
            await db.commit()
        return stats, collected

    async def test_cached_batch_boundary_retains_valid_files_without_empty_failed_memberships(self):
        movies = await self.cached_movies(501)
        items = [{'Id': str(native), 'ProviderIds': {'Tmdb': str(native)}, 'Name': 'Cached', 'Path': '/valid.mkv'}
                 for native in movies]
        items[499].update(Path='x'*1001, UserData={'Played': True})
        stats, collected = await self.pull_cached(items)
        self.assertEqual((stats['movies'], stats['errors'], len(collected)), (500, 1, 500))
        async with self.sessions() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(Collection)), 500)
            self.assertEqual(await db.scalar(select(func.count()).select_from(CollectionFile)), 500)
            self.assertEqual(await db.scalar(select(func.count()).select_from(WatchEvent)), 0)
            self.assertIsNone(await db.scalar(select(Collection).where(Collection.media_id == movies[70499])))

    async def test_cached_allocation_preserves_first_successful_and_earliest_dates(self):
        movies = await self.cached_movies(3, 71000)
        def item(native, identifier, **values):
            return {'Id': identifier, 'ProviderIds': {'Tmdb': str(native)}, 'Name': 'Cached', 'Path': '/valid.mkv', **values}
        before = datetime.utcnow()
        stats, collected = await self.pull_cached([
            item(71000, 'future', DateCreated='2099-01-01T00:00:00Z'),
            item(71001, 'invalid', DateCreated='2098-01-01T00:00:00Z', Path='x'*1001),
            item(71001, 'valid'),
            item(71002, 'later', DateCreated='2022-01-01T00:00:00Z'),
            item(71002, 'earlier', DateCreated='2020-01-01T00:00:00Z')])
        self.assertEqual((stats['movies'], stats['errors'], len(collected)), (3, 1, 3))
        async with self.sessions() as db:
            dates = dict((await db.execute(select(Collection.media_id, Collection.added_at))).all())
            self.assertEqual(dates[movies[71000]], datetime(2099, 1, 1))
            self.assertGreaterEqual(dates[movies[71001]], before)
            self.assertLessEqual(dates[movies[71001]], datetime.utcnow())
            self.assertEqual(dates[movies[71002]], datetime(2020, 1, 1))

    async def test_cancelled_queued_pull_does_not_contact_the_server_after_admission(self):
        user, connection = self.actors[0]
        gate = SyncAdmission(1)
        async with self.sessions() as db:
            job = SyncJob(user_id=user, connection_id=connection, source=CollectionSource.jellyfin)
            db.add(job)
            await db.commit()
            identifier = job.id
        with patch.object(server_sync, 'engine', self.engine), patch.object(server_sync, '_sync_admission', gate), patch.object(
                server_sync.jellyfin, 'get_libraries', AsyncMock()) as libraries:
            async with gate.slot(user):
                pending = asyncio.create_task(server_sync.run_jellyfin_sync(user, identifier, 0, 0, connection))
                await asyncio.sleep(0)
                self.assertEqual(self.engine.pool.checkedout(), 0)
                async with self.sessions() as db:
                    (await db.get(SyncJob, identifier)).status = SyncStatus.cancelled
                    await db.commit()
            await asyncio.wait_for(pending, 3)
            libraries.assert_not_awaited()
        async with self.sessions() as db:
            self.assertEqual((await db.get(SyncJob, identifier)).status, SyncStatus.cancelled)

    async def test_reversed_overlapping_imports_keep_one_public_root_and_both_private_collections(self):
        started = set()
        ready = asyncio.Event()
        create = server_sync.create_media_safely

        async def concurrent_create(db, *args, **kwargs):
            if id(db) not in started:
                started.add(id(db))
                if len(started) == 2:
                    ready.set()
                await asyncio.wait_for(ready.wait(), 5)
            return await create(db, *args, **kwargs)

        async def movie(native, **kwargs):
            return {'id': native, 'title': f'Movie {native}', 'runtime': 90, 'credits': {}}

        async def pull(actor, natives):
            user, connection = actor
            items = [{'Id': f'item-{native}', 'Name': f'Movie {native}', 'ProviderIds': {'Tmdb': str(native)}}
                     for native in natives]
            stats = {'movies': 0, 'episodes': 0, 'skipped': 0, 'errors': 0}
            async with self.sessions() as db:
                await server_sync.sync_items(items, MediaType.movie, CollectionSource.jellyfin, db,
                    stats, user, connection_id=connection, sync_watched=False, sync_ratings=False)
                await db.commit()
            return stats

        with patch.object(server_sync, 'create_media_safely', concurrent_create), patch.object(
                server_sync.tmdb, 'get_movie', movie):
            results = await asyncio.wait_for(asyncio.gather(
                pull(self.actors[0], [51001, 51002, 51003]),
                pull(self.actors[1], [51003, 51002, 51001])), 15)
        self.assertEqual([r['errors'] for r in results], [0, 0])
        async with self.sessions() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(Media)), 3)
            for user, _ in self.actors:
                self.assertEqual(await db.scalar(select(func.count()).select_from(Collection).where(
                    Collection.user_id == user)), 3)
            self.assertEqual(await db.scalar(select(func.count()).select_from(CollectionFile)), 6)

    async def test_identical_racing_show_publications_keep_mappings_without_rewriting_metadata(self):
        async with self.engine.begin() as db:
            await db.execute(text('CREATE TABLE show_update_audit (id integer)'))
            await db.execute(text("""CREATE FUNCTION audit_show_update() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN INSERT INTO show_update_audit VALUES (NEW.id); RETURN NEW; END $$"""))
            await db.execute(text('CREATE TRIGGER show_updates AFTER UPDATE ON shows FOR EACH ROW EXECUTE FUNCTION audit_show_update()'))
        waiting = 0
        ready = asyncio.Event()
        title = 'Original title'

        async def show(native, **kwargs):
            nonlocal waiting
            waiting += 1
            if waiting == 2:
                ready.set()
            await asyncio.wait_for(ready.wait(), 5)
            return {'id': native, 'name': title, 'status': 'Returning Series'}

        async def pull():
            async with self.sessions() as db:
                mapping, _ = await server_sync.sync_shows_batch({'source-show': 52001}, db)
                self.assertIn('source-show', mapping)
                row = await db.get(Show, mapping['source-show'])
                self.assertEqual(row.title, title)
                await db.commit()
                return mapping

        with patch.object(server_sync.tmdb, 'get_show', show):
            first, second = await asyncio.wait_for(asyncio.gather(pull(), pull()), 15)
            self.assertEqual(first, second)
            await pull()
            async with self.sessions() as db:
                self.assertEqual(await db.scalar(text('SELECT count(*) FROM show_update_audit')), 0)
            title = 'Updated title'
            await pull()
        async with self.sessions() as db:
            self.assertEqual(await db.scalar(text('SELECT count(*) FROM show_update_audit')), 1)
