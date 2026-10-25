"""Requested game people resolve explicit reverse IDs without losing careers."""
import asyncio
import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import test_game_person_bridge_pg as bridge_fixture
from core import catalogue, contributor_search
from core.game_imdb_enrichment import now, snapshot
from core.game_imdb_person import COMPONENT, view
from core.game_person_identity import GamePersonIdentity, JOB, request
from models.catalogue import CatalogueEntity, CatalogueIdentity, MetadataSnapshot


@unittest.skipUnless(bridge_fixture.fixture.URL, 'Requires disposable Games PostgreSQL')
class ReversePersonIdentityTests(unittest.IsolatedAsyncioTestCase):
    cleanup = bridge_fixture.PersonBridgeTests.cleanup
    asyncTearDown = bridge_fixture.fixture.GamesDatabaseTests.asyncTearDown

    async def asyncSetUp(self):
        await bridge_fixture.PersonBridgeTests.asyncSetUp(self)
        self.service = GamePersonIdentity(self.Session)
        stamp = now() - timedelta(days=2)
        self.fetch_time = stamp.replace(microsecond=stamp.microsecond//1000*1000)
        async with self.Session() as db:
            root = CatalogueEntity(kind='person', name='Shared actor', attributes={})
            db.add(root)
            await db.flush()
            self.movie_person = root.id
            db.add(CatalogueIdentity(entity_id=root.id, namespace='tmdb.person', external_id='10', source='tmdb'))
            db.add(MetadataSnapshot(provider='imdb', kind=COMPONENT, external_id='nm100', entity_id=self.person,
                coverage={'source_complete':True,'state':'mapped','projection_revision':'original'},
                payload={'profile':{'biography':'Saved profile'},'generation':'original','pages':1,'received':1},
                fetched_at=self.fetch_time, expires_at=now()+timedelta(days=30)))
            await request(db,'nm100',self.person,None,enqueue=True)
            await db.commit()

    async def run_bridge(self, find=None, profile=None):
        with patch('core.game_person_identity.settings_store.get_server_tmdb_key',return_value='fixture-key'), patch(
            'core.game_person_identity.tmdb.find_by_external_id',AsyncMock(return_value=find if find is not None else {'person_results':[{'id':10}]})) as lookup, patch(
            'core.game_person_identity.tmdb.get_person_profile',AsyncMock(return_value=profile if profile is not None else self.profile)) as fetch:
            state=await self.service.run('nm100')
            return state,lookup,fetch

    async def test_exact_reverse_ids_merge_existing_root_preserve_credits_and_selected_artwork(self):
        state,lookup,fetch=await self.run_bridge()
        self.assertEqual(state,'verified')
        lookup.assert_awaited_once_with('nm100','imdb_id',api_key='fixture-key')
        fetch.assert_awaited_once_with(10,api_key='fixture-key')
        async with self.Session() as db:
            self.assertEqual((await catalogue.resolve_identity(db,'imdb.person','nm100')).entity_id,self.movie_person)
            self.assertEqual((await db.get(CatalogueEntity,self.person)).canonical_person_id,self.movie_person)
            root=await db.get(CatalogueEntity,self.movie_person)
            self.assertEqual(root.image_url,'https://example.test/selected.jpg')
            self.assertIn('image_url',root.protected_fields)
            self.assertEqual((await catalogue.resolve_identity(db,'imdb.person','nm200')).entity_id,self.namesake)
            groups=await contributor_search.game_matches(db,'Shared actor','actor')
            self.assertEqual([r['key'] for r in groups['actor']],[f'catalogue:{self.movie_person}'])
            component=await db.scalar(snapshot(COMPONENT,'nm100'))
            proof=(await db.scalar(snapshot(JOB,'nm100'))).payload['last_verified_binding']
            self.assertEqual((proof['imdb_id'],proof['tmdb_id']),('nm100',10))
            self.assertEqual(proof['methods'],['external_id_lookup','person_details.imdb_id'])
            self.assertEqual(component.fetched_at,self.fetch_time)
            self.assertEqual(component.payload['profile'],{'biography':'Saved profile'})
            self.assertEqual(component.payload['generation'],'original')
            self.assertNotEqual(component.coverage['projection_revision'],'original')
            status=await view(db,'nm100',state={'pending':False,'complete':True},check_mapping=False)
            self.assertNotIn('identity_bridge',status['mapping'])
        self.assertEqual(await self.service.run('nm100'),'cached')

    async def test_namesake_ambiguity_missing_and_conflicting_profile_do_not_bind(self):
        for find,profile,expected in (
            ({'person_results':[]},None,'unresolved'),
            ({'person_results':[{'id':10},{'id':20}]},None,'ambiguous'),
            ({'person_results':[{'id':10}]},{**self.profile,'imdb_id':'nm200'},'conflict')):
            async with self.Session() as db:
                job=await db.scalar(snapshot(JOB,'nm100'));job.next_attempt_at=now();job.expires_at=None;await db.commit()
            state,_,fetch=await self.run_bridge(find,profile)
            self.assertEqual(state,expected)
            if expected!='conflict':fetch.assert_not_awaited()
            async with self.Session() as db:
                self.assertEqual((await catalogue.resolve_identity(db,'imdb.person','nm100')).entity_id,self.person)
                self.assertIsNone((await db.get(CatalogueEntity,self.person)).canonical_person_id)

    async def test_failed_provider_keeps_source_freshness_and_retries_without_negative_evidence(self):
        with patch('core.game_person_identity.settings_store.get_server_tmdb_key',return_value='fixture-key'),patch(
            'core.game_person_identity.tmdb.find_by_external_id',AsyncMock(side_effect=TimeoutError)):
            self.assertEqual(await self.service.run('nm100'),'retry')
        async with self.Session() as db:
            component=await db.scalar(snapshot(COMPONENT,'nm100'))
            self.assertNotIn('identity_bridge',component.payload)
            self.assertEqual(component.fetched_at,self.fetch_time)
            job=await db.scalar(snapshot(JOB,'nm100'))
            self.assertEqual(job.error_code,'identity_unavailable')
            self.assertGreater(job.next_attempt_at,now())
            self.assertIsNone(job.lease_until)

    async def test_concurrent_workers_share_lease_and_cancelled_work_is_retryable(self):
        started,release=asyncio.Event(),asyncio.Event()
        async def find(*args,**kwargs):
            started.set();await release.wait();return {'person_results':[]}
        with patch('core.game_person_identity.settings_store.get_server_tmdb_key',return_value='fixture-key'),patch(
            'core.game_person_identity.tmdb.find_by_external_id',AsyncMock(side_effect=find)) as lookup:
            first=asyncio.create_task(self.service.run('nm100'))
            await started.wait()
            self.assertEqual(await self.service.run('nm100'),'cached')
            first.cancel()
            with self.assertRaises(asyncio.CancelledError):await first
            lookup.assert_awaited_once()
        async with self.Session() as db:
            job=await db.scalar(snapshot(JOB,'nm100'))
            self.assertIsNone(job.lease_until)
            self.assertEqual(job.attempts,0)
            self.assertIsNotNone(job.next_attempt_at)

    async def test_missing_credentials_are_unavailable_and_warm_header_needs_no_admission(self):
        with patch('core.game_person_identity.settings_store.get_server_tmdb_key',return_value=None),patch(
            'core.game_person_identity.tmdb.find_by_external_id',AsyncMock(side_effect=AssertionError('No key'))) as lookup:
            self.assertEqual(await self.service.run('nm100'),'unavailable')
            lookup.assert_not_awaited()
        async with self.Session() as db:
            component=await db.scalar(snapshot(COMPONENT,'nm100'))
            with patch('core.game_person_identity.admit',AsyncMock(side_effect=AssertionError('Repeated admission'))):
                self.assertIsNone(await request(db,'nm100',self.person,component.payload['identity_bridge'],enqueue=True))

    async def test_conflicting_existing_identity_never_overwrites_a_reviewed_binding(self):
        async with self.Session() as db:
            db.add(CatalogueIdentity(entity_id=self.movie_person,namespace='imdb.person',external_id='nm300',source='reviewed'))
            await db.commit()
        self.assertEqual((await self.run_bridge())[0],'conflict')
        async with self.Session() as db:
            self.assertEqual((await catalogue.resolve_identity(db,'imdb.person','nm300')).entity_id,self.movie_person)
            self.assertEqual((await catalogue.resolve_identity(db,'imdb.person','nm100')).entity_id,self.person)

    async def test_malformed_find_response_and_wrong_profile_identity_remain_retryable(self):
        for found,profile in (({'person_results':None},None),
                ({'person_results':[{'id':True}]},None),
                ({'person_results':[{'id':10}]},{**self.profile,'id':11})):
            async with self.Session() as db:
                job=await db.scalar(snapshot(JOB,'nm100'));job.next_attempt_at=now();await db.commit()
            self.assertEqual((await self.run_bridge(found,profile))[0],'retry')
            async with self.Session() as db:
                self.assertNotIn('identity_bridge',(await db.scalar(snapshot(COMPONENT,'nm100'))).payload)
                self.assertEqual((await catalogue.resolve_identity(db,'imdb.person','nm100')).entity_id,self.person)

    async def test_requested_page_admits_old_cached_people_but_status_poll_does_not_enqueue(self):
        async with self.Session() as db:
            status=await view(db,'nm100',state={'pending':False,'complete':True},check_mapping=True)
            self.assertTrue(status['identity_pending'])
            await db.commit()
        state,_,_=await self.run_bridge({'person_results':[]})
        self.assertEqual(state,'unresolved')
        async with self.Session() as db:
            with patch('core.game_person_identity.admit',AsyncMock(side_effect=AssertionError('Warm page enqueued work'))):
                status=await view(db,'nm100',state={'pending':False,'complete':True},check_mapping=True)
                self.assertTrue(status['complete']);self.assertFalse(status['pending'])
        async with self.Session() as db:
            row=await db.scalar(snapshot(COMPONENT,'nm100'));row.payload={**row.payload,'identity_bridge':None};await db.commit()
            with patch('core.game_person_identity.admit',AsyncMock(side_effect=AssertionError('Status poll enqueued work'))):
                await view(db,'nm100',state={'pending':False,'complete':True},check_mapping=False)

    async def test_superseded_lease_cannot_apply_late_provider_evidence(self):
        async def find(*args,**kwargs):
            async with self.Session() as db:
                job=await db.scalar(snapshot(JOB,'nm100'));job.lease_token='replacement';await db.commit()
            return {'person_results':[{'id':10}]}
        with patch('core.game_person_identity.settings_store.get_server_tmdb_key',return_value='fixture-key'),patch(
            'core.game_person_identity.tmdb.find_by_external_id',AsyncMock(side_effect=find)),patch(
            'core.game_person_identity.tmdb.get_person_profile',AsyncMock(return_value=self.profile)):
            self.assertEqual(await self.service.run('nm100'),'superseded')
        async with self.Session() as db:
            self.assertEqual((await catalogue.resolve_identity(db,'imdb.person','nm100')).entity_id,self.person)
            self.assertNotIn('identity_bridge',(await db.scalar(snapshot(COMPONENT,'nm100'))).payload)

    async def test_already_verified_binding_reuses_identity_without_provider_requests(self):
        from core.person_identities import verified_tmdb_person
        async with self.Session() as db:
            await verified_tmdb_person(db,10,self.profile);await db.commit()
        with patch('core.game_person_identity.tmdb.find_by_external_id',AsyncMock(side_effect=AssertionError('Known identity refetched'))) as lookup:
            self.assertEqual(await self.service.run('nm100'),'verified')
            lookup.assert_not_awaited()
