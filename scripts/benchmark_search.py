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
from collections import Counter
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'backend'), str(ROOT / 'backend/tests')]
os.environ.setdefault('SECRET_KEY', 'benchmark-only')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

from core import (
    chronological_discovery,
    contributor_evidence,
    contributor_search,
    game_person_identity,
    game_imdb_discovery,
    tmdb,
)
from dependencies import get_current_user, get_current_user_or_api_key
from models import GlobalSettings, Media, User, UserProfileData
from models.base import MediaType
from models.catalogue import (
    CatalogueCredit,
    CatalogueEntity,
    CatalogueIdentity,
    CatalogueRelationship,
    CharacterAppearance,
    CharacterPerformance,
    ContributorEvidence,
    MetadataSnapshot,
    SteamPriceSnapshot,
)
from models.games import GameActivity, GameEntry, GameFavorite
from models.users import UserSettings
from routers.games import router as games_router
from sqlalchemy import delete, event, or_, select
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
    if args.critic_blocking_reference and (not args.case or 'game_browse_critic' not in args.case):
        raise SystemExit('--critic-blocking-reference requires --case game_browse_critic')
    if args.primary_blocking_reference and (not args.case or 'game_details_cold' not in args.case):
        raise SystemExit('--primary-blocking-reference requires --case game_details_cold')
    if args.baseline_ref:
        if not re.fullmatch(r'[0-9a-fA-F]{7,40}', args.baseline_ref):
            raise SystemExit('--baseline-ref must be a local commit SHA')
        # Execute trusted repository source in this process only; never switch
        # branches or overwrite the user's checkout to compare revisions.
        for name in ('tmdb', 'discovery_policy', 'catalog_search', 'release_browse', 'browse',
                     'person_identities', 'game_imdb_person', 'contributor_evidence', 'studio_discovery', 'contributor_details', 'contributor_search'):
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
    game_statistics_enabled=not args.case or any(name.startswith('game_statistics') for name in args.case)
    try:
        async with fixture.Session() as db:
            viewer = await db.get(User, fixture.user_id)
            viewer.games_access = True
            if args.case and 'game_person_review' in args.case:viewer.is_admin=True
            settings = UserSettings(user_id=viewer.id, tmdb_api_key='fixture-provider-key', preferences={})
            db.add(settings)
            global_settings = await db.get(GlobalSettings, 1)
            global_settings.mdblist_api_key = 'fixture-mdb-key'
            for index in range(args.local_titles):
                db.add(Media(title=f'Benchmark title {900000 + index}', media_type=MediaType.movie,
                    tmdb_id=900000 + index, runtime=100, release_date='2026-10-01',
                    tmdb_data=title(900000 + index)))
            first_game=None
            role_work_ids=[]
            children_base=None
            game_contributor_id=None
            game_company_id=None
            availability_evidence=None
            if not args.case or any(name.startswith(('game_contributor','game_people','game_person','game_company')) for name in args.case):
                performer=CatalogueEntity(kind='person',name='Game works performer')
                db.add(performer);await db.flush();game_contributor_id=performer.id
                if args.case and any(name in args.case for name in ('game_company_cached','game_company_availability','game_company_search')):
                    company=CatalogueEntity(kind='organization',name='Portfolio company')
                    db.add(company);await db.flush();game_company_id=company.id
                    db.add(CatalogueIdentity(entity_id=company.id,namespace='igdb.company',external_id='7000000',source='igdb'))
                    stamp=datetime.now(timezone.utc).replace(tzinfo=None)
                    db.add(MetadataSnapshot(provider='igdb',kind='game_company_job',external_id='7000000',entity_id=company.id,
                        coverage={'state':'complete','complete':True,'received':1000,'total':1000,'generation':'cached'},
                        payload={'raw_company_evidence':'COMPANY_RAW_SENTINEL '*50000},fetched_at=stamp,expires_at=stamp+timedelta(days=7)))
                if not args.case or any(name.startswith('game_person') for name in args.case):
                    db.add(CatalogueIdentity(entity_id=performer.id,namespace='imdb.person',external_id='nm8000000',source='imdb'))
                    stamp=datetime.now(timezone.utc).replace(tzinfo=None)
                    db.add_all([
                        MetadataSnapshot(provider='imdb',kind='game_person_job',external_id='nm8000000',entity_id=performer.id,
                            coverage={'state':'complete','complete':True,'received':1000,'total':1000},
                            payload={'private_source_evidence':'SOURCE_EVIDENCE_SENTINEL '*50000},expires_at=stamp+timedelta(days=30)),
                        MetadataSnapshot(provider='imdb',kind='game_person_credits',external_id='nm8000000',entity_id=performer.id,
                            payload={'profile':{'biography':'Benchmark cached biography.'},'generation':'fixture',
                                'identity_bridge':{'version':game_person_identity.VERSION,
                                    'state':'unresolved','next_check_at':(stamp+timedelta(days=7)).isoformat()},
                                'index_generation':'fixture','index_pages':20,'index_titles':1000},
                            coverage={'person_version':2,'source_complete':True,'state':'partial','projection_revision':'fixture-v1','binding_revision':None,
                                'source_games':1000,'mapped_games':0,'unmapped_games':1000},fetched_at=stamp,expires_at=stamp+timedelta(days=30))])
                    # This case measures an already cached discovery attempt,
                    # including unresolved external identities, not first admission.
                    db.add(MetadataSnapshot(provider='imdb',kind='game_person_discovery',external_id='nm8000000',
                        entity_id=performer.id,payload={'generation':'fixture','page':20,'offset':0,'processed':1000},
                        coverage={'state':'complete','complete':True,'received':1000,'matcher_version':game_imdb_discovery.VERSION},fetched_at=stamp,expires_at=stamp+timedelta(days=1)))
                    for page in range(20):
                        db.add(MetadataSnapshot(provider='imdb',kind='game_person_index',external_id=f'nm8000000:fixture:{page}',
                            entity_id=performer.id,coverage={'page':page},payload={'titles':[{'id':f'tt{page*50+i}',
                                'title':f'Review game {page*50+i:04}','year':2024,'roles':['actor'],'position':page*50+i} for i in range(50)]}))
                        db.add(MetadataSnapshot(provider='imdb',kind='game_person_page',external_id=f'nm8000000:fixture:{page}',
                            entity_id=performer.id,coverage={'page':page},payload={'rows':[{'title':{'id':f'tt{page*50+i}',
                                'titleType':{'id':'videoGame'},'titleText':{'text':'SOURCE_EVIDENCE_SENTINEL '*30}},
                                'category':{'id':'actor'}} for i in range(50)]}))
            for index in range(args.local_titles):
                game = CatalogueEntity(kind='game', name=f'Benchmark game {index:05}', attributes={
                    'game_class': 'game', 'release_date': f'{2000 + index % 26}-01-01',
                    'genres': ['Adventure' if index % 2 else 'Role-playing (RPG)']})
                db.add(game)
                await db.flush()
                if first_game is None:first_game=game
                if len(role_work_ids)<100:role_work_ids.append(game.id)
                if game_statistics_enabled:
                    performer=CatalogueEntity(kind='person',name=f'Statistics performer {index:05}')
                    db.add(performer);await db.flush()
                    db.add(CatalogueCredit(work_id=game.id,contributor_id=performer.id,provider='imdb',
                        source_key=f'statistics-credit-{index}',role='actor',attributes={'evidence':'STATS_RAW_SENTINEL '*2048}))
                if game_contributor_id:
                    db.add(CatalogueCredit(work_id=game.id,contributor_id=game_contributor_id,
                        provider='wikidata',source_key=f'game-works-{index}',role='voice_actor',
                        character_label=f'Character {index}',attributes={'statement':'evidence '*2048}))
                if game_company_id:
                    db.add(CatalogueCredit(work_id=game.id,contributor_id=game_company_id,
                        provider='igdb',source_key=f'company-work-{index}',role='developer',attributes={'source_record':'evidence '*2048}))
                db.add(GameEntry(user_id=viewer.id, work_id=game.id,
                    status=['planning', 'playing', 'completed', 'paused', 'dropped'][index % 5],
                    total_seconds=index * 3600, score=8 if index % 3 else None))
                if args.case and 'game_children' in args.case:
                    if children_base is None:children_base=game.id
                    child=CatalogueEntity(kind='game',name=f'Child {index:05}',description='Synopsis '*200,
                        attributes={'game_class':'expansion' if index%2==0 else 'dlc','release_date':f'{2000+index%26}-01-01'})
                    db.add(child);await db.flush()
                    db.add(CatalogueRelationship(source_id=child.id,target_id=children_base,relation=child.attributes['game_class'],provider='igdb'))
                if not args.case or any(name=='game_search' or name.startswith('game_browse') for name in args.case):
                    db.add(CatalogueIdentity(entity_id=game.id, namespace='igdb.game', external_id=str(8000000+index), source='igdb'))
                if args.case and any(name.startswith('game_favorites') for name in args.case):
                    db.add(GameFavorite(user_id=viewer.id, work_id=game.id, position=index))
                if args.case and any(name.startswith('game_activity') for name in args.case):
                    observed = datetime(2026, 10, 20, 12,tzinfo=timezone.utc).replace(tzinfo=None)+timedelta(seconds=index)
                    db.add(GameActivity(user_id=viewer.id, work_id=game.id, status='playing', created_at=observed,
                        payload={'window_started_at': observed.isoformat(), 'status_changed': True}))
            if not args.case or 'game_credits' in args.case:
                credit_work=first_game
                if credit_work is None or game_contributor_id:
                    # Keep the 30-person projection fixture independent of the
                    # contributor stress fixture when cases run together.
                    credit_work=CatalogueEntity(kind='game',name='Credit benchmark',attributes={'game_class':'game'})
                    db.add(credit_work);await db.flush()
                people=[CatalogueEntity(kind='person',name=f'Fixture game actor {i}') for i in range(30)]
                characters=[CatalogueEntity(kind='character',name=f'Fixture character {i}') for i in range(100)]
                db.add_all(people+characters);await db.flush()
                credits=[CatalogueCredit(work_id=credit_work.id,contributor_id=person.id,provider='wikidata',
                    source_key=f'fixture-{i}',role='voice_actor',role_label='Voice actor',
                    attributes={'languages':['en'],'statement':{'evidence':'fixture '*1024}}) for i,person in enumerate(people)]
                appearances=[CharacterAppearance(work_id=credit_work.id,character_id=character.id,provider='igdb') for character in characters]
                db.add_all(credits+appearances);await db.flush()
                db.add_all([CharacterPerformance(work_id=credit_work.id,credit_id=credits[i//3].id,
                    appearance_id=appearances[i].id,language='en',source='wikidata') for i in range(90)])
                game_credits_id=credit_work.id
            if args.case and 'game_time_to_beat' in args.case:
                game=first_game
                game.attributes={**game.attributes,'igdb_id':'9000000','completion_estimates':{'source':'igdb',
                    'source_game_id':'9000000','samples':25,'main_story_seconds':3600,'main_sides_seconds':7200,'completionist_seconds':10800}}
                db.add(CatalogueIdentity(entity_id=first_game.id,namespace='igdb.game',external_id='9000000',source='igdb'))
                stamp=datetime.now(timezone.utc).replace(tzinfo=None)
                db.add(MetadataSnapshot(provider='igdb',kind='game_estimate_job',external_id=str(first_game.id),entity_id=first_game.id,
                    coverage={'state':'complete','complete':True,'source_game_id':'9000000'},
                    payload={'raw_estimates':'TIME_EVIDENCE_SENTINEL '*50000},expires_at=stamp+timedelta(days=30)))
            if args.case and 'game_offers' in args.case:
                from core.config import settings as provider_settings
                profile=await db.scalar(select(UserProfileData).where(UserProfileData.user_id==viewer.id));profile.country='GB'
                db.add(CatalogueIdentity(entity_id=first_game.id,namespace='steam.app',external_id='9000000',source='igdb'))
                stamp=datetime.now(timezone.utc).replace(tzinfo=None)
                db.add(MetadataSnapshot(provider='itad',kind='price',external_id='9000000:GB',entity_id=first_game.id,
                    fetched_at=stamp,expires_at=stamp+timedelta(hours=6),payload={'raw':'OFFER_JOB_SENTINEL '*50000}))
                for country,currency in [('GB','GBP'),('DE','EUR'),('US','USD')]:
                    db.add(SteamPriceSnapshot(work_id=first_game.id,steam_app_id='9000000',country=country,
                        current={'currency':currency,'amountInt':599},historical_low={'currency':currency,'amountInt':299},
                        historical_low_at='2024-01-01',url='https://store.steampowered.com/app/9000000/',
                        payload={'raw':'OFFER_PRICE_SENTINEL '*50000}))
            if args.case and 'game_ratings' in args.case:
                if not await db.scalar(select(CatalogueIdentity.id).where(CatalogueIdentity.entity_id==first_game.id,
                        CatalogueIdentity.namespace=='steam.app')):
                    db.add(CatalogueIdentity(entity_id=first_game.id,namespace='steam.app',external_id='9000000',source='igdb'))
                first_game.attributes={**first_game.attributes,'game_ratings':{'steam_app_id':'9000000',
                    'all_time':{'positive':900,'negative':100,'total':1000,'percentage':90},
                    'recent':{'positive':80,'negative':20,'total':100,'percentage':80},
                    'critic':{'score':94,'source':'steam_store','platform':'PC','url':None}}}
                stamp=datetime.now(timezone.utc).replace(tzinfo=None)
                for part in ('ratings_job','rating_all_time','rating_recent','rating_critic'):
                    db.add(MetadataSnapshot(provider='steam',kind=f'game_{part}',external_id=str(first_game.id),entity_id=first_game.id,
                        coverage={'state':'complete','complete':True,'steam_app_id':'9000000'},
                        payload={'raw':'RATING_EVIDENCE_SENTINEL '*50000},expires_at=stamp+timedelta(hours=6)))
            if args.case and 'game_info' in args.case:
                if not await db.scalar(select(CatalogueIdentity.id).where(CatalogueIdentity.entity_id==first_game.id,
                        CatalogueIdentity.namespace=='steam.app')):
                    db.add(CatalogueIdentity(entity_id=first_game.id,namespace='steam.app',external_id='9000000',source='igdb'))
                first_game.attributes={**first_game.attributes,'game_info':{'steam_app_id':'9000000','age_ratings':[
                    {'board':'USK','region':'Germany','rating':'16','source':'steam','descriptors':[]}],
                    'requirements':{'platform':'PC','source':'steam','minimum':[{'label':'Memory','value':'12 GB RAM'}],'recommended':[]}}}
                stamp=datetime.now(timezone.utc).replace(tzinfo=None)
                for kind in ('game_info_job','game_store_info'):
                    db.add(MetadataSnapshot(provider='steam',kind=kind,external_id=str(first_game.id),entity_id=first_game.id,
                        coverage={'state':'complete','complete':True,'steam_app_id':'9000000'},
                        payload={'raw':'STORE_INFO_SENTINEL '*50000},expires_at=stamp+timedelta(days=7)))
            if args.case and 'game_metadata' in args.case:
                first_game.attributes={**first_game.attributes,'igdb_id':'9000000'}
                db.add(CatalogueIdentity(entity_id=first_game.id,namespace='igdb.game',external_id='9000000',source='igdb'))
                stamp=datetime.now(timezone.utc).replace(tzinfo=None)
                db.add(MetadataSnapshot(provider='igdb',kind='game',external_id='9000000',entity_id=first_game.id,
                    fetched_at=stamp,expires_at=stamp+timedelta(days=7),coverage={'primary_complete':True,'characters_complete':True,'release_precision_version':1,'artwork_policy_version':2},
                    payload={'raw':'PRIMARY_METADATA_SENTINEL '*50000}))
            if args.case and 'game_details_cold' in args.case:
                first_game.attributes={**first_game.attributes,'igdb_id':'9000000'}
                db.add(CatalogueIdentity(entity_id=first_game.id,namespace='igdb.game',external_id='9000000',source='igdb'))
            if args.case and 'game_person_availability' in args.case:
                from core.contributor_availability import movie_job
                from core.game_imdb_bindings import revision
                db.add(CatalogueIdentity(entity_id=game_contributor_id,namespace='tmdb.person',external_id='7000000',source='tmdb'))
                key,config=movie_job('actor',7000000)
                availability_evidence={'kind':key[0],'native_id':key[1],'variant':key[2],'config':config,
                    'payload':{'domain_availability':{'actor':{'movie':'available','series':'empty'}},
                        'raw_movie_credits':'MOVIE_EVIDENCE_SENTINEL '*50000},
                    'expires_at':datetime.now(timezone.utc).replace(tzinfo=None)+timedelta(days=1),'attempts':0,'next_attempt_at':None}
                db.add(ContributorEvidence(**availability_evidence))
                await db.flush()
                component=await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.kind=='game_person_credits',MetadataSnapshot.external_id=='nm8000000'))
                component.coverage={**component.coverage,'binding_revision':await revision(db),
                    'role_coverage':{'actor':{'source':args.local_titles,'unmapped':0}}}
            await db.commit()
        fixture.app.include_router(games_router)
        if args.case and 'game_title_mapping' in args.case:
            from core.game_browse_sources import GameBrowseSources
            from core.game_imdb_discovery_source import GameIMDbDiscoverySource
            mapping_sources=GameBrowseSources(fixture.Session)
            async def mapping_igdb(endpoint,query):
                counters['provider_requests']+=1
                counters['igdb_title_requests']+=1
                await asyncio.sleep(args.provider_delay_ms/1000)
                return [{'id':9000999,'name':'Benchmark mapping game','slug':'benchmark-mapping-game',
                    'game_type':{'id':0},'first_release_date':1506038400}]
            mapping_sources.igdb=mapping_igdb
            mapping_service=GameIMDbDiscoverySource(fixture.Session,sources=mapping_sources)
            mapping_service.explicit_page=AsyncMock(return_value={'outcomes':[{'id':'tt9000999','status':'unmapped'}],'stale':False})
            @fixture.app.get('/tracking/benchmark/game-title-mapping')
            async def mapping_route():
                result=await mapping_service.page(['tt9000999'],titles={'tt9000999':{
                    'id':'tt9000999','title':'Benchmark mapping game','year':2017}})
                outcome=result['outcomes'][0]
                if outcome['status']!='verified' or outcome['igdb_id']!=9000999 or not outcome['evidence']['retrieval_complete']:
                    raise RuntimeError('Title mapping lost exact identity or retrieval completeness')
                return {'results':[{'id':outcome['igdb_id'],'type':'game','title':'Benchmark mapping game'}],
                    'method':outcome['binding_source'],'complete':True}
        if not args.case or any(case in args.case for case in ('game_credits','game_details_cold')):
            from routers.catalogue import router as catalogue_router
            fixture.app.include_router(catalogue_router,prefix='/tracking')
        fixture.viewer = viewer  # Optional-auth catalogue routes use the same approved fixture.
        if game_statistics_enabled:
            from core import game_statistics
            await game_statistics.run_due(fixture.Session)
        fixture.app.dependency_overrides[get_current_user] = lambda: viewer
        fixture.app.dependency_overrides[get_current_user_or_api_key] = lambda: viewer

        def sql_start(conn, cursor, statement, parameters, context, many):
            context.benchmark_started = time.perf_counter()
            counters['db_reads' if statement.lstrip().upper().startswith(('SELECT', 'WITH')) else 'db_writes'] += 1
            normalized = re.sub(r'\s+', ' ', statement).strip()
            identity = hashlib.sha256(normalized.encode()).hexdigest()[:12]
            context.benchmark_identity = identity
            sql_patterns[identity] = normalized
            counters['sql:' + identity] += 1

        def sql_end(conn, cursor, statement, parameters, context, many):
            elapsed = (time.perf_counter() - context.benchmark_started) * 1000
            counters['db_ms'] += elapsed
            counters['sql_ms:' + context.benchmark_identity] += elapsed

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
                if current_case.startswith('game_people'):
                    return {'total_pages':1,'results':[]}
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
                if current_case.startswith('auto_'):
                    if current_case == 'auto_combined_established' and kind == 'tv':
                        return {'page': page, 'total_pages': 1, 'results': []}
                    row = title(880001, kind, search=True)
                    row['title' if kind == 'movie' else 'name'] = 'Fallback Target'
                    row['popularity'] = 80 if current_case == 'auto_separate_empty' and kind == 'tv' else 1
                    row['vote_count'] = 10000 if current_case == 'auto_combined_established' else 0
                    return {'page': page, 'total_pages': 1, 'results': [row]}
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
                items = result.get('results', result.get('items', []))
                if label.startswith('game_contributor') or label in ('game_person_cached','game_company_cached'):items=result['works']
                if label=='game_credits':items=result['people']+result['game_characters']
                if label=='game_person_roles':items=result['roles']
                if label.startswith('game_statistics'):items=result.get('data') if isinstance(result.get('data'),list) else [result.get('data')]
                if result.get('sections'):
                    items = [item for section in result['sections'] for item in section['results']]
                elif isinstance(items, dict):
                    items = [item for section in items.values() for item in section]
                row['rows'] = len(items)
                # Fixture insertion timestamps are intentionally fresh each run;
                # compare game membership/order/progress without those clocks.
                identities = [{key: value for key, value in item.items() if key not in ('updated_at', 'year', 'genres')} for item in items] if label.startswith(('game_list', 'game_favorites')) else items
                row['result_identity'] = hashlib.sha256(json.dumps(identities, sort_keys=True).encode()).hexdigest()
                if label in ('game_browse_pages','game_browse_timeline'):
                    # Retain the full fingerprint above. This additional hash
                    # proves adding precision metadata leaves all older fields,
                    # membership and ordering unchanged across this projection.
                    prior=[{key:value for key,value in item.items() if key not in (
                        'release_date_precision','release_quarter')} for item in items]
                    row['release_compatible_identity']=hashlib.sha256(json.dumps(prior,sort_keys=True).encode()).hexdigest()
                row['payload_bytes'] = len(json.dumps(result).encode())
                row['total'] = result.get('total')
                row['has_more'] = result.get('has_more')
                row['pending'] = result.get('verification_pending')
                if label in ('game_company_availability','game_person_availability'):
                    row['result_identity']=hashlib.sha256(json.dumps(result['domains'],sort_keys=True).encode()).hexdigest()
                    row['pending']=result['pending']
                if label=='game_time_to_beat':
                    row['result_identity']=hashlib.sha256(json.dumps(result['estimates'],sort_keys=True).encode()).hexdigest()
                    row['pending']=result['status']['pending']
                if label=='game_offers':
                    row['result_identity']=hashlib.sha256(json.dumps({key:result[key] for key in ('country','country_source','steam_app_id')},sort_keys=True).encode()).hexdigest()
                    row['rows']=len(result['offers']);row['pending']=result['status']['pending']
                if label=='game_ratings':
                    row['result_identity']=hashlib.sha256(json.dumps(result['ratings'],sort_keys=True).encode()).hexdigest()
                    row['rows']=len(result['ratings'])-1;row['pending']=result['status']['pending']
                if label=='game_info':
                    row['result_identity']=hashlib.sha256(json.dumps(result['info'],sort_keys=True).encode()).hexdigest()
                    row['rows']=len(result['info']['age_ratings']);row['pending']=result['status']['pending']
                if label=='game_metadata':
                    row['result_identity']=hashlib.sha256(json.dumps({key:result[key] for key in ('primary_ready','entity_id','complete')},sort_keys=True).encode()).hexdigest()
                    row['rows']=int(result['primary_ready']);row['pending']=result['pending']
                if label=='game_details_cold':
                    row['primary_ready']=result['game_metadata_status']['primary_ready']
                    row['result_identity']=hashlib.sha256(json.dumps({key:result[key] for key in ('id','type','title')},sort_keys=True).encode()).hexdigest()
            samples.append(row)
            return result

        async def request(path, params):
            if path == 'browse/context':
                response = await fixture.client.post('/tracking/browse/context', json={'items': [
                    {'type': 'movie', 'tmdb_id': identifier} for identifier in range(1000, 1024)
                ]})
            else:
                response = await fixture.client.get('/tracking/' + path, params=params)
            if response.status_code != 200:
                raise RuntimeError(f'{current_case}: {response.status_code}: {response.text[:200]}')
            return response.json()

        async def game_candidates(query):
            if current_case=='game_search_prefix':
                from core.catalogue_providers import CatalogueProviders
                async def prefix_source(endpoint,statement):
                    counters['provider_requests']+=1;counters['provider:igdb/search']+=1
                    await asyncio.sleep(args.provider_delay_ms/1000)
                    return [] if 'search "Elden R";' in statement else [
                        {'id':119133,'name':'Elden Ring','game_type':{'id':0}},
                        {'id':9,'name':'Elden Sword','game_type':{'id':0}}]
                api=CatalogueProviders();api.validate=lambda *args:None;api.igdb=prefix_source
                return await api.search('igdb','game',query,1,50)
            counters['provider_requests'] += 1
            counters['provider:igdb/search'] += 1
            await asyncio.sleep(args.provider_delay_ms / 1000)
            return [{'external_id':str(8000000+i),'name':f'Benchmark game {i:05}','game_class':'game'} for i in range(50)]

        from core import game_browse
        from core.game_browse_sources import GameBrowseSources
        game_sources = GameBrowseSources(fixture.Session)
        game_sources.api.validate = lambda *args: None
        async def game_provider(endpoint, query):
            counters['provider_requests'] += 1
            counters['provider:igdb/'+endpoint] += 1
            if endpoint == 'companies':
                await asyncio.sleep(args.provider_delay_ms / 1000)
                return [{'id':7000000,'name':'Portfolio company'}] if 'Portfolio company' in query else []
            await asyncio.sleep(args.provider_delay_ms / 1000)
            if endpoint=='genres':return [{'id':5,'name':'Role-playing (RPG)'},{'id':9,'name':'Adventure'}]
            if endpoint=='platforms':return [{'id':6,'name':'PC (Microsoft Windows)'},{'id':167,'name':'PlayStation 5','platform_family':{'name':'PlayStation'}}]
            if endpoint=='external_game_sources':return [{'id':1,'name':'IGDB'}]
            if endpoint=='popularity_types':return [{'id':1,'name':'Visits','external_popularity_source':1}]
            if endpoint=='popularity_primitives':
                offset=int(re.search(r'offset (\d+)',query).group(1))
                return [{'game_id':8000000+i,'value':1000-i,'calculated_at':int(time.time())} for i in range(offset,min(offset+100,300))]
            if endpoint=='games':
                searching=query.startswith('search ')
                if query.startswith('search "Elden R";'):return []
                broader=query.startswith('search "Elden";')
                if searching:
                    offset=int(re.search(r'offset (\d+)',query).group(1));ids=range(8000000+offset,8000000+min(offset+100,160 if broader else 300))
                else:ids=map(int,re.search(r'id=\(([\d,]+)\)',query).group(1).split(','))
                return [{'id':identifier,'slug':f'benchmark-game-{identifier-8000000}',
                    'name':(f'Elden Ring benchmark {identifier-8000000:05}' if identifier%2==0 else f'Unrelated benchmark {identifier-8000000:05}') if broader else f'Benchmark game {identifier-8000000:05}',
                    'game_type':{'id':0},'first_release_date':1704067200+((identifier%20)*86400 if searching else 0),
                    'release_dates':[{'date':1704067200+((identifier%20)*86400 if searching else 0),
                        'date_format':{'id':0,'format':'YYYYMMDD'}}],
                    'genres':[{'id':5 if identifier%2==0 else 9,'name':'Role-playing (RPG)' if identifier%2==0 else 'Adventure'}],
                    'platforms':[{'id':6,'name':'PC (Microsoft Windows)'}] if identifier%3 else [{'id':167,'name':'PlayStation 5','platform_family':{'name':'PlayStation'}}]}
                    for identifier in ids]
            raise RuntimeError('Unexpected Games benchmark provider endpoint')
        game_sources.igdb=game_provider
        critic_clock={}
        async def critic_provider(provider,url,**kwargs):
            counters['provider_requests']+=1;counters['provider:'+provider+'/critic']+=1
            await asyncio.sleep(args.provider_delay_ms/1000)
            if provider=='wikidata':
                indices=sorted({int(value) for value in re.findall(r'benchmark-game-(\d+)',kwargs['params']['query'])})
                return {'results':{'bindings':[{
                    name:{'value':value} for name,value in {'key':f'rawg-benchmark-{i}',
                        'item':f'http://www.wikidata.org/entity/Q{4000000+i}',
                        'slug':f'benchmark-game-{i}','id':str(8000000+i)}.items()
                } for i in indices if i%7]}}
            if provider=='rawg':
                index=int(url.rsplit('-',1)[1])
                return {'id':10000+index,'slug':f'rawg-benchmark-{index}',
                    'released':'2024-01-01','metacritic':90 if index%3==0 else 70}
            raise RuntimeError('Unexpected critic benchmark provider')
        game_sources.request=critic_provider
        publish_progress=game_sources.cache.publish
        async def record_critic_progress(provider,kind,key,token,data,max_stale):
            await publish_progress(provider,kind,key,token,data,max_stale)
            if current_case=='game_browse_critic' and data.get('rows') and 'first_verified_ms' not in counters:
                counters['first_verified_ms']=round((time.perf_counter()-critic_clock['start'])*1000,2)
        game_sources.cache.publish=record_critic_progress
        event.listen(fixture.engine.sync_engine, 'before_cursor_execute', sql_start)
        event.listen(fixture.engine.sync_engine, 'after_cursor_execute', sql_end)
        feed = [{**title(1000 + i), 'imdb_votes': 10000, 'imdb_id': f'tt{1000+i}', 'imdb_score': 8} for i in range(120)]
        cases = [
            ('game_credits',f'catalogue/entities/{game_credits_id}/details',{})] if not args.case or 'game_credits' in args.case else []
        if args.case and 'game_title_mapping' in args.case:
            cases += [('game_title_mapping','benchmark/game-title-mapping',{})]
        if game_contributor_id:
            path=f'contributors/actor/catalogue:{game_contributor_id}'
            cases += [('game_contributor',path,{'media_type':'game'}),
                ('game_contributor_listed',path,{'media_type':'game','list_scope':'in','status':'completed'}),
                ('game_contributor_pages',path,{'media_type':'game'})]
            cases += [('game_people_search','quick-search/contributors',{'q':'Game works performer','kind':'actor','work_domain':'game'}),
                ('game_people_shared','quick-search/contributors',{'q':'Game works performer','kind':'actor'})]
            cases += [('game_person_cached',path,{'media_type':'game'}),('game_person_status','games/people/nm8000000/imdb',{})]
            if args.case and 'game_person_roles' in args.case:
                cases += [('game_person_roles','games/people/nm8000000/imdb',{'work_id':role_work_ids})]
            if args.case and 'game_person_name_search' in args.case:
                cases += [('game_person_name_search','quick-search/contributors',{'q':'Game works performer','kind':'people','work_domain':'game'})]
            if args.case and 'game_person_availability' in args.case:
                cases += [('game_person_availability',path+'/availability',{})]
            if args.case and 'game_person_review' in args.case:
                cases += [('game_person_review','games/people/nm8000000/imdb/titles',{})]
        cases += [
            *([('game_company_cached',f'contributors/studio/catalogue:{game_company_id}',{'media_type':'game'})] if game_company_id else []),
            *([('game_company_availability',f'contributors/studio/catalogue:{game_company_id}/availability',{})] if game_company_id else []),
            *([('game_company_search','quick-search/contributors',{'q':'Portfolio company','kind':'studio','work_domain':'game'})] if game_company_id else []),
            ('game_search', 'games/search', {'q': 'Benchmark'}),
            *([('game_time_to_beat',f'games/works/{first_game.id}/time-to-beat',{})] if args.case and 'game_time_to_beat' in args.case else []),
            *([('game_offers',f'games/works/{first_game.id}/offers',{'country':'DE'})] if args.case and 'game_offers' in args.case else []),
            *([('game_ratings',f'games/works/{first_game.id}/ratings',{})] if args.case and 'game_ratings' in args.case else []),
            *([('game_info',f'games/works/{first_game.id}/info',{'country':'DE'})] if args.case and 'game_info' in args.case else []),
            *([('game_metadata',f'games/works/{first_game.id}/metadata',{})] if args.case and 'game_metadata' in args.case else []),
            *([('game_details_cold',f'catalogue/entities/{first_game.id}/details',{})] if args.case and 'game_details_cold' in args.case else []),
            ('game_search_prefix','games/search',{'q':'Elden R'}),
            *([('game_children',f'games/works/{children_base}/children',{'limit':24})] if children_base else []),
            ('game_browse_preview','games/browse',{'limit':6}),
            ('game_browse_grid','games/browse',{'limit':24}),
            ('game_browse_pages','games/browse',{'limit':24}),
            ('game_browse_timeline','games/browse',{'limit':24,'category':'newest','timeline':'true','q':'Benchmark'}),
            ('game_browse_partial_search','games/browse',{'limit':24,'q':'Elden R'}),
            ('game_browse_genre_platform','games/browse',{'limit':24,'genres':'igdb.genre:5','platforms':'pc'}),
            *([('game_browse_critic','games/browse',{'limit':24,'min_metacritic':80})]
                if args.case and 'game_browse_critic' in args.case else []),
            ('game_list', f'games/users/{viewer.username}/list', {'limit': 50}),
            *([('game_statistics_overview',f'games/users/{viewer.username}/statistics',{}),
                ('game_statistics_rebuild',f'games/users/{viewer.username}/statistics',{}),
                ('game_statistics_actors',f'games/users/{viewer.username}/statistics',{'section':'actors','sort':'time'}),
                ('game_statistics_actors_page_two',f'games/users/{viewer.username}/statistics',{'section':'actors','sort':'time','page':2})] if game_statistics_enabled else []),
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
            ('viewer_context', 'browse/context', {}),
            ('auto_combined_empty', None, {}),
            ('auto_combined_established', None, {}),
            ('auto_separate_empty', None, {}),
            ('auto_browse_empty', None, {}),
        ]
        if args.edge_cases_only:
            cases = [case for case in cases if case[0] in ('actor', 'crew', 'studio', 'negative_studio', 'empty_query', 'runtime_timeout', 'provider_failure', 'page_two', 'people_combined', 'all_contributors')]
        elif args.filters_only:
            cases = [case for case in cases if case[0] in ('date_genre_score', 'series_airing', 'series_returning',
                'series_finished', 'series_cancelled', 'movie_released', 'movie_upcoming', 'movie_oldest',
                'movie_alphabetical', 'grid_popular', 'grid_trending', 'grid_score', 'cinema_rereleases', 'filter_options')]
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
            if args.case and 'game_details_cold' in args.case:
                from core import game_metadata
                from core.catalogue_normalize import normalize_igdb,GAME_ARTWORK_VERSION
                async def cold_primary(provider,kind,native,*,on_primary=None):
                    counters['provider_requests']+=1;counters['provider:igdb/primary']+=1
                    await asyncio.sleep(args.provider_delay_ms/1000)
                    raw={'id':int(native),'name':'Benchmark game 00000','summary':'Primary synopsis',
                        'game_type':{'id':0,'type':'Main Game'},'_coverage':{'primary_complete':True,
                            'characters_complete':False,'release_precision_version':1,'artwork_policy_version':GAME_ARTWORK_VERSION}}
                    if on_primary:await on_primary(normalize_igdb(raw),{'game':raw})
                    counters['provider_requests']+=1;counters['provider:igdb/characters']+=1
                    await asyncio.sleep(args.provider_delay_ms/1000)
                    raw['_coverage']['characters_complete']=True
                    return normalize_igdb(raw),{'game':raw}
                stack.enter_context(patch.object(game_metadata,'CatalogueProviders',return_value=SimpleNamespace(validate=lambda *args:None,detail=cold_primary)))
                if args.primary_blocking_reference:
                    original_ensure=game_metadata.GameMetadata.ensure
                    async def blocking_primary(self,db,work,**kwargs):
                        return await original_ensure(self,db,work,**{**kwargs,'wait_seconds':15})
                    stack.enter_context(patch.object(game_metadata.GameMetadata,'ensure',blocking_primary))
            from core import game_company_discovery
            company_discovery = game_company_discovery.GameCompanyDiscovery(fixture.Session, sources=game_sources)
            stack.enter_context(patch.object(game_company_discovery, 'GameCompanyDiscovery', return_value=company_discovery))
            from core import game_people_discovery
            async def people_candidates(term):
                counters['provider_requests'] += 1
                counters['provider:imdb/names'] += 1
                await asyncio.sleep(args.provider_delay_ms / 1000)
                return [{'id':'nm8000000','name':'Game works performer','image':None}] if current_case=='game_person_name_search' else []
            people_source = SimpleNamespace(people_candidates=people_candidates)
            people_discovery = game_people_discovery.GamePeopleDiscovery(fixture.Session, sources=game_sources, source=people_source)
            stack.enter_context(patch.object(game_people_discovery, 'GamePeopleDiscovery', return_value=people_discovery))
            if args.case and 'game_offers' in args.case:
                stack.enter_context(patch.object(provider_settings,'itad_api_key','fixture-itad-key'))
            stack.enter_context(patch.object(tmdb, '_request', side_effect=provider))
            stack.enter_context(patch('core.game_discovery.candidates', side_effect=game_candidates))
            stack.enter_context(patch.object(game_browse,'GameBrowseSources',return_value=game_sources))
            if args.critic_blocking_reference:
                original_page=game_browse.GameBrowse.page
                async def blocking_page(self,query,**kwargs):
                    return await original_page(self,query,**{**kwargs,'wait_seconds':None})
                stack.enter_context(patch.object(game_browse.GameBrowse,'page',blocking_page))
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
                    profile.minimum_votes = 0 if mode == 'show_all' else 250
                    profile.minimum_movie_runtime = 0 if mode == 'show_all' else 30
                    profile.excluded_categories = [] if mode == 'show_all' else [99]
                    await db.commit()
                for label, path, values in cases:
                    current_case = label
                    reset()
                    if label=='game_title_mapping':
                        async with fixture.Session() as db:
                            await db.execute(delete(MetadataSnapshot).where(MetadataSnapshot.provider=='igdb',
                                MetadataSnapshot.kind=='game_title_candidates'))
                            await db.commit()
                    if label=='game_details_cold':
                        async with fixture.Session() as db:
                            await db.execute(delete(MetadataSnapshot).where(MetadataSnapshot.provider=='igdb',
                                MetadataSnapshot.kind=='game',MetadataSnapshot.external_id=='9000000'))
                            await db.commit()
                    if path == 'quick-search/contributors':
                        async with fixture.Session() as db:
                            await db.execute(delete(MetadataSnapshot).where(MetadataSnapshot.kind.in_(('game_company_search','game_people_search'))))
                            await db.commit()
                    if label.startswith('game_browse'):
                        async with fixture.Session() as db:
                            await db.execute(delete(MetadataSnapshot).where(or_(MetadataSnapshot.kind.like('game_browse%'),MetadataSnapshot.kind=='game_timeline_windows')))
                            await db.commit()
                        if label=='game_browse_critic':
                            async with fixture.Session() as db:
                                await db.execute(delete(MetadataSnapshot).where(MetadataSnapshot.kind=='game_critic_bridge'))
                                await db.commit()
                    if path == 'quick-search/contributors':
                        async with fixture.Session() as db:
                            await db.execute(delete(ContributorEvidence))
                            await db.commit()
                    if label=='game_person_availability':
                        # Neighboring contributor-search cases clear their
                        # evidence. Restore this case's cached fixture before
                        # measurement, including when cases run together.
                        async with fixture.Session() as db:
                            await db.merge(ContributorEvidence(**availability_evidence))
                            await db.commit()
                    params = {'region': 'DE', **values}
                    if mode == 'show_all' and path and path.startswith('browse'):
                        params['unfiltered'] = 'true'

                    async def operation(path=path, params=params, label=label, mode=mode):
                        if label == 'game_person_name_search':
                            result = await request(path, params)
                            if [row['key'] for row in result['results']['actor']] != [f'catalogue:{game_contributor_id}']:
                                raise RuntimeError('Name discovery lost its canonical verified game person')
                            if result.get('verification_pending') or not result.get('partial'):
                                raise RuntimeError('Cached unresolved person lost partial mapping evidence or became pending')
                            if 'SOURCE_EVIDENCE_SENTINEL' in json.dumps(result):
                                raise RuntimeError('Name discovery transferred full person source evidence')
                            for identity in counters:
                                sql=sql_patterns.get(identity.removeprefix('sql:'),'') if identity.startswith('sql:') else ''
                                if re.search(r'catalogue_metadata_snapshots_\d+\.payload(?:,|\s+AS|\s+FROM)',sql):
                                    raise RuntimeError('Name discovery hydrated a full career instead of compact job evidence')
                            return result
                        if label == 'game_company_search':
                            result = await request(path, params)
                            if [row['key'] for row in result['results']['studio']] != [f'catalogue:{game_company_id}']:
                                raise RuntimeError('Company discovery lost its verified canonical portfolio')
                            if result.get('verification_pending') or result.get('partial'):
                                raise RuntimeError('Fresh complete company unexpectedly pending or partial')
                            if 'COMPANY_RAW_SENTINEL' in json.dumps(result):
                                raise RuntimeError('Company search transferred full portfolio evidence')
                            return result
                        if label=='game_details_cold':
                            result=await request(path,params)
                            if result['id']!=first_game.id or result['type']!='game':
                                raise RuntimeError('Cold detail shell lost canonical identity')
                            return result
                        if label=='game_metadata':
                            result=await request(path,params)
                            if not result['primary_ready'] or not result['complete'] or result['pending'] or result['entity_id']!=first_game.id:
                                raise RuntimeError('Warm primary metadata lost canonical identity or requeued a fresh snapshot')
                            if counters['provider_requests'] or counters['db_writes']:raise RuntimeError('Warm metadata performed provider work or writes')
                            if 'SENTINEL' in json.dumps(result):raise RuntimeError('Metadata exposed raw source evidence')
                            for identity in counters:
                                sql=sql_patterns.get(identity.removeprefix('sql:'),'') if identity.startswith('sql:') else ''
                                if 'catalogue_metadata_snapshots.payload' in sql:raise RuntimeError('Metadata status hydrated raw provider evidence')
                            return result
                        if label=='game_info':
                            result=await request(path,params)
                            if result['info']['steam_app_id']!='9000000' or result['info']['requirements']['minimum'][0]['value']!='12 GB RAM':
                                raise RuntimeError('Cached game requirements lost identity or minimum tier')
                            if result['country']!='DE' or result['status']['pending'] or not result['status']['complete']:
                                raise RuntimeError('Cached game info lost country or queued fresh data')
                            if counters['provider_requests'] or counters['db_writes']:raise RuntimeError('Warm game info performed provider work or writes')
                            if 'SENTINEL' in json.dumps(result):raise RuntimeError('Game info exposed raw source evidence')
                            for identity in counters:
                                sql=sql_patterns.get(identity.removeprefix('sql:'),'') if identity.startswith('sql:') else ''
                                if 'catalogue_metadata_snapshots.payload' in sql:raise RuntimeError('Game info hydrated raw provider evidence')
                            return result
                        if label=='game_ratings':
                            result=await request(path,params)
                            ratings=result['ratings']
                            if ratings['steam_app_id']!='9000000' or ratings['all_time']['total']!=1000 or ratings['recent']['total']!=100 or ratings['critic']['score']!=94:
                                raise RuntimeError('Cached game ratings lost identity or independent components')
                            if not result['status']['complete'] or result['status']['pending']:
                                raise RuntimeError('Cached game ratings were requeued')
                            if counters['provider_requests'] or counters['db_writes']:raise RuntimeError('Warm ratings performed provider work or writes')
                            if 'SENTINEL' in json.dumps(result):raise RuntimeError('Ratings exposed raw evidence')
                            for identity in counters:
                                sql=sql_patterns.get(identity.removeprefix('sql:'),'') if identity.startswith('sql:') else ''
                                if 'catalogue_metadata_snapshots.payload' in sql:raise RuntimeError('Ratings hydrated raw provider evidence')
                            return result
                        if label=='game_time_to_beat':
                            result=await request(path,params)
                            if result['estimates']['main_sides_seconds']!=7200 or not result['status']['complete']:
                                raise RuntimeError('Cached game estimates changed identity or units')
                            if counters['provider_requests'] or counters['db_writes']:raise RuntimeError('Cached estimate read performed work')
                            if 'TIME_EVIDENCE_SENTINEL' in json.dumps(result):raise RuntimeError('Raw estimate evidence exposed')
                            for identity in counters:
                                sql=sql_patterns.get(identity.removeprefix('sql:'),'') if identity.startswith('sql:') else ''
                                if 'catalogue_metadata_snapshots.payload,' in sql or 'catalogue_metadata_snapshots.payload FROM' in sql:
                                    raise RuntimeError('Estimate read hydrated raw source evidence')
                            return result
                        if label in ('game_company_availability','game_person_availability'):
                            result=await request(path,params)
                            expected={'movie':'unknown','series':'unknown','game':'available'} if label=='game_company_availability' else {'movie':'available','series':'empty','game':'available'}
                            if result['domains']!=expected or result['pending']:
                                raise RuntimeError('Availability lost known Games or invented a negative')
                            if counters['provider_requests'] or counters['db_writes']:raise RuntimeError('Cached availability performed provider work or writes')
                            for identity in counters:
                                sql=sql_patterns.get(identity.removeprefix('sql:'),'') if identity.startswith('sql:') else ''
                                if any(f'{table}.payload{suffix}' in sql for table in ('catalogue_metadata_snapshots','catalogue_contributor_evidence') for suffix in (',',' FROM')):
                                    raise RuntimeError('Availability hydrated a full provider snapshot')
                            return result
                        if label=='game_company_cached':
                            result=await request(path,params)
                            if result['known_works']!=args.local_titles or len(result['works'])!=min(24,args.local_titles):
                                raise RuntimeError('Company portfolio lost its full works or bounded page')
                            state=result.get('game_company_status')
                            if not state or not state['complete'] or state['pending']:raise RuntimeError('Cached company was requeued')
                            if counters['provider_requests'] or counters['db_writes']:raise RuntimeError('Cached company read performed provider work or writes')
                            if 'COMPANY_RAW_SENTINEL' in json.dumps(result):raise RuntimeError('Company read transferred raw source evidence')
                            for identity in counters:
                                sql=sql_patterns.get(identity.removeprefix('sql:'),'') if identity.startswith('sql:') else ''
                                if 'catalogue_metadata_snapshots.payload,' in sql or 'catalogue_metadata_snapshots.payload FROM' in sql:
                                    raise RuntimeError('Company read hydrated a full provider snapshot')
                            return result
                        if label=='game_offers':
                            result=await request(path,params)
                            if (result['country'],result['country_source'],result['steam_app_id'])!=('GB','account','9000000'):
                                raise RuntimeError('Offer hint replaced the saved account country')
                            if len(result['offers'])!=1 or result['offers'][0]['current']['currency']!='GBP' or result['status']['pending']:
                                raise RuntimeError('Cached offer selected another country or requeued a fresh price')
                            if counters['provider_requests'] or counters['db_writes']:raise RuntimeError('Warm offers performed provider work or writes')
                            if 'SENTINEL' in json.dumps(result):raise RuntimeError('Offer exposed source evidence')
                            for identity in counters:
                                sql=sql_patterns.get(identity.removeprefix('sql:'),'') if identity.startswith('sql:') else ''
                                if 'catalogue_steam_prices.payload' in sql or 'catalogue_metadata_snapshots.payload' in sql:
                                    raise RuntimeError('Offer hydrated raw provider evidence')
                            return result
                        if label.startswith('game_statistics'):
                            if label=='game_statistics_rebuild':
                                from models.game_statistics import GameStatsState
                                from sqlalchemy import update
                                async with fixture.Session() as db:
                                    await db.execute(update(GameStatsState).where(GameStatsState.user_id==viewer.id).values(
                                        payload=None,source_revision=GameStatsState.source_revision+1,next_due_at=game_statistics.now()))
                                    await db.commit()
                                if await game_statistics.run_due(fixture.Session)!=1:raise RuntimeError('Statistics rebuild was not published')
                            result=await request(path,params)
                            if result['status']!='ready':raise RuntimeError('Cached Games statistics were not ready')
                            played=args.local_titles-(args.local_titles+4)//5
                            if label in ('game_statistics_overview','game_statistics_rebuild'):
                                if result['data']['totals']['listed_games']!=args.local_titles or result['data']['totals']['played_games']!=played:
                                    raise RuntimeError('Statistics lost parent counts or included Planned games in played totals')
                            else:
                                # Combined/default matrices also seed one
                                # shared portfolio actor across these games.
                                groups=played+int(bool(game_contributor_id and played))
                                if result['total']!=groups or len(result['data'])!=min(24,max(0,groups-(24 if params.get('page')==2 else 0))):
                                    raise RuntimeError('Statistics lost full contributor coverage or bounded paging')
                                times=[row['recorded_seconds'] for row in result['data']]
                                if times!=sorted(times,reverse=True):raise RuntimeError('Statistics sorted only a truncated subset')
                            if counters['provider_requests'] or label!='game_statistics_rebuild' and counters['db_writes']:raise RuntimeError('Cached statistics performed provider work or writes')
                            if 'STATS_RAW_SENTINEL' in json.dumps(result):raise RuntimeError('Statistics exposed source credit evidence')
                            return result
                        if label=='game_children':
                            result=await request(path,params)
                            if result['total']!=args.local_titles or len(result['results'])!=min(24,args.local_titles):
                                raise RuntimeError('DLC paging lost collection coverage')
                            if any(row['game_class']!='expansion' for row in result['results']):
                                raise RuntimeError('DLC category ordering did not prioritize expansions')
                            if counters['provider_requests'] or counters['db_writes']:raise RuntimeError('DLC read performed provider work or writes')
                            return result
                        if label=='game_search_prefix':
                            result=await request(path,params)
                            if [row['igdb_id'] for row in result['results']]!=[119133] or counters['provider_requests']!=2:
                                raise RuntimeError('Partial-title fallback lost the matching game or exceeded its provider bound')
                            return result
                        if label.startswith('game_person'):
                            result=await request(path,params)
                            status=result['game_person_status'] if label=='game_person_cached' else result['status'] if label=='game_person_review' else result
                            if label=='game_person_review':
                                if not result['ready'] or result['total']!=1000 or len(result['results'])!=50 or not result['has_more']:
                                    raise RuntimeError('Admin review lost cached game coverage or page bounds')
                                if [row['imdb_id'] for row in result['results']]!=[f'tt{i}' for i in range(50)]:
                                    raise RuntimeError('Admin review changed source ordering')
                            if not status['complete'] or status['pending']:raise RuntimeError('Cached person source was unnecessarily requeued')
                            if label=='game_person_cached' and (result['known_works']!=args.local_titles or result['description']!='Benchmark cached biography.'):
                                raise RuntimeError('Person component lost the existing game works or cached profile')
                            if label=='game_person_roles' and (len(result.get('roles',[]))!=len(role_work_ids)
                                or {row['id'] for row in result['roles']}!=set(role_work_ids)
                                or any(len(row['characters'])!=1 for row in result['roles'])):
                                raise RuntimeError('Caption projection lost displayed identity or character evidence')
                            if counters['provider_requests'] or counters['db_writes']:raise RuntimeError('Cached person reads performed provider work or writes')
                            if 'SOURCE_EVIDENCE_SENTINEL' in json.dumps(result):raise RuntimeError('Person read exposed raw source evidence')
                            for identity in counters:
                                sql=sql_patterns.get(identity.removeprefix('sql:'),'') if identity.startswith('sql:') else ''
                                if 'catalogue_metadata_snapshots.payload,' in sql or 'catalogue_metadata_snapshots.payload FROM' in sql:
                                    raise RuntimeError('Person read hydrated a full cached filmography')
                            return result
                        if label.startswith('game_people'):
                            result=await request(path,params)
                            rows=result['results']['actor']
                            if len(rows)!=1 or rows[0]['key']!=f'catalogue:{game_contributor_id}' or not rows[0]['url'].endswith('?media_type=game'):
                                raise RuntimeError('Verified game-only actor did not survive contributor search')
                            if 'statement' in json.dumps(result):raise RuntimeError('Game search exposed raw source evidence')
                            return result
                        if label.startswith('game_contributor'):
                            first=await request(path,params)
                            if first['known_works']!=args.local_titles or first['list_count']!=args.local_titles:
                                raise RuntimeError('Game contributor counts lost complete collection membership')
                            works=first['works']
                            if label=='game_contributor_pages' and first['has_more']:
                                second=await request(path,{**params,'page':2,'source':'local','cursor':first['next_cursor']})
                                works+=second['works']
                                if len(works)!=min(48,args.local_titles) or len({row['id'] for row in works})!=len(works):
                                    raise RuntimeError('Game contributor continuation lost or repeated works')
                            if any(row['entry'] is not None or row['type']!='game' for row in works) or 'statement' in json.dumps(first):
                                raise RuntimeError('Game contributor projection exposed full editor/source evidence')
                            return {**first,'works':works}
                        if label=='game_credits':
                            result=await request(path,params)
                            actors=[person for person in result['people'] if person['kind']=='actor']
                            if len(actors)!=30 or len(result['game_characters'])!=100 or sum(len(person.get('performances',[])) for person in actors)!=90:
                                raise RuntimeError('Game credit projection lost cast, independent characters or performance associations')
                            if 'statement' in json.dumps(result):raise RuntimeError('Raw source credit evidence leaked into the detail payload')
                            return result
                        if label=='game_browse_partial_search':
                            result=await request(path,params);rows=list(result['results'])
                            for _ in range(2):
                                if not result['has_more']:raise RuntimeError('Partial game search exhausted before its broader window')
                                result=await request(path,{**params,'cursor':result['cursor']});rows.extend(result['results'])
                            ids=[row['igdb_id'] for row in rows]
                            if ids!=list(range(8000000,8000144,2)) or any(not row['title'].startswith('Elden Ring') for row in rows):
                                raise RuntimeError('Partial game search lost, repeated or admitted unrelated candidates')
                            return {**result,'results':rows}
                        if label=='game_browse_critic':
                            from core.game_source_cache import _flights
                            critic_clock['start']=time.perf_counter()
                            first=await request(path,params)
                            counters['first_response_ms']=round((time.perf_counter()-critic_clock['start'])*1000,2)
                            counters['first_response_pending']=bool(first.get('verification_pending'))
                            if first['results'] and 'first_verified_ms' not in counters:
                                counters['first_verified_ms']=counters['first_response_ms']
                            tasks=[task for key,task in list(_flights.items()) if key[0]==id(fixture.Session)]
                            if tasks:await asyncio.gather(*tasks)
                            result=await request(path,params) if first.get('verification_pending') or tasks else first
                            expected=[8000000+i for i in range(48) if i%7 and i%3==0]
                            if [r['igdb_id'] for r in result['results']]!=expected or any(r['metacritic']<80 for r in result['results']):
                                raise RuntimeError('Critic verification changed exact score eligibility or Trending order')
                            if result.get('verification_pending') or result.get('verification_error') or not result['partial']:
                                raise RuntimeError('Critic verification failed to settle with honest unknown-score coverage')
                            return result
                        if label in {'game_browse_pages','game_browse_timeline'}:
                            first=await request(path,params)
                            second=await request(path,{**params,'cursor':first['cursor']})
                            ids=[row['igdb_id'] for row in first['results']+second['results']]
                            if len(ids)!=48 or len(set(ids))!=48:
                                raise RuntimeError('Games continuation lost or repeated candidates')
                            if label=='game_browse_timeline':
                                dates=[row.get('release_date') or '' for row in first['results']+second['results']]
                                if dates!=sorted(dates,reverse=True):raise RuntimeError('Timeline dates were not finalized before paging')
                            return {**second,'results':first['results']+second['results']}
                        if path:
                            return await request(path, params)
                        if label.startswith('auto_'):
                            # Model the browser's two-phase request protocol.
                            # Frontend tests separately cover state and cancellation.
                            def empty(row):
                                return not row['results'] and not row.get('has_more') and not row.get('notice')
                            if label == 'auto_browse_empty':
                                values = {'q': 'Fallback Target', 'genres': '18', 'min_score': 7, 'region': 'DE'}
                                result = await request('browse', values)
                                if empty(result):
                                    counters['fallback_requests'] += 1
                                    result = await request('browse', {**values, 'unfiltered': 'true'})
                                return result
                            scopes = ('movie', 'series')
                            rows = await asyncio.gather(*(request('catalog', {
                                'q': 'Fallback Target', 'media_type': kind, 'scan_empty': 'true',
                            }) for kind in scopes))
                            combined_empty = all(empty(row) for row in rows)
                            indices = [index for index in range(2) if (
                                combined_empty if label.startswith('auto_combined_') else empty(rows[index]))]
                            counters['fallback_requests'] += len(indices)
                            broad = await asyncio.gather(*(request('catalog', {
                                'q': 'Fallback Target', 'media_type': scopes[index], 'scan_empty': 'true', 'unfiltered': 'true',
                            }) for index in indices))
                            for index, row in zip(indices, broad):
                                rows[index] = row
                            return {'results': [item for row in rows for item in row['results']]}
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

                    journey_started=time.perf_counter()
                    await measure(label, mode, 'cold', operation)
                    if label=='game_details_cold':
                        sample=samples[-1]
                        flight=game_metadata._flights.get((id(fixture.engine),'9000000'))
                        if flight:await flight.primary.wait()
                        primary=await request(f'games/works/{first_game.id}/primary',{})
                        if primary['id']!=first_game.id or primary['overview']!='Primary synopsis':
                            raise RuntimeError('Late primary metadata lost canonical content')
                        if any(key in primary for key in ('entry','notes','people','organizations','game_characters')):
                            raise RuntimeError('Public primary publication transferred private or credit payloads')
                        sample['primary_ready_ms']=round((time.perf_counter()-journey_started)*1000,2)
                        sample['primary_payload_bytes']=len(json.dumps(primary).encode())
                        sample['primary_journey_reads']=counters['db_reads']
                        sample['primary_journey_provider_requests']=counters['provider_requests']
                        if flight:await asyncio.shield(flight.task)
                        sample['complete_journey_reads']=counters['db_reads']
                        sample['complete_journey_writes']=counters['db_writes']
                        sample['complete_journey_provider_requests']=counters['provider_requests']
                        if counters['provider_requests']!=2:raise RuntimeError('Cold primary journey duplicated or skipped provider components')
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
        output['primary_blocking_reference']=args.primary_blocking_reference
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
    parser.add_argument('--critic-blocking-reference',action='store_true',
        help='Compare the same critic workload while waiting for its full page; not a historical checkout replay')
    parser.add_argument('--primary-blocking-reference',action='store_true',
        help='Compare the same cold primary workload with the former 15-second SSR wait; not a full historical checkout replay')
    parser.add_argument('--edge-cases-only', action='store_true')
    parser.add_argument('--filters-only', action='store_true')
    parser.add_argument('--stress-only', action='store_true')
    parser.add_argument('--local-titles', type=int, default=400)
    parser.add_argument('--credit-works', type=int, default=80)
    parser.add_argument('--contributors-only', action='store_true')
    parser.add_argument('--skip-contributor-relevance', action='store_true')
    parser.add_argument('--case', action='append', help='Restrict to named cases; repeat for several')
    asyncio.run(main(parser.parse_args()))
