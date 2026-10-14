"""Reproducible synthetic credit audit, restricted to a disposable test database.

Run from backend with PYTHONPATH=. and CREDIT_AUDIT_DATABASE_URL pointing to
localhost:55449/anylist_test. No provider calls or development data are used.
"""
import argparse
import asyncio
from collections import Counter
from contextvars import ContextVar
import json
import math
import os
from pathlib import Path
import platform
import re
import statistics
import time
import tracemalloc
import uuid

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import event, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core import catalogue
from core.catalogue_additive import fill_document
from core.catalogue_normalize import normalize_tmdb
from core.contributor_search import local_matches, ranked_results
from core.media_details import work_details
from models.catalogue import CatalogueEntity


ACTIVE = ContextVar('credit_audit_measurement', default=None)


def summary(values):
    ordered = sorted(values)
    return {'n': len(values), 'median': round(statistics.median(values), 3),
            'p95': round(ordered[max(0, math.ceil(len(values)*.95)-1)], 3),
            'max': round(max(values), 3)}


class Meter:
    def __init__(self, capture=False):
        self.count = 0
        self.sql_ms = 0
        self.lock_ms = 0
        self.families = Counter()
        self.queries = []
        self.capture = capture

    def record(self, statement, parameters, elapsed):
        self.count += 1
        self.sql_ms += elapsed
        self.families[' '.join(statement.split())[:180]] += 1
        if 'pg_advisory_xact_lock' in statement:
            self.lock_ms += elapsed
        if self.capture and statement.lstrip().upper().startswith('SELECT'):
            self.queries.append((statement, parameters))


def attach(engine):
    @event.listens_for(engine.sync_engine, 'before_cursor_execute')
    def before(conn, cursor, statement, parameters, context, executemany):
        context.credit_audit_started = time.perf_counter()

    @event.listens_for(engine.sync_engine, 'after_cursor_execute')
    def after(conn, cursor, statement, parameters, context, executemany):
        meter = ACTIVE.get()
        if meter:
            meter.record(statement, parameters, (time.perf_counter()-context.credit_audit_started)*1000)


async def measure(operation, *, capture=False, memory=False):
    meter = Meter(capture)
    token = ACTIVE.set(meter)
    lag, stopping = [], asyncio.Event()

    async def heartbeat():
        target = time.perf_counter()+.01
        while not stopping.is_set():
            await asyncio.sleep(.01)
            now = time.perf_counter()
            lag.append(max(0, now-target)*1000)
            target = now+.01

    task = asyncio.create_task(heartbeat())
    if memory:
        tracemalloc.start()
    start = time.perf_counter()
    try:
        value = await operation()
        elapsed = (time.perf_counter()-start)*1000
        peak = tracemalloc.get_traced_memory()[1]/1024/1024 if memory else None
    finally:
        if memory:
            tracemalloc.stop()
        stopping.set()
        await task
        ACTIVE.reset(token)
    result = {'wall_ms': round(elapsed, 3), 'sql_statements': meter.count,
              'sql_ms': round(meter.sql_ms, 3), 'advisory_lock_ms': round(meter.lock_ms, 3),
              'event_loop_lag_max_ms': round(max(lag, default=0), 3),
              'python_peak_mib': round(peak, 3) if peak is not None else None,
              'top_statement_families': meter.families.most_common(5)}
    return value, result, meter.queries


def source(count, native):
    cast_count = count*3//5
    return {'id':native, 'name':f'Audit series {native}', 'number_of_episodes':100,
            'created_by':[], 'production_companies':[{'id':9999999,'name':'Audit Studio, LLC'}],
            'aggregate_credits':{
                'cast':[{'id':native*10000+i//3, 'name':f'Audit Cast {i//3}', 'order':i//3,
                         'total_episode_count':100-i%100,
                         'roles':[{'credit_id':f'{native}-cast-{i}', 'character':f'Named Role {i}',
                                   'episode_count':100-i%100}]} for i in range(cast_count)],
                'crew':[{'id':native*10000+count+i//2,'name':f'Audit Crew {i//2}',
                         'popularity':i%20,
                         'jobs':[{'credit_id':f'{native}-crew-{i}', 'job':[
                             'Director','Writer','Editor','Executive Producer','Production Assistant',
                             'Camera Operator','Thanks','Producer'][i%8], 'episode_count':i%100+1}]
                         } for i in range(count-cast_count)]}}


