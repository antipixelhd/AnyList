"""Primary credit delivery must not wait for optional character enrichment."""
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from models.base import MediaType
from routers.tracking import title_people


class DetailPeopleLoadingTests(unittest.IsolatedAsyncioTestCase):
    async def test_actor_response_returns_credits_without_character_provider_work(self):
        media = SimpleNamespace(id=7, media_type=MediaType.movie, tmdb_data={})
        db = SimpleNamespace(get=AsyncMock(return_value=media))
        people = [{'id':1, 'kind':'actor'}]
        with (patch('core.tracking_projection.is_anime', return_value=False),
              patch('core.media_credits.fill_screen_credits', AsyncMock(return_value='updated')) as credits,
              patch('core.screen_character_art.fill_character_art', AsyncMock(side_effect=AssertionError('optional provider called'))) as characters,
              patch('core.screen_character_art.cached_character_status', AsyncMock(return_value={'state':'pending','pending':True})),
              patch('core.media_details.screen_work', AsyncMock(return_value=object())),
              patch('core.media_details.work_details', AsyncMock(return_value={'people':people,'organizations':[]}))):
            data = await title_people(7, db, SimpleNamespace(id=3))
            self.assertEqual(data['people'],people)
            self.assertEqual(data['credits_status'],'updated')
            credits.assert_awaited_once_with(db,media,3)
            characters.assert_not_awaited()

    async def test_character_demand_keeps_existing_enrichment_and_status(self):
        media = SimpleNamespace(id=7, media_type=MediaType.movie, tmdb_data={})
        db = SimpleNamespace(get=AsyncMock(return_value=media))
        with (patch('core.tracking_projection.is_anime', return_value=False),
              patch('core.media_credits.fill_screen_credits', AsyncMock(return_value='cached')),
              patch('core.screen_character_art.fill_character_art', AsyncMock(return_value={'state':'partial','pending':True,'retry_after':60})) as characters,
              patch('core.screen_character_art.cached_character_status', AsyncMock()) as cached,
              patch('core.media_details.screen_work', AsyncMock(return_value=object())),
              patch('core.media_details.work_details', AsyncMock(return_value={'people':[],'organizations':[]}))):
            data = await title_people(7, db, SimpleNamespace(id=3), characters=True)
            self.assertEqual(data['character_status']['retry_after'],60)
            characters.assert_awaited_once_with(db,media,3)
            cached.assert_not_awaited()
