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
        media = SimpleNamespace(id=10,media_type=MediaType.series)
        db = MagicMock()
        db.delete = AsyncMock()
        with (patch.object(activity,'_prepare_rating_activity',AsyncMock(return_value=(False,None))),
              patch.object(activity,'_active_activity_card',AsyncMock(return_value=None)) as active,
              patch.object(activity,'_series_activity_catalog',AsyncMock(return_value=(episodes,{1:1,2:1})))):
            async def save(before, after):
                return await activity.record_progress_activity(db,user_id=1,media=media,
                    previous_progress=len(before),progress=len(after),status='watching',score=None,
                    previous_watched_ids=before,watched_ids=after)
            card = await save(set(),{2})
            self.assertEqual(card.payload['episodes_watched'],1)
            self.assertEqual(card.payload['position_start'],'S2E1')
            self.assertEqual(card.payload['position'],'S2E1')
            self.assertEqual(card.payload['finished_seasons'],[2])
            active.return_value = card
            await save({2},{1,2})
            self.assertEqual(card.payload['episodes_watched'],2)
            self.assertEqual(card.payload['position_start'],'S1E1')
            self.assertEqual(card.payload['finished_seasons'],[1,2])
            await save({1,2},{2})
            self.assertEqual(card.payload['episodes_watched'],1)
            self.assertEqual(card.payload['position_start'],'S2E1')
            self.assertEqual(card.payload['finished_seasons'],[2])
            # A later generic import also uses canonical IDs once this window
            # has exact evidence, rather than reverting to a count-only prefix.
            db.scalars = AsyncMock(return_value=[2])
            await activity.record_progress_activity(db,user_id=1,media=media,
                previous_progress=1,progress=1,status='watching',score=None)
            self.assertEqual(card.payload['position'],'S2E1')
            self.assertEqual(card.payload['finished_seasons'],[2])
            self.assertIsNone(await save({2},set()))
            db.delete.assert_called_once_with(card)
