"""Revision subscriptions use shared public headers and fresh authorization."""
import asyncio
import unittest
from datetime import timedelta
from unittest.mock import patch

from sqlalchemy import select
import test_game_metadata_pg as fixture
from core import game_credit_updates as updates
from core.game_imdb_enrichment import now,request,JOB
from models import User
from models.catalogue import MetadataSnapshot,CatalogueEntity,CatalogueCredit


@unittest.skipUnless(fixture.fixture.URL,'Requires disposable Games PostgreSQL')
class CreditRevisionTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=fixture.GameMetadataTests.asyncSetUp
    asyncTearDown=fixture.GameMetadataTests.asyncTearDown
    cleanup=fixture.GameMetadataTests.cleanup

    async def initial(self):
        self.path=f'/tracking/games/works/{self.base}/credits'
        response=await self.client.get(self.path)
        self.assertEqual(response.status_code,200,response.text)
        return response.json()['credit_updates']['revision']

    async def publish(self):
        async with self.Session() as db:
            actor=CatalogueEntity(kind='person',name='Published actor');db.add(actor);await db.flush()
            db.add(CatalogueCredit(work_id=self.base,contributor_id=actor.id,provider='imdb',source_key='test',role='actor'))
            job=await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.kind==JOB,MetadataSnapshot.external_id==str(self.base)))
            job.coverage={'state':'complete','complete':True,'received':1,'total':1}
            job.fetched_at=now();job.expires_at=now()+timedelta(days=30)
            job.next_attempt_at=job.lease_until=None
            job.payload={'private_evidence':'SENTINEL '*10000}
            await db.commit()
        updates.published(self.engine,self.base)

    async def test_request_waits_for_publication_and_returns_fresh_cast(self):
        token=await self.initial()
        pending=asyncio.create_task(self.client.get(self.path,params={'revision':token}))
        try:
            await asyncio.sleep(.1);self.assertFalse(pending.done())
            await self.publish()
            response=await asyncio.wait_for(pending,1)
            self.assertEqual(response.status_code,200,response.text)
            data=response.json()
            self.assertNotEqual(data['credit_updates']['revision'],token)
            self.assertTrue(data['game_credit_status']['complete'])
            self.assertIn('Published actor',str(data['people']))
            self.assertNotIn('SENTINEL',response.text)
            self.assertNotIn('private note',response.text)
        finally:
            pending.cancel();await asyncio.gather(pending,return_exceptions=True)
        self.assertFalse(updates._waits)

    async def test_subscribers_share_observation_and_cancel_independently(self):
        token=await self.initial();calls=0;original=updates.revision
        async def counted(*args):
            nonlocal calls
            calls+=1;return await original(*args)
        with patch.object(updates,'revision',counted):
            a=asyncio.create_task(updates.wait_for_revision(self.engine,self.base,token))
            b=asyncio.create_task(updates.wait_for_revision(self.engine,self.base,token))
            try:
                await asyncio.sleep(.1)
                self.assertEqual(calls,1);self.assertEqual(len(updates._waits),1)
                a.cancel();await asyncio.gather(a,return_exceptions=True)
                self.assertFalse(b.done())
                await self.publish();self.assertTrue(await asyncio.wait_for(b,1))
            finally:
                a.cancel();b.cancel();await asyncio.gather(a,b,return_exceptions=True)
        self.assertFalse(updates._waits)

    async def test_cooldown_capacity_and_optional_character_publication(self):
        token=await self.initial()
        with patch.object(updates,'MAX_WAITS',0):
            response=await self.client.get(self.path,params={'revision':token})
            self.assertFalse(response.json()['credit_updates']['waitable'])
        async with self.Session() as db:
            job=await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.kind==JOB,MetadataSnapshot.external_id==str(self.base)))
            job.error_code='unavailable';job.next_attempt_at=now()+timedelta(minutes=1);await db.commit()
        response=await self.client.get(self.path)
        self.assertFalse(response.json()['credit_updates']['waitable'])
        self.assertGreater(response.json()['game_credit_status']['retry_after'],30)
        async with self.Session() as db:
            db.add(MetadataSnapshot(provider='igdb',kind='game',external_id='119133',entity_id=self.base,
                fetched_at=now(),lease_until=now()+timedelta(seconds=30),coverage={'characters_complete':False}))
            await db.commit();known,_=await updates.revision(db,self.base)
        waiter=asyncio.create_task(updates.wait_for_revision(self.engine,self.base,known))
        try:
            await asyncio.sleep(.05)
            async with self.Session() as db:
                row=await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.kind=='game',MetadataSnapshot.external_id=='119133'))
                row.coverage={'characters_complete':True};row.lease_until=None;await db.commit()
            self.assertTrue(await asyncio.wait_for(waiter,1))
        finally:
            waiter.cancel();await asyncio.gather(waiter,return_exceptions=True)

    async def test_approval_is_rechecked_after_wait(self):
        token=await self.initial()
        pending=asyncio.create_task(self.client.get(self.path,params={'revision':token}))
        try:
            await asyncio.sleep(.1)
            async with self.Session() as db:
                viewer=await db.get(User,self.viewer.id)
                viewer.games_access=viewer.is_admin=False;await db.commit()
            await self.publish()
            response=await asyncio.wait_for(pending,1)
            self.assertEqual(response.status_code,403,response.text)
        finally:
            pending.cancel();await asyncio.gather(pending,return_exceptions=True)

    async def test_timeout_and_disconnect_leave_provider_demand_intact(self):
        token=await self.initial()
        self.assertTrue(await updates.wait_for_revision(self.engine,self.base,token,timeout=.03))
        self.assertFalse(updates._waits)
        async def disconnected():return True
        with self.assertRaises(updates.Disconnected):
            await updates.wait_for_revision(self.engine,self.base,token,disconnected=disconnected)
        self.assertFalse(updates._waits)
        with patch.object(updates,'MAX_SUBSCRIBERS',0):
            self.assertFalse(await updates.wait_for_revision(self.engine,self.base,token))
        async with self.Session() as db:
            job=await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.kind==JOB,MetadataSnapshot.external_id==str(self.base)))
            self.assertIsNotNone(job.next_attempt_at)
