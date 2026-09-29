"""Stremio library state and episode formatting, without database or HTTP I/O."""

from datetime import datetime, timezone

from dateutil import parser

from core import stremio


def epoch_ms(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    try:
        parsed = parser.isoparse(str(value))
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def video_parts(video: dict) -> tuple[int, int] | None:
    try:
        return int(video["season"]), int(video["episode"])
    except (KeyError, TypeError, ValueError):
        video_id = str(video.get("id") or "")
        parts = video_id.rsplit(":", 2)
        if len(parts) != 3:
            return None
        try:
            return int(parts[1]), int(parts[2])
        except ValueError:
            return None


def default_state() -> dict:
    return {
        "lastWatched": None,
        "timeWatched": 0,
        "timeOffset": 0,
        "overallTimeWatched": 0,
        "timesWatched": 0,
        "flaggedWatched": 0,
        "duration": 0,
        "video_id": None,
        "watched": None,
        "noNotif": False,
    }


def sorted_videos(meta: dict) -> list[dict]:
    def sort_key(video: dict) -> tuple:
        parts = video_parts(video)
        season, episode = parts if parts is not None else (-1, -1)
        return season, episode, str(video.get("released") or "")

    return sorted(
        [
            video
            for video in (meta.get("videos") or [])
            if isinstance(video, dict) and video.get("id")
        ],
        key=sort_key,
    )


def new_library_item(
    record: dict,
    now: str,
    *,
    in_library: bool = True,
) -> dict:
    return {
        "_id": record["content_id"],
        "name": record.get("name") or record.get("title") or record["content_id"],
        "type": record["content_type"],
        "poster": record.get("poster"),
        "posterShape": record.get("poster_shape") or "poster",
        "removed": not in_library,
        "temp": not in_library,
        "_ctime": now,
        "_mtime": now,
        "state": default_state(),
        "behaviorHints": {},
    }


def same_item(left: dict, right: dict) -> bool:
    return (
        {key: value for key, value in left.items() if key != "_mtime"}
        == {key: value for key, value in right.items() if key != "_mtime"}
    )


def with_watch_state(item: dict, record: dict, series_metadata: dict) -> dict:
    """Return updated watch state while preserving the remote item and other plays."""
    is_watched = bool(record.get("watched", True))
    candidate = dict(item)
    state = {**default_state(), **(candidate.get("state") or {})}
    watched_at_ms = record.get("watched_at")
    watched_at = (
        datetime.fromtimestamp(
            int(watched_at_ms) / 1000,
            tz=timezone.utc,
        ).isoformat().replace("+00:00", "Z")
        if watched_at_ms is not None
        else None
    )
    season = record.get("season")
    episode = record.get("episode")
    if record.get("content_type") == "movie" or season is None or episode is None:
        state["timesWatched"] = (
            max(1, int(state.get("timesWatched") or 0))
            if is_watched
            else 0
        )
        if not is_watched:
            state["flaggedWatched"] = 0
        if is_watched and watched_at is not None:
            state["lastWatched"] = watched_at
    else:
        videos = sorted_videos(series_metadata)
        video_ids = [str(video["id"]) for video in videos]
        watched_ids = stremio.decode_watched_bitfield(
            state.get("watched"),
            video_ids,
        )
        matching_video = next(
            (
                video
                for video in videos
                if video_parts(video)
                == (int(season), int(episode))
            ),
            None,
        )
        if matching_video:
            video_id = str(matching_video["id"])
            if is_watched:
                watched_ids.add(video_id)
            else:
                watched_ids.discard(video_id)
            state["watched"] = stremio.encode_watched_bitfield(
                watched_ids,
                video_ids,
            )
            if is_watched and watched_at is not None:
                state["lastWatched"] = watched_at
    candidate["state"] = state
    return candidate


def with_progress_state(
    item: dict, record: dict, series_metadata: dict, *, in_library: bool,
) -> dict:
    """Return resume state while preserving watch history and temporary membership."""
    content_id = str(record["content_id"])
    candidate = dict(item)
    if candidate.get("removed") and not in_library:
        candidate["temp"] = True
    candidate["removed"] = False
    state = {**default_state(), **(candidate.get("state") or {})}
    state["timeOffset"] = int(record["position"])
    state["duration"] = int(record["duration"])
    if record.get("content_type") == "movie":
        state["video_id"] = content_id
    else:
        videos = sorted_videos(series_metadata)
        matching_video = next(
            (
                video
                for video in videos
                if video_parts(video)
                == (int(record["season"]), int(record["episode"]))
            ),
            None,
        )
        state["video_id"] = (
            str(matching_video["id"])
            if matching_video
            else str(record.get("video_id") or "")
        )
    last_watched_ms = record.get("last_watched")
    if last_watched_ms:
        state["lastWatched"] = datetime.fromtimestamp(
            int(last_watched_ms) / 1000,
            tz=timezone.utc,
        ).isoformat().replace("+00:00", "Z")
    candidate["state"] = state
    return candidate
