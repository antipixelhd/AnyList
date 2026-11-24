import asyncio
import importlib
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select,func
import test_games_pg as fixture
from core.game_editions import GameEditions,candidates
from core.game_release_bundles import EDITION_TYPE, MEMBER_LIMIT, classify
from core.game_types import capabilities,search_candidate
from models.catalogue import CatalogueEntity,CatalogueIdentity,CatalogueRelationship,MetadataSnapshot
from models.games import GameEntry


def source():
    return {'id':100,'first_release_date':1500000000,'expanded_games':[
        {'id':200,'name':'Expanded edition','cover':{'url':'https://images.igdb.com/igdb/image/upload/t_thumb/cover.jpg'},'first_release_date':1600000000,'game_type':{'id':10},'parent_game':{'id':100}}]}


def provider_game(native, name, kind=0, **fields):
    return {'id': native, 'name': name, 'game_type': {'id': kind}, **fields}


ORIGINAL = provider_game(1020, 'Grand Theft Auto V', first_release_date=1379376000,
    platforms=[{'id': 167, 'name': 'PlayStation 5'}])
CONSOLE_BUNDLE = provider_game(134709, 'Grand Theft Auto V', 3, first_release_date=1649721600,
    platforms=[{'id': 167, 'name': 'PlayStation 5'}, {'id': 169, 'name': 'Xbox Series X|S'}])
ONLINE = provider_game(134710, 'Grand Theft Auto Online', 4, parent_game=ORIGINAL)
STORY = provider_game(239060, 'Grand Theft Auto V: Story Mode', 2, parent_game=ONLINE)
COLLECTION = provider_game(7348, 'Halo: The Master Chief Collection', 3, first_release_date=1415664000)
HALO_MEMBERS = [
    provider_game(2640, 'Halo: Combat Evolved Anniversary', 9,
        parent_game=provider_game(740, 'Halo: Combat Evolved')),
    provider_game(125009, 'Halo 2: Anniversary', 9, parent_game=provider_game(986, 'Halo 2')),
    provider_game(990, 'Halo: Reach'), provider_game(987, 'Halo 3'),
    provider_game(989, 'Halo 3: ODST'), provider_game(991, 'Halo 4'),
]


class EditionPolicyTests(unittest.TestCase):
    def test_release_bundle_needs_one_original_and_a_playable_release(self):
        self.assertEqual(classify(CONSOLE_BUNDLE, [ONLINE, STORY]), ORIGINAL)
        self.assertEqual(classify(CONSOLE_BUNDLE, [ORIGINAL, ONLINE]), ORIGINAL)
        self.assertEqual(classify(CONSOLE_BUNDLE, [provider_game(18, 'Console port', 11, parent_game=ORIGINAL)]), ORIGINAL)
        self.assertIs(classify(CONSOLE_BUNDLE, [ONLINE]), False)
        self.assertIs(classify(CONSOLE_BUNDLE, [STORY]), False)
        self.assertIs(classify(CONSOLE_BUNDLE, [ONLINE, STORY, provider_game(12, ORIGINAL['name'])]), False)
        self.assertIs(classify(COLLECTION, HALO_MEMBERS), False)
        self.assertIs(classify({**COLLECTION, 'name': 'Halo 3'}, HALO_MEMBERS), False)
        self.assertIsNone(classify(CONSOLE_BUNDLE, [ONLINE, {**STORY, 'parent_game': ONLINE['id']}]))
        self.assertIsNone(classify(CONSOLE_BUNDLE, [ONLINE] * MEMBER_LIMIT))

    def test_only_explicit_released_newer_editions_qualify(self):
        raw=source();self.assertEqual(candidates(raw)[0]['native'],'200')
        for change in ({'first_release_date':1400000000},{'first_release_date':4102444800},
                       {'parent_game':{'id':999}},{'game_type':{'id':1}},{'version_parent':100},
                       {'platforms':[{'id':34}]}):
            row={**source()['expanded_games'][0],**change}
            self.assertEqual(candidates({**source(),'expanded_games':[row]}),[])
        self.assertEqual(candidates({'id':100}),[])

    def test_compilations_are_independent_but_packs_and_software_are_not(self):
        self.assertTrue(capabilities({'game_class':'bundle'}).independent)
        self.assertTrue(capabilities({'game_class':'bundle'}).searchable)
        self.assertFalse(capabilities({'game_class':'pack'}).independent)
        self.assertFalse(capabilities({'game_class':'bundle','mobile_only':True}).independent)
        self.assertIsNotNone(search_candidate({'id':1,'name':'Compilation','game_type':{'id':3}}))


