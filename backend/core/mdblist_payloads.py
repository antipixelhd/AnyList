"""MDBList wire formatting and nested show/season/episode payload merging."""
from datetime import datetime, timezone
from typing import Any

from core.enrichment import is_unmapped_tvdb_episode
from models.base import MediaType
from models.media import Media
from models.show import Show


def iso_utc(value: datetime | None) -> str:
    value = value or datetime.utcnow()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def empty_payload() -> dict[str, list[dict[str, Any]]]:
    return {"movies": [], "shows": [], "seasons": [], "episodes": []}


def _merge_seasons(existing_seasons: list[dict[str, Any]], new_seasons: list[dict[str, Any]]) -> None:
    """Merge season objects by number in-place, and episodes within each season by number.

    MDBList expects at most one season object per number per show, with all of
    that season's rated/watched episodes nested underneath as a single list.
    """
    by_number = {s["number"]: s for s in existing_seasons if "number" in s}
    for season in new_seasons:
        number = season.get("number")
        target = by_number.get(number)
        if target is None:
            target = {"number": number}
            existing_seasons.append(target)
            by_number[number] = target
        for key, value in season.items():
            if key == "episodes":
                existing_episodes = target.setdefault("episodes", [])
                by_ep_number = {e["number"]: e for e in existing_episodes if "number" in e}
                for episode in value:
                    ep_number = episode.get("number")
                    ep_target = by_ep_number.get(ep_number)
                    if ep_target is None:
                        existing_episodes.append(dict(episode))
                        by_ep_number[ep_number] = existing_episodes[-1]
                    else:
                        ep_target.update(episode)
            elif key != "number":
                target[key] = value


def merge_show_entries(shows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Combine payload entries that share a show tmdb id.

    payload_item() builds one entry per season/episode, so a batch touching
    several seasons or episodes of the same show would otherwise produce
    multiple entries with identical ids.tmdb — MDBList's API expects one show
    object per tmdb id with all of its rated/watched seasons and episodes
    nested underneath.
    """
    merged: dict[int, dict[str, Any]] = {}
    result: list[dict[str, Any]] = []
    for item in shows:
        tmdb_id = (item.get("ids") or {}).get("tmdb")
        if tmdb_id is None:
            result.append(item)
            continue
        existing = merged.get(tmdb_id)
        if existing is None:
            existing = {"ids": item["ids"]}
            merged[tmdb_id] = existing
            result.append(existing)
        for key, value in item.items():
            if key == "seasons":
                _merge_seasons(existing.setdefault("seasons", []), value)
            elif key != "ids":
                existing[key] = value
    return result


def payload_item(
    media: Media,
    *,
    show: Show | None = None,
    watched_at: datetime | None = None,
    rating: float | None = None,
    rated_at: datetime | None = None,
    season_number: int | None = None,
    collected_at: datetime | None = None,
) -> tuple[str, dict[str, Any]] | None:
    # Episodes have no meaningful standalone identity on MDBList — they must be
    # addressed via their parent show's ids plus season/episode numbers, nested
    # under "shows". Sending the episode's own TMDB id (a completely different
    # ID namespace from shows/movies) resolves to an unrelated, wrong item.
    if media.media_type == MediaType.episode:
        if not show or not show.tmdb_id:
            return None
        if media.season_number is None or media.episode_number is None:
            return None
        # Episode enriched from TVDB, no real TMDB counterpart (see #101) —
        # its season/episode numbers are raw TVDB numbers, not safe to send
        # as if they were positions under show.tmdb_id.
        if is_unmapped_tvdb_episode(media):
            return None
        episode: dict[str, Any] = {"number": media.episode_number}
        if watched_at is not None:
            episode["watched_at"] = iso_utc(watched_at)
        if rating is not None:
            episode["rating"] = float(rating)
            episode["rated_at"] = iso_utc(rated_at or datetime.now(timezone.utc))
        if collected_at is not None:
            episode["collected_at"] = iso_utc(collected_at)
        return (
            "shows",
            {
                "ids": {"tmdb": show.tmdb_id},
                "seasons": [{"number": media.season_number, "episodes": [episode]}],
            },
        )

    if not media.tmdb_id:
        return None

    if season_number is not None:
        if media.media_type != MediaType.series:
            return None
        season: dict[str, Any] = {"number": season_number}
        if rating is not None:
            season["rating"] = float(rating)
            season["rated_at"] = iso_utc(rated_at or datetime.now(timezone.utc))
        return (
            "shows",
            {
                "ids": {"tmdb": media.tmdb_id},
                "seasons": [season],
            },
        )

    item: dict[str, Any] = {"ids": {"tmdb": media.tmdb_id}}

    if media.media_type == MediaType.movie:
        kind = "movies"
    elif media.media_type == MediaType.series:
        kind = "shows"
    else:
        return None

    if watched_at is not None:
        item["watched_at"] = iso_utc(watched_at)
    if rating is not None:
        item["rating"] = float(rating)
        item["rated_at"] = iso_utc(rated_at or datetime.now(timezone.utc))
    if collected_at is not None:
        item["collected_at"] = iso_utc(collected_at)
    return kind, item


def rating_removal_item(
    media: Media,
    season_number: int | None = None,
    show: Show | None = None,
) -> tuple[str, dict[str, Any]] | None:
    """Build an MDBList season removal without clearing its show rating."""
    if season_number is not None:
        if not media.tmdb_id or media.media_type != MediaType.series:
            return None
        return (
            "shows",
            {
                "ids": {"tmdb": media.tmdb_id},
                "seasons": [{"number": season_number}],
            },
        )
    return payload_item(media, show=show)
