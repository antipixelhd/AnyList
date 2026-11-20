"""Competing cold portfolios through real scheduler, caches and provider lanes."""
import asyncio
import hashlib
import json
import re
import time
from collections import Counter
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import event,select,func

from core import game_imdb_discovery as discovery
from core.catalogue_providers import ProviderHTTP
from core.game_browse_sources import GameBrowseSources
from core.game_imdb_discovery_source import GameIMDbDiscoverySource
from core.game_imdb_enrichment import now,snapshot
from core.game_imdb_person import GameIMDbPersonEnrichment,request,COMPONENT
from core.game_person_identity import VERSION as IDENTITY_VERSION,JOB as IDENTITY_JOB
from models.catalogue import CatalogueEntity,CatalogueIdentity,CatalogueCredit
from models.game_mapping import GameMappingGeneration
from test_game_imdb_person_pg import credit,page
from test_game_title_match import game
from test_statistics_pg import StatisticsDatabaseTests


async def run(args):
    shared=args.case==['game_people_pipeline_shared']
    fixture=StatisticsDatabaseTests();await fixture.asyncSetUp()
    async with fixture.engine.begin() as conn:
        await conn.run_sync(lambda c:GameMappingGeneration.metadata.create_all(c))
    tasks=[];counts=Counter();people={};first={};finished={};publications=[]
    active=Counter();maximum=Counter()
    def sql(conn,cursor,statement,parameters,context,many):
        reading=getattr(getattr(context.compiled,'statement',None),'is_select',False) or statement.lstrip().upper().startswith('SELECT')
        counts['db_reads' if reading else 'db_writes']+=1
    try:
        sources=GameBrowseSources(fixture.Session);sources.api.validate=lambda *args:None
        http=ProviderHTTP(session_factory=fixture.Session)
        async def provider(lane):
            counts['provider_requests']+=1;counts['provider:'+lane]+=1
            async with http.lane(lane):
                active[lane]+=1;maximum[lane]=max(maximum[lane],active[lane])
                try:await asyncio.sleep(args.provider_delay_ms/1000)
                finally:active[lane]-=1
        async def igdb(endpoint,query):
            await provider('igdb')
            def rows(body):
                match=re.search(r'(?:search |name ~ )"Portfolio ([0-9]+)"',body)
                return [game(9000000+int(match[1]),f'Portfolio {match[1]}',year=2025-int(match[1])%12)] if match else []
            if endpoint=='multiquery':
                parts=re.findall(r'query games "(q[0-9]+)" \{ (.*?) \};',query)
                counts['igdb_logical_queries']+=len(parts);counts[f'igdb_batch_width:{len(parts)}']+=1
                return [{'name':name,'result':rows(body)} for name,body in parts]
            counts['igdb_logical_queries']+=1;counts['igdb_batch_width:1']+=1
            return rows(query)
        async def wiki(*values,**kwargs):
            await provider('wikidata_query');return {'results':{'bindings':[]}}
        sources.api.igdb=igdb;sources.api.http.request=wiki
        person=GameIMDbPersonEnrichment(fixture.Session)
        for index in range(getattr(args,'pipeline_people',2)):
            native=f'nm{9000000+index}'
            async with fixture.Session() as db:
                entity=CatalogueEntity(kind='person',name=f'Person {index}');db.add(entity);await db.flush()
                people[native]=entity.id
                db.add(CatalogueIdentity(entity_id=entity.id,namespace='imdb.person',external_id=native,source='imdb'))
                await db.flush();await request(db,entity.id);await db.commit()
            records=[{**credit(f'tt{9000000+n}'),'title':{'id':f'tt{9000000+n}',
                'titleType':{'id':'videoGame'},'titleText':{'text':f'Portfolio {n}'},
                'releaseYear':{'year':2025-n%12}}} for n in range((0 if shared else index)*12,(1 if shared else index+1)*12)]
            async def connection(*values,records=records,**kwargs):return page(records,len(records))
            person.source=SimpleNamespace(credits=connection)
            if await person.run(native)!='ready':raise RuntimeError('Source fixture failed')
            async with fixture.Session() as db:
                component=await db.scalar(snapshot(COMPONENT,native))
                component.payload={**component.payload,'identity_bridge':{'version':IDENTITY_VERSION,
                    'next_check_at':(now()+timedelta(days=1)).isoformat()}}
                identity=await db.scalar(snapshot(IDENTITY_JOB,native))
                if identity:identity.next_attempt_at=None;identity.coverage={'complete':True,'state':'complete'}
                await db.commit()
        service=discovery.GamePersonDiscovery(fixture.Session,source=GameIMDbDiscoverySource(fixture.Session,sources=sources))
        # The old scheduler has one serial tick loop; keep its successful-work
        # cadence and production idle interval, without changing its semaphore.
        async def legacy():
            while True:await asyncio.sleep(0 if await service.tick() else 2)
        async def projection():
            while True:
                await person.tick()
                async with fixture.Session() as db:
                    for native,pid in people.items():
                        count=await db.scalar(select(func.count(func.distinct(CatalogueCredit.work_id))).where(
                            CatalogueCredit.contributor_id==pid,CatalogueCredit.scope=='person'))
                        if count and native not in first:
                            first[native]=round((time.perf_counter()-started)*1000,2)
                        if count==12 and native not in finished:
                            finished[native]=round((time.perf_counter()-started)*1000,2)
                        if count!=last.get(native,0):
                            names=set(await db.scalars(select(CatalogueEntity.name).join(CatalogueCredit,
                                CatalogueCredit.work_id==CatalogueEntity.id).where(
                                CatalogueCredit.contributor_id==pid,CatalogueCredit.scope=='person')))
                            start=0 if shared else (int(native[2:])-9000000)*12
                            if names!={f'Portfolio {start+i}' for i in range(count)}:
                                raise RuntimeError('Publication skipped newer source titles')
                            publications.append({'person':native,'count':count,'ms':round((time.perf_counter()-started)*1000,2)})
                            last[native]=count
                if len(finished)==len(people):return
                await asyncio.sleep(.02)
        event.listen(fixture.engine.sync_engine,'before_cursor_execute',sql)
        counts.clear();last={};started=time.perf_counter()
        tasks.append(asyncio.create_task(service.serve() if hasattr(service,'serve') else legacy()))
        tasks.append(asyncio.create_task(projection()))
        await asyncio.wait_for(asyncio.shield(tasks[1]),120)
        elapsed=round((time.perf_counter()-started)*1000,2)
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        identities={}
        async with fixture.Session() as db:
            for native,pid in people.items():
                rows=(await db.execute(select(CatalogueEntity.name,CatalogueCredit.role).join(
                    CatalogueCredit,CatalogueCredit.work_id==CatalogueEntity.id).where(
                    CatalogueCredit.contributor_id==pid,CatalogueCredit.scope=='person').order_by(
                    CatalogueEntity.name,CatalogueCredit.role))).all()
                if len(rows)!=12:raise RuntimeError('Lost or duplicated participation')
                identities[native]=[list(row) for row in rows]
                job=await db.scalar(snapshot(discovery.JOB,native))
                if not job.coverage.get('complete'):raise RuntimeError('Premature completion')
        result={'case':args.case[0],'environment':f'real database/cache/provider lanes; controlled HTTP; {len(people)} pre-indexed 12-work careers',
            'provider_delay_ms':args.provider_delay_ms,'baseline_ref':args.baseline_ref,'ms':elapsed,
            'first_works_ms':first,'complete_ms':finished,'publications':publications,
            'result_identity':hashlib.sha256(json.dumps(identities,sort_keys=True).encode()).hexdigest(),
            'max_provider_concurrency':dict(maximum),**counts}
        target=Path(args.output);target.parent.mkdir(parents=True,exist_ok=True)
        target.write_text(json.dumps(result,indent=2),encoding='utf8');print(json.dumps(result))
    finally:
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        if event.contains(fixture.engine.sync_engine,'before_cursor_execute',sql):
            event.remove(fixture.engine.sync_engine,'before_cursor_execute',sql)
        await fixture.asyncTearDown()
