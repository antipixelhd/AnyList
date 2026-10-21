"""Reproducible search work accounting against a disposable PostgreSQL schema.

Uses real routes/SQL and controlled provider payloads, not production credentials.
Example (PowerShell): set STATISTICS_TEST_DATABASE_URL to an anylist_test database;
python scripts/benchmark_search.py --output .local-design/search-browse/before.json
Provider delay is simulated; this is not a VPS or browser latency benchmark.
Profiles keep popularity 25, disable only that floor, or bypass personal curation.
--skip-contributor-relevance isolates the cost of bounded work verification;
it is a benchmark-only control, not an account preference.
"""
import argparse
import asyncio
import hashlib
import importlib
import json
import os
import re
import statistics
import subprocess
import sys
import time
from datetime import datetime, timedelta
from collections import Counter
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'backend'), str(ROOT / 'backend/tests')]
os.environ.setdefault('SECRET_KEY', 'benchmark-only')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

from core import chronological_discovery, contributor_evidence, contributor_search, tmdb
from dependencies import get_current_user, get_current_user_or_api_key
from models import GlobalSettings, Media, User, UserProfileData
from models.base import MediaType
from models.catalogue import CatalogueEntity, ContributorEvidence
from models.games import GameEntry, GameFavorite, GameActivity
from routers.games import router as games_router
from models.users import UserSettings
from sqlalchemy import delete, event, select
from test_statistics_pg import StatisticsDatabaseTests


def title(identifier, kind='movie', *, search=False, future=False):
    value = {'id': identifier, 'title' if kind == 'movie' else 'name': f'Benchmark title {identifier}',
             'popularity': 80 if identifier % 4 else 2, 'vote_count': 500,
             'vote_average': 8, 'genre_ids': [99] if identifier % 7 == 0 else [18],
             'adult': False, 'original_language': 'en', 'origin_country': ['US'],
             'release_date' if kind == 'movie' else 'first_air_date': '2099-01-01' if future else '2026-10-01',
             'poster_path': '/fixture.jpg', 'status': 'Returning Series' if kind == 'tv' else 'Released'}
    if not search:
        value.update(runtime=10 if identifier % 6 == 0 else 100,
                     genres=[{'id': i, 'name': 'Documentary' if i == 99 else 'Drama'} for i in value['genre_ids']],
                     seasons=[{'season_number': 2, 'air_date': '2099-02-01'}],
                     next_episode_to_air={'season_number': 2, 'episode_number': 1, 'air_date': '2099-02-01'})
    return value


