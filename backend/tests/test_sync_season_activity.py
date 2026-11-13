"""Connection/editor activity regressions against disposable PostgreSQL schemas."""

import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch
from sqlalchemy import select, delete
import test_season_completion as fixture
from models import (
    Media,
    Show,
    Rating,
    WatchEvent,
    TrackedEntry,
    TrackingActivity,
    ShowEpisodePosition,
    UserShowEpisodeOrder,
    MediaServerConnection,
    UserSettings,
)
from models.tracking import CloudBaseline, StreamBaseline
from core.season_completion import run_batch
from core.tracking_projection import activity_data


def utc():
    return datetime.now(timezone.utc).replace(tzinfo=None)


@unittest.skipUnless(fixture.fixture.URL, "Requires explicit disposable PostgreSQL")
class SyncSeasonActivityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await fixture.SeasonDatabaseTests.asyncSetUp(self)
        self.root = f"/tracking/entry/{self.series_id}"

    async def asyncTearDown(self):
        await fixture.SeasonDatabaseTests.asyncTearDown(self)

    async def cards(self, db):
        return list(
            await db.scalars(
                select(TrackingActivity).where(
                    TrackingActivity.media_id == self.series_id
                )
            )
        )

    async def reset(self, status="watching", count=0):
        async with self.Session() as db:
            await db.execute(
                delete(WatchEvent).where(
                    WatchEvent.media_id.in_(self.episode_ids[count:])
                )
            )
            entry = await db.scalar(
                select(TrackedEntry).where(TrackedEntry.media_id == self.series_id)
            )
            entry.status, entry.progress, entry.start_date, entry.finish_date = (
                status,
                count,
                None,
                None,
            )
            entry.status_source, entry.status_changed_at = (
                "local",
                utc() - timedelta(hours=2),
            )
            await db.commit()

    async def setup_rating(self):
        async with self.Session() as db:
            entry = await db.scalar(
                select(TrackedEntry).where(TrackedEntry.media_id == self.series_id)
            )
            entry.season_scores = {"1": 7}
            db.add(
                Rating(
                    user_id=self.user_id,
                    media_id=self.series_id,
                    season_number=1,
                    rating=7,
                    rated_at=utc() - timedelta(days=5),
                )
            )
            db.add(
                CloudBaseline(
                    user_id=self.user_id,
                    provider="trakt",
                    approved=True,
                    snapshot={},
                    observed_at=utc() - timedelta(hours=1),
                )
            )
            await db.commit()
        await run_batch(self.Session)

    async def test_accepted_remote_season_rating_creates_activity(self):
        from core.cloud_rating_reconciliation import reconcile_cloud_rating

        await self.setup_rating()
        async with self.Session() as db:
            before = [(c.id, c.score, dict(c.payload)) for c in await self.cards(db)]
            media = await db.get(Media, self.series_id)
            ratings = {
                (r.media_id, r.season_number): r
                for r in await db.scalars(
                    select(Rating).where(Rating.user_id == self.user_id)
                )
            }
            result = await reconcile_cloud_rating(
                db,
                provider="trakt",
                user_id=self.user_id,
                media=media,
                season_number=1,
                remote_score=8,
                remote_rated_at=utc(),
                existing=ratings,
                changed={},
            )
            self.assertEqual(result, "applied")
            await db.commit()
            entry = await db.scalar(
                select(TrackedEntry).where(TrackedEntry.media_id == self.series_id)
            )
            self.assertEqual(entry.season_scores["1"], 8)
            after = await self.cards(db)
            self.assertEqual(
                [
                    (c.id, c.score, dict(c.payload))
                    for c in after
                    if c.id in {row[0] for row in before}
                ],
                before,
            )
            self.assertEqual(
                [
                    (c.score, c.payload.get("season_ratings"))
                    for c in after
                    if c.payload.get("rating_changed")
                ],
                [(8, {"1": 8})],
            )

    async def test_combined_remote_season_rating_creates_activity(self):
        from core.sync_reconciliation import Reconciliation, Observation, _ratings
        from core.pull_cycle import PullCycleState

        await self.setup_rating()
        async with self.Session() as db:
            before = [(c.id, c.score, dict(c.payload)) for c in await self.cards(db)]
            state = Reconciliation(self.user_id, started_at=utc())
            state.ratings = {
                (self.series_id, 1): [
                    Observation("trakt", 8, utc(), 7, utc() - timedelta(hours=1))
                ]
            }
            cycle = PullCycleState(self.user_id)
            await _ratings(db, state, cycle)
            self.assertEqual(cycle.new_ratings[(self.series_id, 1)], 8)
            entry = await db.scalar(
                select(TrackedEntry).where(TrackedEntry.media_id == self.series_id)
            )
            self.assertEqual(entry.season_scores["1"], 8)
            after = await self.cards(db)
            self.assertEqual(
                [
                    (c.id, c.score, dict(c.payload))
                    for c in after
                    if c.id in {row[0] for row in before}
                ],
                before,
            )
            self.assertEqual(
                [
                    (c.score, c.payload.get("season_ratings"))
                    for c in after
                    if c.payload.get("rating_changed")
                ],
                [(8, {"1": 8})],
            )

    async def test_first_rating_snapshot_is_quiet_and_unchanged_ongoing_rating_is_idempotent(
        self,
    ):
        from core.cloud_rating_reconciliation import reconcile_cloud_rating

        await self.setup_rating()
        async with self.Session() as db:
            baseline = await db.scalar(
                select(CloudBaseline).where(CloudBaseline.user_id == self.user_id)
            )
            baseline.approved = False
            media = await db.get(Media, self.series_id)
            rows = {
                (r.media_id, r.season_number): r
                for r in await db.scalars(
                    select(Rating).where(Rating.user_id == self.user_id)
                )
            }
            before = [(c.id, c.score, dict(c.payload)) for c in await self.cards(db)]
            for value, approved in ((8, False), (9, True), (9, True)):
                baseline.approved = approved
                await reconcile_cloud_rating(
                    db,
                    provider="trakt",
                    user_id=self.user_id,
                    media=media,
                    season_number=1,
                    remote_score=value,
                    remote_rated_at=utc(),
                    existing=rows,
                    changed={},
                )
                await db.commit()
                cards = await self.cards(db)
                if not approved:
                    self.assertEqual(
                        [(c.id, c.score, dict(c.payload)) for c in cards], before
                    )
                else:
                    self.assertEqual(
                        [c.score for c in cards if c.payload.get("rating_changed")], [9]
                    )

    async def test_combined_multi_season_ratings_share_one_average_card(self):
        from core.sync_reconciliation import Reconciliation, Observation, _ratings
        from core.pull_cycle import PullCycleState

        await self.setup_rating()
        async with self.Session() as db:
            entry = await db.scalar(
                select(TrackedEntry).where(TrackedEntry.media_id == self.series_id)
            )
            entry.season_scores = {"1": 7, "2": 6}
            db.add(
                Rating(
                    user_id=self.user_id,
                    media_id=self.series_id,
                    season_number=2,
                    rating=6,
                    rated_at=utc() - timedelta(days=5),
                )
            )
            await db.commit()
            state = Reconciliation(self.user_id, started_at=utc())
            state.ratings = {
                (self.series_id, sn): [
                    Observation(
                        "trakt", after, utc(), before, utc() - timedelta(hours=1)
                    )
                ]
                for sn, before, after in ((1, 7, 8), (2, 6, 9))
            }
            await _ratings(db, state, PullCycleState(self.user_id))
            cards = [c for c in await self.cards(db) if c.payload.get("rating_changed")]
            self.assertEqual(len(cards), 1)
            self.assertEqual(cards[0].payload["season_ratings"], {"1": 8, "2": 9})
            self.assertEqual(cards[0].score, 8.5)

    async def test_synced_rating_and_completion_merge_regardless_of_writer_order(self):
        from core.cloud_rating_reconciliation import reconcile_cloud_rating

        for rating_first in (True, False):
            with self.subTest(rating_first=rating_first):
                await self.reset(count=3)
                async with self.Session() as db:
                    await db.execute(
                        delete(TrackingActivity).where(
                            TrackingActivity.media_id == self.series_id
                        )
                    )
                    await db.execute(
                        delete(Rating).where(Rating.user_id == self.user_id)
                    )
                    entry = await db.scalar(
                        select(TrackedEntry).where(
                            TrackedEntry.media_id == self.series_id
                        )
                    )
                    entry.season_scores = {}
                    baseline = await db.scalar(
                        select(CloudBaseline).where(
                            CloudBaseline.user_id == self.user_id
                        )
                    )
                    if baseline is None:
                        db.add(
                            CloudBaseline(
                                user_id=self.user_id,
                                provider="trakt",
                                approved=True,
                                snapshot={},
                                observed_at=utc() - timedelta(hours=1),
                            )
                        )
                    watched_at = utc()
                    db.add(
                        WatchEvent(
                            user_id=self.user_id,
                            media_id=self.episode_ids[-1],
                            completed=True,
                            watched_at=watched_at,
                        )
                    )
                    await db.commit()
                    if not rating_first:
                        from core.season_completion import reconcile

                        await reconcile(db, self.user_id, self.series_id)
                    media = await db.get(Media, self.series_id)
                    await reconcile_cloud_rating(
                        db,
                        provider="trakt",
                        user_id=self.user_id,
                        media=media,
                        season_number=2,
                        remote_score=8,
                        remote_rated_at=watched_at + timedelta(seconds=1),
                        existing={},
                        changed={},
                    )
                    await db.commit()
                await run_batch(self.Session)
                async with self.Session() as db:
                    cards = await self.cards(db)
                    rating_cards = [c for c in cards if c.payload.get("rating_changed")]
                    self.assertEqual(len(rating_cards), 1)
                    self.assertTrue(rating_cards[0].payload.get("season_completion_id"))
                    self.assertEqual(rating_cards[0].payload["season_number"], 2)
                    self.assertEqual(rating_cards[0].score, 8)

    async def test_cloud_completion_absorbs_progress_and_replay_is_stable(self):
        from core.cloud_history_reconciliation import reconcile_cloud_watch_events

        await self.reset(count=3)
        await run_batch(self.Session)
        async with self.Session() as db:
            db.add(
                CloudBaseline(
                    user_id=self.user_id,
                    provider="trakt",
                    approved=True,
                    snapshot={},
                    observed_at=utc() - timedelta(hours=1),
                )
            )
            db.add(
                WatchEvent(
                    user_id=self.user_id,
                    media_id=self.episode_ids[-1],
                    completed=True,
                    watched_at=utc(),
                )
            )
            await db.commit()
            result = await reconcile_cloud_watch_events(
                db,
                user_id=self.user_id,
                provider="trakt",
                new_media_ids={self.episode_ids[-1]},
                initial_import_override=False,
            )
            self.assertEqual(result["applied"], 1, result)
        await run_batch(self.Session)
        async with self.Session() as db:
            media = await db.get(Media, self.series_id)
            rows = activity_data([(c, media) for c in await self.cards(db)], limit=60)
            current = [c for c in rows if c["created_at"].date() == date.today()]
            self.assertEqual(len(current), 1, current)
            self.assertFalse(any(c["payload"].get("episodes_watched") for c in current))
            self.assertEqual(current[0]["payload"]["finished_seasons"], [2])
            self.assertTrue(any(c["payload"].get("series_completed") for c in current))
            before = sorted(c.id for c in await self.cards(db))
            await reconcile_cloud_watch_events(
                db,
                user_id=self.user_id,
                provider="trakt",
                new_media_ids={self.episode_ids[-1]},
                initial_import_override=False,
            )
        await run_batch(self.Session)
        async with self.Session() as db:
            self.assertEqual(sorted(c.id for c in await self.cards(db)), before)
            self.assertFalse(
                any(c.payload.get("started_watching") for c in await self.cards(db))
            )

    async def test_alternate_order_progress_activity_uses_display_numbers(self):
        await self.reset("planning", 0)
        async with self.Session() as db:
            for index, ep_id in enumerate(
                [
                    self.episode_ids[2],
                    self.episode_ids[3],
                    self.episode_ids[0],
                    self.episode_ids[1],
                ]
            ):
                ep = await db.get(Media, ep_id)
                db.add(
                    ShowEpisodePosition(
                        series_tmdb_id=923000,
                        order_key="tmdb:group:audit",
                        display_season=index // 2 + 1,
                        display_episode=index % 2 + 1,
                        tmdb_episode_id=ep.tmdb_id,
                        tmdb_season_number=ep.season_number,
                        tmdb_episode_number=ep.episode_number,
                    )
                )
            db.add(
                UserShowEpisodeOrder(
                    user_id=self.user_id,
                    series_tmdb_id=923000,
                    episode_order="tmdb:group:audit",
                )
            )
            await db.commit()
        summary = (await self.client.get(self.root + "/series-editor")).json()
        response = await self.client.patch(
            self.root,
            json={
                "series_editor": {
                    "order": summary["order"],
                    "revision": summary["revision"],
                    "action": "position",
                    "season_number": 1,
                    "episode_count": 1,
                    "episode_id": self.episode_ids[2],
                }
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json()["series_editor"]["selected_position"]["season_number"], 1
        )
        async with self.Session() as db:
            row = next(
                c for c in await self.cards(db) if c.payload.get("episodes_watched")
            )
            self.assertEqual(row.payload["position"], "S1E1")
            self.assertEqual(row.payload["progress_order"], "tmdb:group:audit")
            # Providers rate aired seasons even when the editor uses a group.
            from core.cloud_rating_reconciliation import reconcile_cloud_rating

            db.add(
                CloudBaseline(
                    user_id=self.user_id,
                    provider="trakt",
                    approved=True,
                    snapshot={},
                    observed_at=utc() - timedelta(hours=1),
                )
            )
            await db.commit()
            await reconcile_cloud_rating(
                db,
                provider="trakt",
                user_id=self.user_id,
                media=await db.get(Media, self.series_id),
                season_number=1,
                remote_score=8,
                remote_rated_at=utc() + timedelta(seconds=1),
                existing={},
                changed={},
            )
            ratings = [
                card
                for card in await self.cards(db)
                if card.payload.get("rating_changed")
            ]
            self.assertEqual(len(ratings), 1)
            self.assertEqual(ratings[0].payload["episode_order"], "tmdb:aired")
            self.assertEqual(ratings[0].payload["season_ratings"], {"1": 8})

    async def test_first_connection_snapshot_and_repeated_playback_do_not_change_manual_state(
        self,
    ):
        from core.tracking_snapshot import observe_stream_snapshot

        await self.reset("paused", 2)
        async with self.Session() as db:
            entry = await db.scalar(
                select(TrackedEntry).where(TrackedEntry.media_id == self.series_id)
            )
            entry.start_date = date(2024, 2, 3)
            entry.start_date_source = "manual"
            entry.season_cursor = {
                "order": "tmdb:aired",
                "season_number": 2,
                "episode_count": 0,
                "episode_id": self.episode_ids[1],
            }
            db.add(
                UserSettings(
                    user_id=self.user_id, preferences={"watch_state_sync": "immediate"}
                )
            )
            conn = MediaServerConnection(
                user_id=self.user_id,
                type="stremio",
                name="Audit",
                url="http://invalid",
                token="fixture",
                sync_watched=True,
            )
            db.add(conn)
            await db.commit()
            row = {
                "content_id": "tmdb:923000",
                "content_type": "series",
                "season": 2,
                "episode": 1,
                "position": 10000,
                "duration": 100000,
                "last_watched": int(
                    utc().replace(tzinfo=timezone.utc).timestamp() * 1000
                ),
            }
            for iteration in range(2):
                await observe_stream_snapshot(
                    db, conn, [], [], [row], {"tmdb:923000": 923000}
                )
                await db.commit()
                await db.refresh(entry)
                self.assertEqual(
                    (entry.status, entry.progress, entry.start_date),
                    ("paused", 2, date(2024, 2, 3)),
                )
                self.assertEqual(entry.season_cursor["season_number"], 2)
                self.assertFalse(
                    any(c.payload.get("started_watching") for c in await self.cards(db))
                )

    async def reviewed_import(self, existing=False, watched_episodes=(1, 2)):
        from core.connection_imports import stage_first_snapshot
        from core.netflix_sessions import _apply_import
        from models import NetflixImportSession

        await self.reset(count=0)
        if existing:
            response = await self.client.patch(
                self.root, json={"start_date": "2024-02-03"}
            )
            self.assertEqual(response.status_code, 200, response.text)
        async with self.Session() as db:
            if not existing:
                await db.execute(
                    delete(TrackedEntry).where(TrackedEntry.media_id == self.series_id)
                )
            media = await db.get(Media, self.series_id)
            show = await db.scalar(select(Show).where(Show.tmdb_id == 923000))
            episodes = [await db.get(Media, ep_id) for ep_id in self.episode_ids]
            conn = MediaServerConnection(
                user_id=self.user_id,
                type="stremio",
                name="Import audit",
                url="http://invalid",
                token="fixture",
                server_user_id="fixture",
                identity_version=0,
            )
            db.add(conn)
            await db.commit()
            stamp = int(datetime(2024, 1, 2, tzinfo=timezone.utc).timestamp() * 1000)
            watched = [
                {
                    "content_id": "tmdb:923000",
                    "content_type": "series",
                    "title": "Two seasons",
                    "season": 1,
                    "episode": en,
                    "watched_at": stamp,
                }
                for en in watched_episodes
            ]
            data = {
                "id": 923000,
                "name": "Two seasons",
                "first_air_date": "2020-01-01",
                "seasons": [{"season_number": n, "episode_count": 2} for n in (1, 2)],
            }

            async def catalogue(native, sn, *args, **kwargs):
                return {
                    "episodes": [
                        {
                            "id": ep.tmdb_id,
                            "name": ep.title,
                            "season_number": sn,
                            "episode_number": ep.episode_number,
                            "air_date": "2020-01-01",
                            "runtime": 45,
                        }
                        for ep in episodes
                        if ep.season_number == sn
                    ]
                }

            with (
                patch(
                    "core.connection_imports.tmdb.get_show_light",
                    new=AsyncMock(return_value=data),
                ),
                patch("core.connection_imports.tmdb.get_season", side_effect=catalogue),
            ):
                await stage_first_snapshot(
                    db, conn, [], watched, [], {"tmdb:923000": 923000}, "fixture"
                )
            baseline = await db.get(StreamBaseline, conn.id)
            session = await db.get(
                NetflixImportSession, baseline.snapshot["import_session_id"]
            )
            # This two-episode fixture hits the import's intentional sparse-history
            # default. Apply the same explicit partial/Watching choice the review UI supports.
            payload = dict(session.payload)
            for item in payload["items"]:
                item["outcome"].update(
                    status="partial", tracking_status="watching", status_overridden=True
                )
            session.payload = payload
            with patch(
                "core.netflix_sessions._get_or_create_show_root",
                new=AsyncMock(return_value=(show, media)),
            ):
                stats = await _apply_import(db, self.user_id, session, AsyncMock())
            await db.commit()
            self.assertEqual(
                stats["existing_entries"] if existing else stats["new_entries"],
                1,
                stats,
            )
        await run_batch(self.Session)

    async def test_reviewed_first_connection_import_absorbs_same_window_start(self):
        await self.reviewed_import()
        async with self.Session() as db:
            media = await db.get(Media, self.series_id)
            cards = await self.cards(db)
            projected = activity_data([(card, media) for card in cards], limit=60)
            starts = [
                c
                for c in projected
                if c["payload"].get("started_watching")
                and c["payload"].get("status_changed")
            ]
            completions = [
                c for c in projected if c["payload"].get("finished_seasons") == [1]
            ]
            self.assertEqual(len(starts), 0, projected)
            self.assertEqual(len(completions), 1, projected)
            self.assertEqual(len(projected), 1, projected)

    async def test_reviewed_import_retains_manual_date_and_one_start_activity(self):
        await self.reviewed_import(existing=True, watched_episodes=(1,))
        async with self.Session() as db:
            entry = await db.scalar(
                select(TrackedEntry).where(TrackedEntry.media_id == self.series_id)
            )
            self.assertEqual(entry.start_date, date(2024, 2, 3))
            media = await db.get(Media, self.series_id)
            cards = activity_data([(c, media) for c in await self.cards(db)], limit=60)
            starts = [
                c
                for c in cards
                if c["payload"].get("started_watching")
                and c["payload"].get("status_changed")
            ]
            self.assertEqual(
                sorted(c["created_at"].date().isoformat() for c in starts),
                ["2024-02-03"],
                cards,
            )
