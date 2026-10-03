"""Shared display projections for tracked titles, entries, and activity.

These functions read already-loaded values; they do not query or mutate state.
"""

from models.media import Media
from core.tracking_rules import effective_score


def is_anime(media: Media) -> bool:
    data = media.tmdb_data or {}
    genres = {
        str(g if isinstance(g, str) else g.get("name", "")).lower()
        for g in data.get("genres", [])
        if isinstance(g, (str, dict))
    }
    countries = {
        str(value).upper() for value in data.get("origin_country", []) if value
    }
    countries.update(
        str(value.get("iso_3166_1", "")).upper()
        for value in data.get("production_countries", [])
        if isinstance(value, dict)
    )
    return "animation" in genres and (
        data.get("original_language") == "ja" or "JP" in countries
    )


def media_data(media):
    data = media.tmdb_data or {}
    regular_seasons = [
        season
        for season in data.get("seasons", [])
        if isinstance(season, dict) and (season.get("season_number") or 0) > 0
    ]
    return {
        "id": media.id,
        "title": media.title,
        "type": media.media_type.value,
        "poster": media.poster_path,
        "backdrop": media.backdrop_path,
        "overview": media.overview,
        "year": (media.release_date or "")[:4],
        "release_date": media.release_date,
        "original_title": media.original_title,
        "runtime": media.runtime or data.get("runtime"),
        "tmdb_score": media.tmdb_rating,
        "imdb_score": media.imdb_rating,
        "rt_critic_score": media.rt_critic_score,
        "rt_audience_score": media.rt_audience_score,
        "external_scores_updated_at": media.external_scores_updated_at,
        "tagline": media.tagline or data.get("tagline"),
        "adult": media.adult,
        "original_language": data.get("original_language"),
        "networks": [
            n for n in data.get("networks", []) if isinstance(n, dict) and n.get("name")
        ],
        "studios": [
            c
            for c in data.get("production_companies", [])
            if isinstance(c, dict) and c.get("name")
        ],
        "creators": [
            c
            for c in data.get("created_by", [])
            if isinstance(c, dict) and c.get("name")
        ],
        "episode_runtime": next(
            (value for value in data.get("episode_run_time", []) if value), None
        ),
        "season_count": data.get("number_of_seasons") or len(regular_seasons) or None,
        "episode_count": data.get("number_of_episodes"),
        "last_air_date": data.get("last_air_date"),
        "release_status": media.status or data.get("status"),
        "genres": [
            g if isinstance(g, str) else g["name"]
            for g in data.get("genres", [])
            if isinstance(g, str) or (isinstance(g, dict) and g.get("name"))
        ],
        "tmdb_id": media.tmdb_id,
        "tvdb_id": media.tvdb_id,
        "imdb_id": media.imdb_id,
        "is_anime": is_anime(media),
    }


def entry_data(entry, media, owner=False):
    result = {
        **media_data(media),
        "status": entry.status,
        "rating_mode": entry.rating_mode,
        "score": effective_score(
            entry.rating_mode, entry.manual_score, entry.season_scores
        ),
        "season_scores": entry.season_scores,
        "progress": entry.progress,
        "favorite": entry.favorite,
        "start_date": entry.start_date,
        "finish_date": entry.finish_date,
        "rewatch_count": entry.rewatch_count,
        "updated_at": entry.updated_at,
        "notes": entry.notes,
    }
    if owner:
        result.update(manual_score=entry.manual_score)
    return result


def activity_data(rows, *, include_user=False, limit=12):
    """Present fixed-window cards, retaining UTC-day grouping for legacy rows."""
    from core.activity import has_activity_event, merge_activity_payload

    grouped = {}
    for row in rows:
        activity, media = row[0], row[1]
        user = row[2] if include_user else None
        profile = row[3] if include_user else None
        key = (
            user.id if user else activity.user_id,
            media.id,
            (activity.payload or {}).get("window_key")
            or (activity.payload or {}).get("window_started_at")
            or activity.created_at.date().isoformat(),
        )
        if key not in grouped:
            grouped[key] = {
                "key": f"{key[0]}:{key[1]}:{key[2]}",
                **(
                    {
                        "user_id": user.id,
                        "username": user.username,
                        "display_name": user.username,
                        "has_avatar": bool(profile.avatar_path),
                    }
                    if user
                    else {}
                ),
                "status": activity.status,
                "score": activity.score,
                "payload": dict(activity.payload or {}),
                "created_at": activity.created_at,
                "media": media_data(media),
            }
        else:
            grouped[key]["payload"] = merge_activity_payload(
                grouped[key]["payload"], activity.payload
            )
    for activity in grouped.values():
        if activity["score"] is None:
            # A known previous score means the rating was cleared. A legacy row
            # with no old or new score cannot establish that an edit occurred.
            activity["payload"]["rating_first"] = False
            if activity["payload"].get("previous_score") is None:
                activity["payload"]["rating_changed"] = False
    return [
        item
        for item in grouped.values()
        if has_activity_event(item["status"], item["payload"])
    ][:limit]
