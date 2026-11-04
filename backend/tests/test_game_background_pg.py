import asyncio
import unittest
from datetime import timedelta
from unittest.mock import AsyncMock, patch

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.dml import Delete,Update

import test_games_pg as fixture
from core.game_background import GameBackground, observe, refresh_weights, state
from core.game_work import Priority, eligible_jobs, now, work_context
from core.catalogue_providers import ProviderHTTP
from models.catalogue import CatalogueCredit, CatalogueEntity, CatalogueIdentity, MetadataSnapshot
from models.game_runner import GameMapperEdge, GameMapperItem, GameMapperInterest, GameProviderTicket, GameProviderTurn,GameWorkDemand


@unittest.skipUnless(fixture.URL,'Requires disposable Games PostgreSQL')
class BackgroundMappingTests(unittest.IsolatedAsyncioTestCase):
    cleanup=fixture.GamesDatabaseTests.cleanup

    async def test_restart_waits_for_canonical_lease_then_reuses_saved_cursor(self):
        from core.game_imdb_enrichment import snapshot
        from core.game_company_works import JOB
        from types import SimpleNamespace
        cursor={'phase':'variants','after':42,'deferred':[]}
        async with self.Session() as db:
            db.add(MetadataSnapshot(provider='igdb',kind=JOB,external_id='123',entity_id=self.base,
                payload=cursor,coverage={'state':'partial'},next_attempt_at=now(),
                lease_token='old-process',lease_until=now()+timedelta(minutes=3)))
            await db.commit()
        async def finish(native):
            async with self.Session() as db:
                row=await db.scalar(snapshot(JOB,native,provider='igdb'))
                self.assertEqual(row.payload,cursor)
                row.lease_token=row.lease_until=row.next_attempt_at=None
                row.expires_at=now()+timedelta(days=1)
                await db.commit()
        worker=SimpleNamespace(_run=AsyncMock(side_effect=finish))
        service=GameBackground(self.Session)
        with work_context(Priority.AUTOMATIC,'automatic'):
            deadline=await service.canonical_job('igdb',JOB,'123',self.base,worker)
            self.assertLess(deadline,now()+timedelta(seconds=3))
            worker._run.assert_not_awaited()
            async with self.Session() as db:
                row=await db.scalar(snapshot(JOB,'123',provider='igdb'))
                row.lease_until=now()-timedelta(seconds=1);await db.commit()
            await service.canonical_job('igdb',JOB,'123',self.base,worker)
            worker._run.assert_awaited_once_with('123')
        async with self.Session() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(MetadataSnapshot).where(
                MetadataSnapshot.provider=='igdb',MetadataSnapshot.kind==JOB,MetadataSnapshot.external_id=='123')),1)

    async def test_status_uses_only_local_names_for_games_people_and_studios(self):
        from core.game_background import summary
        async with self.Session() as db:
            person=CatalogueEntity(kind='person',name='Local Actor')
            studio=CatalogueEntity(kind='organization',name='Local Studio')
            db.add_all([person,studio]);await db.flush()
            db.add_all([CatalogueIdentity(entity_id=person.id,namespace='imdb.person',external_id='nm123',source='imdb'),
                CatalogueIdentity(entity_id=studio.id,namespace='igdb.company',external_id='123',source='igdb')])
            runner=await state(db);runner.lease_until=now()+timedelta(minutes=1)
            await db.commit()
            with patch.object(ProviderHTTP,'request',side_effect=AssertionError('Status must never fetch providers')):
                for key,expected in [('game:10',(await db.get(CatalogueEntity,self.base)).name),
                        ('person:nm123','Local Actor'),('studio:123','Local Studio'),('game:999999',None)]:
                    runner.payload={**runner.payload,'current':key};await db.commit()
                    result=await summary(db)
                    self.assertEqual(result['current'],key)
                    self.assertEqual(result['current_name'],expected)

    async def test_cancelled_stage_is_resumed_once_by_a_replacement_runner(self):
        entered=asyncio.Event()
        service=GameBackground(self.Session)
        service.seed_lists=AsyncMock(return_value=False)
        async def blocked(item):entered.set();await asyncio.Event().wait()
        service.game=blocked
        async with self.Session() as db:
            await observe(db,[('10',self.base)],category=1,source='restart-test')
            await db.commit()
        task=asyncio.create_task(service.tick())
        try:
            await asyncio.wait_for(entered.wait(),5)
            other=GameBackground(self.Session)
            self.assertFalse(await other.tick())
        finally:
            task.cancel();await asyncio.gather(task,return_exceptions=True)
        async with self.Session() as db:
            runner=await state(db)
            self.assertIsNone(runner.lease_token)
            self.assertIsNone(runner.payload.get('current'))
            self.assertEqual((await db.get(GameMapperItem,'game:10')).processed,0)
            # A killed process can leave a stale display lease. The live DB
            # advisory lock, rather than this hint, determines ownership.
            runner.lease_token='abandoned';runner.lease_until=now()+timedelta(minutes=2)
            await db.commit()
        replacement=GameBackground(self.Session)
        replacement.seed_lists=AsyncMock(return_value=False)
        replacement.game=AsyncMock(return_value=now()+timedelta(days=1))
        self.assertTrue(await replacement.tick())
        replacement.game.assert_awaited_once()
        async with self.Session() as db:
            item=await db.get(GameMapperItem,'game:10')
            self.assertEqual((item.processed,item.quanta),(1,1))

    async def test_automatic_batch_does_not_yield_to_its_own_slow_requests(self):
        entered=asyncio.Event();release=asyncio.Event();active=0;sent=[]
        http=ProviderHTTP(session_factory=self.Session)
        async def request(index):
            nonlocal active
            with work_context(Priority.AUTOMATIC,'automatic',f'work:{index}',replace=True):
                async with http.lane('igdb'):
                    active+=1;sent.append(index)
                    self.assertLessEqual(active,2)
                    if active==2:entered.set()
                    await release.wait();active-=1
        with patch.dict('core.catalogue_providers.INTERVALS',{'igdb':.01}):
            tasks=[asyncio.create_task(request(index)) for index in range(4)]
            try:
                await asyncio.wait_for(entered.wait(),3)
                await asyncio.sleep(.7)
                self.assertFalse(any(task.done() for task in tasks))
                release.set();await asyncio.wait_for(asyncio.gather(*tasks),5)
                self.assertEqual(len(sent),4)
            finally:
                release.set();await asyncio.gather(*tasks,return_exceptions=True)

    async def test_automatic_waiter_still_yields_to_queued_higher_priority_work(self):
        from core.game_work import PriorityYield
        entered=asyncio.Event();release=asyncio.Event();active=0
        http=ProviderHTTP(session_factory=self.Session)
        async def held():
            nonlocal active
            with work_context(Priority.FINISH,'higher',replace=True):
                async with http.lane('igdb'):
                    active+=1
                    if active==2:entered.set()
                    await release.wait()
        async def queued(priority):
            with work_context(priority,str(priority),replace=True):
                async with http.lane('igdb'):
                    self.assertNotEqual(priority,Priority.AUTOMATIC)
        with patch.dict('core.catalogue_providers.INTERVALS',{'igdb':.01}):
            tasks=[asyncio.create_task(held()) for _ in range(2)]
            try:
                await asyncio.wait_for(entered.wait(),3)
                automatic=asyncio.create_task(queued(Priority.AUTOMATIC));tasks.append(automatic)
                higher=asyncio.create_task(queued(Priority.FINISH));tasks.append(higher)
                with self.assertRaises(PriorityYield):await asyncio.wait_for(automatic,3)
                self.assertFalse(higher.done())
                release.set();await asyncio.wait_for(higher,5)
            finally:
                release.set();await asyncio.gather(*tasks,return_exceptions=True)

    async def test_cancellation_during_admission_commit_releases_unhanded_slot(self):
        from core.game_work import _dispatchers,_lock_key
        from sqlalchemy.sql import operators
        blocked,release=asyncio.Event(),asyncio.Event()
        execute,commit=AsyncSession.execute,AsyncSession.commit
        async def marked(db,statement,*args,**kwargs):
            if (isinstance(statement,Delete) and statement.table.name=='game_provider_tickets'
                    and getattr(statement.whereclause,'operator',None) is operators.eq):
                db.info['block_mapper_admission']=True
            return await execute(db,statement,*args,**kwargs)
        async def committed(db):
            result=await commit(db)
            if db.info.pop('block_mapper_admission',False) and not blocked.is_set():
                blocked.set();await release.wait()
            return result
        async def request():
            with work_context(Priority.FINISH,'viewer',replace=True):
                async with ProviderHTTP(session_factory=self.Session).lane('igdb'):
                    self.fail('Cancelled waiter must not send HTTP')
        with patch.object(AsyncSession,'execute',marked),patch.object(AsyncSession,'commit',committed):
            task=asyncio.create_task(request())
            try:
                await asyncio.wait_for(blocked.wait(),5)
                pump=next(value.task for value in _dispatchers.values() if value.sessions is self.Session)
                task.cancel();await asyncio.gather(task,return_exceptions=True)
            finally:
                release.set()
                task.cancel();await asyncio.gather(task,return_exceptions=True)
            await asyncio.wait_for(asyncio.shield(pump),5)
        async with self.Session() as db:
            for key in [f'catalogue-igdb-slot:{index}' for index in range(3)]:
                self.assertTrue(await db.scalar(text('SELECT pg_try_advisory_xact_lock(:key)'),{'key':_lock_key(key)}))
            self.assertEqual(await db.scalar(select(func.count()).select_from(GameProviderTicket)),0)

    async def test_lone_continuation_expands_but_arrivals_restore_small_turns(self):
        from core.game_imdb_discovery import acquisition_budget,JOB
        async with self.Session() as db:
            db.add(MetadataSnapshot(provider='imdb',kind=JOB,external_id='nm1',
                next_attempt_at=now(),lease_until=now()+timedelta(minutes=3),lease_token='running'))
            await db.commit()
        self.assertEqual(await acquisition_budget(self.Session,0),4)
        self.assertEqual(await acquisition_budget(self.Session,4),10)
        from core.game_work import demand,job_key
        async with self.Session() as db:
            db.add(MetadataSnapshot(provider='imdb',kind=JOB,external_id='nm9',next_attempt_at=now()))
            with work_context(Priority.AUTOMATIC,'automatic'):
                await demand(db,job_key('imdb',JOB,'nm9'))
            await db.commit()
        self.assertEqual(await acquisition_budget(self.Session,4),10)
        for native in ('nm2','nm3'):
            async with self.Session() as db:
                db.add(MetadataSnapshot(provider='imdb',kind=JOB,external_id=native,next_attempt_at=now()))
                await db.commit()
            self.assertEqual(await acquisition_budget(self.Session,4),4)

    async def test_reverse_identity_subset_reuses_fresh_empty_and_positive_evidence(self):
        from core.game_imdb_discovery_source import GameIMDbDiscoverySource
        source=GameIMDbDiscoverySource(self.Session)
        fetched=now()-timedelta(hours=1)
        fetched=fetched.replace(microsecond=fetched.microsecond//1000*1000)
        async with self.Session() as db:
            db.add(MetadataSnapshot(provider='wikidata',kind='game_imdb_reverse',external_id='v1:cover',
                payload={'items':{'tt1':[],'tt2':['Q2'],'tt3':['Q3']}},
                fetched_at=fetched,expires_at=now()+timedelta(days=1)))
            await db.commit()
        with patch.object(source.sources.api.http,'request',AsyncMock(return_value={'results':{'bindings':[]}})) as http:
            result=await source.items(['tt1','tt2'])
            self.assertEqual(result['data']['items'],{'tt1':[],'tt2':['Q2']})
            self.assertEqual(result['support_snapshots'][0]['key'],'cover')
            self.assertEqual(result['fetched_at'],fetched.isoformat())
            http.assert_not_awaited()
            async with self.Session() as db:
                row=await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.external_id=='v1:cover'))
                row.expires_at=now()-timedelta(seconds=1)
                await db.commit()
            result=await source.items(['tt1','tt2'])
            self.assertEqual(result['data']['items'],{'tt1':[],'tt2':[]})
            http.assert_awaited_once()

    async def asyncSetUp(self):
        await fixture.GamesDatabaseTests.asyncSetUp(self)
        async with self.Session() as db:
            db.add(CatalogueIdentity(entity_id=self.base,namespace='igdb.game',external_id='10',source='igdb'))
            await state(db);await db.commit()

    async def test_interest_is_idempotent_and_counts_distinct_users_in_each_class(self):
        async with self.Session() as db:
            for _ in range(3):await observe(db,[('10',self.base)],category=1,source=f'list:{self.user.id}',user_id=self.user.id)
            await observe(db,[('10',self.base)],category=1,source=f'list:{self.other.id}',user_id=self.other.id)
            await observe(db,[('10',self.base)],category=2,source=f'view:{self.user.id}',user_id=self.user.id)
            await db.commit()
            item=await db.get(GameMapperItem,'game:10')
            self.assertEqual(item.counts,{'1':2,'2':1})
            self.assertEqual((item.category,item.weight),(1,2))

    async def test_shared_contributor_accumulates_games_without_role_duplicates(self):
        from core.game_background import add_items
        async with self.Session() as db:
            await observe(db,[('10',self.base),('20',self.unknown)],category=1,source='one',user_id=self.user.id)
            await add_items(db,'person',[('nm1',None)])
            db.add_all([GameMapperEdge(game=native,target='person:nm1') for native in ('10','20')])
            await db.flush();await refresh_weights(db,['10','20']);await db.commit()
            item=await db.get(GameMapperItem,'person:nm1')
            self.assertEqual((item.weight,item.counts),(2,{'1':2}))

    async def test_automatic_jobs_are_excluded_until_foreground_promotes_them(self):
        from core.game_imdb_enrichment import request
        async with self.Session() as db:
            with work_context(Priority.AUTOMATIC,'automatic'):
                await request(db,self.base)
            await db.commit()
            self.assertIsNone(await db.scalar(select(MetadataSnapshot.id).where(eligible_jobs())))
            with work_context(Priority.DIRECT,'user:1'):
                await request(db,self.base)
            await db.commit()
            self.assertIsNotNone(await db.scalar(select(MetadataSnapshot.id).where(eligible_jobs())))

    async def test_direct_request_bypasses_all_unsent_lower_priority_work(self):
        from core.game_work import Dispatcher
        starts=[];low_active=0;max_low=0
        first=asyncio.Event();both=asyncio.Event();release=asyncio.Event()
        pumps=[Dispatcher(self.Session,'igdb') for _ in range(3)]
        async def lower(index):
            nonlocal low_active,max_low
            with work_context(Priority.FIRST_WORKS,'background',f'work:{index}',replace=True):
                async with pumps[index%2].enter('igdb'):
                    starts.append(index);low_active+=1;max_low=max(max_low,low_active)
                    if index==0:first.set()
                    if low_active==2:both.set()
                    if index<2:await release.wait()
                    else:await asyncio.sleep(.01)
                    low_active-=1
        async def direct():
            with work_context(Priority.DIRECT,'user:1',replace=True):
                async with pumps[2].enter('igdb'):
                    self.assertEqual(low_active,2)
                    starts.append('direct')
        with patch.dict('core.catalogue_providers.INTERVALS',{'igdb':.01}):
            tasks=[asyncio.create_task(lower(0))]
            try:
                await asyncio.wait_for(first.wait(),3)
                tasks.append(asyncio.create_task(lower(1)))
                await asyncio.wait_for(both.wait(),3)
                tasks.extend(asyncio.create_task(lower(index)) for index in range(2,8))
                await asyncio.wait_for(direct(),3)
                self.assertEqual(starts,[0,1,'direct'])
                release.set();await asyncio.wait_for(asyncio.gather(*tasks),10)
            finally:
                release.set()
                for task in tasks:
                    if not task.done():task.cancel()
                await asyncio.gather(*tasks,return_exceptions=True)
        self.assertEqual(max_low,2)
        async with self.Session() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(GameProviderTicket)),0)

    async def test_foreground_prefers_reserve_borrows_background_and_caps_total_at_three(self):
        from core.game_work import _lock_key
        http=ProviderHTTP(session_factory=self.Session)
        started=asyncio.Queue();release=[asyncio.Event() for _ in range(4)]
        async def direct(index):
            with work_context(Priority.DIRECT,f'user:{index}',replace=True):
                async with http.lane('igdb'):
                    started.put_nowait(index);await release[index].wait()
        tasks=[]
        with patch.dict('core.catalogue_providers.INTERVALS',{'igdb':.01}):
            try:
                tasks.append(asyncio.create_task(direct(0)))
                self.assertEqual(await asyncio.wait_for(started.get(),3),0)
                async with self.Session() as db:
                    for index in (0,1,2):
                        free=await db.scalar(text('SELECT pg_try_advisory_xact_lock(:key)'),
                            {'key':_lock_key(f'catalogue-igdb-slot:{index}')})
                        self.assertEqual(free,index!=2)
                for index in (1,2):
                    tasks.append(asyncio.create_task(direct(index)))
                    self.assertEqual(await asyncio.wait_for(started.get(),3),index)
                tasks.append(asyncio.create_task(direct(3)))
                with self.assertRaises(TimeoutError):await asyncio.wait_for(started.get(),.15)
                release[2].set()
                self.assertEqual(await asyncio.wait_for(started.get(),3),3)
            finally:
                for event in release:event.set()
                await asyncio.gather(*tasks,return_exceptions=True)

    async def test_stop_cancels_an_automatic_admission_without_poisoning_evidence(self):
        http=ProviderHTTP(session_factory=self.Session)
        async with self.Session() as db:
            runner=await state(db);runner.enabled=False;await db.commit()
        with work_context(Priority.AUTOMATIC,'automatic',replace=True):
            with self.assertRaises(asyncio.CancelledError):
                async with http.lane('igdb'):self.fail('Stopped automatic work sent HTTP')
        async with self.Session() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(GameProviderTicket)),0)

    async def test_cancelled_waiter_releases_ticket_and_does_not_reserve_a_slot(self):
        http=ProviderHTTP(session_factory=self.Session)
        entered=asyncio.Event();release=asyncio.Event()
        active=0
        async def held():
            nonlocal active
            with work_context(Priority.FINISH,'one',replace=True):
                async with http.lane('igdb'):
                    active+=1
                    if active==2:entered.set()
                    await release.wait()
        async def waiting():
            with work_context(Priority.FINISH,'two',replace=True):
                async with http.lane('igdb'):self.fail('Lower-priority concurrency exceeded two')
        holders=[asyncio.create_task(held()) for _ in range(2)]
        try:
            await asyncio.wait_for(entered.wait(),3)
            waiter=asyncio.create_task(waiting());await asyncio.sleep(.05)
            waiter.cancel();await asyncio.gather(waiter,return_exceptions=True)
            async with self.Session() as db:self.assertEqual(await db.scalar(select(func.count()).select_from(GameProviderTicket)),0)
        finally:release.set();await asyncio.gather(*holders)

    async def test_expired_still_relevant_game_is_due_again(self):
        service=GameBackground(self.Session)
        async with self.Session() as db:
            await observe(db,[('10',self.base)],category=2,source='view',user_id=self.user.id)
            runner=await state(db);runner.payload={'lists_due':(now()+timedelta(hours=1)).isoformat()}
            item=await db.get(GameMapperItem,'game:10');item.processed=1;item.next_at=now()-timedelta(seconds=1)
            await db.commit()
        service.game=AsyncMock(return_value=now()+timedelta(days=7))
        try:self.assertTrue(await service.tick())
        finally:await service.close()
        service.game.assert_awaited_once()
        async with self.Session() as db:
            self.assertEqual((await db.get(GameMapperItem,'game:10')).processed,2)

    async def test_stopped_runner_does_not_advance_jobs_or_seed(self):
        service=GameBackground(self.Session)
        async with self.Session() as db:
            runner=await state(db);runner.enabled=False;await db.commit()
        service.seed_lists=AsyncMock()
        try:self.assertFalse(await service.tick())
        finally:await service.close()
        service.seed_lists.assert_not_awaited()

    async def test_game_expands_eight_cast_eight_crew_two_companies_with_shared_people_once(self):
        from core.game_imdb_enrichment import JOB
        async with self.Session() as db:
            await observe(db,[('10',self.base)],category=1,source='list',user_id=self.user.id)
            for index in range(23):
                company=index>=20
                person=CatalogueEntity(kind='organization' if company else 'person',name=f'Contributor {index}',attributes={})
                db.add(person);await db.flush()
                db.add(CatalogueIdentity(entity_id=person.id,namespace='igdb.company' if company else 'imdb.person',
                    external_id=str(index+100) if company else f'nm{index+1}',source='igdb' if company else 'imdb'))
                db.add(CatalogueCredit(work_id=self.base,contributor_id=person.id,provider='igdb' if company else 'imdb',
                    source_key=f'fixture:{index}',role='publisher' if index==22 else 'developer' if company else 'actor' if index<10 else 'writer',
                    scope='title',position=index,attributes={}))
                if index==0:
                    db.add(CatalogueCredit(work_id=self.base,contributor_id=person.id,provider='imdb',
                        source_key='fixture:shared',role='director',scope='title',position=0,attributes={}))
            db.add(MetadataSnapshot(provider='imdb',kind=JOB,external_id=str(self.base),expires_at=now()+timedelta(days=7),coverage={}))
            item=await db.get(GameMapperItem,'game:10');item.checked_at=now()
            await db.commit()
        service=GameBackground(self.Session)
        service.sources.cache.get=AsyncMock(return_value={'data':{'rows':[{'id':10,'name':'Fixture','game_type':{'id':0},'platforms':[{'id':6,'name':'PC (Microsoft Windows)'}]}]},
            'fetched_at':(now()-timedelta(days=1)).isoformat()})
        service.canonical_job=AsyncMock(return_value=now()+timedelta(days=7))
        try:await service.game(item)
        finally:await service.close()
        async with self.Session() as db:
            keys=set(await db.scalars(select(GameMapperEdge.target)))
            self.assertEqual(len([key for key in keys if key.startswith('person:')]),15)
            self.assertEqual(len([key for key in keys if key.startswith('studio:')]),2)
            self.assertTrue({'studio:120','studio:122'}<=keys)
            self.assertTrue({f'person:nm{index}' for index in range(1,9)}<=keys)
            self.assertNotIn('person:nm9',keys)

    async def test_admin_control_requires_admin_and_returns_compact_status(self):
        from routers.admin_game_runner import router
        self.client._transport.app.include_router(router)
        self.assertEqual((await self.client.get('/admin/game-runner')).status_code,403)
        async with self.Session() as db:
            user=await db.get(type(self.user),self.user.id);user.is_admin=True;await db.commit()
        self.viewer.is_admin=True
        response=await self.client.patch('/admin/game-runner',json={'enabled':False})
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['state'],'stopped')
        self.assertEqual(response.json()['categories'],{'1':0,'2':0,'3':0})
        self.assertNotIn('payload',response.json())
        self.assertIn('no-store',response.headers['cache-control'])

    async def test_cancellation_during_admitted_http_closes_slot_and_removes_ticket(self):
        from core.game_work import _lock_key
        entered=asyncio.Event()
        async def request():
            with work_context(Priority.DIRECT,'viewer',replace=True):
                async with ProviderHTTP(session_factory=self.Session).lane('igdb'):
                    entered.set();await asyncio.Event().wait()
        task=asyncio.create_task(request())
        try:await asyncio.wait_for(entered.wait(),5)
        finally:task.cancel();await asyncio.gather(task,return_exceptions=True)
        async with self.Session() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(GameProviderTicket)),0)
            for slot in range(3):
                self.assertTrue(await db.scalar(text('SELECT pg_try_advisory_xact_lock(:key)'),
                    {'key':_lock_key(f'catalogue-igdb-slot:{slot}')}))

    async def test_cooldown_published_during_final_admission_read_is_retained(self):
        from models.catalogue import MetadataProviderBudget
        async with self.Session() as db:
            # A Retry-After publisher is an already admitted HTTP request.
            db.add(MetadataProviderBudget(provider='igdb'));await db.commit()
        get=AsyncSession.get;execute=AsyncSession.execute
        read=asyncio.Event();resume=asyncio.Event();publishing=asyncio.Event()
        async def paused(db,model,*args,**kwargs):
            result=await get(db,model,*args,**kwargs)
            if model is MetadataProviderBudget and kwargs.get('populate_existing'):
                read.set();await resume.wait()
            return result
        async def observed(db,statement,*args,**kwargs):
            if isinstance(statement,Update) and statement.table.name=='catalogue_provider_budgets':publishing.set()
            return await execute(db,statement,*args,**kwargs)
        http=ProviderHTTP(session_factory=self.Session)
        async def request():
            with work_context(Priority.DIRECT,'viewer',replace=True):
                async with http.lane('igdb'):pass
        with patch.object(AsyncSession,'get',paused),patch.object(AsyncSession,'execute',observed):
            task=asyncio.create_task(request());cooldown=None
            try:
                await asyncio.wait_for(read.wait(),5)
                cooldown=asyncio.create_task(http.defer('igdb',10))
                await asyncio.wait_for(publishing.wait(),5)
                await asyncio.sleep(.1);resume.set()
                await asyncio.wait_for(task,5);await asyncio.wait_for(cooldown,5)
            finally:
                resume.set()
                for pending in (task,cooldown):
                    if pending and not pending.done():pending.cancel()
                await asyncio.gather(*(pending for pending in (task,cooldown) if pending),return_exceptions=True)
        async with self.Session() as db:
            self.assertGreater((await db.get(MetadataProviderBudget,'igdb')).next_request_at,now()+timedelta(seconds=8))

    async def test_unscoped_requests_reuse_fairness_state(self):
        http=ProviderHTTP(session_factory=self.Session)
        with patch.dict('core.catalogue_providers.INTERVALS',{'igdb':.001}):
            for _ in range(4):
                with work_context(Priority.DIRECT,'viewer',replace=True):
                    async with http.lane('igdb'):pass
        async with self.Session() as db:
            self.assertEqual(await db.scalar(select(func.count()).select_from(GameProviderTurn)),2)

    async def test_discovery_retry_uses_deadline_and_surfaces_failure(self):
        from core.game_imdb_discovery import JOB
        from core.catalogue_providers import ProviderError
        retry=now()+timedelta(hours=1)
        async with self.Session() as db:
            db.add(MetadataSnapshot(provider='imdb',kind=JOB,external_id='nm1',next_attempt_at=retry,error_code='unavailable',coverage={}))
            await db.commit()
        service=GameBackground(self.Session)
        service.canonical_job=AsyncMock(return_value=now()+timedelta(days=7))
        service.discovery._run=AsyncMock()
        from types import SimpleNamespace
        try:
            with self.assertRaises(ProviderError) as error:
                await service.contributor(SimpleNamespace(kind='person',native='nm1',entity_id=self.base))
            self.assertGreater(error.exception.retry_after,3500)
            service.discovery._run.assert_not_awaited()
        finally:await service.close()

    async def test_removed_list_cleanup_is_bounded_and_resumable(self):
        async with self.Session() as db:
            db.add_all([GameMapperInterest(source='list:fixture',game=str(index),category=1,user_id=self.user.id,
                expires_at=now()+timedelta(days=1)) for index in range(600)])
            runner=await state(db);runner.payload={'lists_due':(now()+timedelta(hours=1)).isoformat()}
            await db.commit()
        service=GameBackground(self.Session)
        try:
            async with self.Session() as db:
                runner=await state(db)
                await service.seed_lists(db,runner);await db.commit()
                self.assertEqual(await db.scalar(select(func.count()).select_from(GameMapperInterest)),350)
                self.assertIsNotNone(runner.payload['cleanup_cursor'])
                await service.seed_lists(db,runner);await db.commit()
                self.assertEqual(await db.scalar(select(func.count()).select_from(GameMapperInterest)),100)
        finally:await service.close()

    async def test_runner_migration_roundtrip_preserves_catalogue(self):
        import importlib
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        migration=importlib.import_module('migrations.versions.mt080_game_background_runner')
        def roundtrip(connection):
            with Operations.context(MigrationContext.configure(connection)):
                migration.downgrade();migration.upgrade()
        async with self.engine.begin() as connection:await connection.run_sync(roundtrip)
        async with self.Session() as db:
            self.assertIsNotNone(await db.get(CatalogueEntity,self.base))
            self.assertEqual(await db.scalar(select(func.count()).select_from(GameMapperItem)),0)

    async def test_catalogue_frontier_advances_while_old_contributor_work_remains(self):
        async with self.Session() as db:
            db.add(GameMapperItem(key='person:nm1',kind='person',native='nm1',category=4,
                weight=1,counts={'4':1},next_at=now()))
            runner=await state(db)
            runner.payload={'lists_due':(now()+timedelta(hours=1)).isoformat(),
                'highlights_due':(now()+timedelta(hours=1)).isoformat()}
            await db.commit()
        service=GameBackground(self.Session)
        service.seed_browse=AsyncMock();service.contributor=AsyncMock()
        try:self.assertTrue(await service.tick())
        finally:await service.close()
        service.seed_browse.assert_awaited_once()
        service.contributor.assert_not_awaited()

    async def test_list_seed_includes_listed_expansions_and_unlisted_favorites(self):
        from models.games import GameEntry,GameChildState,GameFavorite
        async with self.Session() as db:
            entry=GameEntry(user_id=self.user.id,work_id=self.base,status='playing')
            db.add(entry);await db.flush()
            db.add_all([CatalogueIdentity(entity_id=self.child,namespace='igdb.game',external_id='20',source='igdb'),
                CatalogueIdentity(entity_id=self.unknown,namespace='igdb.game',external_id='30',source='igdb'),
                GameChildState(user_id=self.user.id,work_id=self.child,base_work_id=self.base,
                    base_entry_id=entry.id,status='playing'),GameFavorite(user_id=self.user.id,work_id=self.unknown,position=0)])
            await db.commit()
        service=GameBackground(self.Session)
        try:
            for _ in range(3):
                async with self.Session() as db:
                    runner=await state(db);await service.seed_lists(db,runner);await db.commit()
        finally:await service.close()
        async with self.Session() as db:
            items=list(await db.scalars(select(GameMapperItem)))
            self.assertEqual({item.native for item in items},{'10','20','30'})
            self.assertTrue(all(item.category==1 and item.weight==1 for item in items))

    async def test_foreground_prunes_obsolete_state_with_mapper_stopped(self):
        async with self.Session() as db:
            runner=await state(db);runner.enabled=False
            db.add(GameProviderTurn(key='obsolete',served_at=now()-timedelta(days=8)))
            db.add(GameWorkDemand(key='cache:igdb:game_browse_pages:obsolete',origin=5,priority=5,
                owner='automatic',active_until=now()-timedelta(days=2)))
            await db.commit()
        with work_context(Priority.DIRECT,'viewer',replace=True):
            async with ProviderHTTP(session_factory=self.Session).lane('igdb'):pass
        async with self.Session() as db:
            self.assertIsNone(await db.get(GameProviderTurn,'obsolete'))
            self.assertIsNone(await db.get(GameWorkDemand,'cache:igdb:game_browse_pages:obsolete'))

    async def test_automatic_person_publication_cannot_escape_through_identity_backfill(self):
        from core.game_imdb_person import GameIMDbPersonEnrichment,request
        from core.game_person_identity import backfill,JOB,request as identity_request
        from core.game_imdb_enrichment import snapshot
        from test_game_imdb_person_pg import credit,page
        from types import SimpleNamespace
        async with self.Session() as db:
            person=CatalogueEntity(kind='person',name='Automatic actor',attributes={});db.add(person);await db.flush()
            person_id=person.id
            db.add_all([CatalogueIdentity(entity_id=person.id,namespace='imdb.person',external_id='nm1',source='imdb'),
                CatalogueIdentity(entity_id=self.base,namespace='imdb.title',external_id='tt1',source='reviewed',
                    evidence={'title_type':'videoGame','igdb_id':'10'})])
            await db.flush()
            with work_context(Priority.AUTOMATIC,'automatic',replace=True):await request(db,person.id)
            await db.commit()
        service=GameIMDbPersonEnrichment(self.Session,source=SimpleNamespace(credits=AsyncMock(return_value=page([credit()],1))))
        self.assertEqual(await service.run('nm1'),'ready')
        async with self.Session() as db:
            self.assertEqual(await backfill(db),0)
            self.assertIsNone(await db.scalar(snapshot(JOB,'nm1')))
            with work_context(Priority.DIRECT,'user:1',replace=True):
                await identity_request(db,'nm1',person_id,None,enqueue=True)
            await db.commit()
            self.assertIsNotNone(await db.scalar(snapshot(JOB,'nm1')))

    async def test_concurrent_list_removal_and_other_users_addition_preserve_weight(self):
        from core import game_background
        async with self.Session() as db:
            await observe(db,[('10',self.base)],category=1,source=f'list:{self.user.id}',user_id=self.user.id)
            await db.commit()
        ready=asyncio.Event();release=asyncio.Event();write=game_background.write_weights
        async def pause(db,counts):
            if counts=={'game:10':{}}:
                ready.set();await release.wait()
            return await write(db,counts)
        async def add():
            async with self.Session() as db:
                await observe(db,[('10',self.base)],category=1,source=f'list:{self.other.id}',user_id=self.other.id)
                await db.commit()
        with patch.object(game_background,'write_weights',pause):
            removal=asyncio.create_task(game_background.observe_work(self.base,self.engine,self.user.id,1))
            addition=None
            try:
                await asyncio.wait_for(ready.wait(),5)
                addition=asyncio.create_task(add())
                try:await asyncio.wait_for(asyncio.shield(addition),.2)
                except TimeoutError:pass
                release.set();await asyncio.wait_for(asyncio.gather(removal,addition),5)
            finally:
                release.set()
                for task in (removal,addition):
                    if task and not task.done():task.cancel()
                await asyncio.gather(*(task for task in (removal,addition) if task),return_exceptions=True)
        async with self.Session() as db:
            item=await db.get(GameMapperItem,'game:10')
            self.assertEqual((item.weight,item.counts),(1,{'1':1}))

    async def test_primary_publication_and_detail_observation_do_not_invert_fk_locks(self):
        from core import catalogue,game_background
        from core.game_imdb_enrichment import JOB
        async with self.Session() as db:
            await observe(db,[('10',None)],category=4,source='catalogue')
            db.add(MetadataSnapshot(provider='imdb',kind=JOB,external_id=str(self.base),
                expires_at=now()+timedelta(days=7),coverage={}))
            await db.commit();item=await db.get(GameMapperItem,'game:10')
        locked=asyncio.Event();release=asyncio.Event()
        async def ingest(db,*args):
            work=await db.scalar(select(CatalogueEntity).where(CatalogueEntity.id==self.base).with_for_update())
            locked.set();await release.wait();return work
        service=GameBackground(self.Session)
        service.sources.cache.get=AsyncMock(return_value={'data':{'rows':[{'id':10,'name':'Fixture',
            'game_type':{'id':0},'platforms':[{'id':6,'name':'PC (Microsoft Windows)'}]}]},'fetched_at':now().isoformat()})
        service.canonical_job=AsyncMock(return_value=now()+timedelta(days=7))
        with patch.object(catalogue,'ingest_document',ingest):
            primary=asyncio.create_task(service.game(item));observer=None
            try:
                await asyncio.wait_for(locked.wait(),5)
                observer=asyncio.create_task(game_background.observe_work(self.base,self.engine,self.user.id))
                await asyncio.sleep(.1);release.set()
                await asyncio.wait_for(asyncio.gather(primary,observer),5)
            finally:
                release.set()
                for task in (primary,observer):
                    if task and not task.done():task.cancel()
                await asyncio.gather(*(task for task in (primary,observer) if task),return_exceptions=True)
                await service.close()
        async with self.Session() as db:
            item=await db.get(GameMapperItem,'game:10')
            self.assertEqual(item.entity_id,self.base)
            self.assertEqual(item.category,2)
