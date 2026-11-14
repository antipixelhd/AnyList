"""Manual date/progress intent is independent from status and prior-season dates."""

import unittest
from datetime import date
from sqlalchemy import delete, select
import test_season_completion as seasons
from models import Media, TrackedEntry, TrackingActivity, WatchEvent


@unittest.skipUnless(seasons.fixture.URL, "Requires explicit disposable PostgreSQL")
class EditorDateIntentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await seasons.SeasonDatabaseTests.asyncSetUp(self)
        self.root = f"/tracking/entry/{self.series_id}"
        async with self.Session() as db:
            await db.execute(
                delete(WatchEvent).where(WatchEvent.media_id.in_(self.episode_ids))
            )
            entry = await db.scalar(
                select(TrackedEntry).where(TrackedEntry.media_id == self.series_id)
            )
            entry.status, entry.progress = "planning", 0
            await db.commit()

    async def asyncTearDown(self):
        await seasons.SeasonDatabaseTests.asyncTearDown(self)

    async def summary(self):
        response = await self.client.get(self.root + "/series-editor")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def save(self, *, status=None, season=None, count=None, dates=None, **body):
        summary = await self.summary()
        patch = {"order": summary["order"], "revision": summary["revision"]}
        if count is not None:
            members = [
                ep
                for s in summary["seasons"]
                if s["season_number"] == season
                for ep in s["episodes"]
            ]
            previous = [
                ep
                for s in summary["seasons"]
                if s["season_number"] < season
                for ep in s["episodes"]
            ]
            endpoint = (
                members[count - 1]["id"]
                if count
                else previous[-1]["id"]
                if previous
                else None
            )
            patch.update(
                action="position",
                season_number=season,
                episode_count=count,
                episode_id=endpoint,
            )
        if dates is not None:
            item = next(s for s in summary["seasons"] if s["season_number"] == season)
            patch["seasons"] = [
                {"season_number": season, "membership": item["membership"], **dates}
            ]
        if status is not None:
            body["status"] = status
        response = await self.client.patch(
            self.root, json={**body, "series_editor": patch}
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def test_season_finish_advances_progress_and_keeps_paused_status(self):
        saved = await self.save(
            status="paused", season=1, dates={"completion_date": "2024-02-05"}
        )
        self.assertEqual((saved["status"], saved["progress"]), ("paused", 2))
        summary = await self.summary()
        self.assertEqual(summary["seasons"][0]["completion_date"], "2024-02-05")
        self.assertIsNone(summary["seasons"][1]["start_date"])
        self.assertIsNone(summary["seasons"][1]["completion_date"])

    async def test_overall_finish_advances_progress_without_claiming_completed_series_or_prior_dates(
        self,
    ):
        saved = await self.save(status="dropped", finish_date="2024-02-05")
        self.assertEqual(
            (saved["status"], saved["progress"], saved["finish_date"]),
            ("dropped", 4, "2024-02-05"),
        )
        summary = await self.summary()
        self.assertIsNone(summary["seasons"][0]["start_date"])
        self.assertIsNone(summary["seasons"][0]["completion_date"])
        self.assertEqual(summary["seasons"][1]["completion_date"], "2024-02-05")
        async with self.Session() as db:
            cards = list(
                await db.scalars(
                    select(TrackingActivity).where(
                        TrackingActivity.media_id == self.series_id
                    )
                )
            )
            self.assertFalse(
                any(card.payload.get("series_completed") for card in cards)
            )

    async def test_starting_later_season_zero_and_advancing_sets_only_that_seasons_dates(
        self,
    ):
        saved = await self.save(status="watching", season=2, count=0)
        self.assertEqual(saved["progress"], 2)
        self.assertIsNone(saved["start_date"])
        for count in (0, 1, 2):
            if count:
                saved = await self.save(season=2, count=count)
            summary = await self.summary()
            self.assertIsNone(summary["seasons"][0]["start_date"])
            self.assertIsNone(summary["seasons"][0]["completion_date"])
            self.assertEqual(
                summary["seasons"][1]["start_date"], date.today().isoformat()
            )
            self.assertEqual(
                summary["seasons"][1]["completion_date"],
                date.today().isoformat() if count == 2 else None,
            )
        self.assertEqual(saved["status"], "watching")

    async def test_progress_and_completed_status_preserve_existing_dates(self):
        await self.save(
            status="paused",
            season=2,
            dates={"start_date": "2020-01-01", "completion_date": "2020-02-01"},
        )
        await self.save(status="paused", season=2, count=1, confirm_rollback=True)
        summary = await self.summary()
        self.assertFalse(summary["seasons"][1]["completed"])
        self.assertEqual(summary["seasons"][1]["completion_date"], "2020-02-01")
        await self.save(status="completed")
        summary = await self.summary()
        self.assertEqual(summary["seasons"][1]["start_date"], "2020-01-01")
        self.assertEqual(summary["seasons"][1]["completion_date"], "2020-02-01")
        await self.save(season=2, dates={"start_date": None, "completion_date": None})
        await self.save(
            season=2,
            count=2,
            dates={
                "start_date": date.today().isoformat(),
                "completion_date": date.today().isoformat(),
                "start_preset": True,
                "completion_preset": True,
            },
        )
        summary = await self.summary()
        self.assertIsNone(summary["seasons"][1]["start_date"])
        self.assertIsNone(summary["seasons"][1]["completion_date"])

    async def test_manual_finish_is_allowed_for_an_airing_incomplete_season(self):
        async with self.Session() as db:
            episode = await db.get(Media, self.episode_ids[-1])
            episode.release_date = "2099-01-01"
            await db.commit()
        saved = await self.save(
            status="dropped", season=2, dates={"completion_date": "2024-02-05"}
        )
        self.assertEqual((saved["status"], saved["progress"]), ("dropped", 3))
        summary = await self.summary()
        self.assertFalse(summary["seasons"][1]["completed"])
        self.assertEqual(summary["seasons"][1]["completion_date"], "2024-02-05")
        self.assertEqual(saved["finish_date"], "2024-02-05")

    async def test_first_episode_presets_start_and_movie_finish_keeps_status(self):
        saved = await self.save(status="watching", season=1, count=1)
        self.assertEqual(saved["start_date"], date.today().isoformat())
        summary = await self.summary()
        self.assertEqual(summary["seasons"][0]["start_date"], date.today().isoformat())
        self.assertIsNone(summary["seasons"][0]["completion_date"])
        from models.base import MediaType

        async with self.Session() as db:
            movie = Media(
                title="Paused movie",
                media_type=MediaType.movie,
                release_date="2020-01-01",
            )
            db.add(movie)
            await db.commit()
            movie_id = movie.id
        response = await self.client.patch(
            f"/tracking/entry/{movie_id}",
            json={"status": "paused", "finish_date": "2024-02-05"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            (response.json()["status"], response.json()["progress"]), ("paused", 1)
        )
        self.assertEqual(response.json()["finish_date"], "2024-02-05")
        response = await self.client.patch(
            f"/tracking/entry/{movie_id}",
            json={"status": "paused", "notes": "Keep this progress"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["progress"], 1)
        self.assertEqual(response.json()["finish_date"], "2024-02-05")
