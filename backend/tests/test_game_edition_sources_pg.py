"""Game details reuse explicitly linked edition sources without rebinding games."""
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import test_games_pg as fixture
from core import catalogue
from core.catalogue_providers import CatalogueProviders
from core.game_edition_sources import WORKS_BUNDLE_ATTRIBUTE, edition_sources, steam_store
from core.game_metadata import request as request_metadata
from core.game_imdb_enrichment import now
from core.game_time_to_beat import GameTimeToBeat, JOB as ESTIMATE_JOB, snapshot
from core.steam_targets import work_map
from models.catalogue import CatalogueEntity, CatalogueIdentity, MetadataSnapshot, SteamPriceSnapshot
from models.games import GameAchievement, GameAchievementUnlock, GameEntry


APP = '3240220'
STORE_URL = f'https://store.steampowered.com/app/{APP}/'


def enhanced():
    return {
        'id': 334254, 'name': 'Fixture Enhanced', 'game_type': {'id': 10},
        'parent_game': {'id': 1020, 'name': 'Fixture Original', 'game_type': {'id': 0}},
        'websites': [{'url': STORE_URL}],
        'bundles': [{'id': 334647, 'name': 'Fixture Enhanced', 'game_type': {'id': 3},
                     'external_games': [{'uid': APP, 'url': STORE_URL, 'external_game_source': {'name': 'Steam'}}]}],
    }


class EditionEvidenceTests(unittest.TestCase):
    def test_unconfirmed_or_ambiguous_store_references_do_not_select_a_bundle(self):
        raw = enhanced()
        raw['websites'] = [{'url': 'https://store.steampowered.com/app/271590/'}]
        self.assertIsNone(edition_sources(raw)[1])
        raw = enhanced()
        raw['bundles'].append({**raw['bundles'][0], 'id': 334648})
        self.assertIsNone(edition_sources(raw)[1])


class WorksBundleEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def test_full_detail_verifies_one_game_family_after_primary_is_ready(self):
        edition = enhanced()
        premium = {'id': 98077, 'name': 'Fixture Premium Edition', 'game_type': {'id': 3}, 'version_parent': 1020}
        unrelated = {'id': 990, 'name': 'Another Game', 'game_type': {'id': 0}}
        ambiguous = {**edition, 'id': 334600}
        structured = {**ambiguous, 'websites': [], 'external_games': [
            {'uid': APP, 'external_game_source': {'name': 'Steam'}},
        ]}
        incomplete = {**ambiguous, 'websites': [{'missing_url': True}]}

        for other, expected in ((premium, '334647'), (unrelated, None), (ambiguous, None),
                (structured, None), (incomplete, None)):
            providers = CatalogueProviders()
            primary = AsyncMock()

            async def fetch(endpoint, query):
                if endpoint == 'characters':
                    return []
                if 'where bundles=' in query:
                    primary.assert_awaited_once()
                    self.assertIn('where bundles=(334647)', query)
                    return [edition, other]
                return [edition]

            providers.igdb = AsyncMock(side_effect=fetch)
            doc, _ = await providers._detail('igdb', 'game', 334254, on_primary=primary)
            proof = doc['work']['attributes'][WORKS_BUNDLE_ATTRIBUTE]
            self.assertEqual(proof.get('bundle_id'), expected)
            self.assertNotIn(WORKS_BUNDLE_ATTRIBUTE, primary.call_args.args[0]['work']['attributes'])
            self.assertEqual(providers.igdb.await_count, 3)

    async def test_primary_only_skips_bundle_membership_request(self):
        providers = CatalogueProviders()
        providers.igdb = AsyncMock(return_value=[enhanced()])
        doc, _ = await providers._detail('igdb', 'game', 334254, primary_only=True)
        providers.igdb.assert_awaited_once()
        self.assertNotIn(WORKS_BUNDLE_ATTRIBUTE, doc['work']['attributes'])

    async def test_halo_bundle_has_no_redundancy_probe(self):
        providers = CatalogueProviders()
        halo = {'id':7348,'name':'Halo: The Master Chief Collection','game_type':{'id':3}}
        providers.igdb = AsyncMock(side_effect=[[halo], []])
        doc, _ = await providers._detail('igdb','game',7348)
        self.assertEqual(providers.igdb.await_count,2)
        self.assertNotIn(WORKS_BUNDLE_ATTRIBUTE, doc['work']['attributes'])


