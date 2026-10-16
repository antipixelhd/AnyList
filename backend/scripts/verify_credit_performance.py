"""Validate final credit access on a preserved, disposable audit fixture only."""
import argparse
import asyncio
import importlib
import json
import os
from pathlib import Path
import re
import subprocess
import time
import uuid
from types import ModuleType

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from audit_credit_performance import attach, measure, plans, source, summary
from core import catalogue, contributor_search, screen_characters
from core.catalogue_additive import fill_document
from core.catalogue_normalize import normalize_tmdb
from core.media_details import work_details
from models.catalogue import CatalogueCredit, CatalogueEntity


def resources():
    """CPU is cumulative cgroup CPU time; memory includes the database cache."""
    raw = subprocess.check_output(['docker','exec','anylist-credit-audit','cat',
        '/sys/fs/cgroup/cpu.stat','/sys/fs/cgroup/memory.stat','/sys/fs/cgroup/memory.current'], text=True)
    lines=raw.splitlines()
    values=dict(line.split() for line in lines if len(line.split())==2)
    return {'cpu_ms':int(values['usage_usec'])/1000, 'memory_mib':int(lines[-1])/1024/1024,
            'anonymous_mib':int(values['anon'])/1024/1024,'file_cache_mib':int(values['file'])/1024/1024,
            'shared_memory_mib':int(values['shmem'])/1024/1024,
            'throttled_ms':int(values.get('throttled_usec',0))/1000}


