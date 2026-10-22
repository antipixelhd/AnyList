"""Binding provenance, protected review and actual game identity constraints."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

import test_catalogue_pg as fixture
from core import catalogue
from core.game_imdb_bindings import GameIMDbBindings
from core.game_imdb_source import game_header
from core.game_source_cache import GameSourceCache
from models.catalogue import CatalogueEntity, CatalogueIdentity, MetadataSnapshot
from test_game_credit_sources import item, claim, snak


@unittest.skipUnless(fixture.URL,'Requires explicit disposable catalogue PostgreSQL')
class IMDbBindingTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixture.CatalogueDatabaseTests.asyncSetUp
    asyncTearDown=fixture.CatalogueDatabaseTests.asyncTearDown

    async def setup_game(self,native='10'):
        async with self.Session() as db:
            work=CatalogueEntity(kind='game',name='IGDB game',attributes={'game_class':'game','igdb_id':native,'igdb_slug':'game'})
            db.add(work);await db.flush()
            db.add(CatalogueIdentity(entity_id=work.id,namespace='igdb.game',external_id=native,source='igdb'))
            await db.commit()
            return work.id

    def service(self,imdb_id='tt123',wiki_id='Q100'):
        source=SimpleNamespace(title=AsyncMock(return_value=game_header({'id':imdb_id,'titleText':{'text':'Game'},
            'titleType':{'id':'videoGame'},'releaseYear':{'year':2020}},imdb_id)))
        raw=item(wiki_id,'Game',{'P5794':[claim('game',qualifiers={'P9043':[snak('10')]})], 'P345':[claim(imdb_id)]})
        wiki=SimpleNamespace(sources=SimpleNamespace(cache=GameSourceCache(self.Session)),
            game_item=AsyncMock(return_value={'data':{'item':wiki_id}}),
            entities=AsyncMock(return_value={'data':{'entities':{wiki_id:raw}}}))
        return GameIMDbBindings(self.Session,source=source,wiki=wiki)

    async def test_explicit_mapping_binds_existing_game_once_and_reuses_evidence(self):
        work=await self.setup_game()
        service=self.service()
        result=await service.resolve(work)
        self.assertEqual(result['binding']['id'],'tt123')
        self.assertEqual(result['binding']['source'],'wikidata')
        self.assertEqual(result['binding']['evidence']['wikidata']['wikidata_id'],'Q100')
        self.assertEqual((await service.resolve(work))['binding']['id'],'tt123')
        service.source.title.assert_awaited_once()
        service.wiki.game_item.assert_awaited_once()
        async with self.Session() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(CatalogueEntity).where(CatalogueEntity.kind=='game')),1)
            self.assertEqual((await catalogue.resolve_identity(db,'imdb.title','tt123')).entity_id,work)

    async def test_reviewed_binding_survives_background_remapping(self):
        work=await self.setup_game()
        service=self.service()
        result=await service.review(work,'tt123',77,'Confirmed game and edition')
        self.assertTrue(result['binding']['reviewed'])
        service.wiki.game_item.side_effect=AssertionError('Reviewed mappings must not be remapped')
        self.assertEqual((await service.resolve(work))['binding']['id'],'tt123')
        async with self.Session() as db:
            binding=await catalogue.resolve_identity(db,'imdb.title','tt123')
            self.assertEqual(binding.evidence['reviewer_id'],77)
            self.assertEqual(await db.scalar(select(func.count()).select_from(MetadataSnapshot).where(MetadataSnapshot.kind=='game_binding_review')),1)

    async def test_conflicting_multiple_mappings_are_unresolved_and_not_titles_matched(self):
        work=await self.setup_game()
        service=self.service()
        raw=service.wiki.entities.return_value['data']['entities']['Q100']
        raw['claims']['P345'].append(claim('tt456'))
        self.assertEqual((await service.resolve(work))['status'],'conflict')
        service.source.title.assert_not_awaited()
        async with self.Session() as db:
            self.assertIsNone(await catalogue.resolve_identity(db,'imdb.title','tt123'))

    async def test_one_imdb_title_cannot_collapse_a_base_and_complete_edition(self):
        base=await self.setup_game()
        edition=await self.setup_game('11')
        service=self.service()
        await service.review(base,'tt123',77,'Verified original game')
        with self.assertRaises(catalogue.IdentityConflict):
            await service.review(edition,'tt123',77,'Another intended edition')
        async with self.Session() as db:
            self.assertEqual((await catalogue.resolve_identity(db,'imdb.title','tt123')).entity_id,base)
            self.assertEqual(await db.scalar(select(func.count()).select_from(CatalogueEntity).where(CatalogueEntity.kind=='game')),2)

    async def test_actual_database_requires_validated_video_game_and_only_one_binding(self):
        work=await self.setup_game()
        async with self.Session() as db:
            db.add(CatalogueIdentity(entity_id=work,namespace='imdb.title',external_id='tt123',source='reviewed'))
            with self.assertRaises(IntegrityError):await db.flush()
            await db.rollback()
        service=self.service()
        await service.review(work,'tt123',77,'Validated game identity')
        async with self.Session() as db:
            db.add(CatalogueIdentity(entity_id=work,namespace='imdb.title',external_id='tt456',source='reviewed',evidence={'title_type':'videoGame'}))
            with self.assertRaises(IntegrityError):await db.flush()
            await db.rollback()

    async def test_changed_igdb_root_requires_review_and_preserves_the_existing_pin(self):
        from core.game_imdb_bindings import revision
        work=await self.setup_game()
        service=self.service()
        await service.review(work,'tt123',77,'Verified original game root')
        async with self.Session() as db:
            original=await revision(db)
            game=await db.get(CatalogueEntity,work)
            game.attributes={**game.attributes,'igdb_id':'11'}
            db.add(CatalogueIdentity(entity_id=work,namespace='igdb.game',external_id='11',source='reviewed'))
            await db.commit()
        self.assertEqual((await service.resolve(work))['status'],'conflict')
        service.wiki.game_item.assert_not_awaited()
        async with self.Session() as db:
            pin=await catalogue.resolve_identity(db,'imdb.title','tt123')
            self.assertEqual(pin.evidence['igdb_id'],'10')
        await service.review(work,'tt123',77,'Verified corrected game root')
        async with self.Session() as db:
            self.assertNotEqual(await revision(db),original)
            self.assertEqual((await catalogue.resolve_identity(db,'imdb.title','tt123')).evidence['igdb_id'],'11')
