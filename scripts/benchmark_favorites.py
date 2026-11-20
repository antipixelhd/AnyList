"""Compare Favorites backend reads against the same disposable PostgreSQL fixtures."""

import argparse
import asyncio
import json
import os
from pathlib import Path
from statistics import median
import sys
from time import perf_counter
from uuid import uuid4

from sqlalchemy import delete, event, insert, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))

TABLES = {
    'users', 'user_profiles', 'tracking_preferences', 'global_settings', 'shows', 'media',
    'tracked_entries', 'media_favorites', 'follows', 'tracking_activity', 'catalogue_entities', 'catalogue_identities',
    'game_favorites', 'game_entries', 'game_child_states', 'game_achievements', 'game_achievement_unlocks',
}


async def measure(url, tracked, screen, games, runs, compare):
    from fastapi import Response
    from fastapi.encoders import jsonable_encoder
    from models import Base, GlobalSettings, Media, User, UserProfileData
    from models.base import MediaType, PrivacyLevel
    from models.catalogue import CatalogueEntity
    from models.games import GameFavorite
    from models.tracking import MediaFavorite, TrackedEntry, TrackingPreferences
    from routers.games import favorites as game_favorites
    from routers.tracking import person, person_header

    schema = 'favorites_bench_' + uuid4().hex
    admin = create_async_engine(url)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(url, connect_args={'server_settings': {'search_path': schema}})
    session = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(lambda sync: Base.metadata.create_all(
                sync, tables=[table for table in Base.metadata.sorted_tables if table.name in TABLES],
            ))
        async with session.begin() as db:
            owner = User(id=1, username='favorites-benchmark', email='favorites@example.test', api_key='favorites-bench', games_access=True)
            db.add_all([owner, GlobalSettings(id=1, show_anime=False)])
            await db.flush()
            db.add_all([UserProfileData(user_id=1, privacy_level=PrivacyLevel.public),
                        TrackingPreferences(user_id=1, favorite_order=list(range(screen, 0, -1)))])
            metadata = {'genres': ['Drama'], 'production_companies': [{'name': 'Fixture Studio'}], 'padding': 'x' * 3072}
            await db.execute(insert(Media), [
                dict(id=i, title=f'Title {i}', media_type=MediaType.movie if i % 2 else MediaType.series,
                     poster_path=f'/fixture-{i}.jpg', overview='Synthetic synopsis ' * 15, tmdb_data=metadata)
                for i in range(1, tracked + 1)
            ])
            await db.execute(insert(TrackedEntry), [
                dict(user_id=1, media_id=i, favorite=i == 1, manual_score=8) for i in range(1, tracked + 1)
            ])
            await db.execute(insert(MediaFavorite), [dict(user_id=1, media_id=i) for i in range(2, screen + 1)])
            await db.execute(insert(CatalogueEntity), [
                dict(id=i, kind='game', name=f'Game {i}', image_url=f'https://example.test/game-{i}.jpg', attributes={})
                for i in range(1, games + 1)
            ])
            await db.execute(insert(GameFavorite), [dict(user_id=1, work_id=i, position=i) for i in range(1, games + 1)])

        statements = 0

        @event.listens_for(engine.sync_engine, 'before_cursor_execute')
        def count(*_args):
            nonlocal statements
            statements += 1

        async def call(read, **params):
            async with session() as db:
                return await read(username=owner.username, db=db, viewer=owner, **params)

        async def baseline():
            header = await call(person_header)
            profile, game_page = await asyncio.gather(call(person), call(game_favorites, offset=0, limit=50))
            return header, profile, game_page

        def fingerprint(header, screen_items, game_items, game_total):
            return header, [(item['id'], item['type'], item['title'], item['poster']) for item in screen_items], game_items, game_total

        async def samples(read, extract):
            timings, sizes, counts = [], [], []
            identity = None
            for _ in range(runs):
                start, sql_before = perf_counter(), statements
                response = jsonable_encoder(await read())
                sizes.append(len(json.dumps(response, separators=(',', ':'), ensure_ascii=False).encode()))
                timings.append((perf_counter() - start) * 1000)
                counts.append(statements - sql_before)
                observed = extract(response)
                assert identity is None or observed == identity
                identity = observed
            return {
                'first_ms': round(timings[0], 2), 'median_ms': round(median(timings[1:]), 2),
                'json_bytes': sizes[-1], 'sql_statements': counts[-1],
            }, identity

        old, expected = await samples(baseline, lambda payload: fingerprint(
            payload[0], payload[1]['favorites'],
            [(row['subject']['catalogue_id'], row['title'], row['image_url']) for row in payload[2]['results']],
            payload[2]['total'],
        ))
        report = {'tracked': tracked, 'screen_favorites': screen, 'game_favorites': games, 'baseline': old}
        if compare:
            from routers.tracking import people_favorites
            new, actual = await samples(
                lambda: call(people_favorites, response=Response(), show_screen=True, show_games=True, game_offset=0),
                lambda payload: fingerprint(
                    payload['profile'], payload['screen'],
                    [(item['id'], item['title'], item['poster']) for item in payload['games']['items']], payload['games']['total'],
                ),
            )
            assert actual == expected, 'Favorites membership, order, artwork or header changed'
            from dependencies import get_optional_user_or_api_key
            from db import get_db
            from fastapi import FastAPI
            import httpx
            from routers.tracking import router

            app = FastAPI()
            app.include_router(router, prefix='/tracking')

            async def api_session():
                async with session() as db:
                    yield db

            viewer = owner
            app.dependency_overrides[get_db] = api_session
            app.dependency_overrides[get_optional_user_or_api_key] = lambda: viewer
            path = f'/tracking/people/{owner.username}/favorites'
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture') as client:
                from routers.tracking import FavoriteOrderBody

                assert FavoriteOrderBody(media_ids=list(range(screen))).media_ids == list(range(screen))
                response = await client.get(path)
                assert response.status_code == 200 and response.headers['cache-control'] == 'private, no-store'
                assert (await client.get(path, params={'show_screen': False, 'show_games': False})).json() == {
                    'profile': expected[0], 'screen': [], 'games': None,
                }
                page = (await client.get(path, params={'game_offset': 50})).json()['games']
                assert page['total'] == games
                assert [item['id'] for item in page['items']] == list(range(51, min(games, 100) + 1))
                viewer = User(id=2, username='visitor', games_access=False)
                public = await client.get(path, params={'show_screen': False})
                assert public.status_code == 200 and len(public.json()['screen']) == screen
                assert public.json()['games'] is None
                async with session.begin() as db:
                    from core.media_images import IMAGE_POLICY_VERSION

                    hidden = await db.get(Media, 2)
                    hidden.tmdb_data = {'genres': [{'name': 'Animation'}], 'original_language': 'ja'}
                    independent = await db.get(Media, 3)
                    independent.tmdb_data = {'_anylist_images': {'version': IMAGE_POLICY_VERSION, 'primary_poster': '/selected.jpg'}}
                    await db.execute(delete(TrackedEntry).where(TrackedEntry.user_id == owner.id, TrackedEntry.media_id == 3))
                visible = {item['id']: item for item in (await client.get(path)).json()['screen']}
                assert 1 in visible and 2 not in visible and visible[3]['poster'] == '/selected.jpg'
                async with session.begin() as db:
                    profile = await db.scalar(select(UserProfileData).where(UserProfileData.user_id == owner.id))
                    profile.privacy_level = PrivacyLevel.private
                assert (await client.get(path)).status_code == 403
                viewer = owner
                assert (await client.get(path)).status_code == 200
            report.update(focused=new, matching=True)
        print(json.dumps(report), flush=True)
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-only', action='store_true')
    parser.add_argument('--runs', type=int, default=11)
    options = parser.parse_args()
    url = os.environ.get('FAVORITES_TEST_DATABASE_URL')
    if not url or options.runs < 2:
        parser.error('Set FAVORITES_TEST_DATABASE_URL and use at least two runs.')
    target = make_url(url)
    if target.host not in {'localhost', '127.0.0.1', '::1'} or target.database != 'anylist_favorites_test':
        parser.error('Use a local disposable anylist_favorites_test database.')
    os.environ['DATABASE_URL'] = url
    os.environ.setdefault('SECRET_KEY', 'favorites-benchmark-only')
    for fixture in [(500, 10, 10), (10000, 600, 300)]:
        await measure(url, *fixture, options.runs, not options.baseline_only)


if __name__ == '__main__':
    asyncio.run(main())
