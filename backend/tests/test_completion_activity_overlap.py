"""Completion precedence preserves identities, window boundaries and other actions."""

import unittest
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

from core.season_activity import absorb_completed_progress
from models import TrackingActivity


class CompletionOverlapTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.at = datetime(2024, 1, 2, 12)
        self.groups = [
            (
                sn,
                [
                    SimpleNamespace(
                        id=(sn - 1) * 2 + en, season_number=sn, episode_number=en
                    )
                    for en in (1, 2)
                ],
                2,
            )
            for sn in (1, 2)
        ]
        self.target = TrackingActivity(
            status="watching",
            created_at=self.at.replace(hour=0),
            payload={
                "season_completion_id": 1,
                "season_number": 1,
                "episode_order": "tmdb:aired",
                "window_started_at": self.at.isoformat(),
                "finished_seasons": [1],
            },
        )
        self.db = SimpleNamespace(deleted=set())
        self.db.delete = AsyncMock(side_effect=self.db.deleted.add)

    def progress(self, *, at=None, **extra):
        at = at or self.at - timedelta(hours=1)
        return TrackingActivity(
            status="watching",
            score=8,
            created_at=at,
            payload={
                "window_started_at": at.isoformat(),
                "progress_order": "tmdb:aired",
                "episodes_watched": 3,
                "progress": 3,
                "progress_start": 0,
                "progress_episode_ids": [1, 2, 3],
                "watched_start_ids": [],
                "episode_positions": ["S1E1", "S1E2", "S2E1"],
                "position_start": "S1E1",
                "position": "S2E1",
                **extra,
            },
        )

    async def absorb(self, card):
        await absorb_completed_progress(
            self.db, [card, self.target], [self.target], self.groups, "tmdb:aired"
        )

    async def test_cross_season_progress_survives_and_reconciliation_is_repeatable(
        self,
    ):
        card = self.progress()
        await self.absorb(card)
        self.assertEqual(card.payload["episode_positions"], ["S2E1"])
        self.assertEqual(card.payload["episodes_watched"], 1)
        self.assertEqual(card.payload["progress_episode_ids"], [3])
        self.assertEqual(card.payload["watched_start_ids"], [1, 2])
        self.assertEqual(card.payload["progress_start"], 2)
        before = dict(card.payload)
        await self.absorb(card)
        self.assertEqual(card.payload, before)
        self.assertNotIn(card, self.db.deleted)

    async def test_exact_window_boundary_keeps_history_and_later_overlapping_progress_is_absorbed(
        self,
    ):
        for hours in (-24, 24):
            card = self.progress(at=self.at + timedelta(hours=hours))
            before = dict(card.payload)
            await self.absorb(card)
            self.assertEqual(card.payload, before)
        later = self.progress(
            at=self.at + timedelta(hours=1),
            episodes_watched=2,
            progress_episode_ids=[1, 2],
            episode_positions=["S1E1", "S1E2"],
        )
        await self.absorb(later)
        self.assertIn(later, self.db.deleted)

    async def test_rating_and_other_status_actions_are_not_deleted_with_progress(self):
        card = self.progress(
            episodes_watched=2,
            progress_episode_ids=[1, 2],
            episode_positions=["S1E1", "S1E2"],
            rating_changed=True,
        )
        await self.absorb(card)
        self.assertEqual(card.payload["episodes_watched"], 0)
        self.assertTrue(card.payload["rating_changed"])
        self.assertEqual(card.score, 8)
        self.assertNotIn(card, self.db.deleted)
        card = self.progress(
            episodes_watched=2,
            progress_episode_ids=[1, 2],
            episode_positions=["S1E1", "S1E2"],
            status_changed=True,
        )
        card.status = "dropped"
        await self.absorb(card)
        self.assertTrue(card.payload["status_changed"])
        self.assertNotIn(card, self.db.deleted)

    async def test_legacy_numbered_range_is_trimmed_by_membership(self):
        card = self.progress()
        for key in (
            "progress_order",
            "progress_episode_ids",
            "episode_positions",
            "watched_start_ids",
        ):
            card.payload.pop(key)
        await self.absorb(card)
        self.assertEqual(card.payload["episodes_watched"], 1)
        self.assertEqual(card.payload["episode_positions"], ["S2E1"])

    async def test_import_keeps_another_seasons_start_and_episode(self):
        events = {
            "start": {
                "key": "start",
                "date": self.at.isoformat(),
                "status": "watching",
                "season_number": 2,
                "started_watching": True,
                "status_changed": True,
            },
            "episode:1:2": {
                "key": "episode:1:2",
                "date": self.at.isoformat(),
                "status": "watching",
                "episodes_watched": 1,
                "position": "S1E2",
            },
            "episode:2:1": {
                "key": "episode:2:1",
                "date": self.at.isoformat(),
                "status": "watching",
                "episodes_watched": 1,
                "position": "S2E1",
            },
        }
        card = TrackingActivity(
            status="watching",
            created_at=self.at,
            payload={
                "window_started_at": self.at.isoformat(),
                "streaming_base": {},
                "streaming_events": events,
            },
        )
        await self.absorb(card)
        self.assertEqual(
            set(card.payload["streaming_events"]), {"start", "episode:2:1"}
        )
        self.assertEqual(card.payload["episodes_watched"], 1)
        self.assertTrue(card.payload["started_watching"])

    async def test_alternate_order_absorbs_canonical_members_only(self):
        self.groups = [(1, self.groups[1][1], 2), (2, self.groups[0][1], 2)]
        self.target.payload["episode_order"] = "tmdb:group:test"
        card = self.progress(
            progress_order="tmdb:group:test", progress_episode_ids=[3, 4, 1]
        )
        await absorb_completed_progress(
            self.db, [card], [self.target], self.groups, "tmdb:group:test"
        )
        self.assertEqual(card.payload["progress_episode_ids"], [1])
        self.assertEqual(card.payload["episode_positions"], ["S2E1"])

    async def test_correction_restores_only_still_watched_encapsulated_episodes(self):
        from core.season_activity import restore_absorbed_progress

        card = self.progress(
            episodes_watched=2,
            progress_episode_ids=[1, 2],
            episode_positions=["S1E1", "S1E2"],
        )
        await self.absorb(card)
        self.assertIn(card, self.db.deleted)
        restored = []
        self.db.add = restored.append
        restore_absorbed_progress(
            self.db,
            [],
            self.target.payload["absorbed_progress"],
            user_id=1,
            media_id=2,
            watched_ids={1},
            completed_ids=set(),
        )
        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0].payload["progress_episode_ids"], [1])
        self.assertEqual(restored[0].payload["episodes_watched"], 1)
        self.assertEqual(restored[0].created_at, card.created_at)

    async def test_restored_progress_joins_surviving_window_without_resurrecting_removed_watches(
        self,
    ):
        from core.season_activity import restore_absorbed_progress

        card = self.progress()
        await self.absorb(card)
        self.db.add = lambda row: self.fail("Should reuse the surviving window")
        restore_absorbed_progress(
            self.db,
            [card],
            self.target.payload["absorbed_progress"],
            user_id=1,
            media_id=2,
            watched_ids={1, 3},
            completed_ids=set(),
        )
        self.assertEqual(card.payload["progress_episode_ids"], [1, 3])
        self.assertEqual(card.payload["episodes_watched"], 2)
        self.assertEqual(card.payload["watched_start_ids"], [])

    async def test_alternate_group_does_not_absorb_another_aired_seasons_start(self):
        self.groups = [(1, self.groups[1][1], 2), (2, self.groups[0][1], 2)]
        self.target.payload["episode_order"] = "tmdb:group:test"
        for sn in (1, 2):
            with self.subTest(aired_season=sn):
                event = {
                    "key": "start",
                    "date": self.at.isoformat(),
                    "status": "watching",
                    "season_number": sn,
                    "start_position": f"S{sn}E1",
                    "started_watching": True,
                    "status_changed": True,
                }
                card = TrackingActivity(
                    status="watching",
                    created_at=self.at,
                    payload={
                        "window_started_at": self.at.isoformat(),
                        "streaming_base": {},
                        "streaming_events": {"start": event},
                    },
                )
                await absorb_completed_progress(
                    self.db, [card], [self.target], self.groups, "tmdb:group:test"
                )
                self.assertEqual(card in self.db.deleted, sn == 2)
