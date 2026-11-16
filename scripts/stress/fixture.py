"""Disposable synthetic catalogue, accounts and connection state for mixed load."""

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

TITLE_BASE = 8_000_000
GAME_BASE = 7_000_000
TITLE_COUNT = 6000
GAME_COUNT = 2000
HOT = 12
LIBRARY_SIZE = int(os.environ.get('STRESS_LIBRARY_SIZE', '80'))
if not 1 <= LIBRARY_SIZE <= 500:
    raise ValueError('STRESS_LIBRARY_SIZE must be between 1 and 500')
NOW = datetime.now(timezone.utc).replace(tzinfo=None)


def screen_title(native, series=False):
    return {
        'id': native, 'name' if series else 'title': f'Stress Atlas {native}',
        'first_air_date' if series else 'release_date': '2024-06-15',
        'overview': 'An expedition follows an unfamiliar route through a mountain range. ' * 6,
        'status': 'Ended' if series else 'Released', 'runtime': 105,
        'vote_count': 650, 'vote_average': 7.8, 'popularity': 65,
        'genre_ids': [18], 'genres': [{'id': 18, 'name': 'Drama'}],
        'original_language': 'en', 'origin_country': ['US'],
        'poster_path': '/atlas.jpg', 'backdrop_path': '/atlas-wide.jpg',
        'number_of_seasons': 1, 'number_of_episodes': 8,
        'seasons': [{'id': native, 'name': 'Season 1', 'season_number': 1, 'episode_count': 8,
                     'air_date': '2024-06-15'}],
        'credits': {'cast': [], 'crew': []}, 'images': {'posters': [], 'backdrops': []},
        'external_ids': {}, 'videos': {'results': []}, 'watch/providers': {'results': {}},
        'release_dates': {'results': []}, 'content_ratings': {'results': []},
        'alternative_titles': {'titles': [], 'results': []},
    }


def game(native):
    return {
        'id': native, 'name': f'Stress Expedition {native}', 'game_type': 0,
        'slug': f'stress-expedition-{native}', 'first_release_date': 1718409600,
        'summary': 'An exploration game with a branching story and several playable characters. ' * 4,
        'platforms': [{'id': 6, 'name': 'PC (Microsoft Windows)'}],
        'genres': [{'id': 31, 'name': 'Adventure'}], 'rating_count': 42,
        'rating': 78, 'total_rating_count': 55, 'total_rating': 79,
        'cover': {'id': native, 'image_id': 'atlas-cover'},
    }


def check_environment():
    from urllib.parse import urlsplit

    url = urlsplit(os.environ.get('DATABASE_URL', ''))
    if (url.hostname, url.port, url.path, url.username) != (
        '127.0.0.1', 57639, '/anylist_test_stress', 'anylist_stress'
    ) or os.environ.get('ANYLIST_STRESS_NAMESPACE') != 'private':
        raise SystemExit('Use the isolated runner with its dedicated PostgreSQL instance.')