async def main(args):
    url=os.environ['CREDIT_AUDIT_DATABASE_URL']
    parsed=make_url(url)
    if parsed.host not in ('localhost','127.0.0.1') or parsed.port!=55449 or parsed.database!='anylist_test':
        raise RuntimeError('Only loopback:55449/anylist_test is permitted')
    baseline=json.loads(Path(args.fixture).read_text())
    schema=baseline['schema']
    if not re.fullmatch(r'credit_audit_[a-f0-9]{12}',schema):
        raise RuntimeError('Invalid audit schema')
    engine=create_async_engine(url,pool_size=20,max_overflow=10,
        connect_args={'server_settings':{'search_path':schema+',public','statement_timeout':'120000'}})
    attach(engine)
    sessions=async_sessionmaker(engine,expire_on_commit=False)
    report={'schema':schema,'limits':'PostgreSQL 2 CPU / 2 GiB / 512 MiB shm; Python on Windows host',
            'reads':{},'loads':{},'writes':{},'stress':{}}
    if args.skip_reads:
        report=json.loads(Path(args.output).read_text())
        if report['schema']!=schema:raise RuntimeError('Mismatched verification schema')
    def save(phase,value):
        Path(args.output).write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps({'phase':phase,'value':value}),flush=True)

    async def measured(operation, *, capture=False, memory=False):
        before=resources(); cpu=time.process_time()
        value, result, queries=await measure(operation,capture=capture,memory=memory)
        result['python_cpu_ms']=round((time.process_time()-cpu)*1000,3)
        after=resources()
        result.update(database_cpu_ms=round(after['cpu_ms']-before['cpu_ms'],3),
            database_throttled_ms=round(after['throttled_ms']-before['throttled_ms'],3),
            database_memory_mib=round(after['memory_mib'],3))
        result.update(database_anonymous_mib=round(after['anonymous_mib'],3),
            database_file_cache_mib=round(after['file_cache_mib'],3),database_shared_memory_mib=round(after['shared_memory_mib'],3))
        return value,result,queries

    try:
        # Install the exact final migration, rather than benchmark an earlier
        # in-process version. DDL/backfill is outside request measurements.
        if not args.skip_reads:
            async with engine.begin() as conn:
                await conn.execute(text("SET LOCAL statement_timeout='0'"))
                def migrate(connection):
                    with Operations.context(MigrationContext.configure(connection)):
                        module=importlib.import_module('migrations.versions.mt050_contributor_search')
                        module.downgrade();module.upgrade()
                await conn.run_sync(migrate)
                await conn.execute(text('ANALYZE catalogue_contributor_search'))
            save('migration','applied and backfilled')
        old=ModuleType('credit_audit_baseline_search')
        exec(subprocess.check_output(['git','show','439abaf:backend/core/contributor_search.py'],text=True),old.__dict__)
        async def search(term='Alex',kind='all',implementation=contributor_search.local_matches):
            async with sessions() as db:
                return contributor_search.ranked_results(await implementation(db,term,kind),term)
        async def detail():
            async with sessions() as db:
                return await work_details(db,await db.get(CatalogueEntity,1),extras=False)
        for name,term,kind in ([] if args.skip_reads else [('common','Alex','all'),('short','Al','all'),('studio','Studio','studio'),
            ('exact','Alex Person 004000','actor'),('alias','Archive Alias 000123','all'),('missing','zzzz no match','all')]):
            expected=await search(term,kind,old.local_matches)
            actual=await search(term,kind)
            assert expected==actual,(name,'search results changed')
            stages={}
            for stage,implementation in [('original_query_with_root_index',old.local_matches),('summary',contributor_search.local_matches)]:
                samples=[]
                for _ in range(5):
                    value,stats,queries=await measured(lambda:search(term,kind,implementation),capture=True)
                    samples.append(stats)
                stages[stage]={'wall_ms':summary([s['wall_ms'] for s in samples]),
                    'database_cpu_ms':summary([s['database_cpu_ms'] for s in samples]),
                    'python_cpu_ms':summary([s['python_cpu_ms'] for s in samples]),
                    'last':samples[-1],'plans':await plans(engine,queries)}
            report['reads'][name]={'equal_results':True,**stages}
            save('reads/'+name,{k:{f:v for f,v in r.items() if f!='plans'} for k,r in stages.items()})
        # Amortize resource-probe overhead across ten sequential requests.
        report['cpu_batches']={}
        for name,term,kind in [('common','Alex','all'),('studio','Studio','studio')]:
            stages={}
            for stage,implementation in [('original_query_with_root_index',old.local_matches),('summary',contributor_search.local_matches)]:
                async def cpu_batch():
                    for _ in range(10):
                        await search(term,kind,implementation)
                _,stats,_=await measured(cpu_batch)
                stages[stage]={'requests':10,'per_request_database_cpu_ms':round(stats['database_cpu_ms']/10,3),
                    'per_request_python_cpu_ms':round(stats['python_cpu_ms']/10,3),'batch':stats}
            report['cpu_batches'][name]=stages
            save('cpu_batches/'+name,stages)
        ready=asyncio.Event()
        arrived=0
        async def warm_connection():
            nonlocal arrived
            async with engine.connect() as conn:
                await conn.execute(text('SELECT 1'))
                arrived+=1
                if arrived==24:ready.set()
                await ready.wait()
        if not args.skip_reads:
            await asyncio.gather(*(warm_connection() for _ in range(24)))
        for slots in (() if args.skip_reads else (0,2,4)):
            gate=asyncio.Semaphore(slots or 24)
            async def limited_search(term,kind):
                async with gate:
                    return await search(term,kind,contributor_search._local_matches)
            async def batch():
                async def one(index):
                    operation=detail if index%3==0 else lambda:limited_search('Alex','all') if index%3==1 else limited_search('Studio','studio')
                    _,stats,_=await measure(operation)
                    return stats
                return await asyncio.gather(*(one(i) for i in range(24)))
            values,stats,_=await measured(batch)
            stats['sql_statements']=sum(v['sql_statements'] for v in values)
            stats['sql_ms']=round(sum(v['sql_ms'] for v in values),3)
            report['loads'][str(slots)]={'batch':stats,'latency_ms':summary([v['wall_ms'] for v in values])}
            save('loads/'+str(slots),report['loads'][str(slots)])
        async def ingest(doc,additive=False):
            async with sessions() as db:
                work=await (fill_document(db,doc,'tmdb') if additive else catalogue.ingest_document(db,doc,'tmdb'))
                await db.commit();return work.id
        run_native=1000000+int(uuid.uuid4().hex[:6],16)
        async def revision():
            async with sessions() as db:
                return await db.scalar(text('SELECT revision FROM stats_metadata_revision WHERE id=1'))
        for size in (1000,3000):
            doc=normalize_tmdb(source(size,run_native+size),'series')
            previous=await revision()
            work_id,cold,_=await measured(lambda:ingest(doc))
            cold['metadata_revision_increments']=(await revision())-previous
            assert cold['metadata_revision_increments'] < max(100,size//10), 'Per-credit invalidation storm returned'
            async def state():
                async with sessions() as db:
                    return list((await db.execute(select(CatalogueCredit.__table__).where(CatalogueCredit.work_id==work_id)
                        .order_by(CatalogueCredit.id))).all())
            before=await state()
            previous=await revision()
            _,refresh,_=await measured(lambda:ingest(doc));assert await state()==before
            refresh['metadata_revision_increments']=(await revision())-previous
            previous=await revision()
            _,additive,_=await measured(lambda:ingest(doc,True));assert await state()==before
            additive['metadata_revision_increments']=(await revision())-previous
            _,memory,_=await measured(lambda:ingest(doc,True),memory=True)
            report['writes'][str(size)]={'cold':cold,'refresh':refresh,'cached_additive':additive,
                'additive_memory_instrumented':memory,'same_credit_state':True,'rows':len(before)}
            save('writes/'+str(size),report['writes'][str(size)])
        # Preserve full source associations while stressing one selected role.
        async with engine.begin() as db:
            await db.execute(text("""UPDATE catalogue_entities SET attributes=attributes ||
                jsonb_build_object('synopsis',repeat('Story metadata. ',100),
                    'artwork',(SELECT jsonb_agg('https://example.test/art/'||i||'.jpg') FROM generate_series(1,80) i))
                WHERE id BETWEEN 1 AND 5000"""))
            await db.execute(text("""INSERT INTO catalogue_credits
                (work_id,contributor_id,provider,source_key,role,character_label,character_image_url,screen_character_id,attributes)
                SELECT w,100038,'tvdb','validation-fanout','actor','Audit Role',
                    'https://example.test/fanout/'||w||'.jpg',100038,jsonb_build_object('principal',false)
                FROM generate_series(1,5000) w ON CONFLICT DO NOTHING"""))
        value,normal,queries=await measured(detail,capture=True)
        _,memory,_=await measured(detail,memory=True)
        report['stress']['images']={'normal':normal,'memory_instrumented':memory,'people':len(value['people']),
            'plans':await plans(engine,queries)}
        save('stress/images',{k:v for k,v in report['stress']['images'].items() if k!='plans'})
        async with engine.begin() as db:
            registry_size=await db.scalar(text('SELECT count(*) FROM catalogue_screen_characters WHERE actor_id=100038'))
            if args.existing_role_registry_size is not None and registry_size < args.existing_role_registry_size:
                await db.execute(text("""INSERT INTO catalogue_screen_characters(actor_id,role_key,name,aliases)
                    SELECT 100038,'audit-retained-role-'||g,'Retained Role '||g,'[]'::jsonb
                    FROM generate_series(1,:extra) g ON CONFLICT DO NOTHING"""),
                    {'extra':args.existing_role_registry_size-registry_size})
                registry_size=await db.scalar(text('SELECT count(*) FROM catalogue_screen_characters WHERE actor_id=100038'))
            work_id=await db.scalar(text("INSERT INTO catalogue_entities(kind,name) VALUES('series','Validation many roles') RETURNING id"))
            await db.execute(text("""INSERT INTO catalogue_credits(work_id,contributor_id,provider,source_key,role,character_label,attributes)
                SELECT :work,100038,'tmdb','role:'||g,'actor','Validation Role '||:native||'-'||g,jsonb_build_object('principal',true)
                FROM generate_series(1,3000) g"""),{'work':work_id,'native':str(run_native)})
        async def project():
            async with sessions() as db:
                count=await screen_characters.project_characters(db,work_id);await db.commit();return count
        value,stats,_=await measured(project)
        report['stress']['roles']={'roles':value,'existing_registry_roles':registry_size,'projection':stats}
        async with sessions() as db:
            value=await work_details(db,await db.get(CatalogueEntity,work_id),extras=False)
            report['stress']['roles'].update(displayed_labels=len(value['people'][0]['characters']),
                response_bytes=len(json.dumps(value)))
        save('stress/roles',report['stress']['roles'])
        async with engine.connect() as db:
            report['storage']=[dict(r) for r in (await db.execute(text("""SELECT relname,
                pg_total_relation_size(oid) AS total_bytes,pg_relation_size(oid) AS heap_bytes
                FROM pg_class WHERE relnamespace=current_schema()::regnamespace
                  AND relname IN ('catalogue_contributor_search','catalogue_credits','catalogue_entities')"""))).mappings()]
        save('storage',report['storage'])
    finally:
        Path(args.output).write_text(json.dumps(report,indent=2),encoding='utf-8')
        await engine.dispose()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--skip-reads',action='store_true',help='Reuse completed read/load measurements; recheck writes, CPU batches and stress')
    parser.add_argument('--existing-role-registry-size',type=int,help='Prepare at least this many retained actor roles before the stress measurement')
    asyncio.run(main(parser.parse_args()))
