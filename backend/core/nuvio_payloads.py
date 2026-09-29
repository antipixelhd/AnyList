"""Nuvio wire payloads and comparisons, without database queries or provider I/O."""

from datetime import datetime, timezone

from core import nuvio
from models.base import MediaType
from models.connections import MediaServerConnection
from models.media import Media
from models.playback_progress import PlaybackProgress
from models.show import Show


def profile_id(conn: MediaServerConnection) -> int:
    return nuvio.parse_profile_id(conn.server_user_id)


def imdb_id(entity: Media | Show | None) -> str | None:
    if entity is None:
        return None
    data = entity.tmdb_data or {}
    value = data.get("imdb_id") or (data.get("external_ids") or {}).get("imdb_id") or getattr(entity, "imdb_id", None)
    imdb_id = str(value or "").strip()
    return imdb_id if imdb_id.startswith("tt") and imdb_id[2:].isdigit() else None


def library_content_id(media: Media, show: Show | None = None) -> str | None:
    return imdb_id(show or media)


def _genres(entity: Media | Show) -> list[str]:
    raw_genres = (entity.tmdb_data or {}).get("genres") or []
    genres: list[str] = []
    for genre in raw_genres:
        name = genre.get("name") if isinstance(genre, dict) else genre
        if name:
            genres.append(str(name))
    return genres


def library_item(
    media: Media,
    added_at: datetime,
    show: Show | None = None,
) -> dict | None:
    content_id = library_content_id(media, show)
    if not content_id:
        return None
    entity: Media | Show = show or media
    added = added_at if added_at.tzinfo else added_at.replace(tzinfo=timezone.utc)
    release_date = (
        entity.first_air_date
        if isinstance(entity, Show)
        else entity.release_date
    )
    return {
        "content_id": content_id,
        "content_type": "movie" if media.media_type == MediaType.movie else "series",
        "name": media.title if media.media_type == MediaType.series else entity.title,
        "poster": entity.poster_path,
        "poster_shape": "poster",
        "background": entity.backdrop_path,
        "description": entity.overview,
        "release_info": str(release_date or "")[:4] or None,
        "imdb_rating": entity.tmdb_rating,
        "genres": _genres(entity),
        "added_at": int(added.timestamp() * 1000),
    }


def watched_item(
    media: Media,
    watched_at: datetime | None,
    show: Show | None = None,
    *,
    include_unknown_date: bool = False,
) -> dict | None:
    if watched_at is None:
        if not include_unknown_date:
            # Callers that project only dated plays can omit this record.
            return None
        watched_epoch_ms = None
    else:
        if watched_at.tzinfo is None:
            watched_at = watched_at.replace(tzinfo=timezone.utc)
        watched_epoch_ms = int(watched_at.timestamp() * 1000)

    if media.media_type == MediaType.movie and (content_id := imdb_id(media)):
        return {
            "content_id": content_id,
            "content_type": "movie",
            "title": media.title,
            "watched_at": watched_epoch_ms,
        }
    if (
        media.media_type == MediaType.episode
        and (content_id := imdb_id(show))
        and media.season_number is not None
        and media.episode_number is not None
    ):
        return {
            "content_id": content_id,
            "content_type": "series",
            "title": media.title,
            "season": media.season_number,
            "episode": media.episode_number,
            "watched_at": watched_epoch_ms,
        }
    if media.media_type == MediaType.series and (content_id := imdb_id(media)):
        return {
            "content_id": content_id,
            "content_type": "series",
            "title": media.title,
            "watched_at": watched_epoch_ms,
        }
    return None


