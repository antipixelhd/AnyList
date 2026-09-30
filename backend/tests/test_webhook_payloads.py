import unittest

from core.webhook_payloads import parse_jellyfin_payload, parse_plex_payload, parse_kodi_payload


class ParseJellyfinPayloadEmbyEventFieldTests(unittest.TestCase):
    """Regression test for #160: Emby doesn't send NotificationType at all -
    its webhooks report the event under "Event" (dotted, lowercase names like
    "playback.stop"), which used to be read from the wrong key entirely,
    silently no-oping every inbound Emby webhook."""

    def test_reads_notification_type_from_emby_event_field(self):
        payload = {
            "Event": "playback.stop",
            "Item": {
                "Id": "test1",
                "Name": "Supergirl",
                "Type": "Movie",
                "ProductionYear": 2026,
                "RunTimeTicks": 64800000000,
                "ProviderIds": {"Tmdb": "1081003"},
            },
            "Session": {
                "Id": "testsession1",
                "UserName": "arne",
                "PlayState": {"PositionTicks": 61560000000, "IsPaused": False},
            },
        }
        data = parse_jellyfin_payload(payload)
        self.assertIsNotNone(data)
        self.assertEqual(data["notification_type"], "playback.stop")
        self.assertEqual(data["title"], "Supergirl")

    def test_notification_type_still_prefers_pascal_case_field(self):
        payload = {
            "NotificationType": "PlaybackStop",
            "Event": "playback.stop",
            "Item": {"Id": "test1", "Name": "Supergirl", "Type": "Movie"},
        }
        data = parse_jellyfin_payload(payload)
        self.assertEqual(data["notification_type"], "PlaybackStop")


class ParseJellyfinPayloadNestedEpisodeSeriesNameTests(unittest.TestCase):
    """Regression test for #192: Emby's native webhook notifications use this
    nested Item/Session shape and don't reliably populate SeriesProviderIds
    for an episode the way Jellyfin's "send all properties" plugin does.
    Without a series_name fallback here (mirroring the flat-format branch,
    which already has one), find_or_create_media_jellyfin can never resolve
    show linkage - Now Playing then shows the bare episode title with no
    poster instead of the series."""

    def test_nested_episode_payload_includes_series_name_fallback(self):
        payload = {
            "Event": "playback.start",
            "Item": {
                "Id": "ep1",
                "Name": "Aquamom",
                "Type": "Episode",
                "SeriesName": "Entourage",
                "ParentIndexNumber": 3,
                "IndexNumber": 1,
                "ProviderIds": {"Tmdb": "1081099"},
                # SeriesProviderIds deliberately absent - this is the exact gap.
            },
            "Session": {"Id": "sess1", "UserName": "arne", "PlayState": {}},
        }
        data = parse_jellyfin_payload(payload)
        self.assertIsNotNone(data)
        self.assertIsNone(data["series_tmdb_id"])
        self.assertEqual(data["series_name"], "Entourage")

    def test_nested_movie_payload_has_no_series_name(self):
        # A movie item has no SeriesName field at all - must not crash or
        # fabricate a value.
        payload = {
            "Event": "playback.start",
            "Item": {"Id": "m1", "Name": "Inception", "Type": "Movie"},
            "Session": {"Id": "sess1", "PlayState": {}},
        }
        data = parse_jellyfin_payload(payload)
        self.assertIsNone(data["series_name"])


