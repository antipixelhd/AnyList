"""Company portfolios use explicit source identity and resumable shared work."""
import asyncio
import unittest
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import func,select
import test_games_pg as fixture
from core.catalogue_normalize import normalize_igdb
from core.catalogue_providers import ProviderError
from core.game_company_works import GameCompanyWorks,status,snapshot
from core.game_imdb_enrichment import now
from models.catalogue import CatalogueCredit,CatalogueEntity,CatalogueIdentity


def item(game=10,record=1,role='developer',name='Source game'):
    raw={'id':game,'name':name,'game_type':{'id':0},'summary':'Source synopsis',
        'cover':{'url':'https://images.igdb.com/source.jpg'},'genres':[{'id':12,'name':'RPG'}]}
    doc=normalize_igdb(raw);doc['credits']=[]
    return {'doc':doc,'record':{'id':record,'game':game,'company':70,role:True},'roles':[role]}


def page(items,*,cursor=1,complete=True):
    return {'documents':items,'omitted':0,'received':len(items),'cursor':cursor,'complete':complete}


@unittest.skipUnless(fixture.URL,'Requires disposable Games PostgreSQL')
class CompanyWorksTests(unittest.IsolatedAsyncioTestCase):
    cleanup=fixture.GamesDatabaseTests.cleanup
    async def asyncSetUp(self):
        await fixture.GamesDatabaseTests.asyncSetUp(self)
        async with self.Session() as db:
            company=CatalogueEntity(kind='organization',name='Same company',description='Protected company')
            duplicate=CatalogueEntity(kind='organization',name='Same company')
            db.add_all([company,duplicate]);await db.flush();self.company=company.id
            db.add_all([CatalogueIdentity(entity_id=company.id,namespace='igdb.company',external_id='70',source='igdb'),
                CatalogueIdentity(entity_id=self.base,namespace='igdb.game',external_id='10',source='igdb')])
            base=await db.get(CatalogueEntity,self.base)
            base.description='Existing richer synopsis';base.image_url='https://example.test/selected.jpg'
            base.protected_fields=['image_url'];await db.commit()
            await status(db,self.company,enqueue=True);await db.commit()
        self.source=SimpleNamespace(page=AsyncMock(return_value=page([item(),item(20,2,'publisher')])))
        self.service=GameCompanyWorks(self.Session,source=self.source)

    async def due(self):
        async with self.Session() as db:
            row=await db.scalar(snapshot('70'));row.next_attempt_at=now()-timedelta(seconds=1)
            row.expires_at=now()-timedelta(seconds=1);await db.commit()

    async def active(self):
        async with self.Session() as db:
            return list(await db.scalars(select(CatalogueCredit).where(CatalogueCredit.attributes['company_connection'].astext=='70',
                CatalogueCredit.attributes['source_active'].astext=='true')))

    async def test_explicit_company_roles_are_cached_idempotent_and_preserve_metadata_and_tracking(self):
        await self.client.patch(f'/tracking/games/entries/{self.base}',json={'status':'completed','score':9,'total_seconds':500})
        self.assertEqual(await self.service.run('70'),'ready')
        ids=[row.id for row in await self.active()];self.assertEqual(len(ids),2)
        self.assertEqual(await self.service.run('70'),'cached');self.source.page.assert_awaited_once_with('70',0)
        await self.due();self.assertEqual(await self.service.run('70'),'ready')
        self.assertEqual([row.id for row in await self.active()],ids)
        async with self.Session() as db:
            state=await status(db,self.company);self.assertTrue(state['complete']);self.assertFalse(state['pending'])
            self.assertEqual(state['coverage'],'complete')
            base=await db.get(CatalogueEntity,self.base)
            self.assertEqual(base.description,'Existing richer synopsis');self.assertEqual(base.image_url,'https://example.test/selected.jpg')
            self.assertEqual(await db.scalar(select(func.count()).select_from(CatalogueEntity).where(CatalogueEntity.kind=='organization')),2)
        tracked=(await self.client.get(f'/tracking/games/works/{self.base}')).json()
        self.assertEqual((tracked['score'],tracked['total_seconds']),(9,500))
        response=await self.client.get(f'/tracking/contributors/studio/catalogue:{self.company}',params={'media_type':'game'})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['known_works'],2)
        self.assertTrue(response.json()['game_company_status']['complete'])
        self.assertNotIn('source_record',response.text)

    async def test_partial_failure_resumes_without_retiring_previous_evidence(self):
        await self.service.run('70');original=[row.id for row in await self.active()]
        await self.due();self.source.page.side_effect=[page([item(30,3)],cursor=3,complete=False),ProviderError('unavailable')]
        self.assertEqual(await self.service.run('70'),'retry')
        self.assertTrue(set(original).issubset(row.id for row in await self.active()))
        async with self.Session() as db:
            state=await status(db,self.company);self.assertTrue(state['pending']);self.assertFalse(state['complete'])
            self.assertEqual((await db.scalar(snapshot('70'))).payload['cursor'],3)
        await self.due();self.source.page.side_effect=None;self.source.page.return_value=page([],cursor=3)
        self.assertEqual(await self.service.run('70'),'ready');self.source.page.assert_awaited_with('70',3)
        active=await self.active();self.assertEqual(len(active),1);self.assertNotIn(active[0].id,original)

    async def test_concurrent_requests_share_work_and_cancellation_releases_the_lease(self):
        entered,release=asyncio.Event(),asyncio.Event()
        async def load(*args):entered.set();await release.wait();return page([item()])
        self.source.page.side_effect=load
        first=asyncio.create_task(self.service.run('70'));await entered.wait()
        second=asyncio.create_task(self.service.run('70'));release.set()
        self.assertEqual(await asyncio.gather(first,second),['ready','ready']);self.source.page.assert_awaited_once()
        await self.due();entered.clear();release.clear()
        task=asyncio.create_task(self.service._run('70'));await entered.wait();task.cancel()
        with self.assertRaises(asyncio.CancelledError):await task
        async with self.Session() as db:
            job=await db.scalar(snapshot('70'));self.assertIsNone(job.lease_token);self.assertIsNotNone(job.next_attempt_at)

    async def test_empty_collection_is_complete_only_after_retrieval(self):
        async with self.Session() as db:
            before=await status(db,self.company);self.assertFalse(before['complete'])
        self.source.page.return_value=page([],cursor=0)
        self.assertEqual(await self.service.run('70'),'ready')
        async with self.Session() as db:
            after=await status(db,self.company);self.assertTrue(after['complete']);self.assertEqual(after['received'],0)

    async def test_omitted_or_ambiguous_projection_never_withdraws_old_verified_works(self):
        await self.service.run('70');original={row.id for row in await self.active()}
        await self.due();self.source.page.return_value={**page([],cursor=5),'omitted':1,'received':1}
        self.assertEqual(await self.service.run('70'),'ready')
        self.assertEqual({row.id for row in await self.active()},original)
        async with self.Session() as db:
            result=await status(db,self.company);self.assertTrue(result['complete']);self.assertEqual(result['coverage'],'partial')

    async def test_work_budget_persists_cursor_and_verified_company_aliases_are_all_queued(self):
        async with self.Session() as db:
            alias=CatalogueEntity(kind='organization',name='Alias',canonical_company_id=self.company)
            db.add(alias);await db.flush()
            db.add(CatalogueIdentity(entity_id=alias.id,namespace='igdb.company',external_id='71',source='reviewed'));await db.commit()
            result=await status(db,self.company,enqueue=True);await db.commit()
            self.assertTrue(result['pending']);self.assertIsNotNone(await db.scalar(snapshot('71')))
        self.service.page_budget=1;self.source.page.return_value=page([item()],cursor=1,complete=False)
        self.assertEqual(await self.service.run('70'),'partial')
        async with self.Session() as db:
            job=await db.scalar(snapshot('70'));self.assertIsNone(job.lease_token);self.assertEqual(job.payload['cursor'],1)
        await self.due();self.source.page.return_value=page([],cursor=1)
        self.assertEqual(await self.service.run('70'),'ready');self.source.page.assert_awaited_with('70',1)
        async with self.Session() as db:self.assertTrue((await status(db,self.company))['pending'])

    async def test_shared_steam_reference_cannot_collapse_different_igdb_games(self):
        other=item(30,3)
        other['doc']['work']['identities'].append({'namespace':'steam.app','external_id':'1245620'})
        self.source.page.return_value=page([other])
        self.assertEqual(await self.service.run('70'),'ready')
        async with self.Session() as db:
            identity=await db.scalar(select(CatalogueIdentity).where(CatalogueIdentity.namespace=='igdb.game',CatalogueIdentity.external_id=='30'))
            self.assertNotEqual(identity.entity_id,self.base)
            steam=await db.scalar(select(CatalogueIdentity).where(CatalogueIdentity.namespace=='steam.app',CatalogueIdentity.external_id=='1245620'))
            self.assertEqual(steam.entity_id,self.base)
            root=await db.get(CatalogueEntity,identity.entity_id)
            self.assertEqual(root.attributes['source_identity_candidates'],[{'namespace':'steam.app','external_id':'1245620'}])
