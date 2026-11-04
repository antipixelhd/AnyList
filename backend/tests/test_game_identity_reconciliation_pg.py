"""Exact Steam bridges and rollback guarantees in disposable PostgreSQL."""
import asyncio
import os
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

os.environ.setdefault('SECRET_KEY', 'game-bridge-tests')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core import catalogue
from core.catalogue_normalize import normalize_igdb
from core.game_company_works import prepare_primary_game
from core.game_identity_reconciliation import alias_target
from core.game_imdb_discovery import GamePersonDiscovery
from models import Base, User, GameEntry, GameActivity, GameFavorite, GameAchievement, GameAchievementUnlock, SteamConnection, MediaServerConnection, UserProfileData
from models.catalogue import CatalogueEntity, CatalogueIdentity, CatalogueCredit, CharacterAppearance, CharacterPerformance, MetadataSnapshot

URL = os.getenv('GAMES_TEST_DATABASE_URL') or os.getenv('TRACKING_TEST_DATABASE_URL')


def document(native=113112, app='1145360'):
    return normalize_igdb({'id': native, 'name': 'Hades', 'summary': 'Complete description',
        'game_type': {'id': 0}, 'platforms': [{'id': 6, 'name': 'PC (Microsoft Windows)'}],
        'external_games': [{'external_game_source': {'name': 'Steam'}, 'uid': app,
                            'url': f'https://store.steampowered.com/app/{app}/'}]})


