"""Focused Stremio payload tests, independent of sync-router orchestration."""

import os
import unittest

os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://test:test@localhost/test")

from core import stremio, stremio_payloads


class StremioPayloadTests(unittest.TestCase):
    def test_noop_comparison_ignores_only_mtime(self) -> None:
        left = {"_id": "tt0133093", "_mtime": "old", "state": {"timeOffset": 42}, "custom": True}
        same = {**left, "_mtime": "new"}
        changed = {**same, "custom": False}

        self.assertTrue(stremio_payloads.same_item(left, same))
        self.assertFalse(stremio_payloads.same_item(left, changed))


    def test_episode_unwatch_preserves_other_plays_and_does_not_mutate_input(self):
        import copy

        video_ids = ["tt123:1:1", "tt123:1:2", "tt123:1:3"]
        original = {
            "_id": "tt123", "type": "series", "custom": "keep",
            "state": {
                "watched": stremio.encode_watched_bitfield(set(video_ids), video_ids),
                "lastWatched": "2026-09-01T12:00:00Z", "timesWatched": 3,
                "customPlaybackState": {"keep": True},
            },
        }
        before = copy.deepcopy(original)
        result = stremio_payloads.with_watch_state(
            original,
            {"content_id": "tt123", "content_type": "series", "season": 1,
             "episode": 2, "watched": False, "watched_at": None},
            {"videos": [{"id": value} for value in video_ids]},
        )

        self.assertEqual(stremio.decode_watched_bitfield(result["state"]["watched"], video_ids),
                         {"tt123:1:1", "tt123:1:3"})
        self.assertEqual(result["state"]["lastWatched"], before["state"]["lastWatched"])
        self.assertEqual(result["state"]["timesWatched"], 3)
        self.assertEqual(result["state"]["customPlaybackState"], {"keep": True})
        self.assertEqual(result["custom"], "keep")
        self.assertEqual(original, before)

    def test_resume_uses_cinemeta_video_and_preserves_temporary_membership(self):
        import copy

        original = {
            "_id": "tt123", "type": "series", "removed": True, "temp": False,
            "state": {"watched": "previous-watch-bits", "timesWatched": 2},
        }
        before = copy.deepcopy(original)
        result = stremio_payloads.with_progress_state(
            original,
            {"content_id": "tt123", "content_type": "series", "season": 1,
             "episode": 2, "position": 12_345, "duration": 2_400_000,
             "video_id": "fallback-id"},
            {"videos": [{"id": "cinemeta-video", "season": 1, "episode": 2}]},
            in_library=False,
        )

        self.assertEqual((result["removed"], result["temp"]), (False, True))
        self.assertEqual(result["state"]["video_id"], "cinemeta-video")
        self.assertEqual((result["state"]["timeOffset"], result["state"]["duration"]),
                         (12_345, 2_400_000))
        self.assertEqual(result["state"]["watched"], "previous-watch-bits")
        self.assertEqual(result["state"]["timesWatched"], 2)
        self.assertEqual(original, before)