async def seed(destination, user_count):
    check_environment()
    from core.security import create_session_token
    from db import AsyncSessionLocal, engine
    from models import Media, User, UserProfileData
    from models.base import MediaType
    from models.catalogue import CatalogueCredit, CatalogueEntity, CatalogueIdentity
    from models.connections import MediaServerConnection
    from models.game_runner import GameMapperItem
    from models.games import GameEntry
    from models.tracking import TrackedEntry
    from models.users import UserSettings
    from sqlalchemy import insert, text

    async with AsyncSessionLocal() as db:
        if await db.scalar(text('SELECT count(*) FROM users')):
            raise RuntimeError('The fixture database already has users.')
        titles = []
        for i in range(TITLE_COUNT):
            series = i % 5 == 4
            native = TITLE_BASE + i
            payload = screen_title(native, series)
            titles.append(dict(title=f'Stress Atlas {native}',
                media_type=MediaType.series if series else MediaType.movie, tmdb_id=native,
                runtime=105, release_date='2024-06-15', poster_path='/atlas.jpg',
                tmdb_data=payload if i < HOT * 2 or i >= 1000 else None))
        media = list(await db.scalars(insert(Media).returning(Media.id, sort_by_parameter_order=True), titles))

        games = list(await db.scalars(insert(CatalogueEntity).returning(CatalogueEntity.id, sort_by_parameter_order=True), [
            dict(kind='game', name=f'Stress Expedition {GAME_BASE+i}',
                 attributes={'game_class': 'game', 'release_date': '2024-06-15', 'metadata_fetched_at': NOW.isoformat()} if i < HOT else {'game_class': 'game'})
            for i in range(GAME_COUNT)
        ]))
        await db.execute(insert(CatalogueIdentity), [dict(entity_id=identifier, namespace='igdb.game',
            external_id=str(GAME_BASE+i), source='igdb') for i, identifier in enumerate(games)])
        await db.execute(insert(CatalogueIdentity), [dict(entity_id=games[i], namespace='imdb.title',
            external_id=f'tt{GAME_BASE+i}', source='reviewed', evidence={'igdb_id': str(GAME_BASE+i),
                'title_type': 'videoGame'}) for i in range(0, len(games), 5)])
        await db.execute(insert(GameMapperItem), [dict(key=f'game:{GAME_BASE+i}', kind='game',
            native=str(GAME_BASE+i), entity_id=games[i], category=2, weight=2, next_at=NOW)
            for i in range(GAME_COUNT-260, GAME_COUNT)])
        people = list(await db.scalars(insert(CatalogueEntity).returning(CatalogueEntity.id, sort_by_parameter_order=True), [
            {'name': f'Atlas Performer {j:02}', 'kind': 'person'} for j in range(64)
        ]))
        works = list(await db.scalars(insert(CatalogueEntity).returning(CatalogueEntity.id, sort_by_parameter_order=True), [
            {'name': titles[j]['title'], 'kind': titles[j]['media_type'].value} for j in range(HOT*2)
        ]))
        await db.execute(insert(CatalogueIdentity), [dict(entity_id=work,
            namespace=f'tmdb.{titles[j]["media_type"].value}', external_id=str(TITLE_BASE+j), source='tmdb')
            for j, work in enumerate(works)])
        await db.execute(insert(CatalogueCredit), [dict(work_id=works[(j+k) % len(works)], contributor_id=person,
            role='actor', role_label='Actor', character_label=f'Explorer {j:02}', provider='tmdb',
            source_key=f'synthetic-{j}-{k}', position=j % 8) for j, person in enumerate(people) for k in range(3)])

        users = [User(username=f'stress-{i:04}', email=f'stress-{i:04}@example.test',
                     api_key=os.urandom(20).hex(), games_access=True) for i in range(user_count)]
        db.add_all(users)
        await db.flush()
        await db.execute(insert(UserSettings), [dict(user_id=u.id, preferences={},
            pull_sync_interval=.25 if i % 52 == 0 else None) for i, u in enumerate(users)])
        await db.execute(insert(UserProfileData), [dict(user_id=u.id) for u in users])
        configured = [u for i, u in enumerate(users) if i % 4 == 0]
        connection_ids = await db.scalars(insert(MediaServerConnection).returning(MediaServerConnection.id, sort_by_parameter_order=True), [
            dict(user_id=u.id, type='jellyfin', name='Synthetic Jellyfin', url='http://127.0.0.1:57642',
                 token='synthetic-token', server_user_id=u.username, push_watched=True, push_ratings=True)
            for u in configured
        ])
        connected = dict(zip((u.id for u in configured), connection_ids, strict=True))
        for offset in range(0, len(users), 50):
            batch = users[offset:offset+50]
            await db.execute(insert(TrackedEntry), [dict(user_id=u.id,
                media_id=media[1000+(u.id*13+j) % (TITLE_COUNT-1000)],
                status=('planning', 'completed', 'paused', 'dropped')[j % 4], manual_score=7.5)
                for u in batch for j in range(LIBRARY_SIZE)])
            await db.execute(insert(GameEntry), [dict(user_id=u.id, work_id=games[(u.id*7+j) % GAME_COUNT],
                status=('planning', 'completed', 'playing')[j % 3], score=7.5, total_seconds=3600*j)
                for u in batch for j in range(24)])
        await db.commit()
        async with engine.begin() as connection:
            await connection.execute(text('ANALYZE'))
        payload = {'users': [dict(username=u.username, token=create_session_token(u),
                                  connection=connected.get(u.id)) for u in users],
                   'media': media, 'games': games}
        with open(destination, 'x', encoding='utf-8', opener=private_file) as output:
            json.dump(payload, output)
    await engine.dispose()


async def refresh_tokens(destination):
    check_environment()
    from core.security import create_session_token
    from db import AsyncSessionLocal, engine
    from models import User
    from sqlalchemy import select

    payload = json.loads(destination.read_text(encoding='utf-8'))
    async with AsyncSessionLocal() as db:
        tokens = {u.username: create_session_token(u) for u in await db.scalars(select(User))}
    for user in payload['users']:
        user['token'] = tokens[user['username']]
    destination.write_text(json.dumps(payload), encoding='utf-8')
    await engine.dispose()


def private_file(path, flags):
    return os.open(path, flags, 0o600)


if __name__ == '__main__':
    path = Path(sys.argv[1])
    asyncio.run(refresh_tokens(path) if sys.argv[2] == '--refresh-tokens' else seed(path, int(sys.argv[2])))