class ParseJellyfinPayloadPlayedToCompletionTests(unittest.TestCase):
    """Regression tests for #206: on auto-play, Emby resets Session.PlayState
    to the next episode before firing the "playback.stop" event for the one
    that just finished, so PositionTicks/RunTimeTicks there read 0 - a
    genuinely completed episode looked like a <5% no-op stop and was silently
    dropped. PlaybackInfo carries this event's own authoritative position and
    PlayedToCompletion flag; the flat plugin format exposes the same flag as
    its own top-level property."""

    def test_nested_format_falls_back_to_playback_info_position(self):
        payload = {
            "Event": "playback.stop",
            "Item": {"Id": "ep1", "Name": "Finale", "Type": "Episode", "RunTimeTicks": 10_000_000},
            # Session.PlayState already reset for the auto-playing next episode.
            "Session": {"Id": "sess1", "PlayState": {"PositionTicks": 0}},
            "PlaybackInfo": {"PositionTicks": 10_000_000, "PlayedToCompletion": True},
        }
        data = parse_jellyfin_payload(payload)
        self.assertEqual(data["progress_percent"], 1.0)
        self.assertTrue(data["played_to_completion"])

    def test_nested_format_prefers_session_position_when_present(self):
        # A normal (non-auto-play) stop still has a real Session position -
        # PlaybackInfo must not override a legitimate in-progress stop.
        payload = {
            "Event": "playback.stop",
            "Item": {"Id": "ep1", "Name": "Ep", "Type": "Episode", "RunTimeTicks": 10_000_000},
            "Session": {"Id": "sess1", "PlayState": {"PositionTicks": 3_000_000}},
            "PlaybackInfo": {"PositionTicks": 10_000_000, "PlayedToCompletion": True},
        }
        data = parse_jellyfin_payload(payload)
        self.assertEqual(data["progress_percent"], 0.3)

    def test_nested_format_defaults_played_to_completion_false(self):
        payload = {
            "Event": "playback.stop",
            "Item": {"Id": "ep1", "Name": "Ep", "Type": "Episode"},
            "Session": {"Id": "sess1", "PlayState": {}},
        }
        data = parse_jellyfin_payload(payload)
        self.assertFalse(data["played_to_completion"])

    def test_flat_format_reads_played_to_completion(self):
        payload = {
            "NotificationType": "PlaybackStop",
            "ItemType": "Episode",
            "PlayedToCompletion": True,
        }
        data = parse_jellyfin_payload(payload)
        self.assertTrue(data["played_to_completion"])

    def test_flat_format_defaults_played_to_completion_false(self):
        payload = {"NotificationType": "PlaybackStop", "ItemType": "Episode"}
        data = parse_jellyfin_payload(payload)
        self.assertFalse(data["played_to_completion"])


class ParseJellyfinFlatPayloadSeasonZeroTests(unittest.TestCase):
    """Regression test for #132: a Season 0 (specials) episode has
    SeasonNumber: 0 in the flat webhook payload, which a falsy check like
    `payload.get("SeasonNumber") or None` incorrectly coerces to None."""

    def test_season_zero_is_preserved_not_coerced_to_none(self):
        payload = {
            "NotificationType": "PlaybackStart",
            "ItemType": "Episode",
            "ItemId": "abc123",
            "Name": "Behind the Scenes",
            "SeriesName": "Some Show",
            "SeasonNumber": 0,
            "EpisodeNumber": 1,
            "Provider_tmdb": "999",
        }
        data = parse_jellyfin_payload(payload)
        self.assertIsNotNone(data)
        self.assertEqual(data["season_number"], 0)

    def test_movie_has_no_season_number(self):
        payload = {
            "NotificationType": "PlaybackStart",
            "ItemType": "Movie",
            "ItemId": "xyz",
            "Name": "A Movie",
        }
        data = parse_jellyfin_payload(payload)
        self.assertIsNone(data["season_number"])


