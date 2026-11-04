"""Software exclusion through cached classification, picker updates and writes."""
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import func, select

import test_steam_pg as fixture
from core import catalogue, steam_cache, steam_software, steam_sync, steam_targets
from core.catalogue_providers import ProviderError
from models import GameEntry, SteamConnection
from models.catalogue import MetadataSnapshot


@unittest.skipUnless(fixture.fixture.URL, 'Requires disposable Games PostgreSQL')
class SteamSoftwareTests(unittest.IsolatedAsyncioTestCase):
    cleanup = fixture.SteamDatabaseTests.cleanup
    connect = fixture.SteamDatabaseTests.connect

    async def asyncSetUp(self):
        await fixture.SteamDatabaseTests.asyncSetUp(self)

    async def asyncTearDown(self):
        await fixture.fixture.GamesDatabaseTests.asyncTearDown(self)

    async def test_catalog_is_shared_and_software_is_rejected_before_igdb(self):
        source = SimpleNamespace(software=AsyncMock(return_value={'20', '431960'}))
        await steam_software.classify(self.Session, [{'app_id': '20'}], source=source)
        await steam_software.classify(self.Session, [{'app_id': '431960'}], source=source)
        source.software.assert_awaited_once()
        igdb = SimpleNamespace(steam_games=AsyncMock(side_effect=AssertionError('Software must not reach IGDB')))
        await steam_targets.classify(self.Session, [{'app_id': '20'}], source=igdb)
        async with self.Session() as db:
            metadata = await steam_cache.public_metadata(db, ['20', '431960', '30'])
            self.assertEqual(metadata['20']['steam_app_type'], 'software')
            self.assertEqual(metadata['431960']['steam_app_type'], 'software')
            self.assertNotIn('steam_app_type', metadata['30'])

    async def test_initial_picker_hides_software_and_open_picker_gets_disabled_state(self):
        await self.connect()
        before = (await self.client.get(f'/connections/steam/{self.id}')).json()
        self.assertIn('20', [game['app_id'] for game in before['library']])
        await steam_cache.save_public(self.Session, [{'app_id': '20', 'steam_app_type': 'software'}])
        progress = (await self.client.get(f'/connections/steam/{self.id}/achievement-progress')).json()
        self.assertEqual(next(game for game in progress['games'] if game['app_id'] == '20')['mapping_state'], 'software')
        after = (await self.client.get(f'/connections/steam/{self.id}')).json()
        self.assertNotIn('20', [game['app_id'] for game in after['library']])
        self.assertIn('1245620', [game['app_id'] for game in after['library']])
        response = await self.client.post(f'/connections/steam/{self.id}/background')
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('20', [call.args[1] for call in self.source.achievements.await_args_list])

    async def test_explicit_import_and_auto_add_cannot_bypass_software_evidence(self):
        await self.connect()
        await steam_cache.save_public(self.Session, [{'app_id': app, 'steam_app_type': 'software'}
            for app in ('20', '1245620')])
        response = await self.client.post(f'/connections/steam/{self.id}/import', json={
            'generation': 1, 'selected_apps': ['20', '1245620']})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['imported'], 0)
        async with self.Session() as db:
            self.assertIsNone(await catalogue.resolve_identity(db, 'steam.app', '20'))
            state = await db.get(SteamConnection, self.id)
            # Old canonical mappings must not override Steam's software evidence.
            self.assertIsNotNone(await catalogue.resolve_identity(db, 'igdb.game', '119133'))
            await steam_sync.apply_games(db, state, [fixture.game('1245620', 90)], ['1245620'], add_apps=['1245620'])
            await db.commit()
            self.assertEqual(await db.scalar(select(func.count()).select_from(GameEntry)), 0)
            self.assertEqual(state.checkpoints, {})
            states = await steam_targets.import_states(db, [{'app_id': '1245620'}])
            self.assertEqual(states['1245620'], 'software')

    async def test_expired_catalog_failure_preserves_positive_evidence_and_cools_down(self):
        source = SimpleNamespace(software=AsyncMock(return_value={'20'}))
        await steam_software.classify(self.Session, [{'app_id': '20'}], source=source)
        async with self.Session() as db:
            row = await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.kind == steam_software.KIND))
            row.expires_at = steam_cache.now() - timedelta(days=1)
            await db.commit()
        source.software.side_effect = ProviderError('unavailable')
        for _ in range(2):
            await steam_software.classify(self.Session, [{'app_id': '20'}, {'app_id': '30'}], source=source)
        self.assertEqual(source.software.await_count, 2)
        async with self.Session() as db:
            values = await steam_cache.public_metadata(db, ['20', '30'])
            self.assertEqual(values['20']['steam_app_type'], 'software')
            self.assertNotIn('steam_app_type', values['30'])
