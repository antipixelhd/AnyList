"""Cold portfolio extension for benchmark_search; controlled public providers."""
import asyncio
import hashlib
import json
import re
import time
from collections import Counter
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

from sqlalchemy import event

from core.catalogue_normalize import normalize_igdb
from core.game_browse_sources import GameBrowseSources
from core.game_company_works import GameCompanyWorks, status as company_status
from core.game_imdb_discovery import GamePersonDiscovery, JOB as DISCOVERY
from core.game_imdb_discovery_source import GameIMDbDiscoverySource
from core.game_imdb_enrichment import now, snapshot
from core.game_imdb_person import GameIMDbPersonEnrichment, request, COMPONENT
from core.game_person_identity import VERSION as IDENTITY_VERSION
from models import User
from models.catalogue import CatalogueEntity, CatalogueIdentity
from models.game_mapping import GameMappingGeneration
from test_game_imdb_person_pg import credit, page
from test_game_title_match import game
from test_statistics_pg import StatisticsDatabaseTests


async def run(args):
    samples=[]
    for label in args.case:
        fixture=StatisticsDatabaseTests()
        await fixture.asyncSetUp()
        # Mapping references use the separate catalogue-provider metadata base.
        async with fixture.engine.begin() as connection:
            await connection.run_sync(lambda conn:GameMappingGeneration.metadata.create_all(conn))
        counts=Counter()
        stages=Counter()
        def timed(name,operation):
            async def call(*values,**options):
                begin=time.perf_counter()
                try:return await operation(*values,**options)
                finally:stages[name]+=(time.perf_counter()-begin)*1000
            return call
        def sql(conn,cursor,statement,parameters,context,many):
            reading=getattr(getattr(context.compiled,'statement',None),'is_select',False) or statement.lstrip().upper().startswith('SELECT')
            counts['db_reads' if reading else 'db_writes']+=1
            if 'INSERT INTO catalogue_metadata_snapshots' in statement and 'game_person_index' in str(parameters):
                counts['index_writes']+=1
        event.listen(fixture.engine.sync_engine,'before_cursor_execute',sql)
        try:
            async def provider(kind):
                counts['provider_requests']+=1;counts[f'provider:{kind}']+=1
                await asyncio.sleep(args.provider_delay_ms/1000)
            async with fixture.Session() as db:
                viewer=await db.get(User,fixture.user_id);viewer.games_access=True
                entity=CatalogueEntity(kind='person' if label=='game_person_cold' else 'organization',name='Cold portfolio')
                db.add(entity);await db.flush();entity_id=entity.id
                db.add(CatalogueIdentity(entity_id=entity_id,namespace='imdb.person' if label=='game_person_cold' else 'igdb.company',
                    external_id='nm9000000' if label=='game_person_cold' else '9000000',source='imdb' if label=='game_person_cold' else 'igdb'))
                await db.flush()
                if label=='game_person_cold':await request(db,entity_id)
                else:await company_status(db,entity_id,enqueue=True)
                await db.commit()
            fixture.viewer.games_access=True
            counts.clear();started=time.perf_counter();first_ms=None;first_count=None
            if label=='game_person_cold':
                # 60 works crosses IMDb, discovery and index page boundaries.
                rows=[{**credit(f'tt{9000000+i}'),'title':{'id':f'tt{9000000+i}','titleType':{'id':'videoGame'},
                    'titleText':{'text':f'Portfolio {i}'},'releaseYear':{'year':2017}}} for i in range(60)]
                async def connection(native,cursor,**kwargs):
                    await provider('imdb/credits')
                    return page(rows[:50] if cursor is None else rows[50:],60,cursor is None,'next' if cursor is None else None)
                person=GameIMDbPersonEnrichment(fixture.Session,source=SimpleNamespace(credits=connection))
                await person.run('nm9000000')
                # Optional cross-domain identity resolution is a different
                # workload. Give it the same fresh check as cached fixtures.
                async with fixture.Session() as db:
                    component=await db.scalar(snapshot(COMPONENT,'nm9000000'))
                    component.payload={**component.payload,'identity_bridge':{'version':IDENTITY_VERSION,
                        'next_check_at':(now()+timedelta(days=1)).isoformat()}}
                    await db.commit()
                sources=GameBrowseSources(fixture.Session)
                sources.api.validate=lambda *args:None
                async def igdb(endpoint,query):
                    width=len(re.findall(r'query games "q[0-9]+"',query)) if endpoint=='multiquery' else 1
                    counts['igdb_logical_queries']+=width
                    counts[f'igdb_batch_width:{width}']+=1
                    await provider('igdb/'+endpoint)
                    def result(text):
                        found=re.search(r'(?:search |name ~ )"Portfolio ([0-9]+)"',text)
                        return [game(9000000+int(found[1]),f'Portfolio {found[1]}')] if found else []
                    if endpoint=='multiquery':
                        return [{'name':name,'result':result(body)} for name,body in re.findall(r'query games "(q[0-9]+)" \{ (.*?) \};',query)]
                    return result(query)
                sources.api.igdb=igdb
                async def wiki(*args,**kwargs):
                    await provider('wikidata/reverse');return {'results':{'bindings':[]}}
                sources.api.http.request=wiki
                discover=GamePersonDiscovery(fixture.Session,source=GameIMDbDiscoverySource(fixture.Session,sources=sources))
                discover.source.page=timed('acquisition_matching_ms',discover.source.page)
                discover.ingest=timed('ingestion_ms',discover.ingest)
                discover.bindings.save=timed('binding_ms',discover.bindings.save)
                ticks=0
                while ticks<30:
                    ticks+=1
                    async with fixture.Session() as db:
                        job=await db.scalar(snapshot(DISCOVERY,'nm9000000'))
                        if not job or job.coverage.get('complete'):break
                        job.next_attempt_at=now();await db.commit()
                    outcome=await discover.run('nm9000000')
                    if outcome not in ('ready','partial'):
                        async with fixture.Session() as db:
                            job=await db.scalar(snapshot(DISCOVERY,'nm9000000'))
                            raise RuntimeError(f'{outcome}: {job.error_code}')
                    await timed('projection_ms',person.run)('nm9000000')
                    response=await fixture.client.get(f'/tracking/contributors/actor/catalogue:{entity_id}',params={'media_type':'game'})
                    if response.status_code!=200:raise RuntimeError(response.text)
                    data=response.json()
                    if data.get('works') and first_ms is None:
                        first_ms=round((time.perf_counter()-started)*1000,2);first_count=data['known_works']
                expected=60;kind='actor'
            else:
                async def company_page(native,cursor):
                    await provider('igdb/company-page')
                    items=[]
                    for index in range(cursor,min(cursor+25,60)):
                        doc=normalize_igdb(game(9000000+index,f'Portfolio {index}'))
                        items.append({'doc':doc,'record':{'id':index+1,'game':9000000+index,'developer':True},'roles':['developer']})
                    return {'documents':items,'omitted':0,'received':len(items),'cursor':cursor+len(items),'complete':cursor+len(items)==60}
                service=GameCompanyWorks(fixture.Session,source=SimpleNamespace(page=company_page),page_budget=1)
                for _ in range(3):
                    async with fixture.Session() as db:
                        from core.game_company_works import snapshot as company_snapshot
                        job=await db.scalar(company_snapshot('9000000'));job.next_attempt_at=now();await db.commit()
                    await service.run('9000000')
                    if first_ms is None:first_ms=round((time.perf_counter()-started)*1000,2);first_count=25
                expected=60;kind='studio'
            elapsed=round((time.perf_counter()-started)*1000,2)
            response=await fixture.client.get(f'/tracking/contributors/{kind}/catalogue:{entity_id}',params={'media_type':'game'})
            if response.status_code!=200:raise RuntimeError(response.text)
            data=response.json()
            if data['known_works']!=expected:raise RuntimeError(f"Lost works: {data['known_works']}")
            identity=[(row['title'],row['roles']) for row in data['works']]
            payload_bytes=len(response.content)
            while data['has_more']:
                response=await fixture.client.get(f'/tracking/contributors/{kind}/catalogue:{entity_id}',
                    params={'media_type':'game','cursor':data['next_cursor']})
                if response.status_code!=200:raise RuntimeError(response.text)
                data=response.json();payload_bytes+=len(response.content)
                identity.extend((row['title'],row['roles']) for row in data['works'])
            if len(identity)!=expected or {row[0] for row in identity}!={f'Portfolio {i}' for i in range(expected)}:
                raise RuntimeError('Missing or duplicate paginated portfolio works')
            measured=dict(counts)
            counts.clear();warm_started=time.perf_counter()
            response=await fixture.client.get(f'/tracking/contributors/{kind}/catalogue:{entity_id}',params={'media_type':'game'})
            warm_ms=round((time.perf_counter()-warm_started)*1000,2)
            if response.status_code!=200 or counts['provider_requests'] or counts['db_writes']:
                raise RuntimeError(f'Warm portfolio performed acquisition or writes: {dict(counts)}')
            samples.append({'case':label,'ms':elapsed,'first_works_ms':first_ms,'first_works_count':first_count,
                'total':data['known_works'],'result_identity':hashlib.sha256(json.dumps(identity).encode()).hexdigest(),
                'payload_bytes':payload_bytes,'warm_ms':warm_ms,'warm_db_reads':counts['db_reads'],
                'stages_ms':{key:round(value,2) for key,value in stages.items()},**measured})
        finally:
            event.remove(fixture.engine.sync_engine,'before_cursor_execute',sql)
            await fixture.asyncTearDown()
    target=Path(args.output);target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps({'environment':'controlled cold portfolio workers + ASGI + disposable PostgreSQL; scheduler sleeps excluded',
        'provider_delay_ms':args.provider_delay_ms,'baseline_ref':args.baseline_ref,'samples':samples},indent=2),encoding='utf8')
    for row in samples:print(json.dumps(row))
