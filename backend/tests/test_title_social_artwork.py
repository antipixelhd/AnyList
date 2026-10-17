"""Optional detail reads keep provider requests batched and social data private."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

os.environ.setdefault('SECRET_KEY', 'local-tests-only')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

from models.base import MediaType
from routers.tracking import title_episode_images, title_social


class Result:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class DetailOptionalReadsTests(unittest.IsolatedAsyncioTestCase):
    async def test_episode_artwork_uses_one_tvdb_batch_and_persists_direct_urls(self):
        show = SimpleNamespace(media_type=MediaType.series, tmdb_data={'tracking_catalogue_provider': 'tvdb'},
            tvdb_id=42, tmdb_id=None)
        episodes = [SimpleNamespace(id=number, tvdb_id=100 + number, poster_path=None,
            tmdb_data={}, season_number=1, episode_number=number) for number in (1, 2)]
        db = SimpleNamespace(get=AsyncMock(return_value=show), commit=AsyncMock())
        with patch('routers.tracking.catalog_access', new_callable=AsyncMock), \
             patch('core.tracking_metadata.released_episodes', new_callable=AsyncMock, return_value=episodes), \
             patch('routers.tracking.settings_store.get_user_tvdb_key', new_callable=AsyncMock, return_value='test-key'), \
             patch('core.tvdb.get_series_episodes', new_callable=AsyncMock, return_value=[
                 {'id': 101, 'seasonNumber': 1, 'number': 1, 'image': 'https://artworks.thetvdb.com/a.jpg'},
                 {'id': 102, 'seasonNumber': 1, 'number': 2, 'image': 'https://artworks.thetvdb.com/b.jpg'},
             ]) as fetch:
            result = await title_episode_images(1, db, None)
        self.assertEqual(set(result['images']), {'1', '2'})
        self.assertEqual(fetch.await_count, 1)
        db.commit.assert_awaited_once()

    async def test_social_people_include_self_and_unrated_following_entries(self):
        movie = SimpleNamespace(media_type=MediaType.movie, tmdb_data={})
        me = SimpleNamespace(id=1, username='me')
        friend = SimpleNamespace(id=2, username='friend')
        own_entry = SimpleNamespace(rating_mode='manual', manual_score=7, season_scores={}, status='completed')
        friend_entry = SimpleNamespace(rating_mode='manual', manual_score=None, season_scores={}, status='planning')
        rows = [(own_entry, me, SimpleNamespace(display_name='My name', avatar_path=None)),
                (friend_entry, friend, SimpleNamespace(display_name='Friend', avatar_path=None))]
        db = SimpleNamespace(get=AsyncMock(return_value=movie),
            execute=AsyncMock(side_effect=[Result(rows), Result([])]))
        result = await title_social(1, db, me)
        self.assertEqual([person['username'] for person in result['people']], ['me', 'friend'])
        self.assertTrue(result['people'][0]['self'])
        self.assertEqual(result['people'][0]['score'], 7)
        self.assertIsNone(result['people'][1]['score'])
        self.assertEqual(result['activities'], [])
