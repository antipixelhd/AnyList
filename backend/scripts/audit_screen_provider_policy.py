"""Paired provider-policy audit on the disposable full-credit fixture only.

Provider responses are deterministic stubs. Optional fixed request delay is a
sensitivity experiment, not an observation of upstream API latency.
"""
import argparse
import asyncio
from collections import Counter
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import re
import subprocess
import time
from types import ModuleType, SimpleNamespace
from unittest.mock import patch
import uuid

from sqlalchemy import func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from audit_credit_performance import attach, measure, source, summary
from verify_credit_performance import resources
from core import actor_backfill, catalogue, media_credits, screen_characters
from core.catalogue_additive import fill_document
from core.catalogue_normalize import normalize_tmdb
from core.catalogue_providers import CatalogueProviders
from core.media_details import screen_work
from models import Media, TrackedEntry, User
from models.base import MediaType
from models.catalogue import CatalogueCredit, CatalogueEntity, MetadataSnapshot
from models.show import Show


@asynccontextmanager
async def lane(_provider):
    # Exclude provider rate-limit/accounting waits, identically for both versions.
    yield


def old_module(path):
    module = ModuleType('provider_audit_' + path.replace('/', '_'))
    module.__package__ = 'core'
    exec(subprocess.check_output(['git', 'show', '0be2eac:' + path], text=True), module.__dict__)
    return module


def payloads(native, person_base, case):
    jobs = ['Director', 'Writer', 'Screenplay', 'Composer', 'Editor', 'Producer',
            'Director of Photography', 'Production Design'] * 2
    cast = [{'id': person_base + i + 500, 'name': f'Actor {i}', 'character': f'Character {i}',
             'order': i, 'profile_path': '/portrait.jpg', 'credit_id': f'cast-{i}'} for i in range(100)]
    crew = [{'id': person_base + i + 600, 'name': f'Crew {i}', 'job': job,
             'profile_path': '/portrait.jpg', 'credit_id': f'crew-{i}'} for i, job in enumerate(jobs)]
    primary = {'id': native, 'title': 'Primary title', 'name': 'Primary title',
               'overview': None if case == 'missing_metadata' else 'Primary overview',
               'poster_path': '/poster.jpg', 'release_date': '2026-01-01', 'first_air_date': '2026-01-01',
               'genres': [{'id': 18, 'name': 'Drama'}], 'created_by': [],
               'production_companies': [{'id': person_base + 800 + i, 'name': f'Studio {i}'} for i in range(2)],
               'credits': {'cast': cast, 'crew': [] if case == 'missing_crew' else crew}}
    primary['aggregate_credits'] = {
        'cast': [{**person, 'total_episode_count': 20, 'roles': [
            {'credit_id': person['credit_id'], 'character': person['character'], 'episode_count': 20}]} for person in cast],
        'crew': [{**person, 'jobs': [{'credit_id': person['credit_id'], 'job': person['job'], 'episode_count': 20}]}
                 for person in crew]}
    characters = [{'id': i + 1, 'peopleId': person_base + i, 'personName': person['name'],
                   'name': person['character'], 'type': 3, 'sort': i} for i, person in enumerate(cast)]
    characters += [{'id': i + 101, 'peopleId': person_base + i + 100, 'personName': person['name'],
                    'name': '', 'type': 1, 'peopleType': person['job'], 'sort': i + 100} for i, person in enumerate(crew)]
    secondary = {'id': native + 1, 'name': 'TVDB title', 'overview': 'TVDB overview',
                 'image': 'https://artworks.thetvdb.com/poster.jpg', 'firstAired': '2026-01-01',
                 'genres': [{'id': 18, 'name': 'Drama'}], 'characters': characters,
                 'remoteIds': [{'sourceName': 'TheMovieDB.com', 'id': str(native)}],
                 'companies': [{'id': person_base + 300 + i, 'name': f'Studio {i}',
                                'companyType': {'companyTypeName': 'Studio'}} for i in range(2)]}
    if case != 'tvdb_primary':
        primary.pop('aggregate_credits')
    return primary, secondary


