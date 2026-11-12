"""Verified TMDB/IMDb people share one identity without crossing Games privacy."""
import unittest
import asyncio
import importlib
from unittest.mock import AsyncMock,patch

from sqlalchemy import select,func,text
from alembic.migration import MigrationContext
from alembic.operations import Operations

import test_games_pg as fixture
from core import catalogue,contributor_evidence,contributor_search
from core.person_identities import verified_tmdb_person
from models.catalogue import CatalogueEntity,CatalogueIdentity,CatalogueCredit,ContributorEvidence


@unittest.skipUnless(fixture.URL,'Requires disposable Games PostgreSQL')
class PersonBridgeTests(unittest.IsolatedAsyncioTestCase):
    cleanup=fixture.GamesDatabaseTests.cleanup

    async def asyncSetUp(self):
        await fixture.GamesDatabaseTests.asyncSetUp(self)
        # The tracking fixture intentionally omits the contributor SQL triggers.
        # Exercise this identity bridge with the actual maintained search index.
        async with self.engine.begin() as conn:
            def setup(sync):
                sync.execute(text('DROP TABLE catalogue_contributor_search'))
                with Operations.context(MigrationContext.configure(sync)):
                    for migration in ('mt050_contributor_search','mt061_game_contributor_search',
                                      'mt064_game_credit_participation','mt065_person_credit_scope'):
                        importlib.import_module('migrations.versions.'+migration).upgrade()
            await conn.run_sync(setup)
        async with self.Session() as db:
            game=CatalogueEntity(kind='person',name='Shared actor',image_url='https://example.test/selected.jpg',
                attributes={},protected_fields=['image_url'])
            namesake=CatalogueEntity(kind='person',name='Shared actor',attributes={})
            db.add_all([game,namesake]);await db.flush()
            self.person,self.namesake=game.id,namesake.id
            db.add_all([CatalogueIdentity(entity_id=game.id,namespace='imdb.person',external_id='nm100',source='imdb'),
                CatalogueIdentity(entity_id=namesake.id,namespace='imdb.person',external_id='nm200',source='imdb'),
                CatalogueCredit(work_id=self.base,contributor_id=game.id,provider='imdb',source_key='cast',role='voice_actor')])
            await db.commit()
        self.profile={'id':10,'name':'Shared actor','imdb_id':'nm100','profile_path':'/portrait.jpg',
            'combined_credits':{'cast':[],'crew':[]}}

    async def test_profile_reuses_existing_game_person_idempotently_and_preserves_namesake_and_art(self):
        async with self.Session() as db:
            before=await db.scalar(select(func.count()).select_from(CatalogueEntity).where(CatalogueEntity.kind=='person'))
            for _ in range(2):self.assertEqual((await verified_tmdb_person(db,10,self.profile)).id,self.person)
            await db.commit()
            self.assertEqual(await db.scalar(select(func.count()).select_from(CatalogueEntity).where(CatalogueEntity.kind=='person')),before)
            row=await db.get(CatalogueEntity,self.person)
            self.assertEqual(row.image_url,'https://example.test/selected.jpg')
            identity=await catalogue.resolve_identity(db,'tmdb.person','10')
            self.assertEqual((identity.entity_id,identity.source),(self.person,'tmdb'))
            self.assertEqual((await catalogue.resolve_identity(db,'imdb.person','nm200')).entity_id,self.namesake)

    async def test_existing_movie_root_merges_alias_and_keeps_game_credits(self):
        async with self.Session() as db:
            root=CatalogueEntity(kind='person',name='Movie actor',attributes={});db.add(root);await db.flush()
            db.add(CatalogueIdentity(entity_id=root.id,namespace='tmdb.person',external_id='10',source='tmdb'))
            await db.commit()
            linked=await verified_tmdb_person(db,10,self.profile);await db.commit()
            self.assertEqual(linked.id,root.id)
            self.assertEqual((await db.get(CatalogueEntity,self.person)).canonical_person_id,root.id)
            self.assertEqual((await catalogue.resolve_identity(db,'imdb.person','nm100')).entity_id,root.id)
            found=await contributor_search.game_matches(db,'Shared actor','actor')
            self.assertEqual([row['key'] for row in found['actor']],[f'catalogue:{root.id}'])

    async def test_conflicting_provider_ids_roll_back_and_missing_name_evidence_does_not_merge(self):
        async with self.Session() as db:
            db.add(CatalogueIdentity(entity_id=self.person,namespace='tmdb.person',external_id='20',source='reviewed'))
            root=CatalogueEntity(kind='person',name='Shared actor',attributes={});db.add(root);await db.flush()
            db.add(CatalogueIdentity(entity_id=root.id,namespace='tmdb.person',external_id='10',source='tmdb'));await db.commit()
            with self.assertRaises(catalogue.IdentityConflict):await verified_tmdb_person(db,10,self.profile)
            self.assertIsNone((await db.get(CatalogueEntity,self.person)).canonical_person_id)
            self.assertEqual((await catalogue.resolve_identity(db,'imdb.person','nm100')).entity_id,self.person)
            self.assertIsNone(await verified_tmdb_person(db,10,{'id':10,'name':'Shared actor'}))
            with self.assertRaises(catalogue.IdentityConflict):await verified_tmdb_person(db,11,self.profile)

    async def test_background_verification_reuses_its_single_profile_request_to_bridge_identity(self):
        key,config=contributor_evidence.evidence_key('person',10,{},True)
        async with self.Session() as db:
            db.add(ContributorEvidence(kind=key[0],native_id=key[1],variant=key[2],config=config,
                requested_by=self.user.id,next_attempt_at=contributor_evidence.now()))
            await db.commit()
        with patch.object(contributor_evidence.tmdb,'get_person',AsyncMock(return_value=self.profile)) as request,patch.object(
            contributor_evidence.settings_store,'get_user_tmdb_key',AsyncMock(return_value='fixture-key')):
            self.assertEqual(await contributor_evidence.run_batch(self.Session),1)
        request.assert_awaited_once()
        async with self.Session() as db:
            self.assertEqual((await catalogue.resolve_identity(db,'tmdb.person','10')).entity_id,self.person)

    async def test_game_detail_for_tmdb_person_joins_existing_credits_and_denies_unapproved_viewer(self):
        from core.contributor_details import contributor_page
        with patch('core.tmdb.get_person_profile',AsyncMock(return_value=self.profile)),patch(
            'core.settings_store.get_user_tmdb_key',AsyncMock(return_value='fixture-key')):
            async with self.Session() as db:
                data=await contributor_page(db,self.user,kind='actor',key='tmdb:10',media_type='game')
                self.assertEqual(data['key'],f'catalogue:{self.person}')
                self.assertEqual([row['id'] for row in data['works']],[self.base])
                self.other.games_access=False
                from fastapi import HTTPException
                with self.assertRaises(HTTPException) as denied:
                    await contributor_page(db,self.other,kind='actor',key='tmdb:10',media_type='game')
                self.assertEqual(denied.exception.status_code,403)

    async def test_public_movie_projection_of_merged_person_never_returns_game_works_or_status(self):
        from core.contributor_details import contributor_page
        raw={**self.profile,'combined_credits':{'cast':[{'id':101,'media_type':'movie','title':'Public film',
            'character':'Actor','release_date':'2020-01-01'}],'crew':[]}}
        self.other.games_access=False
        with patch('core.tmdb.get_person',AsyncMock(return_value=raw)),patch(
            'core.settings_store.get_user_tmdb_key',AsyncMock(return_value='fixture-key')),patch(
            'core.contributor_details.result_filters',AsyncMock(return_value={'min_runtime':0,'excluded_genres':[]})):
            async with self.Session() as db:
                data=await contributor_page(db,self.other,kind='actor',key='tmdb:10')
                self.assertEqual(data['key'],f'catalogue:{self.person}')
                self.assertEqual([row['type'] for row in data['works']],['movie'])
                self.assertNotIn('game_person_status',data)

    async def test_concurrent_profile_publication_keeps_one_verified_identity(self):
        async def publish():
            async with self.Session() as db:
                row=await verified_tmdb_person(db,10,self.profile)
                await db.commit()
                return row.id
        self.assertEqual(await asyncio.gather(publish(),publish()),[self.person,self.person])
        async with self.Session() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(CatalogueIdentity).where(
                CatalogueIdentity.namespace=='tmdb.person',CatalogueIdentity.external_id=='10')),1)

    async def test_explicitly_cleared_portrait_is_preserved_without_repeated_bridge_work(self):
        from core.contributor_details import contributor_page
        async with self.Session() as db:
            row=await db.get(CatalogueEntity,self.person);row.image_url=None;await db.commit()
            await verified_tmdb_person(db,10,self.profile);await db.commit()
            self.assertIsNone(row.image_url)
            with patch('core.tmdb.get_person_profile',AsyncMock(return_value=self.profile)),patch(
                'core.settings_store.get_user_tmdb_key',AsyncMock(return_value='fixture-key')),patch(
                'core.person_identities.verified_tmdb_person',AsyncMock(side_effect=AssertionError('Repeated identity work'))) as bridge:
                data=await contributor_page(db,self.user,kind='actor',key='tmdb:10',media_type='game')
                self.assertEqual(data['key'],f'catalogue:{self.person}')
                bridge.assert_not_awaited()
