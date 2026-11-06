import unittest
from sqlalchemy import select
import test_games_pg as games_fixture
import test_steam_pg as fixture
from models import SteamConnection, UserSettings


@unittest.skipUnless(games_fixture.URL, 'Requires disposable Games PostgreSQL')
class SteamProgressEditorTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.SteamDatabaseTests.asyncSetUp
    cleanup = games_fixture.GamesDatabaseTests.cleanup
    connect = fixture.SteamDatabaseTests.connect
    progress = fixture.SteamDatabaseTests.progress
    imported = fixture.SteamDatabaseTests.imported

    async def test_context_is_private_respects_flags_and_acknowledges_per_account(self):
        await self.imported()
        path=f'/tracking/games/works/{self.base}/steam-progress'
        response=await self.client.get(path)
        self.assertEqual(response.status_code,200,response.text)
        self.assertIn('no-store',response.headers['cache-control'])
        data=response.json()
        self.assertEqual(data['playtime_seconds'],3600)
        self.assertEqual(data['achievement_count'],0)
        self.assertEqual(data['acknowledged'],[])
        for field in ('playtime','achievements','playtime'):
            result=await self.client.post(f'/tracking/games/steam-progress/warnings/{field}')
            self.assertEqual(result.status_code,200,result.text)
        self.assertEqual((await self.client.get(path)).json()['acknowledged'],['achievements','playtime'])
        async with self.Session() as db:
            settings=await db.scalar(select(UserSettings).where(UserSettings.user_id==self.user.id))
            settings.preferences={**settings.preferences,'unrelated':'keep'}
            state=await db.get(SteamConnection,self.id)
            state.sync_playtime=False;state.sync_achievements=False
            await db.commit()
        data=(await self.client.get(path)).json()
        self.assertFalse(data['playtime_enabled']);self.assertFalse(data['achievements_enabled'])
        self.assertIsNone(data['playtime_seconds']);self.assertIsNone(data['achievement_count'])
        await self.client.post('/tracking/games/steam-progress/warnings/playtime')
        async with self.Session() as db:
            settings=await db.scalar(select(UserSettings).where(UserSettings.user_id==self.user.id))
            self.assertEqual(settings.preferences['unrelated'],'keep')
        self.viewer=self.other
        data=(await self.client.get(path)).json()
        self.assertFalse(data['playtime_enabled']);self.assertFalse(data['achievements_enabled'])
        self.assertEqual(data['acknowledged'],[])
