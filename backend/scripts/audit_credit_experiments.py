"""Isolated follow-up experiments for audit_credit_performance.py.

Indexes and temporary function patches are confined to the disposable audit.
This is measurement code, not an alternate production ingestion path.
"""
import argparse
import asyncio
from collections import defaultdict
import json
import os
from pathlib import Path
import re
import time
import tracemalloc
from unittest.mock import AsyncMock, patch

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import load_only

from core import catalogue, contributor_search, screen_characters
from core.catalogue_normalize import normalize_tmdb
from core.media_details import work_details
from core.principal_credits import principal_clause
from models.catalogue import CatalogueCredit, CatalogueEntity
from scripts.audit_credit_performance import attach, measure, plans, source, summary


async def main(args):
    baseline=json.loads(Path(args.baseline).read_text(encoding='utf-8'))
    schema=baseline['schema']
    url=os.environ['CREDIT_AUDIT_DATABASE_URL']
    parsed=make_url(url)
    if parsed.host not in ('localhost','127.0.0.1') or parsed.port!=55449 or parsed.database!='anylist_test':
        raise RuntimeError('Only loopback:55449/anylist_test is permitted')
    if not re.fullmatch(r'credit_audit_[a-f0-9]{12}',schema):
        raise RuntimeError('Invalid isolated schema')
    engine=create_async_engine(url,pool_size=20,max_overflow=10,pool_pre_ping=True,
        connect_args={'server_settings':{'search_path':schema,'statement_timeout':'120000'}})
    attach(engine)
    sessions=async_sessionmaker(engine,expire_on_commit=False)
    report={'schema':schema,'baseline_commit':baseline['application_commit'],'read_stages':{},'writes':{}}
    output=Path(args.output)
    def save(label,value):
        output.write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps({'phase':label,**value}),flush=True)

    async def search(term,kind):
        async with sessions() as db:
            result=await contributor_search.local_matches(db,term,kind)
            ranked=contributor_search.ranked_results(result,term)
            return {'candidates':sum(map(len,result.values())),
                    'results':sum(map(len,ranked.values())),
                    'keys':{kind:[row['key'] for row in rows] for kind,rows in ranked.items()}}

    async def read_stage(label):
        stage={}
        for name,term,kind in [('common_people','Alex','all'),('two_letters','Al','all'),
                              ('studio','Studio','studio'),('exact_person','Alex Person 004000','actor'),
                              ('company_alias','Studio 0001, LLC','studio')]:
            observations=[]
            for _ in range(3 if label=='unchanged' else 8):
                value, measured, queries=await measure(lambda:search(term,kind),capture=True)
                observations.append(measured['wall_ms'])
            if report['read_stages']:
                assert value==report['read_stages']['unchanged'][name]['value'], 'Index changed search results'
            stage[name]={'wall_ms':summary(observations),'last':measured,'value':value,
                         'explain':await plans(engine,queries)}
        report['read_stages'][label]=stage
        save(label,{k:v['wall_ms'] for k,v in stage.items()})

    async def ingest(doc):
        async with sessions() as db:
            work=await catalogue.ingest_document(db,doc,'tmdb')
            await db.commit()
            return work.id

    async def revision():
        async with engine.connect() as db:
            return await db.scalar(text('SELECT revision FROM stats_metadata_revision WHERE id=1'))

    try:
        pattern=next(v for v in principal_clause.__code__.co_consts
                     if isinstance(v,str) and 'archive footage|archive sound' in v)
        async with engine.connect() as db:
            row=(await db.execute(text('SELECT :sample ~* :actual AS current_match, '
                ':sample ~* :correct AS corrected_match'),{'sample':'Self (archive footage)',
                'actual':pattern,'correct':pattern.replace('\\\\','\\')})).one()
            report['legacy_archive_filter']={'pattern':pattern,'current_match':row[0],
                                            'corrected_match':row[1]}
        # Compact rolled-back fixture-load space before comparing isolated
        # index changes. This is not a production maintenance operation.
        async with engine.connect() as db:
            await db.execution_options(isolation_level='AUTOCOMMIT')
            for table in ('catalogue_credits','catalogue_entities','catalogue_screen_characters'):
                await db.execute(text('VACUUM (FULL, ANALYZE) '+table))
            report['relation_sizes_after_compaction']=[dict(row) for row in (await db.execute(text(
                "SELECT relname,pg_total_relation_size(oid) AS total_bytes,pg_relation_size(oid) AS heap_bytes "
                "FROM pg_class WHERE relnamespace=current_schema()::regnamespace AND relkind='r' "
                "AND relname IN ('catalogue_entities','catalogue_credits','catalogue_screen_characters','catalogue_metadata_snapshots')"
            ))).mappings()]
        await read_stage('unchanged')
        async with engine.begin() as db:
            await db.execute(text("CREATE INDEX audit_canonical_contributor ON catalogue_entities "
                "((coalesce(canonical_person_id,canonical_company_id,id)))"))
            await db.execute(text('ANALYZE catalogue_entities'))
        await read_stage('canonical_expression_index')
        async with engine.begin() as db:
            await db.execute(text('CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public'))
            await db.execute(text('CREATE INDEX audit_name_trgm ON catalogue_entities USING gin (name gin_trgm_ops)'))
            await db.execute(text("CREATE INDEX audit_alias_trgm ON catalogue_entities USING gin "
                "((attributes ->> 'also_known_as') gin_trgm_ops)"))
            await db.execute(text('ANALYZE catalogue_entities'))
        await read_stage('plus_trigrams')
        async with engine.begin() as db:
            await db.execute(text("CREATE INDEX audit_principal_credit ON catalogue_credits (contributor_id,work_id) "
                "WHERE (attributes ->> 'principal')::boolean IS TRUE"))
            await db.execute(text('ANALYZE catalogue_credits'))
        await read_stage('plus_partial_principal_index')

        class NarrowSearchSession(AsyncSession):
            async def execute(self,statement,*a,**kw):
                descriptions=getattr(statement,'column_descriptions',[])
                if len(descriptions)==2 and descriptions[1].get('name')=='work_count':
                    statement=statement.options(load_only(CatalogueEntity.id,CatalogueEntity.kind,
                                                         CatalogueEntity.name,CatalogueEntity.image_url))
                return await super().execute(statement,*a,**kw)
        sessions=async_sessionmaker(engine,class_=NarrowSearchSession,expire_on_commit=False)
        await read_stage('plus_narrow_search_projection')

        # A real quick-search invocation with synthetic provider latency and IDs.
        async with engine.begin() as db:
            await db.execute(text("""INSERT INTO catalogue_identities(entity_id,namespace,external_id,source)
                SELECT 100000+38+(g-1)*11,'tmdb.person',g::text,'tmdb' FROM generate_series(1,20) g
                ON CONFLICT DO NOTHING"""))
            await db.execute(text("""INSERT INTO catalogue_identities(entity_id,namespace,external_id,source)
                SELECT 1000000+g,'tmdb.company',g::text,'tmdb' FROM generate_series(1,20) g
                ON CONFLICT DO NOTHING"""))
        async def provider_people(*a,**kw):
            await asyncio.sleep(.05)
            return {'results':[{'id':i,'name':f'Alex Person {i:06}',
                'known_for_department':'Acting','known_for':[{'id':1}],'popularity':10} for i in range(1,21)]}
        async def provider_companies(*a,**kw):
            await asyncio.sleep(.05)
            return {'results':[{'id':i,'name':f'Studio {i:04}'} for i in range(1,21)]}
        from types import SimpleNamespace
        async def remote_search():
            async with sessions() as db:
                value=await contributor_search.search_contributors(db,SimpleNamespace(id=1),'Alex','all')
                return {'results':sum(map(len,value['results'].values()))}
        report['remote_search_stubbed']={}
        for label in ('current_identity_expressions','plus_person_and_company_indexes'):
            if label.startswith('plus'):
                async with engine.begin() as db:
                    await db.execute(text('CREATE INDEX audit_canonical_person ON catalogue_entities ((coalesce(canonical_person_id,id)))'))
                    await db.execute(text('CREATE INDEX audit_canonical_company ON catalogue_entities ((coalesce(canonical_company_id,id)))'))
                    await db.execute(text('ANALYZE catalogue_entities'))
            samples=[]
            with (patch.object(contributor_search.settings_store,'get_user_tmdb_key',AsyncMock(return_value='synthetic')),
                 patch.object(contributor_search.tmdb,'search_people',AsyncMock(side_effect=provider_people)) as people,
                 patch.object(contributor_search.tmdb,'search_company',AsyncMock(side_effect=provider_companies)) as companies):
                for _ in range(5):
                    value, measured, queries=await measure(remote_search,capture=True)
                    samples.append(measured['wall_ms'])
                report['remote_search_stubbed'][label]={'value':value,**measured,'wall_ms':summary(samples),
                    'people_calls_per_search':people.await_count/5,'company_calls_per_search':companies.await_count/5,
                    'per_provider_latency_ms':50,'explain':await plans(engine,queries)}
            save('remote_search_stubbed/'+label,{k:v for k,v in report['remote_search_stubbed'][label].items() if k!='explain'})

        async def detail():
            async with sessions() as db:
                work=await db.get(CatalogueEntity,1)
                return await work_details(db,work,extras=False)
        report['indexed_concurrency']={}
        for concurrency in (8,24):
            gate=asyncio.Semaphore(concurrency)
            async def one(index):
                queued=time.perf_counter()
                async with gate:
                    operation=detail if index%3==0 else (lambda:search('Alex','all')) if index%3==1 else lambda:search('Studio','studio')
                    _, measured, _=await measure(operation)
                    measured['queue_inclusive_ms']=(time.perf_counter()-queued)*1000
                    return measured
            started=time.perf_counter()
            measured=await asyncio.gather(*(one(i) for i in range(24)))
            report['indexed_concurrency'][str(concurrency)]={
                'service_wall_ms':summary([m['wall_ms'] for m in measured]),
                'queue_inclusive_wall_ms':summary([m['queue_inclusive_ms'] for m in measured]),
                'batch_wall_ms':round((time.perf_counter()-started)*1000,3),
                'event_loop_lag_max_ms':max(m['event_loop_lag_max_ms'] for m in measured)}
            save('indexed_concurrency/'+str(concurrency),report['indexed_concurrency'][str(concurrency)])

        report['normalization_memory']={}
        for size in (1000,3000,10000):
            tracemalloc.start()
            started=time.perf_counter()
            raw=source(size,700000+size)
            normalized=normalize_tmdb(raw,'series')
            report['normalization_memory'][str(size)]={'wall_ms':round((time.perf_counter()-started)*1000,3),
                'source_and_projection_peak_mib':round(tracemalloc.get_traced_memory()[1]/1024/1024,3),
                'credits':len(normalized['credits'])}
            tracemalloc.stop()
        save('normalization_memory',report['normalization_memory'])

        # Compare the unchanged writer with document-local entity reuse and
        # batched credit upserts. All identities/protected merge logic is retained.
        original_entity=catalogue.upsert_entity
        original_upsert=catalogue.upsert
        original_characters=screen_characters.project_characters
        for size in (1000,3000):
            doc=normalize_tmdb(source(size,800000+size),'series')
            previous=await revision()
            work_id, unchanged, _=await measure(lambda:ingest(doc))
            unchanged['metadata_revision_increments']=(await revision())-previous
            cache={}
            async def memo(db,values,provider):
                fingerprint=json.dumps(values,sort_keys=True)
                if fingerprint not in cache:
                    cache[fingerprint]=await original_entity(db,values,provider)
                return cache[fingerprint]
            with patch.object(catalogue,'upsert_entity',memo):
                previous=await revision()
                _, memoized, _=await measure(lambda:ingest(doc))
                memoized['metadata_revision_increments']=(await revision())-previous

            cache={}
            buffered=[]
            async def buffer(db,model,values,keys,preserve_missing=False):
                if model is not CatalogueCredit:
                    return await original_upsert(db,model,values,keys,preserve_missing)
                if preserve_missing:
                    values={k:v for k,v in values.items() if k in keys or catalogue.has_value(v) or k=='screen_character_id'}
                    if 'attributes' in values:
                        values['attributes']={k:v for k,v in values['attributes'].items() if catalogue.has_value(v)}
                buffered.append(values)
            async def flush_then_characters(db,work):
                groups=defaultdict(list)
                for values in buffered:
                    groups[tuple(sorted(values))].append(values)
                for rows in groups.values():
                    for offset in range(0,len(rows),200):
                        chunk=rows[offset:offset+200]
                        statement=insert(CatalogueCredit).values(chunk)
                        update={k:statement.excluded[k] for k in chunk[0] if k not in ('work_id','provider','source_key')}
                        update['attributes']=CatalogueCredit.attributes.op('||')(statement.excluded.attributes)
                        await db.execute(statement.on_conflict_do_update(
                            index_elements=['work_id','provider','source_key'],set_=update))
                buffered.clear()
                return await original_characters(db,work)
            async with sessions() as db:
                before=[tuple(row) for row in (await db.execute(select(CatalogueCredit.__table__)
                    .where(CatalogueCredit.work_id==work_id)
                    .order_by(CatalogueCredit.source_key))).all()]
            with patch.object(catalogue,'upsert_entity',memo), patch.object(catalogue,'upsert',buffer), \
                 patch.object(screen_characters,'project_characters',flush_then_characters):
                previous=await revision()
                _, batched, _=await measure(lambda:ingest(doc))
                batched['metadata_revision_increments']=(await revision())-previous
            async with sessions() as db:
                after=[tuple(row) for row in (await db.execute(select(CatalogueCredit.__table__)
                    .where(CatalogueCredit.work_id==work_id)
                    .order_by(CatalogueCredit.source_key))).all()]
                work=await db.get(CatalogueEntity,work_id)
                details=await work_details(db,work,extras=False)
            assert before==after, 'Prototype changed retained credit state'
            report['writes'][str(size)]={'unchanged':unchanged,'entity_memoization':memoized,
                'memo_and_batched_credits':batched,'same_credit_state':before==after,
                'credit_rows':len(after),'detail_people':len(details['people'])}
            async def large_detail():
                async with sessions() as db:
                    work=await db.get(CatalogueEntity,work_id)
                    value=await work_details(db,work,extras=False)
                    return {'people':len(value['people']),'response_bytes':len(json.dumps(value))}
            value, measured, queries=await measure(large_detail,capture=True)
            report['writes'][str(size)]['detail_read']={'value':value,**measured,'explain':await plans(engine,queries)}
            save('write_experiment/'+str(size),report['writes'][str(size)])

        # Adversarial reuse of one selected character across many stored works.
        # These unselected source rows must not expand the displayed cast.
        report['character_image_fanout']={}
        for links in (100,1000,5000):
            async with engine.begin() as db:
                await db.execute(text("""UPDATE catalogue_entities SET attributes=attributes ||
                    jsonb_build_object('synopsis',repeat('Story metadata. ',100),
                        'artwork',(SELECT jsonb_agg('https://example.test/art/'||i||'.jpg')
                                   FROM generate_series(1,80) i))
                    WHERE id BETWEEN 1 AND :links"""),{'links':links})
                await db.execute(text("""INSERT INTO catalogue_credits
                    (work_id,contributor_id,provider,source_key,role,role_label,character_label,
                     character_image_url,screen_character_id,position,attributes)
                    SELECT w,100038,'tvdb','audit-fanout','actor','Actor','Audit Role',
                        'https://example.test/role/fanout/'||w||'.jpg',100038,39,
                        '{"selection_version": 1,"principal": false}'::jsonb
                    FROM generate_series(1,:links) w ON CONFLICT DO NOTHING"""),{'links':links})
            samples=[]
            for _ in range(5):
                value, measured, queries=await measure(detail,capture=True)
                samples.append(measured['wall_ms'])
            _, memory, _=await measure(detail,memory=True)
            report['character_image_fanout'][str(links)]={'wall_ms':summary(samples),
                'detail_people':len(value['people']),'memory_instrumented':memory,
                'last':measured,'explain':await plans(engine,queries)}
            save('character_image_fanout/'+str(links),{'wall_ms':summary(samples),
                'peak_mib':memory['python_peak_mib'],'people':len(value['people'])})

        report['many_roles_one_person']={}
        for count in (100,1000,3000):
            async with engine.begin() as db:
                work_id=await db.scalar(text("INSERT INTO catalogue_entities(kind,name,attributes) "
                    "VALUES ('series','Audit many roles','{}'::jsonb) RETURNING id"))
                await db.execute(text("""INSERT INTO catalogue_credits
                    (work_id,contributor_id,provider,source_key,role,role_label,character_label,position,attributes)
                    SELECT :work_id,100038,'tmdb','many-roles:'||g,'actor','Actor',
                        'Explosion Role '||g,0,'{"selection_version": 1,"principal": true}'::jsonb
                    FROM generate_series(1,:count) g"""),{'work_id':work_id,'count':count})
            async def project():
                async with sessions() as db:
                    result=await screen_characters.project_characters(db,work_id)
                    await db.commit()
                    return result
            _, projection, _=await measure(project)
            async def read_roles():
                async with sessions() as db:
                    work=await db.get(CatalogueEntity,work_id)
                    value=await work_details(db,work,extras=False)
                    return {'people':len(value['people']),'role_labels':len(value['people'][0]['characters']),
                            'response_bytes':len(json.dumps(value))}
            value, read, queries=await measure(read_roles,capture=True)
            _, memory, _=await measure(read_roles,memory=True)
            report['many_roles_one_person'][str(count)]={'project_characters':projection,
                'detail_read':read,'value':value,'memory_instrumented':memory,'explain':await plans(engine,queries)}
            save('many_roles/'+str(count),{'project':projection,'detail':read,'value':value})
    finally:
        output.write_text(json.dumps(report,indent=2),encoding='utf-8')
        await engine.dispose()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline',required=True)
    parser.add_argument('--output',required=True)
    asyncio.run(main(parser.parse_args()))
