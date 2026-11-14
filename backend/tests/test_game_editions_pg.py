import asyncio
import unittest
from datetime import datetime,timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
from sqlalchemy import select,func
import test_games_pg as fixture
from core.game_editions import GameEditions,candidates
from core.game_types import capabilities,search_candidate
from models.catalogue import CatalogueEntity,CatalogueIdentity,CatalogueRelationship
from models.games import GameEntry


def source():
    return {'id':100,'first_release_date':1500000000,'expanded_games':[
        {'id':200,'name':'Expanded edition','first_release_date':1600000000,'game_type':{'id':10},'parent_game':{'id':100}}]}


class EditionPolicyTests(unittest.TestCase):
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
