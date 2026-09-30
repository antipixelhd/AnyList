"""Parse ARVIO watch timestamps and episode identities without database or HTTP I/O."""

import json
import re
from datetime import datetime, timezone
from typing import Any

from dateutil import parser

_EPISODE_ID = re.compile(
    r"(?:tv:|series:|tmdb:)?(\d+)[:_\-\s]+(?:s|season)?(\d+)[:_\-\s]+(?:e|ep|episode)?(\d+)",
    re.IGNORECASE,
)


def parse_timestamp(ts: Any) -> datetime | None:
    if not ts:
        return None
    if isinstance(ts, (int, float)):
        if ts > 1e11:
            ts = ts / 1000.0
        try:
            return datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(ts, str):
        try:
            dt = parser.isoparse(ts)
            if dt.tzinfo:
                dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt
        except Exception:
            try:
                val = float(ts)
                return parse_timestamp(val)
            except Exception:
                return None
    return None


def parse_episode_info(item: dict[str, Any] | str | int) -> tuple[int, int, int] | None:
    """Extract (show_tmdb_id, season, episode) from various ARVIO item representations."""
    if isinstance(item, (int, str)):
        item_str = str(item).strip()
        match = _EPISODE_ID.search(item_str)
        if match:
            try:
                return int(match.group(1)), int(match.group(2)), int(match.group(3))
            except ValueError:
                pass
        try:
            parsed = json.loads(item_str)
            if isinstance(parsed, dict):
                item = parsed
        except json.JSONDecodeError:
            return None

    if isinstance(item, dict):
        for field in ("id", "mediaId", "episodeId", "item_id", "itemId"):
            val = item.get(field)
            if isinstance(val, str):
                match = _EPISODE_ID.search(val)
                if match:
                    try:
                        return int(match.group(1)), int(match.group(2)), int(match.group(3))
                    except ValueError:
                        pass

        show_tmdb_id_raw = (
            item.get("showTmdbId")
            or item.get("show_tmdb_id")
            or item.get("showId")
            or item.get("seriesTmdbId")
            or item.get("series_tmdb_id")
            or item.get("seriesId")
            or item.get("series_id")
            or item.get("tmdbId")
            or item.get("tmdb_id")
        )
        season_raw = next((
            item[field] for field in ("season", "seasonNumber", "season_number", "seasonIndex", "s")
            if item.get(field) or item.get(field) == 0
        ), None)
        episode_raw = (
            item.get("episode")
            or item.get("episodeNumber")
            or item.get("episode_number")
            or item.get("episodeIndex")
            or item.get("e")
        )

        if show_tmdb_id_raw is not None and season_raw is not None and episode_raw is not None:
            try:
                return int(show_tmdb_id_raw), int(season_raw), int(episode_raw)
            except (TypeError, ValueError):
                pass

    return None