@unittest.skipUnless(URL, 'Requires explicitly disposable PostgreSQL')
class GameBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if not URL.endswith('/anylist_test'):
            raise RuntimeError('Refusing non-disposable database')
        self.schema = 'game_bridge_' + uuid.uuid4().hex
        self.admin = create_async_engine(URL)
        async with self.admin.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.engine = create_async_engine(URL, connect_args={'server_settings': {'search_path': self.schema}})
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as db:
            self.user = User(email='bridge@test.local', username='bridge', api_key='bridge')
            root = CatalogueEntity(kind='game', name='Imported Hades', attributes={'game_class': 'game'}, field_sources={'name': 'steam'})
            db.add_all([self.user, root]); await db.flush()
            self.root = root.id
            db.add(CatalogueIdentity(entity_id=root.id, namespace='steam.app', external_id='1145360', source='steam'))
            entry = GameEntry(user_id=self.user.id, work_id=root.id, status='playing', total_seconds=7200, notes='Keep my notes', revision=7)
            db.add(entry); await db.flush()
            self.entry_id = entry.id
            db.add_all([GameFavorite(user_id=self.user.id, work_id=root.id, position=0),
                        GameActivity(user_id=self.user.id, work_id=root.id, status='playing', payload={'keep': True})])
            achievement = GameAchievement(work_id=root.id, app_id='1145360', api_name='WIN', name='Win')
            db.add(achievement); await db.flush()
            self.achievement_id = achievement.id
            db.add(GameAchievementUnlock(user_id=self.user.id, work_id=root.id, achievement_id=achievement.id))
            connection = MediaServerConnection(user_id=self.user.id, type='steam', name='Steam', url='https://steamcommunity.com', token='test')
            db.add(connection); await db.flush()
            self.connection_id = connection.id
            db.add(SteamConnection(connection_id=connection.id, user_id=self.user.id, steam_id='123', imported=True,
                selection=['1145360'], checkpoints={'1145360': {'work_id': root.id, 'playtime_minutes': 120}}))
            await db.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()
        async with self.admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
        await self.admin.dispose()

    async def duplicate(self, db):
        doc = document(); doc['work']['identities'] = doc['work']['identities'][:1]
        row = await catalogue.ingest_document(db, doc, 'igdb')
        return row

    async def assert_private_preserved(self, db):
        entry = await db.get(GameEntry, self.entry_id)
        self.assertEqual((entry.work_id, entry.status, entry.total_seconds, entry.notes, entry.revision),
                         (self.root, 'playing', 7200, 'Keep my notes', 7))
        self.assertEqual((await db.get(GameAchievement, self.achievement_id)).work_id, self.root)
        self.assertEqual((await db.get(GameAchievementUnlock, (self.user.id, self.achievement_id))).work_id, self.root)
        state = await db.get(SteamConnection, self.connection_id)
        self.assertEqual(state.checkpoints, {'1145360': {'work_id': self.root, 'playtime_minutes': 120}})
        self.assertEqual(state.selection, ['1145360'])

    async def test_sparse_company_projection_reuses_imported_root_before_creation(self):
        async with self.Session() as db:
            await catalogue.write_lock(db)
            doc = document(); doc['work'] = await prepare_primary_game(db, doc['work'])
            work = await catalogue.ingest_document(db, doc, 'igdb'); await db.commit()
            self.assertEqual(work.id, self.root)
            self.assertEqual(await db.scalar(select(func.count()).select_from(CatalogueEntity).where(CatalogueEntity.kind == 'game')), 1)
            await self.assert_private_preserved(db)

    async def test_person_discovery_reuses_imported_root(self):
        result = await GamePersonDiscovery(self.Session).ingest(document())
        self.assertEqual(result, self.root)
        async with self.Session() as db:
            await self.assert_private_preserved(db)

    async def test_provisional_bulk_publication_also_reuses_the_steam_root(self):
        from core.game_bulk import publish
        async with self.Session() as db:
            person = CatalogueEntity(kind='person', name='Person'); db.add(person); await db.flush()
            job = MetadataSnapshot(provider='anylist', kind='game_bulk', external_id=f'{person.id}:actor',
                entity_id=person.id, coverage={'kind': 'actor'})
            db.add(job); await db.flush()
            raw = {'id': 113112, 'name': 'Hades', 'game_type': 0, 'external_games': [
                {'uid': '1145360', 'url': 'https://store.steampowered.com/app/1145360/',
                 'external_game_source': {'name': 'Steam'}}]}
            await publish(db, job, [{'game': raw, 'source': 'tt123', 'roles': ['actor']}])
            await db.commit()
            self.assertEqual(job.payload['rows'][0]['work_id'], self.root)
            self.assertEqual(await db.scalar(select(func.count()).select_from(CatalogueEntity).where(CatalogueEntity.kind == 'game')), 1)
            await self.assert_private_preserved(db)

    async def test_existing_split_preserves_graph_edits_progress_and_old_url(self):
        async with self.Session() as db:
            duplicate = await self.duplicate(db); old_id = duplicate.id
            duplicate.image_url = 'https://example.test/selected.jpg'
            duplicate.protected_fields = ['image_url']
            person = CatalogueEntity(kind='person', name='Actor'); character = CatalogueEntity(kind='character', name='Character')
            db.add_all([person, character]); await db.flush()
            credit = CatalogueCredit(work_id=old_id, contributor_id=person.id, provider='imdb', source_key='credit', role='actor')
            appearance = CharacterAppearance(work_id=old_id, character_id=character.id, provider='imdb')
            db.add_all([credit, appearance]); await db.flush()
            db.add(CharacterPerformance(work_id=old_id, credit_id=credit.id, appearance_id=appearance.id, language='en'))
            db.add_all([MetadataSnapshot(provider='igdb', kind='game', external_id='113112', entity_id=old_id, error_code='identity_conflict'),
                        MetadataSnapshot(provider='imdb', kind='game_credit_job', external_id=str(self.root), entity_id=self.root, error_code='identity_conflict'),
                        MetadataSnapshot(provider='imdb', kind='game_credit_job', external_id=str(old_id), entity_id=old_id)])
            db.add(MetadataSnapshot(provider='anylist', kind='game_bulk', external_id=f'{person.id}:actor', entity_id=person.id,
                payload={'rows': [{'work_id': old_id, 'source': 'tt123', 'role': 'actor'}]}))
            db.add(MetadataSnapshot(provider='wikidata', kind='game_person_title_map', external_id='tt123', entity_id=old_id,
                payload={'work_id': old_id, 'document': {'id': old_id}}))
            db.add(UserProfileData(user_id=self.user.id, game_banners={str(old_id): 'https://example.test/banner.jpg'}))
            await db.commit()
        async with self.Session() as db:
            row = await catalogue.upsert_entity(db, document()['work'], 'igdb'); await db.commit()
            self.assertEqual(row.id, self.root)
            self.assertIsNone(await db.get(CatalogueEntity, old_id))
            self.assertEqual(await alias_target(db, old_id), self.root)
            self.assertEqual(row.image_url, 'https://example.test/selected.jpg')
            self.assertEqual(set(await db.scalars(select(CatalogueCredit.work_id))), {self.root})
            self.assertEqual(set(await db.scalars(select(CharacterAppearance.work_id))), {self.root})
            self.assertEqual(set(await db.scalars(select(CharacterPerformance.work_id))), {self.root})
            self.assertEqual(await db.scalar(select(func.count()).select_from(MetadataSnapshot).where(MetadataSnapshot.error_code == 'identity_conflict')), 0)
            provisional = await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.kind == 'game_bulk'))
            self.assertEqual(provisional.payload['rows'][0]['work_id'], self.root)
            mapping = await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.kind == 'game_person_title_map'))
            self.assertEqual(mapping.payload, {'work_id': self.root, 'document': {'id': old_id}})
            profile = await db.scalar(select(UserProfileData).where(UserProfileData.user_id == self.user.id))
            self.assertEqual(profile.game_banners, {str(self.root): 'https://example.test/banner.jpg'})
            await self.assert_private_preserved(db)

    async def test_private_duplicate_conflict_rolls_back_everything(self):
        async with self.Session() as db:
            duplicate = await self.duplicate(db); old_id = duplicate.id
            db.add(GameEntry(user_id=self.user.id, work_id=old_id, status='completed', notes='Different decision'))
            await db.commit()
            with self.assertRaises(catalogue.IdentityConflict):
                await catalogue.upsert_entity(db, document()['work'], 'igdb')
            await db.rollback()
        async with self.Session() as db:
            self.assertIsNotNone(await db.get(CatalogueEntity, old_id))
            self.assertIsNone(await alias_target(db, old_id))
            await self.assert_private_preserved(db)

    async def test_protected_field_conflict_is_not_silently_overwritten(self):
        async with self.Session() as db:
            duplicate = await self.duplicate(db)
            root = await db.get(CatalogueEntity, self.root)
            root.protected_fields = duplicate.protected_fields = ['name']
            await db.commit()
            with self.assertRaises(catalogue.IdentityConflict):
                await catalogue.upsert_entity(db, document()['work'], 'igdb')
            await db.rollback()

    async def test_port_reference_does_not_merge_existing_different_igdb_game(self):
        async with self.Session() as db:
            db.add(CatalogueIdentity(entity_id=self.root, namespace='igdb.game', external_id='999', source='igdb'))
            await db.commit()
            doc = document(); doc['work'] = await prepare_primary_game(db, doc['work'])
            work = await catalogue.ingest_document(db, doc, 'igdb'); await db.commit()
            self.assertNotEqual(work.id, self.root)
            self.assertEqual((await catalogue.resolve_identity(db, 'steam.app', '1145360')).entity_id, self.root)

    async def test_non_igdb_bridge_cannot_merge(self):
        async with self.Session() as db:
            await self.duplicate(db); await db.commit()
            with self.assertRaises(catalogue.IdentityConflict):
                await catalogue.upsert_entity(db, document()['work'], 'steam')
            await db.rollback()

    async def test_concurrent_discovery_and_identity_recovery_publish_one_root(self):
        async def discover():
            return await GamePersonDiscovery(self.Session).ingest(document())
        async def recover():
            async with self.Session() as db:
                await catalogue.write_lock(db)
                row = await catalogue.upsert_entity(db, document()['work'], 'igdb')
                await db.commit(); return row.id
        self.assertEqual(await asyncio.gather(discover(), recover()), [self.root, self.root])

    async def test_repair_command_rehearses_then_applies_without_private_changes(self):
        from scripts.reconcile_steam_games import run
        source = SimpleNamespace(steam_games=AsyncMock(return_value=[{'uid': '1145360', 'game': {
            'id': 113112, 'name': 'Hades', 'game_type': 0, 'platforms': [{'id': 6}]}}]))
        report = await run(SimpleNamespace(app_id=['1145360'], apply=False), factory=self.Session, source=source)
        self.assertTrue(report['private_facts_preserved'])
        async with self.Session() as db:
            self.assertIsNone(await catalogue.resolve_identity(db, 'igdb.game', '113112'))
        report = await run(SimpleNamespace(app_id=['1145360'], apply=True), factory=self.Session, source=source)
        self.assertEqual(report['games'][0]['status'], 'reconciled')
        async with self.Session() as db:
            self.assertEqual((await catalogue.resolve_identity(db, 'igdb.game', '113112')).entity_id, self.root)
            await self.assert_private_preserved(db)

    async def test_alias_dependency_resolves_old_tracking_urls(self):
        import httpx
        from fastapi import FastAPI
        from db import get_db
        from core.game_access import approved_games_viewer
        from dependencies import get_current_user_or_api_key
        from routers.games import router
        async with self.Session() as db:
            old_id = (await self.duplicate(db)).id
            await catalogue.upsert_entity(db, document()['work'], 'igdb'); await db.commit()
        app = FastAPI(); app.include_router(router)
        async def database():
            async with self.Session() as db:
                yield db
        app.dependency_overrides[get_db] = database
        app.dependency_overrides[get_current_user_or_api_key] = lambda: self.user
        app.dependency_overrides[approved_games_viewer] = lambda: self.user
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            response = await client.get(f'/tracking/games/works/{old_id}')
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['subject']['catalogue_id'], self.root)