async def seed(engine, works, people):
    """SQL fixture creation is excluded from measured application ingestion."""
    async with engine.begin() as conn:
        await conn.execute(text("SET LOCAL statement_timeout='0'"))
        # Fixture construction is not an ingestion benchmark. Keep FK checks,
        # but bypass user-trigger work until the generated fixture is committed.
        # Every measured application operation runs with all triggers enabled.
        tables=('catalogue_entities','catalogue_credits','catalogue_screen_characters','catalogue_metadata_snapshots')
        for table in tables:
            await conn.execute(text('ALTER TABLE '+table+' DISABLE TRIGGER USER'))
        await conn.execute(text("""INSERT INTO catalogue_entities(id,kind,name,attributes)
            SELECT g, CASE WHEN g%2=0 THEN 'movie' ELSE 'series' END,
                'Audit title '||g, jsonb_build_object('episode_count',100)
            FROM generate_series(1,:works) g ON CONFLICT DO NOTHING"""), {'works':works})
        await conn.execute(text("""INSERT INTO catalogue_entities(id,kind,name,attributes)
            SELECT 100000+g,'person',
                (ARRAY['Alex','Sam','Taylor','Chris'])[g%4+1]||' Person '||lpad(g::text,6,'0'),
                jsonb_build_object('biography',repeat('Descriptive metadata. ',25),
                    'also_known_as',CASE WHEN g%101=0 THEN jsonb_build_array('Rare Alias '||g) ELSE '[]'::jsonb END)
            FROM generate_series(1,:people) g ON CONFLICT DO NOTHING"""), {'people':people})
        await conn.execute(text("""INSERT INTO catalogue_entities(id,kind,name,attributes)
            SELECT 1000000+g,'organization','Studio '||lpad(g::text,4,'0'),
                jsonb_build_object('also_known_as',jsonb_build_array('Studio '||g||', LLC'))
            FROM generate_series(1,200) g ON CONFLICT DO NOTHING"""))
        await conn.execute(text("""INSERT INTO catalogue_entities(id,kind,name,canonical_person_id)
            SELECT 2000000+g,'person','Archive Alias '||lpad(g::text,6,'0'),100000+g
            FROM generate_series(1,1000) g ON CONFLICT DO NOTHING"""))
        await conn.execute(text("""INSERT INTO catalogue_entities(id,kind,name,canonical_company_id)
            SELECT 2100000+g,'organization','Studio '||lpad(g::text,4,'0')||', LLC',1000000+g
            FROM generate_series(1,200) g ON CONFLICT DO NOTHING"""))
        await conn.execute(text("""INSERT INTO catalogue_screen_characters(id,actor_id,role_key,name,aliases)
            SELECT 100000+g,100000+g,'audit role','Audit Role','["audit role"]'::jsonb
            FROM generate_series(1,:people) g ON CONFLICT DO NOTHING"""), {'people':people})
        # Each work has 40 actors, 160 crew, two studios and ten second-provider
        # associations: 212 source rows. There are 24/32 principal people for
        # movies/series, plus company associations and duplicate cast rows.
        await conn.execute(text("""INSERT INTO catalogue_credits
            (work_id,contributor_id,provider,source_key,role,role_label,position,character_label,
             screen_character_id,character_image_url,attributes)
            SELECT w,100000+((w*37+i*11)%:people)+1,'tmdb','seed:'||i,
                CASE WHEN i<40 THEN 'actor' ELSE 'contributor' END,
                CASE WHEN i<40 THEN 'Actor' WHEN i<48 THEN
                    (ARRAY['Director','Writer','Writer','Editor','Composer','Producer','Producer','Production Design'])[i-39]
                    ELSE 'Production Assistant' END,
                CASE WHEN i<40 THEN i ELSE NULL END,
                CASE WHEN i<40 THEN 'Audit Role' ELSE NULL END,
                CASE WHEN i<40 THEN 100000+((w*37+i*11)%:people)+1 ELSE NULL END,
                CASE WHEN i<40 AND i%8=0 THEN 'https://example.test/role/'||w||'/'||i||'.jpg' ELSE NULL END,
                jsonb_build_object('selection_version',1,'principal',
                    (i<CASE WHEN w%2=0 THEN 16 ELSE 24 END OR (i>=40 AND i<48)),
                    'episode_count',i%100+1,'total_episode_count',100,'popularity',i%30)
            FROM generate_series(1,:works) w CROSS JOIN generate_series(0,199) i
            ON CONFLICT DO NOTHING"""), {'works':works,'people':people})
        await conn.execute(text("""INSERT INTO catalogue_credits
            (work_id,contributor_id,provider,source_key,role,role_label,position,character_label,screen_character_id,attributes)
            SELECT w,CASE WHEN ((w*37+i*11)%:people)+1<=1000
                THEN 2000000+((w*37+i*11)%:people)+1 ELSE 100000+((w*37+i*11)%:people)+1 END,
                'tvdb','duplicate:'||i,'actor','Actor',i,'Audit Role',
                CASE WHEN ((w*37+i*11)%:people)+1>1000 THEN 100000+((w*37+i*11)%:people)+1 ELSE NULL END,
                jsonb_build_object('selection_version',1,'principal',true)
            FROM generate_series(1,:works) w CROSS JOIN generate_series(0,9) i ON CONFLICT DO NOTHING"""),
            {'works':works,'people':people})
        await conn.execute(text("""INSERT INTO catalogue_credits
            (work_id,contributor_id,provider,source_key,role,attributes)
            SELECT w,CASE WHEN i=0 THEN 1000000 ELSE 2100000 END+(w%200)+1,
                CASE WHEN i=0 THEN 'tmdb' ELSE 'tvdb' END,'studio:'||i,'producer',
                '{"selection_version": 1,"principal": true}'::jsonb
            FROM generate_series(1,:works) w CROSS JOIN generate_series(0,1) i ON CONFLICT DO NOTHING"""), {'works':works})
        await conn.execute(text("""INSERT INTO catalogue_metadata_snapshots(provider,kind,external_id,entity_id,coverage,payload)
            SELECT 'tmdb',CASE WHEN w%2=0 THEN 'movie' ELSE 'series' END,w::text,w,
                '{"credits_complete": true}'::jsonb,
                jsonb_build_object('audit_padding',repeat(md5(w::text),2048),
                    'credits',jsonb_build_object('cast','[]'::jsonb,'crew','[]'::jsonb),
                    'aggregate_credits',jsonb_build_object('cast','[]'::jsonb,'crew','[]'::jsonb))
            FROM generate_series(1,:works) w ON CONFLICT DO NOTHING"""), {'works':works})
        for table in ('catalogue_entities','catalogue_screen_characters'):
            await conn.execute(text(f"SELECT setval(pg_get_serial_sequence('{table}','id'),(SELECT max(id) FROM {table}))"))
        for table in tables:
            await conn.execute(text('ALTER TABLE '+table+' ENABLE TRIGGER USER'))
            await conn.execute(text('ANALYZE '+table))
        if await conn.scalar(text("SELECT to_regprocedure('refresh_contributor_search(integer[])') IS NOT NULL")):
            await conn.execute(text("""DO $$ DECLARE batch integer[]; cursor_id integer := 0;
              BEGIN LOOP
                SELECT array_agg(id) INTO batch FROM (SELECT id FROM catalogue_entities WHERE id>cursor_id
                  AND kind IN ('person','organization') ORDER BY id LIMIT 1000) e;
                EXIT WHEN batch IS NULL;
                PERFORM refresh_contributor_search(batch); SELECT max(id) INTO cursor_id FROM unnest(batch) id;
              END LOOP; END $$"""))
    async with engine.connect() as conn:
        for table in ('catalogue_entities','catalogue_credits','catalogue_screen_characters','catalogue_metadata_snapshots'):
            await conn.execute(text('ANALYZE '+table))
        if await conn.scalar(text("SELECT to_regclass('catalogue_contributor_search') IS NOT NULL")):
            await conn.execute(text('ANALYZE catalogue_contributor_search'))
        await conn.commit()