class ParseJellyfinUserDataSavedPayloadTests(unittest.TestCase):
    """Regression test for #69: Jellyfin's official Webhook plugin has no
    "MarkPlayed" event — manually toggling watched/unwatched raises
    UserDataSaved with SaveReason=TogglePlayed instead. The parser must
    surface both fields so the handler can tell a real toggle apart from
    the same notification firing on every playback tick/rating/favorite."""

    def test_extracts_played_and_save_reason_on_manual_toggle(self):
        payload = {
            "NotificationType": "UserDataSaved",
            "ItemType": "Episode",
            "ItemId": "abc123",
            "Name": "Pilot",
            "SeriesName": "Some Show",
            "SeasonNumber": 1,
            "EpisodeNumber": 1,
            "Provider_tmdb": "999",
            "SaveReason": "TogglePlayed",
            "Played": True,
        }
        data = parse_jellyfin_payload(payload)
        self.assertIsNotNone(data)
        self.assertEqual(data["save_reason"], "TogglePlayed")
        self.assertIs(data["played"], True)

    def test_extracts_played_false_for_unwatch_toggle(self):
        payload = {
            "NotificationType": "UserDataSaved",
            "ItemType": "Movie",
            "ItemId": "xyz",
            "Name": "A Movie",
            "SaveReason": "TogglePlayed",
            "Played": False,
        }
        data = parse_jellyfin_payload(payload)
        self.assertEqual(data["save_reason"], "TogglePlayed")
        self.assertIs(data["played"], False)

    def test_still_parses_non_toggle_save_reasons(self):
        # UserDataSaved also fires for playback progress, ratings, favorites,
        # etc. — the handler (not the parser) is responsible for ignoring
        # those via save_reason, so parsing itself must not drop them.
        payload = {
            "NotificationType": "UserDataSaved",
            "ItemType": "Movie",
            "ItemId": "xyz",
            "Name": "A Movie",
            "SaveReason": "PlaybackProgress",
            "Played": False,
        }
        data = parse_jellyfin_payload(payload)
        self.assertEqual(data["save_reason"], "PlaybackProgress")


class ParseJellyfinMultiEpisodePayloadTests(unittest.TestCase):
    """Regression tests for #138 follow-up: Jellyfin can mux several episodes
    into one file and fire a single webhook event for it, exposing the span
    via IndexNumber/IndexNumberEnd on the nested-format Item."""

    def test_nested_format_extracts_index_number_end(self):
        payload = {
            "NotificationType": "MarkPlayed",
            "Item": {"Type": "Episode", "Id": "abc", "Name": "Ep 1-2", "IndexNumber": 1, "IndexNumberEnd": 2},
            "Session": {},
        }
        data = parse_jellyfin_payload(payload)
        self.assertEqual(data["episode_number"], 1)
        self.assertEqual(data["episode_number_end"], 2)

    def test_nested_format_single_episode_has_no_end(self):
        payload = {
            "NotificationType": "MarkPlayed",
            "Item": {"Type": "Episode", "Id": "abc", "Name": "Ep 1", "IndexNumber": 1},
            "Session": {},
        }
        data = parse_jellyfin_payload(payload)
        self.assertIsNone(data["episode_number_end"])

    def test_flat_format_extracts_episode_number_end(self):
        # "Send all properties" - the setup this repo's README documents,
        # since custom templates produce invalid JSON - includes
        # EpisodeNumberEnd alongside EpisodeNumber for a combined file.
        # Confirmed against a live payload while diagnosing #138 follow-up.
        payload = {
            "NotificationType": "PlaybackStart",
            "ItemType": "Episode",
            "ItemId": "abc",
            "SeasonNumber": 1,
            "EpisodeNumber": 1,
            "EpisodeNumberEnd": 2,
        }
        data = parse_jellyfin_payload(payload)
        self.assertEqual(data["episode_number_end"], 2)

    def test_flat_format_end_is_none_when_absent(self):
        # A normal single-episode file has no EpisodeNumberEnd key at all.
        payload = {
            "NotificationType": "MarkPlayed",
            "ItemType": "Episode",
            "ItemId": "abc",
            "SeasonNumber": 1,
            "EpisodeNumber": 1,
        }
        data = parse_jellyfin_payload(payload)
        self.assertIsNone(data["episode_number_end"])