async def main(args):
    url = os.environ['CREDIT_AUDIT_DATABASE_URL']
    parsed = make_url(url)
    if parsed.host not in ('localhost', '127.0.0.1') or parsed.port != 55449 or parsed.database != 'anylist_test':
        raise RuntimeError('Only loopback:55449/anylist_test is permitted')
    schema = json.loads(Path(args.fixture).read_text())['schema']
    if not re.fullmatch(r'credit_audit_[a-f0-9]{12}', schema):
        raise RuntimeError('Invalid isolated schema')
    engine = create_async_engine(url, connect_args={'server_settings': {'search_path': schema + ',public'}})
    attach(engine)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    old_credits = old_module('backend/core/media_credits.py')
    old_actors = old_module('backend/core/actor_backfill.py')
    report = {'schema': schema, 'before': '0be2eac', 'after': '68e051d', 'samples_per_version': args.samples,
              'limits': 'PostgreSQL 2 CPU / 2 GiB; Python Windows host uncapped',
              'provider_note': 'Stub responses, no external API; fixed delay per request is simulated; no rate-limit lane waits',
              'results': {}}
    if args.skip_imports:
        previous = json.loads(Path(args.output).read_text())
        if previous['schema'] != schema:
            raise RuntimeError('Mismatched paired-import fixture')
        report['imports'] = previous['imports']
        if 'role_projection' in previous:
            report['role_projection'] = previous['role_projection']
    trial = 0
    run_offset = int(uuid.uuid4().hex[:4], 16) * 1000

    async def import_samples():
        """Separate core ingestion changes from cross-run host/database noise."""
        old_catalogue = old_module('backend/core/catalogue.py')
        old_additive = old_module('backend/core/catalogue_additive.py')
        report['imports'] = {}
        for size in (1000, 3000):
            samples = {'before': [], 'after': []}
            for index in range(args.samples):
                for version in (('before', 'after') if index % 2 == 0 else ('after', 'before')):
                    native = 9000000 + run_offset + size + index * 10 + (version == 'after')
                    doc = normalize_tmdb(source(size, native), 'series')
                    ingest = old_catalogue.ingest_document if version == 'before' else catalogue.ingest_document
                    additive = old_additive.fill_document if version == 'before' else fill_document
                    async def operation(repair=False):
                        async with sessions() as db:
                            work = await (additive(db, doc, 'tmdb') if repair else ingest(db, doc, 'tmdb'))
                            await db.commit()
                            return work.id
                    results = {}
                    work_id = None
                    for stage in ('cold', 'refresh', 'additive'):
                        before = resources()
                        cpu = time.process_time()
                        work_id, stats, _ = await measure(lambda: operation(stage == 'additive'))
                        python_cpu = (time.process_time() - cpu) * 1000
                        after = resources()
                        stats.update(database_cpu_ms=round(after['cpu_ms']-before['cpu_ms'], 3),
                                     python_cpu_ms=round(python_cpu, 3))
                        results[stage] = stats
                    async with sessions() as db:
                        count = await db.scalar(select(func.count()).select_from(CatalogueCredit).where(
                            CatalogueCredit.work_id == work_id))
                        assert count == size + 1
                    samples[version].append(results)
            aggregate = {version: {stage: {metric: summary([r[stage][metric] for r in values])
                for metric in ('wall_ms', 'sql_statements', 'database_cpu_ms', 'python_cpu_ms', 'event_loop_lag_max_ms')}
                for stage in ('cold', 'refresh', 'additive')} for version, values in samples.items()}
            report['imports'][str(size)] = {'summary': aggregate, 'samples': samples}
            Path(args.output).write_text(json.dumps(report, indent=2), encoding='utf-8')
            print(json.dumps({'imports': size, 'cold_ms': {v: a['cold']['wall_ms']['median']
                                                        for v, a in aggregate.items()}}), flush=True)

    async def role_samples():
        old_roles = old_module('backend/core/screen_characters.py')
        previous_source = subprocess.check_output(['git', 'show', '0be2eac:backend/core/screen_characters.py'], text=True)
        current_source = (Path(__file__).resolve().parents[1] / 'core/screen_characters.py').read_text()
        samples = {'before': [], 'after': []}
        for index in range(3):
            for version in (('before', 'after') if index % 2 == 0 else ('after', 'before')):
                async with sessions() as db:
                    work = CatalogueEntity(kind='series', name='Paired role audit')
                    actor = CatalogueEntity(kind='person', name='Paired role actor')
                    db.add_all([work, actor])
                    await db.flush()
                    work_id, actor_id = work.id, actor.id
                    await db.execute(text("""INSERT INTO catalogue_screen_characters(actor_id,role_key,name,aliases)
                        SELECT :actor,'retained-'||g,'Retained Role '||g,jsonb_build_array('retained role '||g)
                        FROM generate_series(1,9001) g"""), {'actor': actor_id})
                    await db.execute(text("""INSERT INTO catalogue_credits
                        (work_id,contributor_id,provider,source_key,role,character_label,attributes)
                        SELECT :work,:actor,'tmdb','role-'||g,'actor','New Role '||g,
                        jsonb_build_object('principal',true,'selection_version',1) FROM generate_series(1,3000) g"""),
                        {'work': work_id, 'actor': actor_id})
                    await db.commit()
                project = old_roles.project_characters if version == 'before' else screen_characters.project_characters
                async def operation():
                    async with sessions() as db:
                        count = await project(db, work_id)
                        await db.commit()
                        assert count == 3000
                before = resources()
                _, stats, _ = await measure(operation)
                after = resources()
                stats['database_cpu_ms'] = round(after['cpu_ms'] - before['cpu_ms'], 3)
                samples[version].append(stats)
        aggregate = {version: {metric: summary([row[metric] for row in values])
                              for metric in ('wall_ms', 'sql_statements', 'database_cpu_ms', 'event_loop_lag_max_ms')}
                     for version, values in samples.items()}
        report['role_projection'] = {'source_unchanged': previous_source == current_source,
                                     'retained_roles': 9001, 'new_roles': 3000, 'summary': aggregate, 'samples': samples}
        Path(args.output).write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps({'role_projection_ms': {v: values['wall_ms']['median'] for v, values in aggregate.items()}}), flush=True)

    async def run(case, version, latency):
        nonlocal trial
        trial += 1
        native, person_base = 50000000 + run_offset + trial * 10, 100000000 + run_offset + trial * 1000
        primary, secondary = payloads(native, person_base, case)
        calls = Counter()

        async def response(key, value):
            calls[key] += 1
            if latency:
                await asyncio.sleep(latency / 1000)
            return value

        async def tmdb_title(*_args, **_kwargs):
            return await response('tmdb.title', primary)

        async def tmdb_aggregate(*_args, **_kwargs):
            return await response('tmdb.aggregate', primary['aggregate_credits'])

        async def tvdb_title(*_args, **_kwargs):
            return await response('tvdb.title', secondary)

        async def person(pid, *_args, **_kwargs):
            return await response('tvdb.person', {'id': pid, 'remoteIds': [
                {'sourceName': 'TheMovieDB.com', 'id': str(pid + 500)}]})

        async def company(cid, *_args, **_kwargs):
            return await response('tvdb.company', {'id': cid, 'remoteIds': [
                {'sourceName': 'TheMovieDB.com', 'id': str(cid + 500)}]})

        provider = CatalogueProviders(SimpleNamespace(lane=lane), tmdb_key='fixture', tvdb_key='fixture')
        kind = 'series' if case == 'tvdb_primary' else 'movie'
        async with sessions() as db:
            media = Media(title='Audit provider title', media_type=MediaType(kind), tmdb_id=native,
                          tvdb_id=native + 1, tmdb_data={})
            user = User(email=f'provider-{native}@example.test', username=f'provider-{native}', api_key=f'fixture-{native}')
            db.add_all([media, user])
            await db.flush()
            db.add(TrackedEntry(user_id=user.id, media_id=media.id))
            if kind == 'series':
                db.add(Show(title='TVDB catalogue', tmdb_id=native, tvdb_id=native + 1, canonical_source='tvdb'))
            await db.commit()
            media_id, user_id = media.id, user.id

        fill = old_credits.fill_screen_credits if version == 'before' else media_credits.fill_screen_credits
        actors = old_actors.backfill_actors if version == 'before' else actor_backfill.backfill_actors
        async def operation(background=False):
            async with sessions() as db:
                if background:
                    return await actors(db, provider, user_id=user_id, force=True, limit=1)
                return await fill(db, await db.get(Media, media_id), providers=provider)

        async def measured(background=False):
            before = resources()
            cpu = time.process_time()
            _, stats, _ = await measure(lambda: operation(background))
            python_cpu = (time.process_time() - cpu) * 1000
            after = resources()
            stats.update(database_cpu_ms=round(after['cpu_ms'] - before['cpu_ms'], 3),
                         python_cpu_ms=round(python_cpu, 3), calls=dict(calls), request_count=sum(calls.values()),
                         simulated_provider_wait_ms=latency * sum(calls.values()))
            calls.clear()
            return stats

        with (patch('core.tmdb.get_movie', tmdb_title), patch('core.tmdb.get_show', tmdb_title),
              patch('core.tmdb.get_show_aggregate_credits', tmdb_aggregate),
              patch('core.tvdb.get_movie', tvdb_title), patch('core.tvdb.get_series', tvdb_title),
              patch('core.tvdb.get_person', person), patch('core.tvdb.get_company', company)):
            cold = await measured()
            cached = await measured()
            background = await measured(True)
        async with sessions() as db:
            media = await db.get(Media, media_id)
            work = await screen_work(db, media)
            stored = await db.scalar(select(func.count()).select_from(CatalogueCredit).where(CatalogueCredit.work_id == work.id))
            tvdb_snapshot = await db.scalar(select(MetadataSnapshot).where(
                MetadataSnapshot.provider == 'tvdb', MetadataSnapshot.entity_id == work.id))
            checks = {'source_rows': stored, 'overview_source': work.field_sources.get('description'),
                      'retained_tvdb_characters': len(tvdb_snapshot.payload['characters']) if tvdb_snapshot else 0}
            if version == 'after':
                assert cached['request_count'] == 0
                if case == 'complete':
                    assert cold['calls'] == {'tmdb.title': 1}
                    assert stored == 118
                elif case == 'missing_metadata':
                    assert cold['calls'] == {'tmdb.title': 1, 'tvdb.title': 1}
                    assert stored == 118 and work.description == 'TVDB overview'
                elif case == 'missing_crew':
                    assert cold['calls'].get('tvdb.title') == 1
                    assert cold['calls'].get('tvdb.person', 0) <= 12
                    assert not cold['calls'].get('tvdb.company')
                    assert stored == 118
                else:
                    assert not any(key.startswith('tmdb') for key in cold['calls'])
                    assert work.field_sources['description'] == 'tvdb'
                    assert (await db.scalar(select(Show.canonical_source).where(Show.tvdb_id == native + 1))) == 'tvdb'
            if tvdb_snapshot:
                assert len(tvdb_snapshot.payload['characters']) == 116
        return {'cold': cold, 'cached': cached, 'background': background, 'checks': checks}

    try:
        if not args.skip_imports:
            await import_samples()
        if args.imports_only:
            return
        if 'role_projection' not in report:
            await role_samples()
        for latency in (0, args.simulated_latency_ms):
            for case in ('complete', 'missing_metadata', 'missing_crew', 'tvdb_primary'):
                samples = {'before': [], 'after': []}
                for index in range(args.samples):
                    order = ('before', 'after') if index % 2 == 0 else ('after', 'before')
                    for version in order:
                        samples[version].append(await run(case, version, latency))
                aggregate = {}
                for version, values in samples.items():
                    aggregate[version] = {stage: {metric: summary([v[stage][metric] for v in values])
                        for metric in ('wall_ms', 'sql_statements', 'database_cpu_ms', 'python_cpu_ms',
                                       'event_loop_lag_max_ms', 'request_count', 'simulated_provider_wait_ms')}
                        for stage in ('cold', 'cached', 'background')}
                key = f'{case}/{latency}ms'
                report['results'][key] = {'summary': aggregate, 'samples': samples}
                Path(args.output).write_text(json.dumps(report, indent=2), encoding='utf-8')
                print(json.dumps({'case': key, 'cold_ms': {v: a['cold']['wall_ms']['median'] for v, a in aggregate.items()},
                                  'cached_ms': {v: a['cached']['wall_ms']['median'] for v, a in aggregate.items()}}), flush=True)
    finally:
        await engine.dispose()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--fixture', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--samples', type=int, default=5)
    parser.add_argument('--simulated-latency-ms', type=int, default=20)
    parser.add_argument('--imports-only', action='store_true')
    parser.add_argument('--skip-imports', action='store_true', help='Reuse completed paired-import samples in the output')
    asyncio.run(main(parser.parse_args()))