async def main(args):
    if not os.environ.get('STATISTICS_TEST_DATABASE_URL'):
        raise SystemExit('Set STATISTICS_TEST_DATABASE_URL to a disposable anylist_test database.')
    if args.baseline_ref:
        if not re.fullmatch(r'[0-9a-fA-F]{7,40}', args.baseline_ref):
            raise SystemExit('--baseline-ref must be a local commit SHA')
        # Execute trusted repository source in this process only; never switch
        # branches or overwrite the user's checkout to compare revisions.
        for name in ('tmdb', 'discovery_policy', 'catalog_search', 'release_browse', 'browse',
                     'contributor_evidence', 'studio_discovery', 'contributor_search'):
            module = importlib.import_module('core.' + name)
            process = await asyncio.to_thread(subprocess.run, ['git', 'show', f'{args.baseline_ref}:backend/core/{name}.py'],
                cwd=ROOT, check=True, capture_output=True, text=True, encoding='utf-8')
            source = process.stdout
            exec(compile(source, module.__file__, 'exec'), module.__dict__)  # noqa: S102 -- trusted local revision, benchmark process only
    fixture = StatisticsDatabaseTests()
    await fixture.asyncSetUp()
    samples = []
    counters = Counter()
    sql_patterns = {}
    current_case = ''
    try:
        async with fixture.Session() as db:
            viewer = await db.get(User, fixture.user_id)
            settings = UserSettings(user_id=viewer.id, tmdb_api_key='fixture-provider-key', preferences={})
            db.add(settings)
            global_settings = await db.get(GlobalSettings, 1)
            global_settings.mdblist_api_key = 'fixture-mdb-key'
            for index in range(args.local_titles):
                db.add(Media(title=f'Benchmark title {900000 + index}', media_type=MediaType.movie,
                    tmdb_id=900000 + index, runtime=100, release_date='2026-10-01',
                    tmdb_data=title(900000 + index)))
            for index in range(args.local_titles):
                game = CatalogueEntity(kind='game', name=f'Benchmark game {index:05}', attributes={
                    'game_class': 'game', 'release_date': f'{2000 + index % 26}-01-01',
                    'genres': ['Adventure' if index % 2 else 'Role-playing (RPG)']})
                db.add(game)
                await db.flush()
                db.add(GameEntry(user_id=viewer.id, work_id=game.id,
                    status=['planning', 'playing', 'completed', 'paused', 'dropped'][index % 5],
                    total_seconds=index * 3600, score=8 if index % 3 else None))
                if args.case and any(name.startswith('game_favorites') for name in args.case):
                    db.add(GameFavorite(user_id=viewer.id, work_id=game.id, position=index))
                if args.case and any(name.startswith('game_activity') for name in args.case):
                    observed = datetime(2026, 10, 20, 12)+timedelta(seconds=index)
                    db.add(GameActivity(user_id=viewer.id, work_id=game.id, status='playing', created_at=observed,
                        payload={'window_started_at': observed.isoformat(), 'status_changed': True}))
            await db.commit()
        fixture.app.include_router(games_router)
        fixture.app.dependency_overrides[get_current_user] = lambda: viewer
        fixture.app.dependency_overrides[get_current_user_or_api_key] = lambda: viewer

        def sql_start(conn, cursor, statement, parameters, context, many):
            context.benchmark_started = time.perf_counter()
            counters['db_reads' if statement.lstrip().upper().startswith(('SELECT', 'WITH')) else 'db_writes'] += 1
            normalized = re.sub(r'\s+', ' ', statement).strip()
            identity = hashlib.sha256(normalized.encode()).hexdigest()[:12]
            sql_patterns[identity] = normalized
            counters['sql:' + identity] += 1

        def sql_end(conn, cursor, statement, parameters, context, many):
            counters['db_ms'] += (time.perf_counter() - context.benchmark_started) * 1000

        async def provider(client, url, headers, params, retries):
            counters['provider_requests'] += 1
            counters['provider:' + url.removeprefix(tmdb.TMDB_BASE)] += 1
            if current_case == 'provider_failure':
                raise tmdb.TMDBUnavailable('Controlled provider outage')
            await asyncio.sleep(args.provider_delay_ms / 1000)
            path = url.removeprefix(tmdb.TMDB_BASE + '/').split('/')
            if path[:2] == ['watch', 'providers']:
                return {'results': [{'provider_id': 8, 'provider_name': 'Fixture service'}]}
            params = params or {}
            page = int(params.get('page', 1))
            if path[0] == 'search' and path[1] in ('person', 'company'):
                return {'total_pages': 1, 'results': [
                    {'id': 700000 + i, 'name': f'Benchmark {i}', 'known_for_department': 'Directing' if current_case == 'crew' or (current_case in ('people_combined', 'all_contributors') and i % 2) else 'Acting',
                     'popularity': 80, 'known_for': [title(1000 + i, search=True)], 'logo_path': '/logo.jpg'} for i in range(8)]}
            if path[0] == 'person':
                return {'id': int(path[1]), 'combined_credits': {
                    'cast': [{**title(1000 + i, search=True), 'media_type': 'movie'} for i in range(args.credit_works)],
                    'crew': [{**title(1000 + i, search=True), 'media_type': 'movie', 'job': 'Director'} for i in range(args.credit_works)]}}
            if path[0] in ('search', 'discover'):
                if current_case == 'empty_query' or (current_case == 'negative_studio' and path[0] == 'discover'):
                    return {'page': page, 'total_pages': 0, 'results': []}
                kind = 'tv' if path[1] == 'tv' else 'movie'
                future = str(params.get('primary_release_date.gte') or params.get('first_air_date.gte') or params.get('air_date.gte') or params.get('release_date.gte') or '') > '2026-12-31'
                count = 500 if 'sparse' in str(params.get('query', '')) else 8
                secondary = 'release_date.gte' in params or 'air_date.gte' in params
                rows = [title(1000 + (10000 if secondary else 0) + (page - 1) * 20 + i, kind, search=True, future=future) for i in range(20)]
                if 'sparse' in str(params.get('query', '')):
                    for row in rows:
                        row['popularity'] = 1
                return {'page': page, 'total_pages': count, 'results': rows}
            kind = path[0]
            identifier = int(path[1])
            if current_case == 'runtime_timeout':
                await asyncio.sleep(1.2)
            if len(path) > 2:
                if path[2] == 'watch':
                    return {'results': {'DE': {'flatrate': [{'provider_id': 8}]}}}
                return {'results': [{'iso_3166_1': 'DE', 'release_dates': [{'type': 3, 'release_date': '2026-10-01T00:00:00Z'}]}]}
            result = title(identifier, kind, future=current_case == 'movie_upcoming')
            for name in params.get('append_to_response', '').split(','):
                if name == 'watch/providers':
                    result[name] = {'results': {'DE': {'flatrate': [{'provider_id': 8}]}}}
                elif name == 'release_dates':
                    result[name] = {'results': [{'iso_3166_1': 'DE', 'release_dates': [{'type': 3, 'release_date': '2026-10-01T00:00:00Z'}]}]}
                elif name:
                    result[name] = {}
            return result

        def reset():
            tmdb._cache._store.clear()
            chronological_discovery._cache._store.clear()
            contributor_search._candidate_cache._store.clear()
            contributor_evidence._summary_cache._store.clear()
            from core import catalog_search
            if hasattr(catalog_search, '_runtime_retry'):
                catalog_search._runtime_retry._store.clear()

        async def measure(label, mode, state, operation):
            counters.clear()
            start = time.perf_counter()
            result = await operation()
            row = {'case': label, 'mode': mode, 'state': state,
                   'ms': round((time.perf_counter() - start) * 1000, 2), **dict(counters)}
            if isinstance(result, dict):
                items = result.get('results', [])
                if result.get('sections'):
                    items = [item for section in result['sections'] for item in section['results']]
                elif isinstance(items, dict):
                    items = [item for section in items.values() for item in section]
                row['rows'] = len(items)
                # Fixture insertion timestamps are intentionally fresh each run;
                # compare game membership/order/progress without those clocks.
                identities = [{key: value for key, value in item.items() if key not in ('updated_at', 'year', 'genres')} for item in items] if label.startswith(('game_list', 'game_favorites')) else items
                row['result_identity'] = hashlib.sha256(json.dumps(identities, sort_keys=True).encode()).hexdigest()
                row['payload_bytes'] = len(json.dumps(result).encode())
                row['total'] = result.get('total')
                row['has_more'] = result.get('has_more')
                row['pending'] = result.get('verification_pending')
            samples.append(row)
            return result

        async def request(path, params):
            response = await fixture.client.get('/tracking/' + path, params=params)
            if response.status_code != 200:
                raise RuntimeError(f'{current_case}: {response.status_code}: {response.text[:200]}')
            return response.json()

        event.listen(fixture.engine.sync_engine, 'before_cursor_execute', sql_start)
        event.listen(fixture.engine.sync_engine, 'after_cursor_execute', sql_end)
        feed = [{**title(1000 + i), 'imdb_votes': 10000, 'imdb_id': f'tt{1000+i}', 'imdb_score': 8} for i in range(120)]
        cases = [
            ('game_list', f'games/users/{viewer.username}/list', {'limit': 50}),
            ('game_list_page_two', f'games/users/{viewer.username}/list', {'limit': 50, 'offset': 50}),
            ('game_list_genre_year', f'games/users/{viewer.username}/list', {'genre': 'Role-playing (RPG)', 'year': '2024', 'limit': 2}),
            ('game_list_status', f'games/users/{viewer.username}/list', {'status': 'playing', 'sort': 'score', 'limit': 50}),
            ('game_favorites', f'games/users/{viewer.username}/favorites', {'limit': 50}),
            ('game_favorites_page_two', f'games/users/{viewer.username}/favorites', {'limit': 50, 'offset': 50}),
            ('game_activity_profile', f'games/users/{viewer.username}/activity', {'limit': 12}),
            ('movie_exact', 'catalog', {'q': 'Benchmark title', 'media_type': 'movie'}),
            ('movie_typo', 'catalog', {'q': 'benchnark titl', 'media_type': 'movie'}),
            ('series_exact', 'catalog', {'q': 'Benchmark title', 'media_type': 'series'}),
            ('combined_titles', None, {}),
            ('movie_genre_score', 'browse', {'q': 'Benchmark title', 'genres': '18', 'min_score': 7}),
            ('movie_streaming', 'browse', {'q': 'Benchmark title', 'provider': 8}),
            ('movie_cinema', 'browse', {'q': 'Benchmark title', 'cinema': 'true'}),
            ('movie_newest', 'browse', {'sort': 'newest'}),
            ('series_upcoming', 'browse', {'media_type': 'series', 'sort': 'upcoming'}),
            ('timeline_text', 'browse', {'media_type': 'series', 'q': 'Benchmark', 'timeline': 'true'}),
            ('default_sections', 'browse/sections', {}),
            ('actor', 'quick-search/contributors', {'q': 'Benchmark', 'kind': 'actor'}),
            ('crew', 'quick-search/contributors', {'q': 'Benchmark', 'kind': 'staff'}),
            ('studio', 'quick-search/contributors', {'q': 'Benchmark', 'kind': 'studio'}),
            ('sparse_viewport', None, {}),
            ('details_then_timeline', None, {}),
            ('negative_studio', 'quick-search/contributors', {'q': 'Negative', 'kind': 'studio'}),
            ('empty_query', 'catalog', {'q': 'zz-no-match', 'media_type': 'movie'}),
            ('runtime_timeout', 'catalog', {'q': 'Benchmark title', 'media_type': 'movie'}),
            ('provider_failure', 'catalog', {'q': 'zz-no-match', 'media_type': 'movie'}),
            ('page_two', 'browse', {'q': 'Benchmark title', 'page': 2, 'source': 'remote'}),
            ('people_combined', 'quick-search/contributors', {'q': 'Benchmark', 'kind': 'people'}),
            ('all_contributors', 'quick-search/contributors', {'q': 'Benchmark', 'kind': 'all'}),
            ('date_genre_score', 'browse', {'q': 'Benchmark title', 'genres': '18', 'min_score': 7, 'start': '2026-01-01', 'end': '2026-12-31'}),
            ('series_airing', 'browse', {'q': 'Benchmark title', 'media_type': 'series', 'status': 'airing'}),
            ('series_returning', 'browse', {'q': 'Benchmark title', 'media_type': 'series', 'status': 'returning'}),
            ('series_finished', 'browse', {'q': 'Benchmark title', 'media_type': 'series', 'status': 'finished'}),
            ('series_cancelled', 'browse', {'q': 'Benchmark title', 'media_type': 'series', 'status': 'cancelled'}),
            ('movie_released', 'browse', {'q': 'Benchmark title', 'status': 'released'}),
            ('movie_upcoming', 'browse', {'sort': 'upcoming'}),
            ('movie_oldest', 'browse', {'sort': 'oldest'}),
            ('movie_alphabetical', 'browse', {'sort': 'title'}),
            ('grid_popular', 'browse', {'sort': 'popular'}),
            ('grid_trending', 'browse', {'sort': 'trending'}),
            ('grid_score', 'browse', {'sort': 'score', 'min_score': 7}),
            ('cinema_rereleases', 'browse', {'sort': 'newest', 'cinema': 'true', 'include_rereleases': 'true'}),
            ('filter_options', 'browse/facets', {'genres': '18'}),
        ]
        if args.edge_cases_only:
            cases = [case for case in cases if case[0] in ('actor', 'crew', 'studio', 'negative_studio', 'empty_query', 'runtime_timeout', 'provider_failure', 'page_two', 'people_combined', 'all_contributors')]
        elif args.filters_only:
            cases = cases[-14:]
        elif args.stress_only:
            cases = [case for case in cases if case[0] in ('movie_exact', 'combined_titles', 'actor', 'crew', 'studio', 'default_sections')]
        elif args.contributors_only:
            cases = [case for case in cases if case[1] == 'quick-search/contributors']
        if args.case:
            selected = set(args.case)
            if selected - {case[0] for case in cases}:
                raise ValueError('Unknown case for the selected matrix')
            cases = [case for case in cases if case[0] in selected]
        with ExitStack() as stack:
            stack.enter_context(patch.object(tmdb, '_request', side_effect=provider))
            stack.enter_context(patch('core.mdblist_discovery.ranked_rows', AsyncMock(return_value=(feed, False))))
            if args.skip_contributor_relevance:
                async def unverified_studios(db, viewer, rows, api_key):
                    return rows, False
                stack.enter_context(patch.object(contributor_search, 'verified_studios', side_effect=unverified_studios))
                stack.enter_context(patch.object(contributor_search, 'verified_people', AsyncMock(return_value=False)))
            for mode in ('curated', 'floor_disabled', 'show_all'):
                async with fixture.Session() as db:
                    saved = await db.scalar(select(UserSettings).where(UserSettings.user_id == viewer.id))
                    saved.preferences = {'minimum_popularity': 25 if mode == 'curated' else 0}
                    profile = await db.scalar(select(UserProfileData).where(UserProfileData.user_id == viewer.id))
                    profile.minimum_movie_runtime = 0 if mode == 'show_all' else 30
                    profile.excluded_categories = [] if mode == 'show_all' else [99]
                    await db.commit()
                for label, path, values in cases:
                    current_case = label
                    reset()
                    if path == 'quick-search/contributors':
                        async with fixture.Session() as db:
                            await db.execute(delete(ContributorEvidence))
                            await db.commit()
                    params = {'region': 'DE', **values}
                    if mode == 'show_all' and path and path.startswith('browse'):
                        params['unfiltered'] = 'true'

                    async def operation(path=path, params=params, label=label, mode=mode):
                        if path:
                            return await request(path, params)
                        if label == 'combined_titles':
                            rows = await asyncio.gather(*(request('catalog', {'q': 'Benchmark title', 'media_type': kind}) for kind in ('movie', 'series')))
                            return {'results': [item for row in rows for item in row['results']]}
                        if label == 'details_then_timeline':
                            for identifier in range(1000, 1020):
                                await tmdb.get_movie(identifier, api_key='fixture-provider-key')
                            return await request('browse', {'sort': 'newest', 'region': 'DE', **({'unfiltered': 'true'} if mode == 'show_all' else {})})
                        result = await request('browse', {'q': 'sparse', **({'unfiltered': 'true'} if mode == 'show_all' else {})})
                        collected = list(result['results'])
                        for _ in range(5):
                            if not result['has_more'] or len(collected) >= 24:
                                break
                            result = await request('browse', {'q': 'sparse', 'page': result['page'] + 1,
                                'source': result['source'], 'cursor': result['cursor'],
                                **({'unfiltered': 'true'} if mode == 'show_all' else {})})
                            collected.extend(result['results'])
                        return {**result, 'results': collected}

                    await measure(label, mode, 'cold', operation)
                    if path == 'quick-search/contributors':
                        async def verify(operation=operation):
                            for _ in range(20):
                                async with fixture.Session() as db:
                                    jobs = list(await db.scalars(select(ContributorEvidence).where(ContributorEvidence.next_attempt_at.is_not(None))))
                                    for job in jobs:
                                        job.next_attempt_at = contributor_evidence.now()
                                    await db.commit()
                                if not jobs:
                                    break
                                await contributor_evidence.run_batch(fixture.Session)
                            return await operation()
                        await measure(label, mode, 'verification_worker', verify)
                    for _ in range(args.rounds):
                        await measure(label, mode, 'warm', operation)
                    if path == 'quick-search/contributors':
                        # Expiry is an intentional refresh, not a repeated slow
                        # check within the same validity window.
                        async with fixture.Session() as db:
                            for job in await db.scalars(select(ContributorEvidence)):
                                job.expires_at = contributor_evidence.now() - timedelta(seconds=1)
                            await db.commit()
                        await measure(label, mode, 'expired', operation)
        output = {'environment': 'controlled ASGI routes + disposable PostgreSQL; auth dependency stub',
                  'local_titles': args.local_titles, 'credit_works_per_section': args.credit_works,
                  'contributor_relevance': not args.skip_contributor_relevance,
                  'provider_delay_ms': args.provider_delay_ms, 'rounds': args.rounds,
                  'baseline_ref': args.baseline_ref, 'sql_patterns': sql_patterns, 'samples': samples}
        target = ROOT / args.output
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(output, indent=2), encoding='utf-8')
        print(f'Recorded {len(samples)} samples in {target}')
        for mode in ('curated', 'floor_disabled', 'show_all'):
            for label, _, _ in cases:
                rows = [r for r in samples if r['mode'] == mode and r['case'] == label]
                cold = rows[0]
                warm = [r for r in rows if r['state'] == 'warm']
                print(mode, label, cold['ms'], round(statistics.median(r['ms'] for r in warm), 2),
                      cold.get('db_reads', 0), cold.get('provider_requests', 0))
    finally:
        await tmdb.close()
        await fixture.asyncTearDown()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    parser.add_argument('--rounds', type=int, default=3)
    parser.add_argument('--provider-delay-ms', type=float, default=50)
    parser.add_argument('--baseline-ref', help='Load benchmarked modules from a trusted local commit SHA')
    parser.add_argument('--edge-cases-only', action='store_true')
    parser.add_argument('--filters-only', action='store_true')
    parser.add_argument('--stress-only', action='store_true')
    parser.add_argument('--local-titles', type=int, default=400)
    parser.add_argument('--credit-works', type=int, default=80)
    parser.add_argument('--contributors-only', action='store_true')
    parser.add_argument('--skip-contributor-relevance', action='store_true')
    parser.add_argument('--case', action='append', help='Restrict to named cases; repeat for several')
    asyncio.run(main(parser.parse_args()))
