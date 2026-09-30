"""Normalize Jellyfin/Emby, Plex, and Kodi webhook payloads.

Parsing does not read configuration, query the database, or deliver provider writes.
Handlers own authentication, media resolution, and accepted state changes.
"""
import re
from typing import Optional

from core.jellyfin import extract_quality


def parse_jellyfin_payload(payload: dict) -> dict | None:
    # Emby doesn't send NotificationType at all - its webhooks report the event
    # under "Event" (dotted, lowercase names like "playback.stop"), which the
    # handlers below already know how to match - it just wasn't being read (#160).
    notification_type = (
        payload.get("NotificationType")
        or payload.get("notificationType")
        or payload.get("Event")
        or payload.get("event", "")
    )

    # ── Nested format (raw Jellyfin API / custom HTTP destination) ────────────
    item = payload.get("Item") or payload.get("item") or {}
    session = payload.get("Session") or payload.get("session") or {}
    if item and item.get("Type") in ("Movie", "Episode"):
        play_state = session.get("PlayState", {})
        # Emby resets Session.PlayState to the next (auto-playing) episode
        # before firing the "playback.stop" event for the one that just
        # finished, so PositionTicks/RunTimeTicks there can already read 0 -
        # PlaybackInfo carries this event's own, authoritative position and
        # completion state instead (see #206).
        playback_info = payload.get("PlaybackInfo") or {}
        position_ticks = play_state.get("PositionTicks") or playback_info.get("PositionTicks", 0)
        runtime_ticks = item.get("RunTimeTicks", 0)

        media_sources = item.get("MediaSources", [])
        if media_sources:
            streams = media_sources[0].get("MediaStreams", [])
            quality = extract_quality(streams)
            quality["file_path"] = media_sources[0].get("Path")
        else:
            quality = {}

        return {
            "notification_type": notification_type,
            "jellyfin_id": item.get("Id"),
            "title": item.get("Name"),
            "year": item.get("ProductionYear"),
            # Nested episode payloads carry SeriesProviderIds (resolved above),
            # so the title+year fallback in _resolve_show_for_episode is rarely
            # reached - and item.ProductionYear here is the episode's year, not
            # the series', so it's not a safe disambiguator (#373).
            "series_year": None,
            "media_type": "movie" if item.get("Type") == "Movie" else "episode",
            "tmdb_id": item.get("ProviderIds", {}).get("Tmdb"),
            "series_tmdb_id": item.get("SeriesProviderIds", {}).get("Tmdb"),
            # Emby's native webhook notifications use this nested shape and don't
            # reliably populate SeriesProviderIds the way Jellyfin's "send all
            # properties" plugin does - without a series_name fallback here,
            # find_or_create_media_jellyfin can never resolve show linkage for
            # an Emby episode, leaving Now Playing showing the episode title
            # with no poster instead of the series (see #192).
            "series_name": item.get("SeriesName"),
            "season_number": item.get("ParentIndexNumber"),
            "episode_number": item.get("IndexNumber"),
            # Jellyfin/Emby can mux several episodes into one file and fire a single
            # webhook event for it (see #138) - IndexNumberEnd marks the span.
            "episode_number_end": item.get("IndexNumberEnd"),
            "progress_percent": round(position_ticks / runtime_ticks, 4) if runtime_ticks else 0.0,
            "progress_seconds": int(position_ticks / 10_000_000) if position_ticks else 0,
            "runtime_ticks": runtime_ticks or None,
            "is_paused": bool(play_state.get("IsPaused", False)),
            "session_id": session.get("Id") or session.get("PlaySessionId"),
            "username": session.get("UserName") or payload.get("NotificationUsername", ""),
            "quality": quality,
            # Authoritative "finished the item" signal for a stop event - trusted
            # over the computed position ratio above, which the auto-play race
            # above can zero out even though playback genuinely completed (#206).
            "played_to_completion": bool(playback_info.get("PlayedToCompletion")),
        }

    # ── Flat format (Jellyfin Webhook plugin — Generic Destination) ───────────
    item_type = payload.get("ItemType", "")
    if item_type not in ("Movie", "Episode"):
        return None

    tmdb_id = (
        payload.get("Provider_tmdb")
        or payload.get("Provider_Tmdb")
        or payload.get("Provider_tmdbid")
    )
    position_ticks = payload.get("PlaybackPositionTicks") or payload.get("PositionTicks") or 0
    runtime_ticks = payload.get("RunTimeTicks") or 0

    # SeasonNumber/EpisodeNumber are absent from the payload for movies;
    # 0 is a valid season number (specials), so don't coerce it away.
    season_num = payload.get("SeasonNumber")
    episode_num = payload.get("EpisodeNumber")

    return {
        "notification_type": notification_type,
        "jellyfin_id": payload.get("ItemId"),
        "title": payload.get("Name"),
        "year": payload.get("Year") or payload.get("ProductionYear"),
        # For an episode, the Webhook plugin's flat format sets Year to the
        # *series'* production year - the disambiguator when two shows share a
        # title and neither the episode nor the payload carries a series TMDB
        # id (#373). Movies don't need it (matched by their own tmdb_id).
        "series_year": payload.get("Year") if item_type == "Episode" else None,
        "media_type": "movie" if item_type == "Movie" else "episode",
        "tmdb_id": str(tmdb_id) if tmdb_id else None,
        "series_tmdb_id": None,  # not exposed in flat format; resolved in find_or_create
        "series_name": payload.get("SeriesName"),  # used to look up show when series_tmdb_id is absent
        "season_number": season_num,
        "episode_number": episode_num,
        # "Send all properties" (the setup this repo documents, since custom
        # templates produce invalid JSON - see README) includes this alongside
        # EpisodeNumber for a multi-episode file (see #138 follow-up).
        "episode_number_end": payload.get("EpisodeNumberEnd"),
        "progress_percent": round(position_ticks / runtime_ticks, 4) if runtime_ticks else 0.0,
        "progress_seconds": int(position_ticks / 10_000_000) if position_ticks else 0,
        "runtime_ticks": runtime_ticks or None,
        "is_paused": bool(payload.get("IsPaused", False)),
        "session_id": payload.get("PlaySessionId") or payload.get("DeviceId"),
        "username": payload.get("UserName") or payload.get("NotificationUsername", ""),
        "quality": {},
        # Only present on UserDataSaved events (manual watched/unwatched toggle,
        # rating change, favorite, etc. all raise this same notification type).
        "save_reason": payload.get("SaveReason"),
        "played": payload.get("Played"),
        # Same authoritative completion signal as the nested format's
        # PlaybackInfo.PlayedToCompletion (#206) - the flat plugin template
        # exposes it as its own top-level property.
        "played_to_completion": bool(payload.get("PlayedToCompletion")),
    }


