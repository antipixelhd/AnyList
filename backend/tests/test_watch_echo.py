import unittest

from core import watch_echo


class ConsumeRecentlyPushedWatchedTests(unittest.TestCase):
    def setUp(self):
        watch_echo._recently_pushed_watched.clear()

    def test_unmarked_media_is_not_consumed(self):
        self.assertFalse(watch_echo.consume_recently_pushed_watched(user_id=1, media_id=2))

    def test_markers_are_isolated_by_user_and_media(self):
        watch_echo.mark_pushed_watched(user_id=1, media_id=2)
        self.assertFalse(watch_echo.consume_recently_pushed_watched(user_id=3, media_id=2))
        self.assertFalse(watch_echo.consume_recently_pushed_watched(user_id=1, media_id=4))
        self.assertTrue(watch_echo.consume_recently_pushed_watched(user_id=1, media_id=2))

    def test_marked_media_is_consumed_once(self):
        watch_echo.mark_pushed_watched(user_id=1, media_id=2)
        self.assertTrue(watch_echo.consume_recently_pushed_watched(user_id=1, media_id=2))
        self.assertFalse(watch_echo.consume_recently_pushed_watched(user_id=1, media_id=2))

    def test_expired_marker_is_not_consumed(self):
        import datetime
        watch_echo.mark_pushed_watched(user_id=1, media_id=2)
        watch_echo._recently_pushed_watched[(1, 2)][0] = datetime.datetime.utcnow() - watch_echo._PUSHED_WATCHED_TTL - datetime.timedelta(seconds=1)
        self.assertFalse(watch_echo.consume_recently_pushed_watched(user_id=1, media_id=2))

    def test_two_pending_pushes_each_consume_their_own_echo(self):
        # A user pushing the same item to two Jellyfin/Emby connections at
        # once expects two echoes back - the second shouldn't be treated as
        # an unexpected duplicate just because the first already consumed.
        watch_echo.mark_pushed_watched(user_id=1, media_id=2)
        watch_echo.mark_pushed_watched(user_id=1, media_id=2)
        self.assertTrue(watch_echo.consume_recently_pushed_watched(user_id=1, media_id=2))
        self.assertTrue(watch_echo.consume_recently_pushed_watched(user_id=1, media_id=2))
        self.assertFalse(watch_echo.consume_recently_pushed_watched(user_id=1, media_id=2))