class ParseJellyfinSeriesYearTests(unittest.TestCase):
    """#373: the flat plugin sets Year to the series' production year on an
    episode payload - carried as series_year so _resolve_show_for_episode can
    pick between two shows that share a title."""

    def test_flat_episode_carries_the_series_year(self):
        data = parse_jellyfin_payload({
            "NotificationType": "PlaybackStop", "ItemType": "Episode",
            "Name": "The Holy Trinity", "SeriesName": "The Grand Tour",
            "Year": 2026, "SeasonNumber": 1, "EpisodeNumber": 1,
        })
        self.assertEqual(data["series_year"], 2026)

    def test_flat_movie_has_no_series_year(self):
        data = parse_jellyfin_payload({
            "NotificationType": "PlaybackStop", "ItemType": "Movie",
            "Name": "The Matrix", "Year": 1999,
        })
        self.assertIsNone(data["series_year"])

    def test_nested_episode_has_no_series_year(self):
        # item.ProductionYear there is the episode's year, not the series'.
        data = parse_jellyfin_payload({
            "Event": "playback.stop",
            "Item": {"Id": "ep1", "Name": "Ep", "Type": "Episode", "ProductionYear": 2026},
            "Session": {"Id": "s", "PlayState": {}},
        })
        self.assertIsNone(data["series_year"])


class ParseJellyfinRuntimeTicksTests(unittest.TestCase):
    """#383: the parser now surfaces RunTimeTicks so the handler can backfill
    Media.runtime from it, instead of only using it for the progress ratio."""

    _MIN = 600_000_000  # RunTimeTicks per minute

    def test_nested_payload_carries_runtime_ticks(self):
        data = parse_jellyfin_payload({
            "Event": "playback.progress",
            "Item": {"Id": "m1", "Name": "Heat", "Type": "Movie", "RunTimeTicks": 170 * self._MIN},
            "Session": {"Id": "s", "PlayState": {"PositionTicks": 0}},
        })
        self.assertEqual(data["runtime_ticks"], 170 * self._MIN)

    def test_flat_payload_carries_runtime_ticks(self):
        data = parse_jellyfin_payload({
            "NotificationType": "PlaybackProgress", "ItemType": "Episode", "ItemId": "e1",
            "Name": "Ep", "SeasonNumber": 1, "EpisodeNumber": 1, "RunTimeTicks": 22 * self._MIN,
        })
        self.assertEqual(data["runtime_ticks"], 22 * self._MIN)

    def test_absent_runtime_ticks_is_none(self):
        data = parse_jellyfin_payload({
            "NotificationType": "PlaybackStop", "ItemType": "Movie", "ItemId": "m2", "Name": "x",
        })
        self.assertIsNone(data["runtime_ticks"])