def parse_plex_payload(payload: dict) -> dict | None:
    event = payload.get("event", "")
    metadata = payload.get("Metadata") or {}
    media_type = metadata.get("type")  # "movie" | "episode"

    if media_type not in ("movie", "episode") and event not in ("library.new", "library.update"):
        return None

    # Skip live TV streams — no stable media identity, creates junk history/sessions
    if metadata.get("librarySectionType") == "livetv" or metadata.get("live"):
        return None

    # Extract TMDB/TVDB/IMDb IDs from the Guid array: [{"id": "tmdb://12345"}, ...].
    # A legacy-agent item has no Guid array at all, only a lowercase 'guid'
    # string (e.g. 'com.plexapp.agents.thetvdb://73762/4/3') - get_guids()
    # falls back to that, and the extract_* helpers recognize both the
    # modern short prefixes and the legacy 'com.plexapp.agents.X://' ones,
    # so older/manually-matched libraries still resolve.
    import core.plex as plex_client
    guids = plex_client.get_guids(metadata)
    _tmdb_id = plex_client.extract_tmdb_id(guids)
    tmdb_id = str(_tmdb_id) if _tmdb_id else None
    tvdb_id = plex_client.extract_tvdb_id(guids)
    imdb_id = plex_client.extract_imdb_id(guids)

    # Extract series identifiers from grandparent
    grandparent_guid = metadata.get("grandparentGuid", "")
    grandparent_tmdb_id: Optional[str] = None
    grandparent_tvdb_id: Optional[str] = None
    grandparent_imdb_id: Optional[str] = None

    # Try regex on grandparentGuid — handle both modern short forms (tmdb://, tvdb://)
    # and legacy Plex agent forms (com.plexapp.agents.themoviedb://, thetvdb://)
    tmdb_match = re.search(r'(?:^tmdb|themoviedb(?:\.com)?)://(\d+)', grandparent_guid, re.IGNORECASE)
    if tmdb_match:
        grandparent_tmdb_id = tmdb_match.group(1)
    tvdb_match = re.search(r'(?:^tvdb|thetvdb(?:\.com)?)://(\d+)', grandparent_guid, re.IGNORECASE)
    if tvdb_match:
        grandparent_tvdb_id = tvdb_match.group(1)
    imdb_match = re.search(r'imdb://(tt\d+)', grandparent_guid, re.IGNORECASE)
    if imdb_match:
        grandparent_imdb_id = imdb_match.group(1)

    view_offset_ms = metadata.get("viewOffset", 0)
    duration_ms = metadata.get("duration", 0)
    progress_percent = round(view_offset_ms / duration_ms, 4) if duration_ms else 0.0
    progress_seconds = int(view_offset_ms / 1000)

    quality = plex_client.extract_quality(metadata.get("Media", []))

    return {
        "event": event,
        "title": metadata.get("title") or metadata.get("grandparentTitle", ""),
        "year": metadata.get("year"),
        "media_type": "movie" if media_type == "movie" else "episode",
        "tmdb_id": tmdb_id,
        "tvdb_id": tvdb_id,
        "imdb_id": imdb_id,
        "season_number": metadata.get("parentIndex"),
        "episode_number": metadata.get("index"),
        "rating": metadata.get("userRating"),
        "session_key": metadata.get("sessionKey") or metadata.get("ratingKey", ""),
        "progress_percent": progress_percent,
        "progress_seconds": progress_seconds,
        "duration_ms": duration_ms,
        "plex_rating_key": metadata.get("ratingKey"),
        "library_section_id": str(metadata["librarySectionID"]) if metadata.get("librarySectionID") else None,
        "library_section_type": metadata.get("librarySectionType"),
        "account_title": (payload.get("Account") or {}).get("title", ""),
        "grandparent_tmdb_id": grandparent_tmdb_id,
        "grandparent_tvdb_id": grandparent_tvdb_id,
        "grandparent_imdb_id": grandparent_imdb_id,
        "grandparent_title": metadata.get("grandparentTitle"),
        "grandparent_rating_key": str(metadata["grandparentRatingKey"]) if metadata.get("grandparentRatingKey") else None,
        "quality": quality,
    }


