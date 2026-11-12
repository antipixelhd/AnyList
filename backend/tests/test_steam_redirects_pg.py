"""Steam redirect fallback and independent per-app progress in disposable PostgreSQL."""
import asyncio
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import delete, func, select

import test_steam_pg as fixture
from core import catalogue, steam_cache, steam_redirects, steam_sync, steam_targets
from core.catalogue_providers import ProviderError
from models import GameEntry, SteamConnection
from models.catalogue import CatalogueIdentity, MetadataSnapshot


def reference(app, native=119133, kind=0):
    return {'uid': app, 'game': {'id': native, 'name': 'Canonical game', 'game_type': kind}}


@unittest.skipUnless(fixture.fixture.URL, 'Requires disposable Games PostgreSQL')
class SteamRedirectDatabaseTests(unittest.IsolatedAsyncioTestCase):
    cleanup = fixture.SteamDatabaseTests.cleanup
    connect = fixture.SteamDatabaseTests.connect

    async def asyncSetUp(self):
        await fixture.SteamDatabaseTests.asyncSetUp(self)
        async with self.Session() as db:
            await db.execute(delete(MetadataSnapshot).where(MetadataSnapshot.provider == 'steam',
                MetadataSnapshot.kind == steam_cache.PUBLIC_KIND, MetadataSnapshot.external_id.in_(['20', '30', '40'])))
            await db.commit()

    async def asyncTearDown(self):
        await fixture.fixture.GamesDatabaseTests.asyncTearDown(self)

    async def test_fallback_combines_progress_without_binding_source_identity(self):
        probe = AsyncMock(return_value='1245620')
        source = SimpleNamespace(steam_games=AsyncMock(side_effect=lambda apps:
            [reference('1245620')] if apps == [1245620] else []))
        await steam_targets.classify(self.Session, [fixture.game('20', 40)], source=source, redirect_probe=probe)
        await steam_targets.classify(self.Session, [fixture.game('20', 40)], source=source, redirect_probe=probe)
        probe.assert_awaited_once_with('20')
        self.assertEqual(source.steam_games.await_count, 2)  # Source miss, then parent verification.
        await self.connect()
        review = (await self.client.get(f'/connections/steam/{self.id}')).json()
        self.assertEqual(next(game for game in review['library'] if game['app_id'] == '20')['mapping_state'], 'ready')
        async with self.Session() as db:
            self.assertEqual(await steam_targets.targets(db, ['20']), {'20': self.base})
            self.assertIsNone(await catalogue.resolve_identity(db, 'steam.app', '20'))
            state = await db.get(SteamConnection, self.id)
            games = [fixture.game('1245620', 60, 2, 3), fixture.game('20', 40, 1, 2)]
            await steam_sync.apply_games(db, state, games, ['1245620', '20'], initial=True)
            await db.commit()
            entry = await db.scalar(select(GameEntry).where(GameEntry.work_id == self.base))
            self.assertEqual((entry.total_seconds, entry.achievement_count), (6000, 3))
            games[1]['playtime_minutes'] = 50
            await steam_sync.apply_games(db, state, games, ['1245620', '20'])
            await db.commit()
            self.assertEqual(entry.total_seconds, 6600)
            self.assertEqual(state.checkpoints['20']['playtime_minutes'], 50)
            self.assertEqual(state.checkpoints['1245620']['playtime_minutes'], 60)
            self.assertEqual(await db.scalar(select(func.count()).select_from(GameEntry)), 1)

    async def test_parent_can_be_verified_and_created_without_owning_parent_app(self):
        source = SimpleNamespace(steam_games=AsyncMock(side_effect=lambda apps:
            [reference('2073850', 214417)] if apps == [2073850] else []))
        await steam_targets.classify(self.Session, [fixture.game('2076040', 60)], source=source,
            redirect_probe=AsyncMock(return_value='2073850'))
        await self.connect()
        async with self.Session() as db:
            state = await db.get(SteamConnection, self.id)
            await steam_sync.apply_games(db, state, [fixture.game('2076040', 60)], ['2076040'], initial=True)
            await db.commit()
            target = (await steam_targets.targets(db, ['2076040']))['2076040']
            entry = await db.scalar(select(GameEntry).where(GameEntry.work_id == target))
            self.assertEqual(entry.total_seconds, 3600)
            self.assertEqual((await catalogue.resolve_identity(db, 'igdb.game', '214417')).entity_id, target)
            self.assertIsNone(await catalogue.resolve_identity(db, 'steam.app', '2076040'))

    async def test_legacy_membership_moves_once_and_preserves_progress_baseline(self):
        from models.catalogue import CatalogueEntity
        await self.connect()
        async with self.Session() as db:
            old = CatalogueEntity(kind='game', name='Legacy playtest', attributes={'game_class': 'game'})
            db.add(old); await db.flush(); old_id = old.id
            db.add(CatalogueIdentity(entity_id=old_id, namespace='steam.app', external_id='20', source='steam'))
            db.add(GameEntry(user_id=self.user.id, work_id=old_id, total_seconds=1200, notes='Keep playtest note'))
            state = await db.get(SteamConnection, self.id)
            state.checkpoints = {'20': {'work_id': old_id, 'playtime_minutes': 20}}
            await db.commit()
        source = SimpleNamespace(steam_games=AsyncMock(side_effect=lambda apps:
            [reference('1245620')] if apps == [1245620] else []))
        await steam_targets.classify(self.Session, [fixture.game('20', 30)], source=source,
            redirect_probe=AsyncMock(return_value='1245620'))
        async with self.Session() as db:
            state = await db.get(SteamConnection, self.id)
            for _ in range(2):
                await steam_sync.apply_games(db, state, [fixture.game('20', 30)], ['20'])
                await db.commit()
            entry = await db.scalar(select(GameEntry).where(GameEntry.work_id == self.base))
            self.assertEqual((entry.total_seconds, entry.notes), (1800, 'Keep playtest note'))
            self.assertEqual(state.checkpoints['20']['work_id'], self.base)
            self.assertEqual((await catalogue.resolve_identity(db, 'steam.app', '20')).entity_id, old_id)
            self.assertEqual(await db.scalar(select(func.count()).select_from(GameEntry)), 1)

    async def test_new_source_mapping_wins_during_provider_lookup(self):
        async def probe(app):
            async with self.Session() as db:
                await catalogue.write_lock(db)
                await catalogue.upsert_entity(db, {'kind': 'game', 'name': 'Newly verified game',
                    'attributes': {'game_class': 'game', 'igdb_id': '900020'},
                    'identities': [{'namespace': 'steam.app', 'external_id': app},
                        {'namespace': 'igdb.game', 'external_id': '900020'}]}, 'igdb')
                await db.commit()
            return '1245620'
        source = SimpleNamespace(steam_games=AsyncMock(side_effect=lambda apps:
            [reference('1245620')] if apps == [1245620] else []))
        await steam_targets.classify(self.Session, [fixture.game('20', 30)], source=source, redirect_probe=probe)
        async with self.Session() as db:
            self.assertEqual(await steam_targets.targets(db, ['20']), {})
            self.assertIsNotNone(await catalogue.resolve_identity(db, 'igdb.game', '900020'))

    async def test_exclusions_known_mappings_and_incomplete_evidence_never_probe(self):
        await steam_cache.save_public(self.Session, [{'app_id': '40', 'steam_app_type': 'software'}])
        source = SimpleNamespace(steam_games=AsyncMock(return_value=[reference('20', 90, 13), reference('30', 91, 5)]))
        probe = AsyncMock(side_effect=AssertionError('Excluded app must not be probed'))
        await steam_targets.classify(self.Session, [fixture.game(app, 10) for app in ('20', '30', '40', '1245620')],
            source=source, redirect_probe=probe)
        source.steam_games.assert_awaited_once_with([20, 30])
        # A malformed/partial reference is not an exact miss.
        source.steam_games.return_value = [{'uid': '50', 'game': None}]
        await steam_targets.classify(self.Session, [fixture.game('50', 10)], source=source, redirect_probe=probe)
        probe.assert_not_awaited()

    async def test_parent_requires_complete_unambiguous_eligible_evidence(self):
        for parent_rows in ([], [reference('100', 1), reference('100', 2)],
                [reference('100', 1, 5)], [reference('100')] * 500,
                [reference('100'), {'uid': '100', 'game': None}],
                [{'uid': '100', 'game': {'id': 119133, 'game_type': 0}}]):
            async with self.Session() as db:
                await db.execute(delete(MetadataSnapshot).where(MetadataSnapshot.provider == 'steam',
                    MetadataSnapshot.kind == steam_cache.PUBLIC_KIND, MetadataSnapshot.external_id == '20'))
                await db.commit()
            source = SimpleNamespace(steam_games=AsyncMock(side_effect=lambda apps:
                parent_rows if apps == [100] else []))
            await steam_targets.classify(self.Session, [fixture.game('20', 60)], source=source,
                redirect_probe=AsyncMock(return_value='100'))
            async with self.Session() as db:
                self.assertEqual(await steam_targets.targets(db, ['20']), {})
                self.assertIsNone(await catalogue.resolve_identity(db, 'steam.app', '100'))

    async def test_redirect_cache_shares_claims_caches_misses_and_retries_failures(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed(app):
            entered.set(); await release.wait(); return '100'
        probe = AsyncMock(side_effect=delayed)
        first = asyncio.create_task(steam_redirects.lookup(self.Session, '20', probe=probe))
        await entered.wait()
        second = asyncio.create_task(steam_redirects.lookup(self.Session, '20', probe=probe))
        await asyncio.sleep(0.05)
        self.assertFalse(second.done())
        release.set(); self.assertEqual(await first, '100')
        self.assertEqual(await second, '100')
        self.assertEqual(await steam_redirects.lookup(self.Session, '20', probe=probe), '100')
        probe.assert_awaited_once()
        miss = AsyncMock(return_value=None)
        for _ in range(2):
            self.assertIsNone(await steam_redirects.lookup(self.Session, '30', probe=miss))
        miss.assert_awaited_once()
        failure = AsyncMock(side_effect=ProviderError('unavailable'))
        for _ in range(2):
            self.assertIsNone(await steam_redirects.lookup(self.Session, '40', probe=failure))
        failure.assert_awaited_once()
        async with self.Session() as db:
            row = await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.kind == steam_redirects.KIND,
                MetadataSnapshot.external_id == '40'))
            self.assertIsNone(row.fetched_at)
            row.next_attempt_at = steam_cache.now() - timedelta(seconds=1)
            await db.commit()
        recovered = AsyncMock(return_value='100')
        self.assertEqual(await steam_redirects.lookup(self.Session, '40', probe=recovered), '100')

    async def test_import_waits_for_preparation_and_retains_selected_redirected_app(self):
        await self.connect()
        entered, release = asyncio.Event(), asyncio.Event()
        async def delayed(app):
            entered.set(); await release.wait(); return '1245620'
        probe = AsyncMock(side_effect=delayed)
        source = SimpleNamespace(steam_games=AsyncMock(side_effect=lambda apps:
            [reference('1245620')] if apps == [1245620] else []))
        original = steam_targets.classify
        async def classify(factory, games):
            await original(factory, games, source=source, redirect_probe=probe)
        preparation = asyncio.create_task(classify(self.Session, [fixture.game('20', 60)]))
        await entered.wait()
        with patch.object(steam_targets, 'classify', side_effect=classify):
            importing = asyncio.create_task(self.client.post(f'/connections/steam/{self.id}/import',
                json={'generation': 1, 'selected_apps': ['20']}))
            await asyncio.sleep(0.2)
            self.assertFalse(importing.done())
            release.set()
            await preparation
            response = await importing
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['imported'], 1)
        probe.assert_awaited_once()
        async with self.Session() as db:
            state = await db.get(SteamConnection, self.id)
            self.assertEqual(state.selection, ['20'])
            self.assertEqual(state.checkpoints['20']['work_id'], self.base)

    async def test_busy_redirect_returns_retry_without_finalizing_import(self):
        await self.connect()
        stamp = steam_cache.now()
        async with self.Session() as db:
            db.add(MetadataSnapshot(provider='steam', kind=steam_redirects.KIND, external_id='20',
                lease_token='other-worker', lease_until=stamp + timedelta(minutes=1)))
            await db.commit()
        original = steam_targets.classify
        async def classify(factory, games):
            await original(factory, games, source=SimpleNamespace(steam_games=AsyncMock(return_value=[])),
                redirect_probe=AsyncMock(side_effect=AssertionError('Active claim must not be duplicated')))
        with patch.object(steam_targets, 'classify', side_effect=classify), patch.object(steam_redirects, 'WAIT_SECONDS', 0):
            response = await self.client.post(f'/connections/steam/{self.id}/import',
                json={'generation': 1, 'selected_apps': ['20']})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn('still running', response.json()['detail'])
        async with self.Session() as db:
            state = await db.get(SteamConnection, self.id)
            self.assertFalse(state.imported)
            self.assertEqual(state.checkpoints, {})
            self.assertNotIn('20', state.known_apps)