async def plans(engine, queries):
    result = []
    async with engine.connect() as conn:
        for statement, parameters in queries:
            plan = await conn.exec_driver_sql('EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) '+statement, parameters)
            payload = plan.scalar_one()
            result.append({'sql':statement,'plan':json.loads(payload) if isinstance(payload,str) else payload})
    return result


async def main(args):
    url = os.environ.get('CREDIT_AUDIT_DATABASE_URL','')
    parsed = make_url(url)
    if parsed.host not in ('localhost','127.0.0.1') or parsed.port != 55449 or parsed.database != 'anylist_test':
        raise RuntimeError('Only loopback:55449/anylist_test is permitted')
    output = Path(args.output)
    previous=json.loads(output.read_text(encoding='utf-8')) if args.resume else None
    schema = previous['schema'] if previous else 'credit_audit_'+uuid.uuid4().hex[:12]
    if not re.fullmatch(r'credit_audit_[a-f0-9]{12}',schema):
        raise RuntimeError('Invalid isolated schema')
    admin = create_async_engine(url)
    if not previous:
        async with admin.begin() as conn:
            await conn.execute(text('CREATE SCHEMA '+schema))
    engine = create_async_engine(url, pool_size=20, max_overflow=10, pool_pre_ping=True,
        connect_args={'server_settings':{'search_path':schema+',public','statement_timeout':'120000'}})
    attach(engine)
    sessions = async_sessionmaker(engine,expire_on_commit=False)
    report = {'schema':schema,'python':platform.python_version(),'host':platform.platform(),
              'application_commit':args.commit,'fixture_notes':
              'Synthetic, 212 credits/title, 40 cast +160 crew +10 duplicate cast +2 company; '
              'source profiles ~500B; snapshots ~64KiB compressible; local Docker 2CPU/2GiB; '
              'warm/first-touch caches, no provider network, application functions not HTTP.',
              'normalization':{},'ingestion':{},'datasets':{}}
    if previous:
        report=previous
    report['shared_memory_limit_mib']=512
    def save():
        output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(report,indent=2),encoding='utf-8')
    def publish(label,data):
        print(json.dumps({'phase':label,**data}),flush=True)
        save()

    async def read_detail(work_id=1):
        async with sessions() as db:
            work = await db.get(CatalogueEntity,work_id)
            value = await work_details(db,work,extras=False)
            return {'people':len(value['people']),'studios':len(value['organizations']),
                    'response_bytes':len(json.dumps(value).encode()),'orm_objects':len(db.identity_map)}

    async def search(term='Alex',kind='all'):
        async with sessions() as db:
            groups = await local_matches(db,term,kind)
            return {'candidates':sum(map(len,groups.values())),
                    'results':sum(map(len,ranked_results(groups,term).values()))}

    try:
        async with engine.connect() as conn:
            def migrate(connection):
                scripts=ScriptDirectory.from_config(Config('alembic.ini'))
                context=MigrationContext.configure(connection)
                with context.begin_transaction():
                    with Operations.context(context):
                        for revision in reversed(list(scripts.walk_revisions())):
                            revision.module.upgrade()
            if not previous:
                await conn.run_sync(migrate)
            report['schema_basis']='All actual Alembic upgrades, including integrity and statistics triggers'
            report['postgres']=await conn.scalar(text('SELECT version()'))

        for count in (100,1000,3000):
            raw=source(count,800000+count)
            times=[]
            for _ in range(8):
                started=time.perf_counter(); doc=normalize_tmdb(raw,'series')
                times.append((time.perf_counter()-started)*1000)
            report['normalization'][str(count)]={'wall_ms':summary(times),
                'credits':len(doc['credits']),'principal_rows':sum(c['attributes']['principal'] for c in doc['credits'])}

        for works, people in ((1000,100000),(5000,200000)):
            if args.skip_completed and 'concurrent_24' in report['datasets'].get(str(works),{}):
                continue
            await seed(engine,works,people)
            scale=str(works)
            results={}
            async with engine.connect() as conn:
                results['sizes']=(await conn.execute(text("""SELECT relname,
                    pg_total_relation_size(oid) AS total_bytes, pg_relation_size(oid) AS heap_bytes
                    FROM pg_class WHERE relnamespace=current_schema()::regnamespace AND relkind='r'
                    ORDER BY relname"""))).mappings().all()
                results['sizes']=[dict(r) for r in results['sizes']]
                results['credit_count']=await conn.scalar(text('SELECT count(*) FROM catalogue_credits'))
                results['disabled_user_triggers']=await conn.scalar(text("SELECT count(*) FROM pg_trigger "
                    "WHERE tgrelid IN (SELECT oid FROM pg_class WHERE relnamespace=current_schema()::regnamespace) "
                    "AND NOT tgisinternal AND tgenabled='D'"))
            report['datasets'][scale]=results
            for name, operation in [('detail',read_detail),('common_people',search),
                    ('exact_person',lambda:search('Alex Person 004000','actor')),
                    ('studio',lambda:search('Studio','studio')),
                    ('company_alias',lambda:search('Studio 0001, LLC','studio')),
                    ('person_alias',lambda:search('Archive Alias 000123','all')),
                    ('no_match',lambda:search('zzzz no match','all'))]:
                value, first, queries=await measure(operation,capture=True)
                samples=[]
                for _ in range(12):
                    _, measurement, _=await measure(operation)
                    samples.append(measurement['wall_ms'])
                _, memory, _=await measure(operation,memory=True)
                results[name]={'value':value,'first_touch':first,'warm_wall_ms':summary(samples),
                               'memory_instrumented':memory,'explain':await plans(engine,queries)}
                publish(scale+'/'+name,{'wall_ms':results[name]['warm_wall_ms'],'queries':first['sql_statements']})
            for concurrency in (8,24):
                gate=asyncio.Semaphore(concurrency)
                async def one(index):
                    queued=time.perf_counter()
                    async with gate:
                        operation=read_detail if index%3==0 else search if index%3==1 else lambda:search('Studio','studio')
                        started=time.perf_counter()
                        try:
                            async with asyncio.timeout(20):
                                _, measured, _=await measure(operation)
                        except TimeoutError:
                            measured={'wall_ms':(time.perf_counter()-started)*1000,
                                      'error':'20s audit deadline','sql_statements':-1,'event_loop_lag_max_ms':0}
                        measured['queue_inclusive_ms']=(time.perf_counter()-queued)*1000
                        return measured
                started=time.perf_counter()
                samples=await asyncio.gather(*(one(i) for i in range(24)))
                results['concurrent_'+str(concurrency)]={
                    'service_wall_ms':summary([r['wall_ms'] for r in samples]),
                    'queue_inclusive_wall_ms':summary([r['queue_inclusive_ms'] for r in samples]),
                    'deadlines_exceeded':sum('error' in r for r in samples),
                    'audit_deadline_seconds':20,
                    'batch_wall_ms':round((time.perf_counter()-started)*1000,3),
                    'lag_max_ms':max(r['event_loop_lag_max_ms'] for r in samples),
                    'sql_statement_counts':dict(Counter(r['sql_statements'] for r in samples))}
                publish(scale+'/concurrency'+str(concurrency),results['concurrent_'+str(concurrency)])

        async def ingest(doc, additive=False):
            async with sessions() as db:
                work=await (fill_document(db,doc,'tmdb') if additive else catalogue.ingest_document(db,doc,'tmdb'))
                await db.commit()
                return work.id

        for count in (100,1000,3000):
            doc=normalize_tmdb(source(count,800000+count),'series')
            _, fresh, _=await measure(lambda:ingest(doc))
            _, refresh, _=await measure(lambda:ingest(doc))
            _, additive, _=await measure(lambda:ingest(doc,True))
            _, memory, _=await measure(lambda:ingest(doc,True),memory=True)
            report['ingestion'][str(count)]={'cold_identities':fresh,'refresh':refresh,
                                           'cached_additive':additive,'additive_memory_instrumented':memory}
            publish('ingest/'+str(count),report['ingestion'][str(count)])

        async def writer(index):
            doc=normalize_tmdb(source(300,900000+index),'series')
            _, measured, _=await measure(lambda:ingest(doc))
            return measured
        started=time.perf_counter()
        writers=await asyncio.gather(*(writer(i) for i in range(4)))
        report['concurrent_writers']={'batch_wall_ms':round((time.perf_counter()-started)*1000,3),'writers':writers}
        publish('concurrent_writers',report['concurrent_writers'])
    finally:
        save()
        await engine.dispose()
        if not args.keep_schema:
            async with admin.begin() as conn:
                await conn.execute(text('DROP SCHEMA '+schema+' CASCADE'))
        await admin.dispose()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',required=True)
    parser.add_argument('--commit',default='unknown')
    parser.add_argument('--keep-schema',action='store_true')
    parser.add_argument('--resume',action='store_true',help='Resume a preserved synthetic schema from the output JSON')
    parser.add_argument('--skip-completed',action='store_true')
    asyncio.run(main(parser.parse_args()))