@unittest.skipUnless(fixture.URL,'Requires disposable Games PostgreSQL')
class EditionDatabaseTests(unittest.IsolatedAsyncioTestCase):
    cleanup=fixture.GamesDatabaseTests.cleanup
    async def asyncSetUp(self):
        await fixture.GamesDatabaseTests.asyncSetUp(self)
        async with self.Session() as db:
            db.add(CatalogueIdentity(entity_id=self.base,namespace='igdb.game',external_id='100',source='igdb'))
            db.add(GameEntry(user_id=self.user.id,work_id=self.base,status='completed',total_seconds=5000))
            await db.commit()

    async def test_shared_cached_association_preserves_roots_and_private_tracking(self):
        provider=SimpleNamespace(igdb=AsyncMock(return_value=[source()]))
        service=GameEditions(self.Session,provider)
        first,second=await asyncio.gather(service.read(self.base),service.read(self.base))
        self.assertEqual(first,second);self.assertEqual(provider.igdb.await_count,1)
        self.assertEqual((await service.read(self.base)),first);self.assertEqual(provider.igdb.await_count,1)
        self.assertTrue(first['editions'][0]['poster'].startswith('https://images.igdb.com/'))
        target=first['editions'][0]['id'];self.assertNotEqual(target,self.base)
        async with self.Session() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(GameEntry)),1)
            self.assertEqual(await db.scalar(select(GameEntry.total_seconds)),5000)
            edge=await db.scalar(select(CatalogueRelationship).where(CatalogueRelationship.relation=='version'))
            self.assertEqual((edge.source_id,edge.target_id),(target,self.base))
            self.assertEqual(edge.attributes['edition_type'],'expanded_game')
        with patch('core.game_editions.CatalogueProviders',return_value=provider):
            response=await self.client.get(f'/tracking/games/works/{self.base}/editions')
        self.assertEqual(response.status_code,200);self.assertEqual(response.json(),first)
        self.assertEqual(provider.igdb.await_count,1)
        self.viewer=None
        response=await self.client.get(f'/tracking/games/works/{self.base}/editions')
        self.assertEqual(response.status_code,401)

    async def test_shallow_association_preserves_precise_metadata_and_protected_name(self):
        async with self.Session() as db:
            target=CatalogueEntity(kind='game',name='Selected title',protected_fields=['name'],
                field_sources={'attributes.release_date':'igdb'},attributes={'game_class':'expanded_game',
                'release_date':'2020-09-13','release_date_precision':'day','release_date_evidence':'release_dates'})
            db.add(target);await db.flush()
            db.add(CatalogueIdentity(entity_id=target.id,namespace='igdb.game',external_id='200',source='igdb'));await db.commit()
        result=await GameEditions(self.Session,SimpleNamespace(igdb=AsyncMock(return_value=[source()]))).read(self.base)
        self.assertEqual(result['editions'][0]['title'],'Selected title')
        async with self.Session() as db:
            work=await db.get(CatalogueEntity,target.id)
            self.assertEqual(work.attributes['release_date'],'2020-09-13')
            self.assertEqual(work.attributes['release_date_precision'],'day')

    async def test_compilation_database_tracking_and_favorites_accept_but_packs_reject(self):
        from models.games import GameFavorite
        async with self.Session() as db:
            bundle=CatalogueEntity(kind='game',name='Collection',attributes={'game_class':'bundle'})
            db.add(bundle);await db.flush()
            db.add(GameEntry(user_id=self.user.id,work_id=bundle.id,status='playing',total_seconds=360000))
            db.add(GameFavorite(user_id=self.user.id,work_id=bundle.id));await db.commit()
            self.assertEqual(await db.scalar(select(GameEntry.total_seconds).where(GameEntry.work_id==bundle.id)),360000)

    async def test_release_bundle_routes_to_original_and_cannot_start_separate_tracking(self):
        from core import catalogue, game_discovery
        from core.catalogue_providers import CatalogueProviders
        from core.game_browse_sources import GameBrowseSources
        from core.game_metadata import GameMetadata

        provider = CatalogueProviders()
        provider._igdb_pages = AsyncMock(return_value=([], True))
        provider.igdb = AsyncMock(side_effect=[[CONSOLE_BUNDLE], [ONLINE, STORY], [ORIGINAL],
            [COLLECTION], HALO_MEMBERS])
        primary = AsyncMock()
        doc, _ = await provider._detail('igdb', 'game', CONSOLE_BUNDLE['id'], on_primary=primary)
        collection_doc, _ = await provider._detail('igdb', 'game', COLLECTION['id'], on_primary=primary)
        self.assertEqual(provider.igdb.await_count, 5)
        self.assertEqual(primary.await_count, 2)
        self.assertTrue(all('release_bundle' not in call.args[0] for call in primary.await_args_list))
        self.assertEqual(collection_doc['release_bundle'], {})
        async with self.Session() as db:
            bundle = await catalogue.ingest_document(db, doc, 'igdb')
            collection = await catalogue.ingest_document(db, collection_doc, 'igdb')
            bundle_id, collection_id = bundle.id, collection.id
            root = await catalogue.resolve_identity(db, 'igdb.game', str(ORIGINAL['id']))
            db.add(CatalogueRelationship(source_id=root.entity_id, target_id=bundle_id,
                relation='recommendation', provider='igdb'))
            await db.commit()
        with patch.object(game_discovery, 'candidates', AsyncMock(return_value=[
            search_candidate(CONSOLE_BUNDLE), search_candidate(ORIGINAL), search_candidate(COLLECTION)])):
            response = await self.client.get('/tracking/games/search?q=Grand+Theft+Auto')
        self.assertEqual(response.status_code, 200, response.text)
        original, halo = response.json()['results']
        self.assertEqual((original['igdb_id'], original['year'], original['game_class']), (1020, '2013', 'game'))
        self.assertEqual((halo['igdb_id'], halo['id'], halo['game_class']), (7348, collection_id, 'bundle'))
        with patch.object(game_discovery, 'candidates', AsyncMock(return_value=[search_candidate(CONSOLE_BUNDLE)])):
            response = await self.client.get('/tracking/games/search?q=Grand+Theft+Auto')
        self.assertEqual(response.json()['results'], [original])

        browse = GameBrowseSources(self.Session)
        browse.igdb = AsyncMock(return_value=[CONSOLE_BUNDLE, COLLECTION])
        page = await browse.catalogue_feed('popular', q='Grand Theft Auto', platforms=[167])
        self.assertEqual([row['igdb_id'] for row in page['data']['rows']], [1020])
        page = await browse.catalogue_feed('popular', q='Grand Theft Auto', start='2022-01-01')
        self.assertEqual(page['data']['rows'], [])
        self.assertEqual(browse.igdb.await_count, 2)

        version = provider_game(334254, 'Grand Theft Auto V Enhanced', 10, first_release_date=1741046400,
            parent_game=ORIGINAL)
        releases = {**ORIGINAL, 'expanded_games': [version]}
        editions = GameEditions(self.Session, SimpleNamespace(igdb=AsyncMock(return_value=[releases])))
        result = await editions.read(original['id'])
        self.assertEqual([(row['title'], row['year']) for row in result['editions']],
            [('Grand Theft Auto V Enhanced', 2025)])
        self.assertNotIn(bundle_id, [row['id'] for row in result['editions']])

        with (
            patch.object(GameMetadata, 'ensure', AsyncMock(return_value=({'primary_ready': True}, False))),
            patch.object(GameMetadata, 'resolve', AsyncMock(return_value=bundle_id)),
            patch('core.game_background.observe_work', AsyncMock()),
            patch('routers.games.request_game_metadata', AsyncMock()),
        ):
            details = await self.client.get(f'/catalogue/entities/{bundle_id}/details')
            self.assertEqual(details.status_code, 200, details.text)
            self.assertEqual(details.json()['subject']['catalogue_id'], original['id'])
            self.assertTrue(details.json()['game_bundle_redirect'])
            self.assertNotIn(bundle_id, [row['id'] for row in details.json()['relations'] + details.json()['similar']])
            resolved = await self.client.post(f'/tracking/games/resolve/{CONSOLE_BUNDLE["id"]}')
            self.assertEqual(resolved.json(), {'id': original['id']})
            save = await self.client.patch(f'/tracking/games/entries/{bundle_id}', json={'status': 'planning'})
            self.assertEqual(save.status_code, 422, save.text)
            favorite = await self.client.put(f'/tracking/games/favorites/{bundle_id}', json={'favorite': True})
            self.assertEqual(favorite.status_code, 422, favorite.text)
            imported = await self.client.post('/tracking/games/import/backloggd',
                json={'games': [{'work_id': bundle_id, 'status': 'planning'}]})
            self.assertEqual(imported.status_code, 200, imported.text)
            self.assertEqual(imported.json()['games'], [])
            saved_collection = await self.client.patch(f'/tracking/games/entries/{collection_id}',
                json={'status': 'planning'})
            self.assertEqual(saved_collection.status_code, 200, saved_collection.text)

            async with self.Session() as db:
                # Simulate private state created before the release was classified.
                db.add(GameEntry(user_id=self.user.id, work_id=bundle_id, status='playing', total_seconds=90, revision=1))
                await db.commit()
            legacy = await self.client.get(f'/catalogue/entities/{bundle_id}/details')
            self.assertEqual(legacy.json()['subject']['catalogue_id'], bundle_id)
            edited = await self.client.patch(f'/tracking/games/entries/{bundle_id}',
                json={'notes': 'Existing playthrough', 'revision': 1})
            self.assertEqual(edited.status_code, 200, edited.text)
            self.assertEqual(edited.json()['total_seconds'], 90)

    async def test_release_bundle_migration_queues_same_title_candidates_for_verification(self):
        from core import catalogue
        from core.catalogue_normalize import normalize_igdb

        async with self.Session() as db:
            for raw in (ORIGINAL, CONSOLE_BUNDLE, COLLECTION):
                await catalogue.ingest_document(db, normalize_igdb(raw), 'igdb')
            await db.commit()
        migration = importlib.import_module('migrations.versions.mt105_release_bundle_evidence')
        def upgrade(connection):
            with Operations.context(MigrationContext.configure(connection)):
                migration.upgrade()
                migration.upgrade()
        async with self.engine.begin() as connection:
            await connection.run_sync(upgrade)
        async with self.Session() as db:
            pending = (await db.scalars(select(MetadataSnapshot.external_id).where(
                MetadataSnapshot.provider == 'igdb', MetadataSnapshot.kind == 'game',
                MetadataSnapshot.next_attempt_at.is_not(None)))).all()
            self.assertEqual(pending, ['134709'])
            self.assertEqual(await db.scalar(select(func.count()).select_from(CatalogueRelationship).where(
                CatalogueRelationship.attributes['edition_type'].astext == EDITION_TYPE)), 0)