def progress_item(
    progress: PlaybackProgress,
    media: Media,
    show: Show | None = None,
    *,
    preserve_fractional_seconds: bool = False,
) -> dict | None:
    try:
        raw_seconds = float(progress.progress_seconds)
        progress_seconds = max(0.0, raw_seconds) if preserve_fractional_seconds else max(0, int(raw_seconds))
        progress_percent = float(progress.progress_percent)
    except (TypeError, ValueError):
        return None
    if progress_seconds <= 0 or progress_percent <= 0:
        return None

    position_ms = round(progress_seconds * 1000) if preserve_fractional_seconds else progress_seconds * 1000
    if media.runtime and media.runtime > 0:
        duration_ms = media.runtime * 60_000
    else:
        duration_ms = round(position_ms / max(min(progress_percent, 1.0), 0.01))
    duration_ms = max(position_ms, duration_ms)

    updated_at = progress.updated_at
    last_watched = None
    if updated_at is not None:
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        last_watched = int(updated_at.timestamp() * 1000)

    if media.media_type == MediaType.movie and (content_id := imdb_id(media)):
        item = {
            "content_id": content_id,
            "content_type": "movie",
            "video_id": content_id,
            "position": position_ms,
            "duration": duration_ms,
            "progress_key": content_id,
        }
        if last_watched is not None:
            item["last_watched"] = last_watched
        return item
    if (
        media.media_type == MediaType.episode
        and (content_id := imdb_id(show))
        and media.season_number is not None
        and media.episode_number is not None
    ):
        item = {
            "content_id": content_id,
            "content_type": "series",
            "video_id": f"{content_id}:{media.season_number}:{media.episode_number}",
            "season": media.season_number,
            "episode": media.episode_number,
            "position": position_ms,
            "duration": duration_ms,
            "progress_key": f"{content_id}_s{media.season_number}e{media.episode_number}",
        }
        if last_watched is not None:
            item["last_watched"] = last_watched
        return item
    return None


def progress_matches(left: dict, right: dict) -> bool:
    return all(left.get(key) == right.get(key) for key in (
        "content_id", "content_type", "position", "duration", "season", "episode", "progress_key",
    ))


def obsolete_progress_keys(remote_rows: list[dict], desired_items: list[dict]) -> list[str]:
    """A full push keeps one active resume per locally Watching title."""
    desired = {str(item["content_id"]): str(item["progress_key"]) for item in desired_items}
    obsolete = []
    for row in remote_rows:
        content_id = str(row.get("content_id") or "")
        if content_id not in desired or str(row.get("progress_key") or "") == desired[content_id]:
            continue
        if not row.get("progress_key"):
            raise nuvio.NuvioAPIError("Nuvio progress row has no progress_key; refusing an unsafe clear")
        obsolete.append(str(row["progress_key"]))
    return list(dict.fromkeys(obsolete))


def _valid_imdb_id(value: object) -> str | None:
    candidate = str(value or "").strip()
    return candidate if candidate.startswith("tt") and candidate[2:].isdigit() else None


def content_id_for_baseline(baseline, media: Media, show: Show | None) -> str | None:
    entity = show if media.media_type.value == "episode" else media
    target_tmdb = getattr(entity, "tmdb_id", None)
    mappings = (baseline.snapshot or {}).get("mappings", {}) if baseline else {}
    matching = [str(key) for key, value in mappings.items() if target_tmdb is not None and str(value) == str(target_tmdb)]

    data = getattr(entity, "tmdb_data", None) or {}
    external = data.get("external_ids") if isinstance(data, dict) else {}
    direct = _valid_imdb_id(
        getattr(entity, "imdb_id", None)
        or (data.get("imdb_id") if isinstance(data, dict) else None)
        or (external.get("imdb_id") if isinstance(external, dict) else None)
    )
    if direct and direct in matching:
        return direct
    imdb_mapping = next((key for key in matching if _valid_imdb_id(key)), None)
    return imdb_mapping or (matching[0] if matching else None) or direct


def remap_payload(item: dict, media: Media, show: Show | None, baseline) -> dict:
    """Use the content ID already observed for this title on this Nuvio profile."""
    if baseline is None:
        return item
    content_id = content_id_for_baseline(baseline, media, show)
    if not content_id or content_id == item.get("content_id"):
        return item
    item = {**item, "content_id": content_id}
    if "progress_key" in item:
        if item.get("season") is not None and item.get("episode") is not None:
            item["video_id"] = f"{content_id}:{item['season']}:{item['episode']}"
            item["progress_key"] = f"{content_id}_s{item['season']}e{item['episode']}"
        else:
            item["video_id"] = content_id
            item["progress_key"] = content_id
    return item