@unittest.skipUnless(fixture.URL, 'Requires explicit disposable Games PostgreSQL')
class EditionSourceTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.GamesDatabaseTests.asyncSetUp
    cleanup = fixture.GamesDatabaseTests.cleanup

    async def publish_edition(self):
        providers = CatalogueProviders()
        providers.igdb = AsyncMock(return_value=[enhanced()])
        doc, _ = await providers._detail('igdb', 'game', 334254, primary_only=True)
        self.assertIn('bundles.external_games.uid', providers.igdb.call_args.args[1])
        async with self.Session() as db:
            work = await catalogue.ingest_document(db, doc, 'igdb')
            await db.commit()
            return work.id

    async def test_public_bundle_url_uses_edition_completion_estimates(self):
        from unittest.mock import patch
        from core import steam_sync
        from core.game_edition_sources import BUNDLE_ALIAS
        from core.game_metadata import GameMetadata
        from sqlalchemy import select

        edition = enhanced()
        other = {'id':98077,'name':'Premium Edition','game_type':{'id':3},'version_parent':1020}
        providers = CatalogueProviders()
        providers.igdb = AsyncMock(side_effect=[[edition], [], [edition, other]])
        doc, _ = await providers._detail('igdb','game',334254)
        async with self.Session() as db:
            work = await catalogue.ingest_document(db,doc,'igdb')
            work_id = work.id
            original = await catalogue.resolve_identity(db,'igdb.game','1020')
            original = await db.get(CatalogueEntity,original.entity_id)
            catalogue.merge_fields(original,{'attributes':{'completion_estimates':{
                'source':'igdb','source_game_id':'1020','main_story_seconds':7200,
            }}},'igdb')
            bundle = work.attributes[BUNDLE_ALIAS]['work_id']
            await db.commit()
        with (
            patch.object(GameMetadata,'ensure',AsyncMock(return_value=({'primary_ready':True},False))),
            patch('core.game_background.observe_work',AsyncMock()),
        ):
            result = await self.client.get(f'/catalogue/entities/{work_id}/details')
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json()['subject']['catalogue_id'],bundle)
        self.assertEqual(result.json()['completion_estimates']['main_story_seconds'],7200)

        state = SimpleNamespace(user_id=self.user.id, timezone='UTC', checkpoints={}, auto_add_games=False,
                                sync_playtime=True,sync_achievements=False)
        async with self.Session() as db:
            await steam_sync.apply_games(db,state,[{'app_id':APP,'name':edition['name'],'playtime_minutes':120,
                'last_played':0,'poster':None,'release_date':None}],[APP],initial=True)
            self.assertEqual(list(await db.scalars(select(GameEntry.work_id).where(
                GameEntry.user_id==self.user.id))),[bundle])
            self.assertEqual(state.checkpoints[APP]['work_id'],bundle)
            # A backup or an old client can still save to the edition ID.
            # Its owner must keep access, even after public consolidation.
            db.add(GameEntry(user_id=self.user.id,work_id=work_id,status='played'))
            await db.commit()
        with (
            patch.object(GameMetadata,'ensure',AsyncMock(return_value=({'primary_ready':True},False))),
            patch('core.game_background.observe_work',AsyncMock()),
        ):
            private = await self.client.get(f'/catalogue/entities/{work_id}/details')
        self.assertEqual(private.json()['subject']['catalogue_id'],work_id)

    async def detail_read(self, work, endpoint, **params):
        response = await self.client.get(f'/tracking/games/works/{work}/{endpoint}', params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def test_empty_edition_estimates_queue_and_reuse_original_then_yield_to_direct_data(self):
        work = await self.publish_edition()
        edition_source = SimpleNamespace(igdb=AsyncMock(return_value=[{'id': 1, 'game_id': 334254, 'count': 1}]))
        service = GameTimeToBeat(self.Session, source=edition_source)
        self.assertTrue((await self.detail_read(work, 'time-to-beat'))['status']['pending'])
        self.assertEqual(await service.run(work), 'ready')
        self.assertTrue((await self.detail_read(work, 'time-to-beat'))['status']['pending'])
        async with self.Session() as db:
            identity = await catalogue.resolve_identity(db, 'igdb.game', '1020')
            original = identity.entity_id
        edition_source.igdb.return_value = [{'id': 2, 'game_id': 1020, 'normally': 7200, 'count': 10}]
        self.assertEqual(await service.run(original), 'ready')
        result = await self.detail_read(work, 'time-to-beat')
        self.assertTrue(result['status']['complete'])
        self.assertEqual((result['estimates']['source_game_id'], result['estimates']['main_sides_seconds']), ('1020', 7200))

        async with self.Session() as db:
            edition = await db.get(CatalogueEntity, work)
            self.assertIsNone(edition.attributes['completion_estimates']['main_sides_seconds'])
            estimate_job = await db.scalar(snapshot(ESTIMATE_JOB, work))
            estimate_job.next_attempt_at = now()
            await db.commit()
        edition_source.igdb.return_value = [{'id': 1, 'game_id': 334254, 'hastily': 3600}]
        self.assertEqual(await service.run(work), 'ready')
        result = await self.detail_read(work, 'time-to-beat')
        self.assertEqual((result['estimates']['source_game_id'], result['estimates']['main_story_seconds']), ('334254', 3600))
        self.assertIsNone(result['estimates']['main_sides_seconds'])

    async def test_store_sections_share_the_bundle_app_and_preserve_import_routing(self):
        work_id = await self.publish_edition()
        async with self.Session() as db:
            work = await db.get(CatalogueEntity, work_id)
            owner, app = await steam_store(db, work)
            self.assertEqual(app, APP)
            self.assertNotEqual(owner.id, work_id)
            owner_id = owner.id
            self.assertEqual(await work_map(db, [APP]), {APP: owner_id})
            catalogue.merge_fields(owner, {'attributes': {
                'game_info': {'steam_app_id': APP, 'requirements': {'minimum': [{'value': '8 GB'}]}},
                'game_ratings': {'steam_app_id': APP, 'all_time': {'total': 10}},
                'achievement_catalogue': {'steam_app_id': APP, 'status': 'complete', 'total': 1, 'definitions_complete': True},
            }}, 'steam')
            db.add(SteamPriceSnapshot(work_id=owner_id, steam_app_id=APP, country='DE', payload={},
                                     current={'currency': 'EUR', 'amountInt': 2999}, url=STORE_URL))
            achievement = GameAchievement(work_id=owner_id, app_id=APP, api_name='WIN', name='Win', active=True)
            db.add_all([achievement, GameEntry(user_id=self.viewer.id, work_id=owner_id, status='playing')])
            await db.flush()
            db.add(GameAchievementUnlock(user_id=self.viewer.id, work_id=owner_id, achievement_id=achievement.id))
            await db.commit()

        self.assertEqual((await self.detail_read(work_id, 'info'))['info']['steam_app_id'], APP)
        self.assertEqual((await self.detail_read(work_id, 'ratings'))['ratings']['all_time']['total'], 10)
        self.assertEqual((await self.detail_read(work_id, 'offers', country='DE'))['offers'][0]['steam_app_id'], APP)
        self.assertIsNone((await self.detail_read(work_id, 'achievement-catalogue'))['catalogue']['total'])
        self.assertEqual((await self.detail_read(work_id, 'achievements'))['results'], [])
        self.assertEqual((await self.detail_read(work_id, 'achievement-catalogue', include_store='true'))['catalogue']['total'], 1)
        public_row = (await self.detail_read(work_id, 'achievements', include_store='true'))['results'][0]
        self.assertEqual(public_row['app_id'], APP)
        self.assertFalse(public_row['unlocked'])
        self.assertTrue((await self.detail_read(owner_id, 'achievements'))['results'][0]['unlocked'])
        async with self.Session() as db:
            from core.media_details import game_primary_details
            work = await db.get(CatalogueEntity, work_id)
            self.assertIn({'name': 'Steam', 'url': STORE_URL}, (await game_primary_details(db, work))['sources'])
            db.add(CatalogueIdentity(entity_id=work_id, namespace='steam.app', external_id='42', source='igdb'))
            await db.commit()
            self.assertEqual(await steam_store(db, work), (work, '42'))
            self.assertEqual(await work_map(db, [APP]), {APP: owner_id})

    async def test_warm_preupgrade_metadata_is_queued_once_for_edition_evidence(self):
        from core.catalogue_normalize import GAME_ARTWORK_VERSION
        from core.game_release_dates import VERSION

        stamp = now()
        async with self.Session() as db:
            work = await db.get(CatalogueEntity, self.base)
            work.attributes = {'game_class': 'expanded_game', 'igdb_id': '334254'}
            db.add_all([
                CatalogueIdentity(entity_id=work.id, namespace='igdb.game', external_id='334254', source='igdb'),
                MetadataSnapshot(provider='igdb', kind='game', external_id='334254', entity_id=work.id,
                    fetched_at=stamp, expires_at=stamp + timedelta(days=5),
                    coverage={'primary_complete': True, 'characters_complete': True,
                              'release_precision_version': VERSION, 'artwork_policy_version': GAME_ARTWORK_VERSION}),
            ])
            await db.commit()
            _, state = await request_metadata(db, work)
            await db.commit()
            self.assertTrue(state['primary_ready'])
            self.assertTrue(state['pending'])
            _, state = await request_metadata(db, work)
            self.assertTrue(state['pending'])