def parse_kodi_payload(payload: dict) -> dict | None:
    method = payload.get("method") or payload.get("event") or ""

    if method in ("Player.OnPlay", "playback_started"):
        notification_type = "play"
    elif method in ("Player.OnPause", "playback_paused"):
        notification_type = "pause"
    elif method in ("Player.OnResume", "playback_resumed"):
        notification_type = "resume"
    elif method in ("Player.OnStop", "playback_stopped"):
        notification_type = "stop"
    elif method in ("Player.OnAVChange", "playback_seeked"):
        notification_type = "progress"
    else:
        return None

    params_data = (payload.get("params") or {}).get("data") or {}
    addon_data = payload.get("data") or {}
    item = payload.get("item") or addon_data.get("item") or params_data.get("item") or {}
    player = payload.get("player") or addon_data.get("player") or params_data.get("player") or {}

    item_type = item.get("type", "")
    if item_type not in ("movie", "episode"):
        return None

    unique_ids = item.get("uniqueid") or {}
    tmdb_id = unique_ids.get("tmdb") or unique_ids.get("tmdbid") or item.get("tmdb_id")
    imdb_id = unique_ids.get("imdb") or item.get("imdbnumber")
    tvdb_id = unique_ids.get("tvdb")

    def hms_to_seconds(t: dict) -> int:
        return t.get("hours", 0) * 3600 + t.get("minutes", 0) * 60 + t.get("seconds", 0)

    time_info = player.get("time") or {}
    totaltime_info = player.get("totaltime") or {}
    position_seconds = int(payload.get("position_seconds") or hms_to_seconds(time_info))
    total_seconds = int(payload.get("total_seconds") or hms_to_seconds(totaltime_info) or item.get("runtime") or 0)
    progress_percent = round(position_seconds / total_seconds, 4) if total_seconds else 0.0

    ended = bool(params_data.get("end", False)) if method == "Player.OnStop" else False

    return {
        "notification_type": notification_type,
        "media_type": "movie" if item_type == "movie" else "episode",
        "title": item.get("title") or item.get("label") or "",
        "year": item.get("year"),
        "tmdb_id": str(tmdb_id) if tmdb_id else None,
        "imdb_id": str(imdb_id) if imdb_id else None,
        "tvdb_id": str(tvdb_id) if tvdb_id else None,
        "series_name": item.get("showtitle"),
        "season_number": item.get("season"),
        "episode_number": item.get("episode"),
        "progress_percent": progress_percent,
        "progress_seconds": position_seconds,
        "is_paused": notification_type == "pause",
        "ended": ended,
        "session_id": str(item.get("id") or payload.get("session_id") or "0"),
    }
