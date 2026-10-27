"""Actual mt058 schema, API/privacy and race checks in disposable PostgreSQL."""
import asyncio
import importlib
import os
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

os.environ.setdefault('SECRET_KEY', 'games-test-only')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

import httpx
from alembic.migration import MigrationContext
from alembic.operations import Operations
from fastapi import FastAPI, HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core import game_tracking
from db import get_db
from dependencies import get_current_user, get_current_user_or_api_key, get_optional_user, get_tracking_write_user
from models import Base, GlobalSettings, User, UserProfileData, Follow
from models.base import PrivacyLevel
from models.catalogue import CatalogueEntity, CatalogueIdentity, CatalogueRelationship, MetadataSnapshot
from models.games import GameAchievement, GameChildState, GameEntry, GameFavorite, GameActivity
from routers.games import router
from routers.tracking import router as preferences_router
from routers.catalogue import router as catalogue_router
from routers.profile import router as profile_router
from schemas_games import ChildPatch

URL = os.getenv('GAMES_TEST_DATABASE_URL')


@unittest.skipUnless(URL, 'Requires explicit disposable games PostgreSQL')
class GamesDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        if not (':55449/catalogue_test' in URL or '/anylist_test' in URL):
            raise RuntimeError('Refusing non-disposable games database')
        self.schema = 'games_test_' + uuid.uuid4().hex
        self.admin = create_async_engine(URL)
        async with self.admin.begin() as conn:
            await conn.execute(text(f'CREATE SCHEMA "{self.schema}"'))
        self.engine = create_async_engine(URL, connect_args={'server_settings': {'search_path': f'{self.schema},public'}})
        self.addAsyncCleanup(self.cleanup)

        def setup(conn):
            conn.execute(text(f'SET LOCAL search_path TO "{self.schema}"'))
            Base.metadata.create_all(conn, tables=[t for t in Base.metadata.sorted_tables if not t.name.startswith('game_')])
            for name in ('show_games', 'show_movies_series', 'show_game_achievements'):
                conn.execute(text(f'ALTER TABLE tracking_preferences DROP COLUMN {name}'))
            with Operations.context(MigrationContext.configure(conn)):
                importlib.import_module('migrations.versions.mt058_games_tracking').upgrade()
                importlib.import_module('migrations.versions.mt059_game_favorite_order').upgrade()
                importlib.import_module('migrations.versions.mt066_game_statistics').upgrade()
                importlib.import_module('migrations.versions.mt069_game_achievement_catalogue').upgrade()

        async with self.engine.begin() as conn:
            await conn.run_sync(setup)
        self.Session = async_sessionmaker(self.engine, expire_on_commit=False)
        async with self.Session() as db:
            user = User(username='player', email='player@example.test', api_key='player-key', games_access=True)
            other = User(username='other', email='other@example.test', api_key='other-key', games_access=True)
            base = CatalogueEntity(kind='game', name='Elden fixture', attributes={'game_class': 'game',
                'achievement_catalogue': {'status': 'complete', 'total': 3, 'definitions_complete': True}})
            child = CatalogueEntity(kind='game', name='Expansion fixture', attributes={'game_class': 'expansion'})
            dlc = CatalogueEntity(kind='game', name='Playable DLC', attributes={'game_class': 'dlc'})
            pack = CatalogueEntity(kind='game', name='Weapon pack', attributes={'game_class': 'pack'})
            unknown = CatalogueEntity(kind='game', name='No Steam fixture', attributes={'game_class': 'game'})
            db.add_all([user, other, base, child, dlc, pack, unknown,
                        GlobalSettings(id=1, enable_logged_out_navigation=True)])
            await db.flush()
            db.add_all([UserProfileData(user_id=u.id, privacy_level=PrivacyLevel.public) for u in (user, other)])
            db.add(CatalogueIdentity(entity_id=base.id, namespace='steam.app', external_id='1245620', source='igdb'))
            db.add_all([CatalogueRelationship(source_id=c.id, target_id=base.id, relation=r, provider='igdb')
                        for c, r in ((child, 'expansion'), (dlc, 'dlc'))])
            await db.flush()
            db.add_all([GameAchievement(work_id=base.id, app_id='1245620', api_name=f'A{i}', name=f'Achievement {i}') for i in range(3)])
            await db.commit()
            self.user, self.other = user, other
            self.base, self.child, self.dlc, self.pack, self.unknown = [e.id for e in (base, child, dlc, pack, unknown)]
            self.achievement_ids = list((await db.scalars(select(GameAchievement.id).order_by(GameAchievement.id))).all())
        self.viewer = user
        app = FastAPI()
        app.include_router(router)
        app.include_router(preferences_router, prefix='/tracking')
        app.include_router(catalogue_router)
        app.include_router(profile_router, prefix='/profile')

        async def database():
            async with self.Session() as db:
                yield db

        def authenticated():
            if not self.viewer:
                raise HTTPException(401, 'Sign in')
            return self.viewer

        app.dependency_overrides[get_db] = database
        for dep in (get_current_user, get_current_user_or_api_key, get_tracking_write_user):
            app.dependency_overrides[dep] = authenticated
        app.dependency_overrides[get_optional_user] = lambda: self.viewer
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test')

    async def cleanup(self):
        if hasattr(self, 'client'):
            await self.client.aclose()
        await self.engine.dispose()
        async with self.admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{self.schema}" CASCADE'))
        await self.admin.dispose()

    async def save(self, work_id=None, **body):
        response = await self.client.patch(f'/tracking/games/entries/{work_id or self.base}', json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def test_public_game_source_cache_coalesces_and_preserves_bounded_last_good_with_durable_retry(self):
        from core import game_source_cache
        from core.catalogue_providers import ProviderError
        cache=game_source_cache.GameSourceCache(self.Session)
        loader=AsyncMock(return_value={'rows':[]})
        results=await asyncio.gather(*(cache.get('steam','game_browse_ranks','fixture',loader) for _ in range(3)))
        self.assertEqual(loader.await_count,1)
        self.assertEqual(results[0]['data'],{'rows':[]})
        self.assertFalse(results[0]['stale'])
        self.assertEqual(await cache.peek('steam','game_browse_ranks','fixture',max_age=timedelta(hours=2)),{'rows':[]})
        async with self.Session() as db:
            row=await db.scalar(cache.query('steam','game_browse_ranks','fixture'))
            row.expires_at=datetime.now(timezone.utc).replace(tzinfo=None)-timedelta(seconds=1)
            await db.commit()
        failed=AsyncMock(side_effect=ProviderError('unavailable'))
        prior=await cache.get('steam','game_browse_ranks','fixture',failed)
        self.assertTrue(prior['stale']);self.assertEqual(prior['data'],{'rows':[]})
        await asyncio.gather(*list(game_source_cache._flights.values()),return_exceptions=True)
        again=await cache.get('steam','game_browse_ranks','fixture',failed)
        self.assertEqual(again['error'],'unavailable');self.assertEqual(failed.await_count,1)
        async with self.Session() as db:
            row=await db.scalar(cache.query('steam','game_browse_ranks','fixture'))
            self.assertEqual(row.attempts,1)
            self.assertEqual(row.payload,{'rows':[]})
            row.fetched_at=datetime.now(timezone.utc).replace(tzinfo=None)-timedelta(days=10)
            db.add(MetadataSnapshot(provider='steam',kind='game_browse_ranks',external_id=f'{game_source_cache.SCHEMA_VERSION}:held',
                lease_token='another-worker',lease_until=datetime.now(timezone.utc).replace(tzinfo=None)+timedelta(minutes=1)))
            await db.commit()
        with self.assertRaisesRegex(ProviderError,'cursor_expired'):
            await cache.peek('steam','game_browse_ranks','fixture',max_age=timedelta(hours=2))
        with self.assertRaisesRegex(ProviderError,'retry_pending'):
            await cache.get('steam','game_browse_ranks','fixture',failed)
        with self.assertRaisesRegex(ProviderError,'refreshing'):
            await cache.get('steam','game_browse_ranks','held',failed)
        self.assertEqual(failed.await_count,1)
        limited=game_source_cache.GameSourceCache(self.Session,max_records=2)
        async with self.Session() as db:
            db.add(MetadataSnapshot(provider='steam',kind='legacy_metadata',external_id='retained'))
            await db.commit()
        for index in range(4):
            await limited.get('steam','game_cache_capacity',str(index),loader)
        async with self.Session() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(MetadataSnapshot).where(
                MetadataSnapshot.provider=='steam',MetadataSnapshot.kind=='game_cache_capacity')),2)
            self.assertIsNotNone(await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.kind=='legacy_metadata')))

    async def test_game_critic_bridge_and_scores_use_actual_snapshot_schema_and_reuse_warm_evidence(self):
        from core.game_browse_sources import GameBrowseSources
        from core.catalogue_providers import ProviderError
        source=GameBrowseSources(self.Session)
        binding={key:{'value':value} for key,value in {'key':'fixture-game','item':'http://www.wikidata.org/entity/Q1','slug':'game-10','id':'10'}.items()}
        source.request=AsyncMock(side_effect=[{'results':{'bindings':[binding]}},
            {'id':50,'slug':'fixture-game','released':'2024-01-01','metacritic':90}])
        rows=[{'igdb_id':10,'igdb_slug':'game-10','year':'2024'}]
        self.assertEqual(await source.critic_scores(rows),{10:90})
        self.assertEqual(await source.critic_scores(rows),{10:90})
        self.assertEqual(source.request.await_count,2)
        with self.assertRaisesRegex(ProviderError,'invalid_request'):
            await source.cache.get('wikidata','game_'+('x'*25),'invalid',AsyncMock())

    async def test_game_browse_projects_only_displayed_private_state_and_shared_public_pages(self):
        from core import game_browse
        from core.catalogue_providers import ProviderError
        async with self.Session() as db:
            db.add(CatalogueIdentity(entity_id=self.base,namespace='igdb.game',external_id='119133',source='igdb'))
            await db.commit()
        await self.save(status='playing',score=8,notes='Private note',total_seconds=12345)
        page={'rows':[{'igdb_id':119133,'type':'game','title':'Base','identity_evidence':{'private':'no'},'platform_ids':[]},
            {'igdb_id':999,'type':'game','title':'Remote'}],'has_more':False,'cursor':None,'partial':False}
        service=game_browse.GameBrowse()
        service.page=AsyncMock(return_value=page)
        with patch.object(game_browse,'GameBrowse',return_value=service),patch.object(game_browse,'filters',AsyncMock(return_value={})):
            own=await self.client.get('/tracking/games/browse')
            self.assertEqual(own.status_code,200,own.text)
            self.assertEqual([(r['id'],r['list_status'],r['score']) for r in own.json()['results']],[(self.base,'playing',8),(None,None,None)])
            for excluded in ['Private note','total_seconds','entry','identity_evidence','platform_ids']:
                self.assertNotIn(excluded,own.text)
            self.viewer=self.other
            other=await self.client.get('/tracking/games/browse')
            self.assertIsNone(other.json()['results'][0]['list_status'])
            self.viewer=None
            anonymous=await self.client.get('/tracking/games/browse')
            self.assertEqual(anonymous.status_code,401)
            async with self.Session() as db:
                global_settings=await db.get(GlobalSettings,1);global_settings.enable_logged_out_navigation=False;await db.commit()
            self.assertEqual((await self.client.get('/tracking/games/browse')).status_code,401)
            self.viewer=self.user
            for error,status in [('cursor_expired',409),('invalid_cursor',422),('unavailable',503)]:
                service.page.side_effect=ProviderError(error)
                self.assertEqual((await self.client.get('/tracking/games/browse')).status_code,status)

    async def test_game_search_maps_identities_without_creating_entries_and_resolution_rechecks_type(self):
        from core import game_discovery,game_metadata
        from core.catalogue_providers import ProviderError
        async with self.Session() as db:
            db.add(CatalogueIdentity(entity_id=self.base, namespace='igdb.game', external_id='119133', source='igdb'))
            await db.commit()
        candidates = [dict(external_id='119133', name='Base', game_class='game'),
                      dict(external_id='240009', name='Erdtree', game_class='expansion')]
        with patch.object(game_discovery, 'candidates', AsyncMock(return_value=candidates)):
            result = await self.client.get('/tracking/games/search?q=Elden')
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual([(row['id'], row['igdb_id']) for row in result.json()['results']], [(self.base, 119133), (None, 240009)])
        with patch.object(game_metadata.GameMetadata, 'resolve', AsyncMock(return_value=self.base)) as resolve:
            result = await self.client.post('/tracking/games/resolve/900')
            self.assertEqual(result.json(), {'id': self.base})
            self.assertEqual(resolve.call_args.args[-1], '900')
        self.assertFalse((await self.client.get(f'/tracking/games/works/{self.base}')).json()['listed'])
        with patch.object(game_metadata.GameMetadata, 'resolve', AsyncMock(return_value=self.pack)):
            self.assertEqual((await self.client.post('/tracking/games/resolve/901')).status_code, 422)
        with patch.object(game_metadata.GameMetadata, 'resolve', AsyncMock(side_effect=ProviderError('not_configured'))):
            self.assertEqual((await self.client.post('/tracking/games/resolve/902')).status_code, 503)
        self.assertEqual((await self.client.get('/tracking/games/search?q=%20%20')).status_code, 422)
        self.viewer = None
        with patch.object(game_metadata.GameMetadata,'resolve',AsyncMock(return_value=self.base)) as resolve:
            self.assertEqual((await self.client.post('/tracking/games/resolve/900')).status_code,401)
            resolve.assert_not_awaited()
            async with self.Session() as db:
                self.assertEqual(await db.scalar(select(func.count()).select_from(GameEntry)),0)
                global_settings=await db.get(GlobalSettings,1)
                global_settings.enable_logged_out_navigation=False
                await db.commit()
            self.assertEqual((await self.client.post('/tracking/games/resolve/900')).status_code,401)
            self.assertEqual(resolve.await_count,0)

    async def test_completion_snapshot_cumulative_time_replays_and_unknown_dates(self):
        row = await self.save(status='completed', total_seconds=36000, replay_count=3)
        self.assertEqual(row['finish_date'], date.today().isoformat())
        self.assertEqual(row['completion_seconds'], 36000)
        self.assertTrue(row['completion_comparison_eligible'])
        updated = await self.save(revision=row['revision'], total_seconds=90000, replay_count=7)
        self.assertEqual(updated['status'], 'completed')
        self.assertEqual(updated['completion_seconds'], 36000)
        invalid = await self.save(revision=updated['revision'], total_seconds=100)
        self.assertEqual(invalid['completion_seconds'], 36000)
        self.assertFalse(invalid['completion_comparison_eligible'])
        historical = await self.save(self.unknown, status='completed', finish_date=None, total_seconds=8000)
        self.assertIsNone(historical['finish_date'])
        self.assertIsNone(historical['completion_seconds'])
        known = await self.save(self.unknown, revision=historical['revision'], completion_seconds=7000)
        self.assertFalse(known['completion_comparison_eligible'])
        changed = await self.save(self.unknown, revision=known['revision'], status='playing')
        self.assertEqual(changed['completion_seconds'], 7000)
        refused = await self.client.patch(f'/tracking/games/entries/{self.unknown}', json={
            'revision': changed['revision'], 'completion_seconds': 9000})
        self.assertEqual(refused.status_code, 422)

    async def test_first_completion_prefills_empty_total_and_stale_edits_rejected(self):
        row = await self.save(status='completed', completion_seconds=0)
        self.assertEqual(row['total_seconds'], 0)
        self.assertEqual(row['completion_seconds'], 0)
        stale = await self.client.patch(f'/tracking/games/entries/{self.base}', json={'revision': 10, 'score': 9})
        self.assertEqual(stale.status_code, 409)
        missing = await self.client.patch(f'/tracking/games/entries/{self.base}', json={'score': 9})
        self.assertEqual(missing.status_code, 409)

    async def test_favorite_does_not_require_membership_and_editor_save_is_atomic(self):
        favorite = await self.client.put(f'/tracking/games/favorites/{self.base}', json={'favorite': True})
        self.assertEqual(favorite.status_code, 200, favorite.text)
        self.assertFalse(favorite.json()['listed'])
        self.assertTrue(favorite.json()['favorite'])
        row = await self.save(status='playing', favorite=False)
        self.assertFalse(row['favorite'])
        row = await self.save(revision=row['revision'], favorite=True, score=8)
        self.assertTrue(row['favorite'])
        self.assertEqual(row['score'], 8)
        await self.client.delete(f'/tracking/games/entries/{self.base}?revision={row["revision"]}&confirmed=true')
        state = (await self.client.get(f'/tracking/games/works/{self.base}')).json()
        self.assertFalse(state['favorite'])
        self.assertFalse(state['listed'])

    async def test_expansion_requires_base_and_retains_unlisted_private_state(self):
        path = f'/tracking/games/children/{self.child}'
        blocked = await self.client.patch(path, json={'status': 'completed'})
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()['detail']['code'], 'base_game_required')
        empty = await self.client.get('/tracking/games/users/player/list')
        self.assertEqual(empty.json()['total'], 0)
        base = await self.save(status='playing', score=7)
        child = await self.client.patch(path, json={'status': 'completed', 'score': 9})
        self.assertEqual(child.status_code, 200, child.text)
        self.assertTrue(child.json()['listed'])
        self.assertIsNone(child.json()['total_seconds'])
        rows = (await self.client.get('/tracking/games/users/player/list?sort=score')).json()
        self.assertEqual([r['score'] for r in rows['results']], [9, 7])
        removed = await self.client.delete(f'/tracking/games/entries/{self.base}?revision={base["revision"]}&confirmed=true')
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertEqual((await self.client.get('/tracking/games/users/player/list')).json()['total'], 0)
        retained = (await self.client.get(f'/tracking/games/works/{self.child}')).json()
        self.assertEqual(retained['score'], 9)
        self.assertFalse(retained['listed'])
        await self.save(status='planning')
        self.assertEqual((await self.client.get('/tracking/games/users/player/list')).json()['total'], 1)
        restored = await self.client.patch(path, json={'revision': retained['revision']})
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(restored.json()['score'], 9)
        self.assertEqual(restored.json()['status'], 'completed')

    async def test_child_cannot_write_hours_or_change_parent_rating(self):
        row = await self.save(score=6)
        bad = await self.client.patch(f'/tracking/games/children/{self.child}', json={'total_seconds': 100})
        self.assertEqual(bad.status_code, 422)
        dlc = await self.client.patch(f'/tracking/games/children/{self.dlc}?listed=false', json={'status': 'completed', 'score': 10})
        self.assertEqual(dlc.status_code, 200, dlc.text)
        self.assertFalse(dlc.json()['listed'])
        self.assertEqual((await self.client.get(f'/tracking/games/works/{self.base}')).json()['score'], row['score'])
        for work_id in (self.child, self.dlc, self.pack):
            bad = await self.client.patch(f'/tracking/games/entries/{work_id}', json={})
            self.assertEqual(bad.status_code, 422)

    async def test_optional_achievements_unknown_explicit_zero_and_mode_switch_evidence(self):
        row = await self.save()
        self.assertIsNone(row['achievements']['percentage'])
        row = await self.save(revision=row['revision'], achievement_mode='targeted', expected_achievement_total=3,
                              unlocked_ids=self.achievement_ids[:2])
        self.assertEqual(row['achievements']['count'], 2)
        response = await self.client.patch(f'/tracking/games/entries/{self.base}', json={
            'revision': row['revision'], 'achievement_mode': 'count', 'achievement_count': 1, 'expected_achievement_total': 3})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()['detail']['code'], 'confirm_simplified_tracking')
        row = await self.save(revision=row['revision'], achievement_mode='count', achievement_count=0,
                              expected_achievement_total=3, confirm_simplified=True)
        self.assertEqual(row['achievements']['percentage'], 0)
        self.assertEqual(row['achievements']['exact_count'], 2)
        exact = (await self.client.get(f'/tracking/games/works/{self.base}/achievements')).json()
        self.assertEqual(sum(a['unlocked'] for a in exact['results']), 2)
        row = await self.save(revision=row['revision'], achievement_mode='targeted', expected_achievement_total=3)
        self.assertEqual(row['achievements']['count'], 2)
        unknown = await self.save(self.unknown, achievement_mode='percentage', achievement_percentage=0)
        self.assertEqual(unknown['achievements']['percentage'], 0)
        self.assertIsNone(unknown['achievements']['count'])
        bad = await self.client.patch(f'/tracking/games/entries/{self.unknown}', json={
            'revision': unknown['revision'], 'achievement_mode': 'count', 'achievement_count': 1})
        self.assertEqual(bad.status_code, 409)

    async def test_achievement_bounds_stale_catalogue_and_cross_work_selection(self):
        row = await self.save()
        for payload, code in (({'achievement_mode': 'count', 'achievement_count': 4, 'expected_achievement_total': 3}, 422),
                              ({'achievement_mode': 'count', 'achievement_count': 1, 'expected_achievement_total': 2}, 409),
                              ({'achievement_mode': 'targeted', 'unlocked_ids': [999999], 'expected_achievement_total': 3}, 422)):
            r = await self.client.patch(f'/tracking/games/entries/{self.base}', json={'revision': row['revision'], **payload})
            self.assertEqual(r.status_code, code, r.text)
        unchanged = (await self.client.get(f'/tracking/games/works/{self.base}')).json()
        self.assertEqual(unchanged['revision'], row['revision'])
        self.assertIsNone(unchanged['achievements']['mode'])

    async def test_incremental_achievement_updates_preserve_unloaded_evidence(self):
        row = await self.save(achievement_mode='targeted', expected_achievement_total=3,
                              unlocked_ids=self.achievement_ids[:2])
        row = await self.save(revision=row['revision'], achievement_mode='targeted', expected_achievement_total=3,
                              unlock_updates={str(self.achievement_ids[2]): True})
        self.assertEqual(row['achievements']['exact_count'], 3)
        row = await self.save(revision=row['revision'], achievement_mode='targeted', expected_achievement_total=3,
                              unlock_updates={str(self.achievement_ids[0]): False})
        self.assertEqual(row['achievements']['exact_count'], 2)
        page = (await self.client.get(f'/tracking/games/works/{self.base}/achievements?limit=1&offset=1')).json()
        self.assertTrue(page['results'][0]['unlocked'])

    async def test_game_details_include_only_current_viewers_tracking_and_verified_base_link(self):
        from core.media_details import catalogue_title
        await self.save(notes='Owner only', status='playing')
        async with self.Session() as db:
            entity = await db.get(CatalogueEntity, self.base)
            entity.attributes={**entity.attributes,'platforms':[{'id':'6','name':'Windows'},{'id':'14','name':'Mac'},
                {'id':'3','name':'Linux'},{'id':'7','name':'PlayStation'}]}
            own = await catalogue_title(db, entity, self.user.id)
            self.assertEqual(own['platforms'],['PC','PlayStation'])
            other = await catalogue_title(db, entity, self.other.id)
            child = await catalogue_title(db, await db.get(CatalogueEntity, self.child), self.user.id)
        self.assertEqual(own['entry']['notes'], 'Owner only')
        self.assertIsNone(other['entry'])
        self.assertEqual(own['subject'], {'kind': 'game', 'catalogue_id': self.base})
        self.assertEqual(child['base_game']['id'], self.base)
        self.assertIsNone(child['entry'])

    async def test_game_and_expansion_share_cards_require_approval(self):
        from core.share_preview import card_data
        await self.save(notes='Never in a preview',score=9)
        async with self.Session() as db:
            for identifier in (self.base,self.child,self.pack):
                with self.assertRaises(HTTPException) as denied:
                    await card_data(db,f'/games/{identifier}')
                self.assertEqual(denied.exception.status_code,403)

    async def test_anonymous_game_details_are_restricted_without_changing_public_navigation(self):
        await self.save(notes='Private game note',status='playing')
        self.viewer=None
        access=await self.client.get('/profile/public-access-status')
        self.assertTrue(access.json()['enable_logged_out_navigation'])
        response=await self.client.get(f'/catalogue/entities/{self.base}/details')
        self.assertEqual(response.status_code,403,response.text)
        self.assertNotIn('Private game note',response.text)
        self.assertEqual((await self.client.get('/tracking/people/player/header')).status_code,200)

    async def test_game_list_pages_filter_the_whole_collection_and_exclude_retained_expansions(self):
        await self.save(status='completed', score=9)
        child = await self.client.patch(f'/tracking/games/children/{self.child}', json={'status': 'paused', 'score': 8})
        self.assertEqual(child.status_code, 200, child.text)
        async with self.Session() as db:
            for index in range(65):
                game = CatalogueEntity(kind='game', name=f'Library {index:03}', attributes={
                    'game_class': 'game', 'release_date': '2020-01-01', 'genres': ['Adventure']})
                db.add(game)
                await db.flush()
                db.add(GameEntry(user_id=self.user.id, work_id=game.id, status='playing', total_seconds=index * 3600))
            late = CatalogueEntity(kind='game', name='Zulu % game', attributes={
                'game_class': 'game', 'release_date': '2024-01-01', 'genres': ['Role-playing (RPG)']})
            db.add(late)
            await db.flush()
            db.add(GameEntry(user_id=self.user.id, work_id=late.id, status='planning'))
            await db.commit()
            late_id = late.id
        first = (await self.client.get('/tracking/games/users/player/list?limit=50')).json()
        second = (await self.client.get('/tracking/games/users/player/list?limit=50&offset=50')).json()
        self.assertEqual(first['total'], 68)
        self.assertEqual(len(first['results']), 50)
        self.assertEqual(len(second['results']), 18)
        self.assertFalse({r['subject']['catalogue_id'] for r in first['results']} & {r['subject']['catalogue_id'] for r in second['results']})
        self.assertEqual(first['status_counts']['paused'], 1)
        self.assertEqual(first['status_counts']['playing'], 65)
        self.assertEqual(first['years'], ['2024', '2020'])
        self.assertEqual(first['genres'], ['Adventure', 'Role-playing (RPG)'])
        self.assertTrue(first['profile']['owner'])
        filtered = (await self.client.get('/tracking/games/users/player/list', params={
            'q': '%', 'genre': 'Role-playing (RPG)', 'year': '2024', 'status': 'planning', 'limit': 1})).json()
        self.assertEqual(filtered['total'], 1)
        self.assertEqual(filtered['results'][0]['subject']['catalogue_id'], late_id)
        self.assertEqual(filtered['results'][0]['year'], '2024')
        self.assertEqual((await self.client.get('/tracking/games/users/player/list?year=bad')).status_code, 422)
        async with self.Session() as db:
            base = await db.scalar(select(GameEntry).where(GameEntry.user_id == self.user.id, GameEntry.work_id == self.base))
            revision = base.revision
        await self.client.delete(f'/tracking/games/entries/{self.base}?confirmed=true&revision={revision}')
        after = (await self.client.get('/tracking/games/users/player/list')).json()
        self.assertEqual(after['total'], 66)
        self.assertNotIn('paused', after['status_counts'])

    async def test_currently_playing_shelf_is_bounded_and_owner_scoped(self):
        await self.save(status='playing', total_seconds=3600)
        child = await self.client.patch(f'/tracking/games/children/{self.child}', json={'status': 'playing'})
        self.assertEqual(child.status_code, 200)
        shelf = await self.client.get('/tracking/games/playing?limit=1')
        self.assertEqual(shelf.status_code, 200, shelf.text)
        self.assertEqual(shelf.json()['total'], 1)
        self.assertEqual(shelf.json()['results'][0]['subject']['catalogue_id'], self.base)
        self.viewer = self.other
        self.assertEqual((await self.client.get('/tracking/games/playing')).json(), {'results': [], 'total': 0})
        self.viewer = None
        self.assertEqual((await self.client.get('/tracking/games/playing')).status_code, 401)

    async def test_favorites_without_membership_have_private_safe_pages_and_independent_order(self):
        async with self.Session() as db:
            extras = [CatalogueEntity(kind='game', name=f'Favorite {index}', attributes={'game_class': 'game'}) for index in range(3)]
            db.add_all(extras)
            await db.commit()
            extra_ids = [game.id for game in extras]
            db.add(GameFavorite(user_id=self.other.id, work_id=extra_ids[-1], position=0))
            await db.commit()
        identifiers = [self.base, self.unknown, *extra_ids[:2]]
        for identifier in identifiers:
            response = await self.client.put(f'/tracking/games/favorites/{identifier}', json={'favorite': True})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertFalse(response.json()['listed'])
        self.assertEqual((await self.client.get('/tracking/games/users/player/list')).json()['total'], 0)
        response = await self.client.put('/tracking/games/favorites/order', json={'work_ids': identifiers[:2][::-1]})
        self.assertEqual(response.status_code, 200, response.text)
        page = (await self.client.get('/tracking/games/users/player/favorites?limit=2')).json()
        self.assertEqual(page['total'], 4)
        self.assertEqual([row['subject']['catalogue_id'] for row in page['results']], identifiers[:2][::-1])
        self.assertEqual([row['subject']['catalogue_id'] for row in (await self.client.get('/tracking/games/users/player/favorites?limit=2&offset=2')).json()['results']], identifiers[2:])
        self.assertTrue(all(row['status'] is None and row['achievements']['mode'] is None for row in page['results']))
        for payload, status in (([identifiers[0], identifiers[0]], 422), ([extra_ids[-1]], 409)):
            response = await self.client.put('/tracking/games/favorites/order', json={'work_ids': payload})
            self.assertEqual(response.status_code, status, response.text)
        self.viewer = self.other
        await self.client.patch('/tracking/preferences', json={'show_games': False})
        public = await self.client.get('/tracking/games/users/player/favorites')
        self.assertEqual(public.status_code, 200)
        self.assertFalse(public.json()['profile']['owner'])
        self.assertTrue(all(row['revision'] is None and row['notes'] is None for row in public.json()['results']))
        self.viewer = None
        self.assertEqual((await self.client.get('/tracking/people/player/header')).status_code, 200)
        self.assertEqual((await self.client.get('/tracking/people/player')).status_code, 200)
        self.assertEqual((await self.client.get('/tracking/games/users/player/favorites')).status_code, 401)
        async with self.Session() as db:
            profile = await db.scalar(select(UserProfileData).where(UserProfileData.user_id == self.user.id))
            profile.privacy_level = PrivacyLevel.private
            await db.commit()
        self.assertEqual((await self.client.get('/tracking/games/users/player/favorites')).status_code, 401)
        self.assertEqual((await self.client.get('/tracking/people/player/header')).status_code, 403)
        keyed = await self.client.get('/tracking/games/users/player/favorites', headers={'X-Api-Key': self.user.api_key})
        self.assertEqual(keyed.status_code, 200, keyed.text)
        self.assertTrue(keyed.json()['profile']['owner'])

    async def test_concurrent_favorites_and_legacy_position_backfill(self):
        responses = await asyncio.gather(*(self.client.put(f'/tracking/games/favorites/{identifier}', json={'favorite': True}) for identifier in (self.base, self.unknown)))
        self.assertTrue(all(response.status_code == 200 for response in responses))
        async with self.Session() as db:
            positions = list((await db.scalars(select(GameFavorite.position).where(GameFavorite.user_id == self.user.id))).all())
            self.assertEqual(sorted(positions), [0, 1])
        migration = importlib.import_module('migrations.versions.mt059_game_favorite_order')
        def legacy_round_trip(conn):
            with Operations.context(MigrationContext.configure(conn)):
                migration.downgrade()
                migration.upgrade()
        async with self.engine.begin() as conn:
            await conn.run_sync(legacy_round_trip)
        async with self.Session() as db:
            rows = (await db.execute(select(GameFavorite.work_id, GameFavorite.position).where(GameFavorite.user_id == self.user.id).order_by(GameFavorite.position))).all()
            self.assertEqual(rows, [(self.base, 0), (self.unknown, 1)])
        swapped = await self.client.put('/tracking/games/favorites/order', json={'work_ids': [self.unknown, self.base]})
        self.assertEqual(swapped.status_code, 200, swapped.text)

    async def test_public_game_activity_omits_notes_and_preserves_fixed_windows_and_achievement_unknowns(self):
        row = await self.save(status='playing', total_seconds=3600, notes='Never public notes',
            achievement_mode='targeted', expected_achievement_total=3, unlocked_ids=self.achievement_ids[:1])
        first = (await self.client.get('/tracking/games/users/player/activity')).json()['results']
        self.assertEqual(len(first), 1)
        self.assertTrue(first[0]['key'].startswith('game:'))
        self.assertNotIn('Never public notes', str(first))
        self.assertNotIn('changed', first[0]['payload'])
        self.assertAlmostEqual(first[0]['payload']['achievement_percentage'], 100/3)
        row = await self.save(revision=row['revision'], notes='Another private note')
        unchanged = (await self.client.get('/tracking/games/users/player/activity')).json()['results']
        self.assertEqual(unchanged, first)
        async with self.Session() as db:
            activity = await db.scalar(select(GameActivity).where(GameActivity.user_id == self.user.id))
            activity.payload = {**activity.payload, 'window_started_at': (datetime.now(timezone.utc).replace(tzinfo=None)-timedelta(hours=25)).isoformat()}
            # A recent edit cannot extend a window whose first event is old.
            activity.created_at = datetime.now(timezone.utc).replace(tzinfo=None)
            await db.commit()
        row = await self.save(revision=row['revision'], total_seconds=7200)
        current = (await self.client.get('/tracking/games/users/player/activity')).json()['results']
        self.assertEqual(len(current), 2)
        self.assertTrue(current[0]['payload']['hours_changed'])
        self.assertFalse(current[0]['payload']['achievements_changed'])
        self.assertIsNone(current[0]['payload'].get('achievement_percentage'))
        async with self.Session() as db:
            db.add_all([GameActivity(user_id=self.user.id, work_id=self.base, status='playing', payload={'changed': ['notes']}) for _ in range(70)])
            await db.commit()
        self.assertEqual(len((await self.client.get('/tracking/games/users/player/activity')).json()['results']), 2)

    async def test_game_following_and_social_enforce_privacy_and_exclude_unlisted_children(self):
        row = await self.save(status='completed', score=9, total_seconds=0)
        child = await self.client.patch(f'/tracking/games/children/{self.child}', json={'status': 'completed', 'score': 8})
        self.assertEqual(child.status_code, 200, child.text)
        async with self.Session() as db:
            db.add(Follow(follower_id=self.other.id, following_id=self.user.id))
            await db.commit()
        self.viewer = self.other
        feed = (await self.client.get('/tracking/games/activity')).json()['results']
        self.assertEqual({row['media']['id'] for row in feed}, {self.base, self.child})
        social = (await self.client.get(f'/tracking/games/works/{self.base}/social')).json()
        self.assertEqual([person['username'] for person in social['people']], ['player'])
        self.assertEqual(social['people'][0]['total_seconds'], 0)
        self.viewer = self.user
        row = await self.save(revision=row['revision'], total_seconds=7200)
        self.viewer = self.other
        social = (await self.client.get(f'/tracking/games/works/{self.base}/social')).json()
        self.assertEqual(social['people'][0]['total_seconds'], 7200)
        self.assertEqual(social['activities'], [])
        self.assertEqual([row['user_id'] for row in social['following_activities']], [self.user.id])
        async with self.Session() as db:
            profile = await db.scalar(select(UserProfileData).where(UserProfileData.user_id == self.user.id))
            profile.privacy_level = PrivacyLevel.private
            await db.commit()
        self.assertEqual((await self.client.get('/tracking/games/activity')).json()['results'], [])
        private_social = (await self.client.get(f'/tracking/games/works/{self.base}/social')).json()
        self.assertEqual(private_social['people'], [])
        self.assertEqual(private_social['following_activities'], [])
        self.assertEqual((await self.client.get('/tracking/games/users/player/activity')).status_code, 403)
        self.viewer = self.user
        own = (await self.client.get(f'/tracking/games/works/{self.base}/social')).json()
        self.assertEqual(own['people'][0]['self'], True)
        self.assertEqual([row['user_id'] for row in own['activities']], [self.user.id])
        self.assertEqual(own['following_activities'], [])
        removed = await self.client.delete(f'/tracking/games/children/{self.child}?revision={child.json()["revision"]}')
        self.assertEqual(removed.status_code, 200)
        visible = (await self.client.get('/tracking/games/users/player/activity')).json()['results']
        self.assertEqual([row['media']['id'] for row in visible], [self.base])

    async def test_late_first_completion_fact_reprojects_old_activity_without_replacing_it(self):
        row=await self.save(status='completed',finish_date='2000-01-01',total_seconds=36000)
        async with self.Session() as db:
            activity=await db.scalar(select(GameActivity).where(GameActivity.work_id==self.base))
            activity.created_at=datetime.now(timezone.utc).replace(tzinfo=None)-timedelta(days=2)
            await db.commit()
        path='/tracking/games/users/player/activity'
        original=(await self.client.get(path)).json()['results']
        self.assertIsNone(original[0]['payload']['completion_seconds'])
        for seconds in (7200,3600,0,None):
            row=await self.save(revision=row['revision'],completion_seconds=seconds)
            current=(await self.client.get(path)).json()['results']
            self.assertEqual(len(current),1)
            self.assertEqual(current[0]['key'],original[0]['key'])
            self.assertEqual(current[0]['created_at'],original[0]['created_at'])
            self.assertEqual(current[0]['payload']['completion_seconds'],seconds)
            self.assertEqual(row['total_seconds'],36000)
            social=(await self.client.get(f'/tracking/games/works/{self.base}/social')).json()
            self.assertEqual(social['activities'][0]['payload']['completion_seconds'],seconds)

    async def test_details_retain_complete_gallery_and_protected_primary_choice(self):
        rows=[f'https://images.igdb.com/igdb/image/upload/t_thumb/sc{i}.jpg' for i in range(23)]
        selected=rows[-1]
        async with self.Session() as db:
            work=await db.get(CatalogueEntity,self.base)
            work.attributes={**work.attributes,'artwork':[selected,*rows[:-1]],'scene_artwork':rows,'artwork_policy_version':1}
            work.protected_fields=['attributes.artwork']
            await db.commit()
        response=await self.client.get(f'/catalogue/entities/{self.base}/details')
        self.assertEqual(response.status_code,200,response.text)
        data=response.json()
        self.assertEqual(data['backdrop'],selected)
        self.assertEqual(len(data['artwork']),23)
        self.assertEqual(set(data['artwork']),set(rows))
        self.assertEqual(data['artwork_preview']['image'],rows[0])

    async def test_visibility_does_not_block_direct_links_or_public_profiles_and_notes_stay_private(self):
        await self.save(notes='Private note', status='playing')
        prefs = await self.client.patch('/tracking/preferences', json={'show_games': False, 'show_movies_series': False})
        self.assertEqual(prefs.status_code, 200, prefs.text)
        self.assertFalse(prefs.json()['show_games'])
        self.assertTrue(prefs.json()['combine_lists'])
        self.assertEqual((await self.client.get(f'/tracking/games/works/{self.base}')).json()['notes'], 'Private note')
        self.viewer = self.other
        public = await self.client.get('/tracking/games/users/player/list')
        self.assertEqual(public.status_code, 200)
        self.assertEqual(public.json()['total'], 1)
        self.assertIsNone(public.json()['results'][0]['notes'])
        self.assertIsNone(public.json()['results'][0]['revision'])
        async with self.Session() as db:
            profile = await db.get(UserProfileData, self.user.id)
            # The profile key is not user_id in all schema generations.
            if profile is None:
                profile = await db.scalar(select(UserProfileData).where(UserProfileData.user_id == self.user.id))
            profile.privacy_level = PrivacyLevel.private
            await db.commit()
        self.assertEqual((await self.client.get('/tracking/games/users/player/list')).status_code, 403)
        self.viewer = None
        self.assertEqual((await self.client.get('/tracking/games/users/player/list')).status_code, 401)

    async def test_database_rejects_cross_owner_or_wrong_work_children_and_independent_packs(self):
        await self.save()
        async with self.Session() as db:
            for values in ({'user_id': self.other.id, 'work_id': self.child, 'base_work_id': self.base, 'base_entry_id': 1},
                           {'user_id': self.user.id, 'work_id': self.child, 'base_work_id': self.unknown, 'base_entry_id': 1}):
                actual_id = await db.scalar(select(GameEntry.id).where(GameEntry.work_id == self.base))
                values['base_entry_id'] = actual_id
                db.add(GameChildState(**values))
                with self.assertRaises(IntegrityError):
                    await db.commit()
                await db.rollback()
            db.add(GameEntry(user_id=self.user.id, work_id=self.pack))
            with self.assertRaises(IntegrityError):
                await db.commit()
            await db.rollback()

    async def test_concurrent_base_removal_and_child_save_cannot_leave_orphan_membership(self):
        base = await self.save()

        async def save_child():
            async with self.Session() as db:
                try:
                    return await game_tracking.save_child(db, self.user.id, self.child, ChildPatch(status='completed'), listed=True)
                except HTTPException as error:
                    self.assertEqual(error.status_code, 409)

        async def remove_base():
            async with self.Session() as db:
                await game_tracking.remove_entry(db, self.user.id, self.base, base['revision'])

        await asyncio.gather(save_child(), remove_base())
        async with self.Session() as db:
            self.assertIsNone(await db.scalar(select(GameEntry).where(GameEntry.work_id == self.base)))
            child = await db.scalar(select(GameChildState).where(GameChildState.work_id == self.child))
            if child:
                self.assertIsNone(child.base_entry_id)

    async def test_migration_round_trip(self):
        migration = importlib.import_module('migrations.versions.mt058_games_tracking')
        favorites = importlib.import_module('migrations.versions.mt059_game_favorite_order')
        statistics = importlib.import_module('migrations.versions.mt066_game_statistics')
        achievements = importlib.import_module('migrations.versions.mt069_game_achievement_catalogue')
        def round_trip(conn):
            with Operations.context(MigrationContext.configure(conn)):
                achievements.downgrade()
                statistics.downgrade()
                favorites.downgrade()
                migration.downgrade()
                migration.upgrade()
                favorites.upgrade()
                statistics.upgrade()
                achievements.upgrade()
        async with self.engine.begin() as conn:
            await conn.run_sync(round_trip)
        self.assertEqual((await self.client.get('/tracking/preferences')).status_code, 200)
