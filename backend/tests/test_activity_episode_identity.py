"""Activity windows retain exact episode evidence through gaps and corrections."""
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault('SECRET_KEY', 'test-only-secret')
os.environ.setdefault('DATABASE_URL', 'postgresql+asyncpg://test:test@localhost/test')

from core import activity
from models.base import MediaType


class EpisodeIdentityActivityTests(unittest.IsolatedAsyncioTestCase):
    async def test_gaps_and_rollbacks_update_positions_and_finished_seasons(self):
        episodes = [SimpleNamespace(id=1,season_number=1,episode_number=1),
                    SimpleNamespace(id=2,season_number=2,episode_number=1)]
        for ep in episodes:
            ep.release_date, ep.tmdb_data = '2020-01-01', {}
        async def groups(*args, display_positions, **kwargs):
            display_positions.update({ep.id: (ep.season_number, ep.episode_number) for ep in episodes})
            return 'tmdb:aired', [(ep.season_number, [ep], 1) for ep in episodes], {}
        media = SimpleNamespace(id=10,media_type=MediaType.series,tvdb_air_schedule=None)
        db = MagicMock()
        db.delete = AsyncMock()
        windows = {}
        async def active_window(db, *, progress_scope, **kwargs):
            return windows.get(progress_scope)
        with (patch.object(activity,'_prepare_rating_activity',AsyncMock(return_value=(False,None))),
              patch.object(activity,'_active_activity_card',side_effect=active_window),
              patch('core.season_completion.season_groups',side_effect=groups)):
            async def save(before, after):
                card = await activity.record_progress_activity(db,user_id=1,media=media,
                    previous_progress=len(before),progress=len(after),status='watching',score=None,
                    previous_watched_ids=before,watched_ids=after)
                if card:
                    windows[card.payload['progress_scope']] = card
                return card
            card = await save(set(),{2})
            self.assertEqual(card.payload['episodes_watched'],1)
            self.assertEqual(card.payload['position_start'],'S2E1')
            self.assertEqual(card.payload['position'],'S2E1')
            self.assertEqual(card.payload['finished_seasons'],[2])
            earlier = await save({2},{1,2})
            self.assertEqual(earlier.payload['episodes_watched'],1)
            self.assertEqual(earlier.payload['position_start'],'S1E1')
            self.assertEqual(earlier.payload['finished_seasons'],[1])
            await save({1,2},{2})
            self.assertEqual(card.payload['episodes_watched'],1)
            self.assertEqual(card.payload['position_start'],'S2E1')
            self.assertEqual(card.payload['finished_seasons'],[2])
            # A provider update with no new watches leaves both windows alone.
            db.scalars = AsyncMock(return_value=[2])
            await activity.record_progress_activity(db,user_id=1,media=media,
                previous_progress=1,progress=1,status='watching',score=None,added_episode_ids=set())
            self.assertEqual(card.payload['position'],'S2E1')
            self.assertEqual(card.payload['finished_seasons'],[2])
            self.assertIsNone(await save({2},set()))
            self.assertEqual([call.args[0] for call in db.delete.await_args_list], [earlier, card])