class ParseKodiPayloadTests(unittest.TestCase):
    @staticmethod
    def _hms(seconds: int) -> dict:
        return {"hours": seconds // 3600, "minutes": (seconds % 3600) // 60, "seconds": seconds % 60}

    def _stop_payload(self, *, position: int, total: int, end: bool) -> dict:
        return {
            "method": "Player.OnStop",
            "item": {"type": "movie", "title": "The Matrix", "year": 1999,
                     "uniqueid": {"tmdb": "603"}, "id": 7},
            "player": {"time": self._hms(position), "totaltime": self._hms(total)},
            "params": {"data": {"end": end}},
        }

    def test_unknown_method_is_ignored(self):
        self.assertIsNone(parse_kodi_payload({"method": "System.OnWake"}))

    def test_music_item_is_ignored(self):
        payload = {"method": "Player.OnPlay", "item": {"type": "song", "title": "x"}}
        self.assertIsNone(parse_kodi_payload(payload))

    def test_play_maps_and_reads_ids(self):
        payload = {
            "method": "Player.OnPlay",
            "item": {"type": "movie", "title": "The Matrix", "year": 1999,
                     "uniqueid": {"tmdb": "603", "imdb": "tt0133093"}},
            "player": {"time": self._hms(0), "totaltime": self._hms(8160)},
        }
        data = parse_kodi_payload(payload)
        self.assertEqual(data["notification_type"], "play")
        self.assertEqual(data["media_type"], "movie")
        self.assertEqual(data["tmdb_id"], "603")
        self.assertEqual(data["imdb_id"], "tt0133093")

    def test_episode_carries_series_and_numbers(self):
        payload = {
            "method": "Player.OnPlay",
            "item": {"type": "episode", "title": "Pilot", "showtitle": "Lost",
                     "season": 1, "episode": 1, "uniqueid": {}},
            "player": {"time": self._hms(60), "totaltime": self._hms(2520)},
        }
        data = parse_kodi_payload(payload)
        self.assertEqual(data["media_type"], "episode")
        self.assertEqual(data["series_name"], "Lost")
        self.assertEqual((data["season_number"], data["episode_number"]), (1, 1))

    def test_stop_near_end_reports_high_progress_even_without_end_flag(self):
        # Issue #2: stopping in the credits (end flag false) should still land
        # as a >= 90% watch so the stop handler marks it completed.
        data = parse_kodi_payload(self._stop_payload(position=7350, total=7680, end=False))
        self.assertFalse(data["ended"])
        self.assertGreaterEqual(data["progress_percent"], 0.90)

    def test_stop_with_end_flag_sets_ended(self):
        data = parse_kodi_payload(self._stop_payload(position=0, total=7680, end=True))
        self.assertTrue(data["ended"])

    def test_stop_early_reports_low_progress(self):
        data = parse_kodi_payload(self._stop_payload(position=120, total=7680, end=False))
        self.assertLess(data["progress_percent"], 0.05)

    def test_synthetic_mark_watched_payload_is_complete(self):
        # Shape the add-on POSTs for a "mark as watched" (time == totaltime).
        data = parse_kodi_payload(self._stop_payload(position=7680, total=7680, end=True))
        self.assertTrue(data["ended"])
        self.assertEqual(data["progress_percent"], 1.0)
        self.assertEqual(data["session_id"], "7")


class ParsePlexPayloadTests(unittest.TestCase):
    def test_live_tv_and_unsupported_playback_items_are_ignored(self):
        for metadata in (
            {"type": "movie", "live": True},
            {"type": "episode", "librarySectionType": "livetv"},
            {"type": "track"},
        ):
            with self.subTest(metadata=metadata):
                self.assertIsNone(parse_plex_payload({"event": "media.play", "Metadata": metadata}))

    def test_legacy_episode_identity_and_parent_guids_survive_normalization(self):
        result = parse_plex_payload({"event": "media.pause", "Account": {"title": "Member"}, "Metadata": {
            "type": "episode", "guid": "com.plexapp.agents.thetvdb://73762/4/3",
            "grandparentGuid": "com.plexapp.agents.themoviedb://100",
            "grandparentTitle": "Series", "title": "Episode", "parentIndex": 4, "index": 3,
            "viewOffset": 60000, "duration": 120000, "sessionKey": "session",
        }})
        self.assertEqual(result["tvdb_id"], "73762")
        self.assertEqual(result["grandparent_tmdb_id"], "100")
        self.assertEqual(result["season_number"], 4)
        self.assertEqual(result["episode_number"], 3)
        self.assertEqual(result["progress_percent"], 0.5)
        self.assertEqual(result["progress_seconds"], 60)
        self.assertEqual(result["account_title"], "Member")

    def test_quality_uses_first_version_and_preserves_distinct_stream_languages(self):
        result = parse_plex_payload({"event": "library.new", "Metadata": {
            "type": "movie", "Guid": [{"id": "tmdb://603"}, {"id": "imdb://tt0133093"}],
            "Media": [{"width": 1920, "height": 800, "videoCodec": "h264", "audioCodec": "aac",
                       "audioChannels": 2, "Part": [{"file": "/movies/title.mkv", "Stream": [
                           {"streamType": 2, "languageTag": "en"},
                           {"streamType": 2, "languageCode": "en"},
                           {"streamType": 2, "language": "German"},
                           {"streamType": 3, "languageCode": "fr"},
                       ]}]}, {"videoResolution": "4k"}],
        }})
        self.assertEqual(result["tmdb_id"], "603")
        self.assertEqual(result["imdb_id"], "tt0133093")
        self.assertEqual(result["quality"], {
            "resolution": "1080p", "video_codec": "h264", "audio_codec": "aac", "audio_channels": "2.0",
            "file_path": "/movies/title.mkv", "audio_languages": ["en", "German"], "subtitle_languages": ["fr"],
        })
