"""Authenticated compatibility checks for inherited history writes.

Set TRACKING_TEST_DATABASE_URL to a disposable local PostgreSQL database.
"""
import os
import unittest
from unittest.mock import AsyncMock, patch

os.environ.setdefault("SECRET_KEY", "local-tests-only")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

import httpx
from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from core.security import create_access_token
from db import get_db
from models import GlobalSettings, Media, Show, User, UserProfileData
from models.base import MediaType, PrivacyLevel
from models.tracking import TrackedEntry, TrackingActivity
from routers.history import router as history_router
from routers.ratings import router as ratings_router
from routers.tracking import router as tracking_router


@unittest.skipUnless(os.getenv("TRACKING_TEST_DATABASE_URL"), "Requires disposable PostgreSQL database")
class LegacyHistoryTrackingCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(os.environ["TRACKING_TEST_DATABASE_URL"])
        self.connection = await self.engine.connect()
        self.transaction = await self.connection.begin()
        self.db = AsyncSession(bind=self.connection, expire_on_commit=False, join_transaction_mode="create_savepoint")
        self.user = User(username="history-compat-owner", email="history-compat@example.test", api_key="history-compat-api-key")
        self.movie = Media(title="Compatibility Fixture", media_type=MediaType.movie, tmdb_id=987654320)
        self.db.add_all([self.user, self.movie])
        await self.db.flush()
        self.db.add(UserProfileData(user_id=self.user.id, privacy_level=PrivacyLevel.public))
        settings = await self.db.get(GlobalSettings, 1)
        if settings is None:
            self.db.add(GlobalSettings(id=1, enable_logged_out_navigation=False))
        else:
            settings.enable_logged_out_navigation = False
        await self.db.commit()

        app = FastAPI()
        app.include_router(history_router, prefix="/history")
        app.include_router(ratings_router, prefix="/ratings")
        app.include_router(tracking_router, prefix="/tracking")

        async def session():
            yield self.db

        app.dependency_overrides[get_db] = session
        self.push_patch = patch("routers.history._push_watch_state", new=AsyncMock())
        self.push_patch.start()
        self.outbound_patch = patch("core.local_outbound.dispatch_local_tracking_delta", new=AsyncMock())
        self.outbound_patch.start()
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
        self.jwt = create_access_token(subject=self.user.id)

    async def asyncTearDown(self):
        self.push_patch.stop()
        self.outbound_patch.stop()
        await self.client.aclose()
        await self.db.close()
        await self.transaction.rollback()
        await self.connection.close()
        await self.engine.dispose()

    async def test_api_key_history_write_appears_in_tracking_title_and_profile_activity(self):
        written = await self.client.post(
            "/history",
            headers={"X-Api-Key": self.user.api_key},
            json={"media_type": "movie", "tmdb_id": self.movie.tmdb_id, "completed": True},
        )
        self.assertEqual(written.status_code, 200, written.text)

        # The history route authenticates through the actual X-Api-Key lookup;
        # UI-facing read routes use a real user JWT because they do not accept
        # the legacy API key scheme.
        headers = {"Authorization": f"Bearer {self.jwt}"}
        profile = await self.client.get(f"/tracking/profile/{self.user.username}/movie", headers=headers)
        self.assertEqual(profile.status_code, 200, profile.text)
        self.assertEqual(profile.json()["entries"][0]["status"], "completed")
        self.assertEqual(profile.json()["entries"][0]["progress"], 1)

        activity_profile = await self.client.get(f"/tracking/people/{self.user.username}", headers=headers)
        self.assertEqual(activity_profile.status_code, 200, activity_profile.text)
        self.assertEqual(activity_profile.json()["recent_activity"][0]["status"], "completed")

        entry = (await self.db.execute(select(TrackedEntry).where(
            TrackedEntry.user_id == self.user.id, TrackedEntry.media_id == self.movie.id,
        ))).scalar_one()
        activity = (await self.db.execute(select(TrackingActivity).where(
            TrackingActivity.user_id == self.user.id, TrackingActivity.media_id == self.movie.id,
        ))).scalars().all()
        self.assertEqual(entry.status, "completed")
        self.assertEqual(entry.progress, 1)
        self.assertEqual(len(activity), 1)

    async def test_api_key_episode_history_updates_series_progress_and_activity(self):
        show = Show(title="Compatibility Series", tmdb_id=987654321)
        series = Media(title="Compatibility Series", media_type=MediaType.series, tmdb_id=show.tmdb_id)
        self.db.add_all([show, series])
        await self.db.flush()
        episodes = [
            Media(title=f"Episode {number}", media_type=MediaType.episode,
                  show_id=show.id, season_number=1, episode_number=number,
                  release_date=f"2020-01-0{number}")
            for number in range(1, 4)
        ]
        self.db.add_all(episodes)
        await self.db.commit()

        written = await self.client.post(
            "/history",
            headers={"X-Api-Key": self.user.api_key},
            json={"media_type": "episode", "media_id": episodes[-1].id, "completed": True},
        )
        self.assertEqual(written.status_code, 200, written.text)

        headers = {"Authorization": f"Bearer {self.jwt}"}
        profile = await self.client.get(f"/tracking/profile/{self.user.username}/series", headers=headers)
        self.assertEqual(profile.status_code, 200, profile.text)
        entry = next(row for row in profile.json()["entries"] if row["id"] == series.id)
        self.assertEqual(entry["status"], "watching")
        self.assertEqual(entry["progress"], 3)

        activity_profile = await self.client.get(f"/tracking/people/{self.user.username}", headers=headers)
        self.assertEqual(activity_profile.status_code, 200, activity_profile.text)
        activity = activity_profile.json()["recent_activity"][0]
        self.assertEqual(activity["status"], "watching")
        self.assertEqual(activity["payload"]["episodes_watched"], 1)
        self.assertEqual(activity["payload"]["progress"], 3)

    async def test_api_key_rating_write_appears_in_tracking_entry_and_activity(self):
        written = await self.client.post(
            "/ratings",
            headers={"X-Api-Key": self.user.api_key},
            json={"media_type": "movie", "media_id": self.movie.id, "rating": 8.5},
        )
        self.assertEqual(written.status_code, 200, written.text)

        headers = {"Authorization": f"Bearer {self.jwt}"}
        profile = await self.client.get(f"/tracking/profile/{self.user.username}/movie", headers=headers)
        self.assertEqual(profile.status_code, 200, profile.text)
        entry = profile.json()["entries"][0]
        self.assertEqual(entry["status"], "planning")
        self.assertEqual(entry["score"], 8.5)

        activity_profile = await self.client.get(f"/tracking/people/{self.user.username}", headers=headers)
        self.assertEqual(activity_profile.status_code, 200, activity_profile.text)
        activity = activity_profile.json()["recent_activity"][0]
        self.assertEqual(activity["score"], 8.5)
        self.assertTrue(activity["payload"]["rating_changed"])


if __name__ == "__main__":
    unittest.main()
