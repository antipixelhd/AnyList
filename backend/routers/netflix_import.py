"""Private, resumable Netflix viewing-history import drafts."""

from __future__ import annotations

import hashlib
import inspect
import csv
import io
import logging
import re
import unicodedata
import uuid
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core import tmdb
from core.enrichment import create_media_safely
from core.status_provenance import mark_status_change
from core.tracking_rules import effective_score
from db import engine, get_db
from dependencies import get_current_user
from models import Media, NetflixImportSession, Rating, Show, User, UserSettings, WatchEvent
from models.events import WatchEvent as WatchEventModel
from models.base import MediaType
from models.episode_order import EpisodeOrderMapping
from models.global_settings import GlobalSettings
from models.tracking import TrackedEntry, TrackingDeletion

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
DRAFT_DAYS = 7
_running: set[str] = set()
_committing: set[str] = set()
_cancelled: set[str] = set()


class ImportCancelled(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def _tmdb_key(db: AsyncSession, user_id: int) -> str | None:
    settings = (await db.execute(select(UserSettings).where(UserSettings.user_id == user_id))).scalar_one_or_none()
    if settings and settings.tmdb_api_key:
        return settings.tmdb_api_key
    global_settings = (await db.execute(select(GlobalSettings).where(GlobalSettings.id == 1))).scalar_one_or_none()
    return global_settings.tmdb_api_key if global_settings else None


def _to_date(value: Any) -> date | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _as_list(value: Any) -> list:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _dates_for(source: dict) -> list[str]:
    values = source.get("dates") or source.get("source_dates") or source.get("watched_dates")
    if not values:
        values = source.get("date") or source.get("watched_at")
    output = []
    for value in _as_list(values):
        parsed = _to_date(value)
        if parsed:
            text = parsed.isoformat()
            if text not in output:
                output.append(text)
    return sorted(output)


def _item_id(kind: str, title: str) -> str:
    normal = unicodedata.normalize("NFKC", title).casefold().strip()
    normal = re.sub(r"\s+", " ", normal)
    return f"{kind}_{hashlib.sha256(normal.encode('utf-8')).hexdigest()[:20]}"


def _candidate(group: dict, kind: str) -> dict | None:
    nested = group.get("candidate") or group.get("match")
    base = nested if isinstance(nested, dict) else group
    tmdb_id = base.get("tmdb_id") or base.get("id")
    if not tmdb_id:
        return None
    try:
        tmdb_id = int(tmdb_id)
    except (TypeError, ValueError):
        # Unmatched import groups have a stable source ID, not a TMDB ID.
        return None
    if tmdb_id <= 0:
        return None
    candidate_type = base.get("media_type") or base.get("type") or kind
    if candidate_type in ("tv", "series", "show"):
        candidate_type = "show"
    elif candidate_type == "movie":
        candidate_type = "movie"
    return {
        **(base.get("metadata") or base.get("details") or {}),
        **base,
        "media_type": candidate_type,
        "tmdb_id": tmdb_id,
        "title": base.get("title") or base.get("name") or group.get("candidate_title") or group.get("source_title") or group.get("title"),
        "year": base.get("year"),
        "poster_path": base.get("poster_path") or base.get("poster"),
    }


def _normalise_episode(ep: dict) -> dict:
    season = ep.get("season_number", ep.get("season", ep.get("seasonNumber")))
    number = ep.get("episode_number", ep.get("episode", ep.get("number", ep.get("episodeNumber"))))
    raw_rows = ep.get("source_rows", ep.get("rows", ep.get("row_count", 1)))
    row_numbers = raw_rows if isinstance(raw_rows, list) else ep.get("source_row_numbers", [])
    try:
        row_count = int(ep.get("row_count", len(row_numbers) if row_numbers else raw_rows) or 0)
    except (TypeError, ValueError):
        row_count = len(row_numbers) if row_numbers else 1
    matched = bool(ep.get("matched", ep.get("tmdb_episode_id") is not None or ep.get("episode_number", ep.get("number")) is not None))
    return {
        **ep,
        "season_number": int(season) if season is not None else None,
        "episode_number": int(number) if number is not None else None,
        "title": ep.get("title") or ep.get("name") or ep.get("episode_title"),
        "release_date": ep.get("release_date") or ep.get("air_date"),
        "tmdb_episode_id": ep.get("tmdb_episode_id") or ep.get("tmdb_id") or (ep.get("id") if ep.get("media_type") == "episode" else None),
        "source_title": ep.get("source_title"),
        "source_episode_title": ep.get("source_episode_title") or ep.get("source_title_episode") or ep.get("source_episode") or ep.get("episode_title"),
        "dates": _dates_for(ep),
        "source_rows": row_count,
        "source_row_numbers": row_numbers,
        "matched": matched,
        "confidence": ep.get("confidence") or ("high" if matched else "low"),
        "reason": ep.get("reason") or ep.get("evidence"),
        "decision": ep.get("decision") or {"action": None},
    }


def _resolve_item_episodes(item: dict) -> None:
    if item.get("kind") != "show" or not item.get("match", {}).get("candidate"):
        return
    episodes = item.get("episodes") or []
    if not episodes or all(episode.get("resolution") for episode in episodes):
        return
    original_latest = max((
        (int(episode["season_number"]), int(episode["episode_number"]))
        for episode in episodes if episode.get("matched") and episode.get("season_number") and episode.get("episode_number")
    ), default=None)
    from core.netflix_import import resolve_netflix_episodes
    resolve_netflix_episodes(episodes, item.get("catalog_episodes") or [])
    for episode in episodes:
        if (episode.get("decision") or {}).get("action") != "remap":
            episode["decision"] = {"action": None}
    represented: dict[int, set[int]] = {}
    for episode in episodes:
        if episode.get("matched") and episode.get("season_number") and episode.get("episode_number"):
            represented.setdefault(int(episode["season_number"]), set()).add(int(episode["episode_number"]))
    for season in item.get("seasons") or []:
        season["represented"] = len(represented.get(season["season_number"], set()))
    latest = max((
        (int(episode["season_number"]), int(episode["episode_number"]))
        for episode in episodes if episode.get("matched") and episode.get("season_number") and episode.get("episode_number")
    ), default=None)
    current_endpoint = (item.get("outcome", {}).get("latest_season"), item.get("outcome", {}).get("latest_episode"))
    if latest and not item.get("outcome", {}).get("endpoint_overridden") and (
        current_endpoint == (None, None) or current_endpoint == original_latest
    ):
        item["outcome"]["latest_season"], item["outcome"]["latest_episode"] = latest


def _default_show_status(seasons: list[dict]) -> str:
    if seasons and all(
        season.get("catalogue_complete") and season.get("total_released") is not None
        and season.get("represented", 0) >= season["total_released"] for season in seasons
    ):
        return "completed"
    if all(int(season.get("represented") or 0) <= 2 for season in seasons):
        return "skip"
    return "partial"


def _existing_state(media: Media | None, tracked: TrackedEntry | None) -> dict:
    return {
        "catalog": media is not None,
        "tracked": tracked is not None,
        "rated": tracked is not None and effective_score(tracked.rating_mode, tracked.manual_score, tracked.season_scores or {}) is not None,
        "status": tracked.status if tracked else None,
        "progress": tracked.progress if tracked else 0,
    }


def _resolve_draft_items(items: list[dict]) -> None:
    for item in items:
        _resolve_item_episodes(item)
        if item.get("kind") == "show" and not item.get("is_anime") and not item.get("outcome", {}).get("status_overridden"):
            item["outcome"]["status"] = _default_show_status(item.get("seasons") or [])


def _normalise_groups(prepared: dict) -> list[dict]:
    result: list[dict] = []
    for collection_key, kind in (("movies", "movie"), ("shows", "show"), ("unmatched", "show")):
        for raw in prepared.get(collection_key, []) or []:
            if not isinstance(raw, dict):
                continue
            source_title = raw.get("source_title") or raw.get("title") or raw.get("name") or "Unknown title"
            item_kind = raw.get("kind") or raw.get("media_type") or kind
            item_kind = "movie" if item_kind in ("movie", "movies") else "show"
            candidate = _candidate(raw, item_kind)
            candidates = raw.get("candidates") or raw.get("suggestions") or []
            normalized_candidates = []
            for c in candidates:
                if isinstance(c, dict):
                    normalized = _candidate({"candidate": c}, item_kind)
                    if normalized:
                        normalized_candidates.append(normalized)
            if candidate and not normalized_candidates:
                normalized_candidates.append(candidate)

            raw_episodes = raw.get("episodes") or raw.get("matched_episodes") or raw.get("source_episodes") or []
            episodes = [_normalise_episode(ep) for ep in raw_episodes if isinstance(ep, dict)]
            seasons = []
            for season in raw.get("seasons") or raw.get("season_counts") or []:
                if not isinstance(season, dict):
                    continue
                sn = season.get("season_number", season.get("season", season.get("number")))
                if sn is None:
                    continue
                represented = season.get("represented", season.get("represented_episodes", season.get("episode_count_represented")))
                if represented is None:
                    represented = len({ep["episode_number"] for ep in episodes if ep.get("season_number") == int(sn) and ep.get("matched") and ep.get("episode_number")})
                total = season.get("total_released", season.get("total_released_episodes", season.get("released_episode_count", season.get("episode_count"))))
                seasons.append({
                    "season_number": int(sn),
                    "represented": int(represented or 0),
                    "total_released": int(total) if total is not None else None,
                    "catalogue_complete": bool(season.get("catalogue_complete", False)),
                    "name": season.get("name"),
                })
            for ep in episodes:
                sn = ep.get("season_number")
                if sn is not None and sn > 0 and not any(s["season_number"] == sn for s in seasons):
                    represented = len({x["episode_number"] for x in episodes if x.get("season_number") == sn and x.get("matched") and x.get("episode_number")})
                    seasons.append({"season_number": sn, "represented": represented, "total_released": None, "catalogue_complete": False})
            seasons.sort(key=lambda s: s["season_number"])
            source_dates = _dates_for(raw) or sorted({d for ep in episodes for d in ep["dates"]})
            source_rows = raw.get("source_rows", raw.get("rows", raw.get("row_count")))
            if source_rows is None:
                source_rows = sum(ep["source_rows"] for ep in episodes) if item_kind == "show" else len(source_dates)
            status = raw.get("status") or ("matched" if candidate and raw.get("confidence") == "high" else "review")
            confidence = raw.get("confidence") or ("high" if status == "matched" else "low")
            match_state = "matched" if status == "matched" and candidate else "review" if candidate else "unmatched"
            latest = max((ep for ep in episodes if ep.get("matched") and ep.get("season_number", 0) > 0 and ep.get("episode_number")), key=lambda ep: (ep["season_number"], ep["episode_number"]), default=None)
            default_progress = _default_show_status(seasons)
            is_anime = bool(raw.get("is_anime"))
            item_id = _item_id(item_kind, source_title)
            for episode in episodes:
                source_title_for_ep = episode.get("source_title") or f"{source_title}: {episode.get('season_label') or ''}: {episode.get('source_episode_title') or episode.get('title') or ''}"
                source_digest = hashlib.sha256(unicodedata.normalize("NFKC", source_title_for_ep).casefold().encode("utf-8")).hexdigest()[:18]
                episode["source_id"] = f"ep_{source_digest}"
                if episode.get("matched") and not episode["dates"]:
                    episode["dates"] = _dates_for({"watched_dates": episode.get("watched_dates")})
                if episode.get("reason") is None and episode.get("evidence"):
                    episode["reason"] = episode["evidence"]
            result.append({
                "id": item_id,
                "kind": item_kind,
                "is_anime": is_anime,
                "source_title": source_title,
                "source_dates": source_dates,
                "source_rows": int(source_rows or 0),
                "match": {
                    "state": match_state,
                    "confidence": confidence,
                    "reason": raw.get("reason") or raw.get("match_reason"),
                    "candidate": candidate,
                    "candidates": normalized_candidates,
                },
                "episodes": episodes,
                "catalog_episodes": [_normalise_episode(ep) for ep in (raw.get("catalog_episodes") or raw.get("released_episodes") or []) if isinstance(ep, dict)],
                "seasons": seasons,
                "decision": {"action": "skip" if is_anime else "confirm" if match_state == "matched" else None},
                "outcome": {
                    "status": "skip" if is_anime else "completed" if item_kind == "movie" else default_progress,
                    "latest_season": latest.get("season_number") if latest else None,
                    "latest_episode": latest.get("episode_number") if latest else None,
                    "tracking_status": "watching",
                    "status_overridden": False,
                },
                "existing": _existing_state(None, None),
                "source_evidence": raw.get("source_evidence") or {},
            })
    seen: dict[str, int] = {}
    for item in result:
        identity = item["id"]
        seen[identity] = seen.get(identity, 0) + 1
        if seen[identity] > 1:
            item["id"] = f"{identity}_{seen[identity]}"
    _resolve_draft_items(result)
    return result


async def _prepare_remapped_item(item: dict, media_type: str, tmdb_id: int, api_key: str, language: str | None) -> dict:
    """Fetch a selected TMDB identity and rebuild episode evidence for it.

    The client sends only an ID and type from catalog search. All metadata and
    episode positions are reloaded from TMDB so a stale candidate cannot carry
    another show's episode IDs into the final transaction.
    """
    from core.netflix_import import is_anime_candidate, parse_netflix_csv, prepare_netflix_import

    if media_type == "movie":
        source_episodes = item.get("episodes") or []
        if item.get("kind") == "show" and (
            len(source_episodes) != 1 or source_episodes[0].get("resolution") == "exact"
        ):
            raise ValueError("Only a single unconfirmed episode observation can be remapped to a movie safely.")
        details = await tmdb.get_movie_light(tmdb_id, api_key=api_key, language=language)
        if int(details.get("id") or 0) != tmdb_id:
            raise ValueError("TMDB returned a different movie than the selected result.")
        title = details.get("title") or item.get("source_title")
        candidate = {
            **details,
            "tmdb_id": tmdb_id,
            "title": title,
            "media_type": "movie",
            "year": int(details["release_date"][:4]) if (details.get("release_date") or "")[:4].isdigit() else None,
            "poster_path": details.get("poster_path"),
            "details": details,
        }
        refreshed = dict(item)
        refreshed["kind"] = "movie"
        if item.get("kind") == "show" and source_episodes:
            refreshed["source_title"] = source_episodes[0].get("source_title") or item.get("source_title")
        refreshed["is_anime"] = is_anime_candidate(details)
        refreshed["match"] = {"state": "matched", "confidence": "manual", "reason": "Title selected by the user.", "candidate": candidate, "candidates": [candidate]}
        refreshed["episodes"] = []
        refreshed["catalog_episodes"] = []
        refreshed["seasons"] = []
        refreshed["decision"] = {"action": "skip" if refreshed["is_anime"] else "remap"}
        refreshed["outcome"] = {**item.get("outcome", {}), "status": "skip" if refreshed["is_anime"] else "completed"}
        return refreshed

    source_episodes = item.get("episodes") or []
    if not source_episodes:
        # A manually chosen show can reinterpret a movie-shaped Netflix row.
        # Keep the original viewing dates and let the episode resolver bound
        # any positional guess to released catalogue episodes.
        source_episodes = [{
            "source_title": item.get("source_title") or "",
            "source_episode_title": item.get("source_title") or "",
            "dates": item.get("source_dates") or [],
            "source_row_numbers": [],
        }]
    details = await tmdb.get_show_light(tmdb_id, api_key=api_key, language=language)
    if int(details.get("id") or 0) != tmdb_id:
        raise ValueError("TMDB returned a different show than the selected result.")
    title = details.get("name") or item.get("source_title")
    csv_buffer = io.StringIO(newline="")
    writer = csv.writer(csv_buffer)
    writer.writerow(["Title", "Date"])
    remapped_rows: list[tuple[str, int, str]] = []
    for episode in source_episodes:
        original = episode.get("source_title") or ""
        if ":" in original:
            suffix = original.split(":", 1)[1].strip()
            remapped_title = f"{title}: {suffix}"
        elif episode.get("source_episode_title"):
            season_label = episode.get("season_label") or (f"Season {episode['season_number']}" if episode.get("season_number") else "")
            remapped_title = ": ".join(part for part in (title, season_label, episode["source_episode_title"]) if part)
        else:
            continue
        dates = sorted(episode.get("dates") or [])
        row_numbers = sorted((int(row) for row in episode.get("source_row_numbers") or []), reverse=True)
        for index, watched_date in enumerate(dates):
            original_row = row_numbers[min(index, len(row_numbers) - 1)] if row_numbers else 0
            remapped_rows.append((watched_date, original_row, remapped_title))
    for watched_date, _original_row, remapped_title in sorted(
        remapped_rows, key=lambda row: (row[0], -row[1]), reverse=True,
    ):
        writer.writerow([remapped_title, watched_date])
    history = parse_netflix_csv(csv_buffer.getvalue(), language=language)

    async def forced_search(query: str, **kwargs):
        return {"results": [{
            "id": tmdb_id,
            "name": title,
            "first_air_date": details.get("first_air_date"),
            "poster_path": details.get("poster_path"),
            "overview": details.get("overview"),
        }]}

    async def forced_details(_selected_id: int, **kwargs):
        return details

    prepared = await prepare_netflix_import(
        history,
        api_key=api_key,
        language=language,
        search_shows_fn=forced_search,
        show_details_fn=forced_details,
        max_concurrency=4,
    )
    groups = _normalise_groups(prepared)
    refreshed = next((group for group in groups if group.get("kind") == "show"), None)
    if refreshed is None or not refreshed.get("match", {}).get("candidate"):
        raise ValueError("The selected show could not be matched to the Netflix episode names.")
    refreshed["id"] = item["id"]
    refreshed["source_title"] = item.get("source_title")
    refreshed["source_dates"] = item.get("source_dates", [])
    refreshed["source_rows"] = item.get("source_rows", 0)
    refreshed["match"]["candidate"]["details"] = details
    refreshed["match"]["candidate"]["tvdb_id"] = (details.get("external_ids") or {}).get("tvdb_id")
    refreshed["match"]["reason"] = "Show selected by the user; episode positions were resolved where possible."
    previous_outcome = item.get("outcome") or {}
    # A remap rebuilds episode evidence, but the staged title rating belongs
    # to the draft item, even when a movie-shaped row becomes a show.
    if previous_outcome.get("manual_score_staged"):
        refreshed["outcome"]["manual_score"] = previous_outcome.get("manual_score")
        refreshed["outcome"]["manual_score_staged"] = True
    if item.get("kind") == "show":
        if previous_outcome.get("status_overridden") and previous_outcome.get("status") in ("completed", "partial"):
            refreshed["outcome"].update({
                "status": previous_outcome["status"],
                "status_overridden": True,
                "tracking_status": previous_outcome.get("tracking_status", "watching"),
            })
        if previous_outcome.get("endpoint_overridden"):
            available = {int(season["season_number"]): int(season.get("total_released") or 0)
                         for season in refreshed.get("seasons") or [] if season.get("season_number")}
            prior_season = previous_outcome.get("latest_season")
            prior_episode = previous_outcome.get("latest_episode")
            if prior_season in available and available[prior_season] > 0 and isinstance(prior_episode, int):
                refreshed["outcome"].update({
                    "latest_season": prior_season,
                    "latest_episode": min(max(1, prior_episode), available[prior_season]),
                    "endpoint_overridden": True,
                })
    refreshed["is_anime"] = is_anime_candidate(refreshed["match"]["candidate"], details)
    if refreshed["is_anime"]:
        refreshed["decision"] = {"action": "skip"}
        refreshed["outcome"]["status"] = "skip"
    refreshed["decision"] = {"action": "skip" if refreshed["is_anime"] else "remap"}
    refreshed["existing"] = {"catalog": False, "tracked": False, "status": None, "progress": 0}
    return refreshed


def _summary(items: list[dict], parsed_counts: dict | None = None, errors: list | None = None) -> dict:
    parsed_counts = parsed_counts or {}
    errors = errors or []
    included = [i for i in items if i.get("decision", {}).get("action") != "skip" and i.get("outcome", {}).get("status") != "skip"]
    movies = [i for i in included if i.get("kind") == "movie"]
    shows = [i for i in included if i.get("kind") == "show"]
    partial_coverage = [i for i in shows if not i.get("seasons") or any(
        s.get("total_released") is None or s["represented"] < s["total_released"] for s in i.get("seasons", [])
    )]
    source_watches = sum(len(set(i.get("source_dates", []))) for i in movies)
    unmatched = [i for i in items if (
        i.get("match", {}).get("state") in ("review", "unmatched") and not i.get("decision", {}).get("action")
    )]
    skipped = [i for i in items if i.get("decision", {}).get("action") == "skip" or i.get("outcome", {}).get("status") == "skip"]
    skipped_episode_count = sum(1 for i in items for e in i.get("episodes", []) if e.get("decision", {}).get("action") == "skip")
    inferred = 0
    source_episodes = 0
    final_episodes = 0
    cutoff_exclusions = 0
    guessed_episodes = 0
    for item in shows:
        outcome = item.get("outcome", {})
        try:
            source_positions, chosen_positions, excluded = _show_import_positions(item, outcome, date.today())
        except ValueError:
            source_positions, chosen_positions, excluded = {}, set(), 0
        source_watches += sum(len(set(date_value for ep in episodes for date_value in ep.get("dates", []))) for episodes in source_positions.values())
        source_episodes += len(source_positions)
        final_episodes += len(chosen_positions)
        guessed_episodes += sum(ep.get("resolution") == "guessed" for episodes in source_positions.values() for ep in episodes)
        inferred += len(chosen_positions - set(source_positions))
        cutoff_exclusions += excluded
    return {
        "movies": len(movies),
        "shows": len(shows),
        "new_entries": sum(not i.get("existing", {}).get("tracked") for i in included),
        "existing_entries": sum(bool(i.get("existing", {}).get("tracked")) for i in included),
        "source_watches": source_watches,
        "source_episodes": source_episodes,
        "episodes": final_episodes,
        "partial_shows": sum(item.get("outcome", {}).get("status") == "partial" for item in shows),
        "guessed_episodes": guessed_episodes,
        "discarded_episodes": sum(episode.get("resolution") == "discarded" for item in included for episode in item.get("episodes", [])),
        "covered_episodes": sum(episode.get("resolution") == "covered" for item in included for episode in item.get("episodes", [])),
        "duplicates": int(parsed_counts.get("duplicate_rows", parsed_counts.get("duplicates", 0)) or 0),
        "excluded_rows": int(parsed_counts.get("excluded_rows", 0) or 0),
        "inferred_episodes": inferred,
        "skipped": len(skipped) + skipped_episode_count,
        "unmatched": len(unmatched),
        "cutoff_exclusions": cutoff_exclusions,
        "partial_progress": len(partial_coverage),
        "errors": len(errors),
    }


async def _load_owned(db: AsyncSession, session_id: str, user_id: int, *, lock: bool = False) -> NetflixImportSession:
    query = select(NetflixImportSession).where(NetflixImportSession.id == session_id, NetflixImportSession.user_id == user_id)
    if lock:
        query = query.with_for_update()
    session = (await db.execute(query)).scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Import session not found")
    if session.expires_at <= _now() and session.status != "committed":
        session.source_csv = None
        session.payload = {}
        session.status = "cancelled"
        session.phase = "complete"
        await db.commit()
        raise HTTPException(status_code=410, detail="Import session expired")
    return session


def _public_session(session: NetflixImportSession) -> dict:
    payload = session.payload or {}
    result = session.result or {}
    items = deepcopy(payload.get("items", [])) if session.status not in ("committed", "cancelled", "failed") else []
    _resolve_draft_items(items)
    errors = payload.get("errors", []) if session.status != "committed" else []
    summary = result.get("summary") if session.status == "committed" else _summary(items, payload.get("counts"), errors)
    if session.error_message and not errors:
        errors = [{"message": session.error_message}]
    return {
        "id": session.id,
        "status": session.status,
        "phase": session.phase,
        "progress": session.progress or {"current": 0, "total": 0, "message": ""},
        "revision": session.revision,
        "items": items,
        "summary": summary,
        "errors": errors,
        "result": result or None,
    }


async def _set_progress(session_id: str, user_id: int, current: int, total: int, message: str, phase: str = "match") -> bool:
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as db:
        session = (await db.execute(select(NetflixImportSession).where(
            NetflixImportSession.id == session_id, NetflixImportSession.user_id == user_id,
        ))).scalar_one_or_none()
        if session is None or session.status != "preparing" or session_id in _cancelled:
            return False
        session.phase = phase
        session.progress = {"current": max(0, int(current)), "total": max(0, int(total)), "message": str(message)[:160]}
        await db.commit()
        return True


async def _prepare_session(session_id: str, user_id: int, language: str | None) -> None:
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with maker() as db:
            session = (await db.execute(select(NetflixImportSession).where(
                NetflixImportSession.id == session_id, NetflixImportSession.user_id == user_id,
            ))).scalar_one_or_none()
            if session is None or session.status != "preparing":
                return
            content = session.source_csv
            api_key = await _tmdb_key(db, user_id)
            resolved_language = language or (session.payload or {}).get("language")
            if not content:
                raise ValueError("The uploaded CSV is no longer available for preparation.")
            if not api_key:
                raise ValueError("A TMDB API key is required to match Netflix titles.")
            session.phase = "parse"
            session.progress = {"current": 0, "total": 1, "message": "Reading Netflix viewing history"}
            await db.commit()

        from core.netflix_import import parse_netflix_csv, prepare_netflix_import
        history = parse_netflix_csv(content, language=resolved_language)
        history_dict = history.to_dict() if hasattr(history, "to_dict") else {}
        row_count = len(getattr(history, "rows", []) or history_dict.get("rows", []))
        await _set_progress(session_id, user_id, 1, 1, "Netflix CSV parsed", phase="match")

        async def progress_fn(current: int, total: int, message: str = "Matching titles"):
            alive = await _set_progress(session_id, user_id, current, total, message, phase="match")
            if not alive:
                raise ImportCancelled()

        kwargs = {"api_key": api_key, "language": resolved_language, "max_concurrency": 4}
        try:
            params = inspect.signature(prepare_netflix_import).parameters
            if "progress_fn" in params:
                kwargs["progress_fn"] = progress_fn
        except (TypeError, ValueError):
            pass
        prepared = await prepare_netflix_import(history, **kwargs)
        if not isinstance(prepared, dict):
            prepared = prepared.to_dict() if hasattr(prepared, "to_dict") else {}
        items = _normalise_groups(prepared)
        errors = prepared.get("errors") or history_dict.get("errors") or []
        counts = prepared.get("counts") or history_dict.get("counts") or {}
        if row_count and not counts:
            counts = {"source_rows": row_count, "duplicate_rows": getattr(history, "duplicate_count", 0)}

        async with maker() as db:
            session = (await db.execute(select(NetflixImportSession).where(
                NetflixImportSession.id == session_id, NetflixImportSession.user_id == user_id,
            ).with_for_update())).scalar_one_or_none()
            if session is None or session.status != "preparing" or session_id in _cancelled:
                return
            for item in items:
                candidate = item["match"].get("candidate")
                if not candidate:
                    continue
                media_type = MediaType.movie if item["kind"] == "movie" else MediaType.series
                media = (await db.execute(select(Media).where(
                    Media.tmdb_id == candidate["tmdb_id"], Media.media_type == media_type,
                ))).scalar_one_or_none()
                if media:
                    tracked = (await db.execute(select(TrackedEntry).where(
                        TrackedEntry.user_id == user_id, TrackedEntry.media_id == media.id,
                    ))).scalar_one_or_none()
                    item["existing"] = _existing_state(media, tracked)
                if item["kind"] == "show":
                    show = (await db.execute(select(Show).where(Show.tmdb_id == candidate["tmdb_id"]))).scalar_one_or_none()
                    if show and show.canonical_source == "tvdb":
                        tmdb_eps = [e for e in item.get("episodes", []) if e.get("matched") and e.get("season_number", 0) > 0 and e.get("episode_number")]
                        tmdb_positions = {(e["season_number"], e["episode_number"]) for e in tmdb_eps}
                        mappings = (await db.execute(select(EpisodeOrderMapping).where(EpisodeOrderMapping.series_tmdb_id == candidate["tmdb_id"]))).scalars().all()
                        mapped = {(m.tmdb_season_number, m.tmdb_episode_number) for m in mappings}
                        if tmdb_positions - mapped:
                            item["match"]["state"] = "review"
                            item["match"]["reason"] = "This existing show uses TVDB episode order; confirm an episode order before importing."
                            item["decision"]["action"] = None
            session.payload = {"language": resolved_language, "items": items, "errors": errors, "counts": counts}
            session.source_csv = None
            session.status = "review"
            session.phase = "review"
            session.progress = {"current": len(items), "total": len(items), "message": "Title matching complete"}
            session.revision += 1
            session.error_message = None
            await db.commit()
    except ImportCancelled:
        return
    except Exception as exc:
        logger.exception("Netflix import preparation failed for session %s", session_id)
        async with maker() as db:
            session = (await db.execute(select(NetflixImportSession).where(
                NetflixImportSession.id == session_id, NetflixImportSession.user_id == user_id,
            ))).scalar_one_or_none()
            if session and session.status == "preparing":
                session.status = "failed"
                session.phase = "complete"
                session.source_csv = None
                session.error_message = str(exc)[:500]
                session.progress = {"current": 0, "total": 0, "message": "Preparation failed"}
                session.revision += 1
                await db.commit()
    finally:
        _running.discard(session_id)
        _cancelled.discard(session_id)


def _schedule_commit(background_tasks: BackgroundTasks, session_id: str, user_id: int, idempotency_key: str | None) -> None:
    if idempotency_key and session_id not in _committing:
        _committing.add(session_id)
        background_tasks.add_task(_commit_session, session_id, user_id, idempotency_key)


def _schedule(background_tasks: BackgroundTasks, session_id: str, user_id: int, language: str | None) -> None:
    if session_id not in _running:
        _running.add(session_id)
        background_tasks.add_task(_prepare_session, session_id, user_id, language)


@router.post("/netflix", status_code=202)
async def upload_netflix_history(
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    language: str | None = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not (file.filename or "").lower().endswith(".csv"):
        raise HTTPException(status_code=400, detail="Choose a Netflix viewing-history CSV file.")
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="The Netflix CSV is too large to import.")
        chunks.append(chunk)
    content = b"".join(chunks)
    if not content:
        raise HTTPException(status_code=400, detail="The uploaded CSV is empty.")
    now = _now()
    await db.execute(delete(NetflixImportSession).where(
        NetflixImportSession.user_id == current_user.id,
        NetflixImportSession.status != "committed",
        NetflixImportSession.expires_at <= now,
    ))
    if not await _tmdb_key(db, current_user.id):
        raise HTTPException(status_code=400, detail="Add a TMDB API key in Settings before importing Netflix history.")
    session = NetflixImportSession(
        id=str(uuid.uuid4()), user_id=current_user.id, status="preparing", phase="parse",
        progress={"current": 0, "total": 1, "message": "Upload received"}, revision=0,
        source_csv=content, payload={"language": language}, created_at=now, updated_at=now,
        expires_at=now + timedelta(days=DRAFT_DAYS),
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    _schedule(background_tasks, session.id, current_user.id, language)
    return _public_session(session)


@router.get("/{session_id}")
async def get_netflix_import(
    session_id: str,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    session = await _load_owned(db, session_id, current_user.id)
    if session.status == "preparing" and session_id not in _running:
        _schedule(background_tasks, session_id, current_user.id, (session.payload or {}).get("language"))
    elif session.status == "committing" and session_id not in _committing:
        _schedule_commit(background_tasks, session_id, current_user.id, session.idempotency_key)
    return _public_session(session)


@router.patch("/{session_id}/items/{item_id}")
async def update_netflix_import_item(
    session_id: str,
    item_id: str,
    patch: dict = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    action = patch.get("action")
    remapped_item = None
    if action == "remap":
        # Read a copy, release the DB transaction, and do provider requests
        # before taking the write lock used for the actual draft update.
        before = await _load_owned(db, session_id, current_user.id)
        if before.status != "review" or patch.get("revision") != before.revision:
            raise HTTPException(status_code=409, detail="This import changed in another request. Reload it and try again.")
        old_item = next((i for i in (before.payload or {}).get("items", []) if i.get("id") == item_id), None)
        if old_item is None:
            raise HTTPException(status_code=404, detail="Import item not found")
        media_type, tmdb_id = patch.get("media_type"), patch.get("tmdb_id")
        if media_type not in ("movie", "show") or not isinstance(tmdb_id, int) or tmdb_id <= 0:
            raise HTTPException(status_code=422, detail="A remap requires media_type and a positive TMDB id.")
        api_key = await _tmdb_key(db, current_user.id)
        language = (before.payload or {}).get("language")
        await db.commit()
        if not api_key:
            raise HTTPException(status_code=400, detail="A TMDB API key is required to remap titles.")
        try:
            remapped_item = await _prepare_remapped_item(old_item, media_type, tmdb_id, api_key, language)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        except Exception as exc:
            logger.exception("Could not prepare Netflix remap %s -> %s", item_id, tmdb_id)
            raise HTTPException(status_code=502, detail="The selected title could not be loaded from TMDB. Try again.")

    session = await _load_owned(db, session_id, current_user.id, lock=True)
    if session.status != "review":
        raise HTTPException(status_code=409, detail="This import is no longer editable.")
    if patch.get("revision") != session.revision:
        raise HTTPException(status_code=409, detail="This import changed in another request. Reload it and try again.")
    payload = dict(session.payload or {})
    items = deepcopy(payload.get("items", []))
    _resolve_draft_items(items)
    if remapped_item is not None:
        items = [remapped_item if value.get("id") == item_id else value for value in items]
    item = next((value for value in items if value.get("id") == item_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail="Import item not found")
    if action is not None and action not in ("confirm", "remap", "skip"):
        raise HTTPException(status_code=422, detail="Action must be confirm, remap, or skip.")
    if action == "skip":
        item["decision"] = {"action": "skip"}
    elif action == "remap":
        if remapped_item is None:
            raise HTTPException(status_code=422, detail="The selected title remap could not be prepared.")
        item["decision"] = {"action": "skip" if item.get("is_anime") else "remap"}
    elif action == "confirm":
        if item.get("is_anime"):
            raise HTTPException(status_code=422, detail="Anime imports are currently unavailable.")
        if not item.get("match", {}).get("candidate"):
            raise HTTPException(status_code=422, detail="Choose a suggested title or use Remap before confirming.")
        item["decision"] = {"action": "confirm"}
    outcome_status = patch.get("status")
    if outcome_status is not None:
        if item.get("is_anime") and outcome_status != "skip":
            raise HTTPException(status_code=422, detail="Anime imports are currently unavailable.")
        if outcome_status not in ("completed", "partial", "skip"):
            raise HTTPException(status_code=422, detail="Progress must be completed, partial, or skip.")
        if item["kind"] == "movie" and outcome_status == "partial":
            raise HTTPException(status_code=422, detail="Movies can only be completed or skipped.")
        item["outcome"]["status"] = outcome_status
        item["outcome"]["status_overridden"] = True
    for key in ("latest_season", "latest_episode"):
        if key in patch:
            value = patch[key]
            if value is not None and (not isinstance(value, int) or value < 1):
                raise HTTPException(status_code=422, detail=f"{key} must be a positive integer.")
            item["outcome"][key] = value
            item["outcome"]["endpoint_overridden"] = True
    tracking_status = patch.get("tracking_status")
    if tracking_status is not None:
        if tracking_status not in ("watching", "paused", "dropped"):
            raise HTTPException(status_code=422, detail="Partial progress status must be watching, paused, or dropped.")
        item["outcome"]["tracking_status"] = tracking_status
        item["outcome"]["status_overridden"] = True
    if "manual_score" in patch:
        score = patch["manual_score"]
        if score is None:
            item["outcome"].pop("manual_score", None)
            item["outcome"].pop("manual_score_staged", None)
        elif (isinstance(score, bool) or not isinstance(score, (int, float))
              or not 0.5 <= score <= 10 or round(float(score) * 2) != float(score) * 2):
            raise HTTPException(status_code=422, detail="Rating must be a half-step from 0.5 to 10, or null to clear it.")
        else:
            item["outcome"]["manual_score"] = float(score)
            item["outcome"]["manual_score_staged"] = True
    if patch.get("episode_mappings"):
        raise HTTPException(status_code=422, detail="Episode matches are resolved automatically and cannot be edited.")
    if action is None and outcome_status is None and tracking_status is None and "manual_score" not in patch and not any(
        key in patch for key in ("latest_season", "latest_episode")
    ):
        raise HTTPException(status_code=422, detail="Include a title decision or progress change.")
    if item["kind"] == "show" and item["outcome"]["status"] != "skip" and (
        outcome_status is not None or "latest_season" in patch or "latest_episode" in patch
    ):
        try:
            _show_import_positions(item, item["outcome"], date.today())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
    candidate = item.get("match", {}).get("candidate")
    if candidate:
        media_type = MediaType.movie if item["kind"] == "movie" else MediaType.series
        media = (await db.execute(select(Media).where(Media.tmdb_id == candidate["tmdb_id"], Media.media_type == media_type))).scalar_one_or_none()
        tracked = (await db.execute(select(TrackedEntry).where(TrackedEntry.user_id == current_user.id, TrackedEntry.media_id == media.id))).scalar_one_or_none() if media else None
        item["existing"] = _existing_state(media, tracked)
    payload["items"] = items
    session.payload = payload
    session.error_message = None
    session.revision += 1
    session.updated_at = _now()
    await db.commit()
    await db.refresh(session)
    return _public_session(session)


@router.delete("/{session_id}")
async def cancel_netflix_import(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Signal the in-process transaction before waiting for its row lock. The
    # commit worker checks this flag before its single final COMMIT.
    preliminary = await _load_owned(db, session_id, current_user.id)
    if preliminary.status == "committing":
        _cancelled.add(session_id)
    session = await _load_owned(db, session_id, current_user.id, lock=True)
    if session.status == "committed":
        raise HTTPException(status_code=409, detail="An imported history cannot be cancelled after it has been committed.")
    if session.status != "cancelled":
        _cancelled.add(session_id)
        session.status = "cancelled"
        session.phase = "complete"
        session.source_csv = None
        session.payload = {}
        session.progress = {"current": 0, "total": 0, "message": "Import cancelled"}
        session.revision += 1
        session.updated_at = _now()
        await db.commit()
        await db.refresh(session)
    return _public_session(session)


def _metadata(candidate: dict) -> dict:
    return candidate.get("details") or candidate.get("metadata") or {}


def _poster(path: str | None, size: str = "w500") -> str | None:
    if not path:
        return None
    return tmdb.poster_url(path, size=size)


async def _get_or_create_movie(db: AsyncSession, candidate: dict) -> Media:
    tmdb_id = int(candidate["tmdb_id"])
    media = (await db.execute(select(Media).where(Media.tmdb_id == tmdb_id, Media.media_type == MediaType.movie))).scalar_one_or_none()
    if media:
        return media
    details = _metadata(candidate)
    title = candidate.get("title") or details.get("title") or "Unknown title"
    media, _ = await create_media_safely(
        db, tmdb_id, MediaType.movie,
        title=title,
        original_title=details.get("original_title"),
        overview=details.get("overview"),
        poster_path=_poster(candidate.get("poster_path") or details.get("poster_path")),
        backdrop_path=_poster(details.get("backdrop_path"), "w1280"),
        release_date=details.get("release_date"),
        runtime=details.get("runtime"),
        tmdb_rating=details.get("vote_average"),
        status=None,
        tmdb_data={
            "genres": [g.get("name") for g in details.get("genres", []) if isinstance(g, dict)],
            "original_language": details.get("original_language"),
            "external_ids": details.get("external_ids") or {},
        },
        adult=bool(details.get("adult", False)),
    )
    return media


async def _get_or_create_show_root(db: AsyncSession, candidate: dict, item: dict) -> tuple[Show, Media]:
    tmdb_id = int(candidate["tmdb_id"])
    details = _metadata(candidate)
    show = (await db.execute(select(Show).where(Show.tmdb_id == tmdb_id))).scalar_one_or_none()
    seasons = item.get("seasons", [])
    season_metadata = [
        {"season_number": s["season_number"], "episode_count": s.get("total_released"), "name": s.get("name")}
        for s in seasons if s.get("season_number") is not None
    ]
    if show is None:
        show = Show(
            tmdb_id=tmdb_id,
            tvdb_id=candidate.get("tvdb_id") or details.get("external_ids", {}).get("tvdb_id"),
            canonical_source="tmdb",
            title=candidate.get("title") or details.get("name") or item.get("source_title") or "Unknown show",
            original_title=details.get("original_name"),
            overview=details.get("overview"),
            poster_path=_poster(candidate.get("poster_path") or details.get("poster_path")),
            backdrop_path=_poster(details.get("backdrop_path"), "w1280"),
            tmdb_rating=details.get("vote_average"),
            status=details.get("status"),
            tagline=details.get("tagline"),
            first_air_date=details.get("first_air_date"),
            last_air_date=details.get("last_air_date"),
            tmdb_data={
                "genres": [g.get("name") for g in details.get("genres", []) if isinstance(g, dict)],
                "external_ids": details.get("external_ids") or {},
                "original_language": details.get("original_language"),
                "seasons": details.get("seasons") or season_metadata,
            },
        )
        db.add(show)
        await db.flush()
    series = (await db.execute(select(Media).where(Media.tmdb_id == tmdb_id, Media.media_type == MediaType.series))).scalar_one_or_none()
    if series is None:
        series, _ = await create_media_safely(
            db, tmdb_id, MediaType.series,
            title=show.title,
            original_title=show.original_title,
            overview=show.overview,
            poster_path=show.poster_path,
            backdrop_path=show.backdrop_path,
            release_date=show.first_air_date,
            tmdb_rating=show.tmdb_rating,
            status=show.status,
            tagline=show.tagline,
            tmdb_data=show.tmdb_data or {},
            adult=bool(details.get("adult", False)),
        )
    return show, series


def _all_catalog_episodes(item: dict) -> dict[tuple[int, int], dict]:
    episodes: dict[tuple[int, int], dict] = {}
    for ep in [*(item.get("catalog_episodes") or []), *(item.get("episodes") or [])]:
        season, number = ep.get("season_number"), ep.get("episode_number")
        if season is None or number is None or int(season) <= 0:
            continue
        key = (int(season), int(number))
        if key not in episodes or ep.get("matched"):
            episodes[key] = ep
    for season in item.get("seasons") or []:
        season_number = season.get("season_number")
        total_released = season.get("total_released")
        if not season_number or not total_released:
            continue
        for number in range(1, int(total_released) + 1):
            position = (int(season_number), number)
            existing = episodes.get(position)
            if existing is None:
                episodes[position] = {
                    "season_number": position[0], "episode_number": number,
                    "title": f"Episode {number}", "released_for_import": True,
                }
            elif not existing.get("release_date") and not existing.get("air_date"):
                episodes[position] = {**existing, "released_for_import": True}
    return episodes


async def _get_or_create_episode(
    db: AsyncSession,
    show: Show,
    candidate: dict,
    episode: dict,
    mappings: dict[tuple[int, int], EpisodeOrderMapping],
) -> Media | None:
    source_season = int(episode["season_number"])
    source_number = int(episode["episode_number"])
    if show.canonical_source == "tvdb":
        mapping = mappings.get((source_season, source_number))
        if mapping is None:
            return None
        local_season, local_number = mapping.tvdb_season_number, mapping.tvdb_episode_number
        tvdb_id = mapping.tvdb_id
    else:
        local_season, local_number = source_season, source_number
        tvdb_id = episode.get("tvdb_id")
    media = (await db.execute(select(Media).where(
        Media.show_id == show.id,
        Media.media_type == MediaType.episode,
        Media.season_number == local_season,
        Media.episode_number == local_number,
    ))).scalar_one_or_none()
    if media:
        # The season endpoint can prove that a numbered position is released
        # even when TMDB omitted its air date. Keep that fact separate from the
        # date field so imported progress can include it without inventing one.
        metadata = dict(media.tmdb_data or {})
        if episode.get("released_for_import"):
            metadata["tracking_import_released"] = True
            media.tmdb_data = metadata
        return media
    details = episode.get("metadata") or episode
    tmdb_episode_id = episode.get("tmdb_episode_id") or episode.get("tmdb_id")
    fields = {
        "tvdb_id": tvdb_id,
        "title": episode.get("title") or details.get("name") or f"Episode {local_number}",
        "overview": details.get("overview"),
        "poster_path": _poster(details.get("still_path") or details.get("poster_path")),
        "release_date": episode.get("release_date") or episode.get("air_date"),
        "tmdb_rating": details.get("vote_average"),
        "runtime": details.get("runtime"),
        "show_id": show.id,
        "season_number": local_season,
        "episode_number": local_number,
        "tmdb_data": {"runtime": details.get("runtime"), "source": "netflix-import"},
    }
    if episode.get("released_for_import"):
        fields["tmdb_data"]["tracking_import_released"] = True
    if tmdb_episode_id:
        media, _ = await create_media_safely(db, int(tmdb_episode_id), MediaType.episode, **fields)
    else:
        media = Media(tmdb_id=None, media_type=MediaType.episode, **fields)
        db.add(media)
        await db.flush()
    return media


def _position_for_episode(episode: dict) -> tuple[int, int] | None:
    decision = episode.get("decision") or {}
    if decision.get("action") == "skip":
        return None
    if decision.get("action") == "remap":
        return (int(episode["season_number"]), int(episode["episode_number"]))
    if episode.get("matched") and episode.get("season_number") and episode.get("episode_number"):
        return (int(episode["season_number"]), int(episode["episode_number"]))
    return None


def _episode_is_released(episode: dict, today: date) -> bool:
    if episode.get("released_for_import"):
        return True
    released = _to_date(episode.get("release_date") or episode.get("air_date"))
    return released is not None and released <= today


def _show_import_positions(item: dict, outcome: dict, today: date) -> tuple[dict, set[tuple[int, int]], int]:
    catalog = _all_catalog_episodes(item)
    source_positions: dict[tuple[int, int], list[dict]] = {}
    for episode in item.get("episodes", []):
        if (episode.get("decision") or {}).get("action") == "skip":
            continue
        position = _position_for_episode(episode)
        if position is not None:
            source_positions.setdefault(position, []).append(episode)

    endpoint = (outcome.get("latest_season"), outcome.get("latest_episode"))
    cutoff_exclusions = 0
    status = outcome.get("status")
    if status == "partial" and endpoint[0] is not None and endpoint[1] is not None:
        endpoint = (int(endpoint[0]), int(endpoint[1]))
        endpoint_episode = catalog.get(endpoint)
        if endpoint_episode is None or not _episode_is_released(endpoint_episode, today):
            raise ValueError(f"{item.get('source_title')}: choose a latest episode that exists in the prepared released catalogue.")
        excluded = {position: episodes for position, episodes in source_positions.items() if position > endpoint}
        cutoff_exclusions = sum(len(episodes) for episodes in excluded.values())
        source_positions = {position: episodes for position, episodes in source_positions.items() if position <= endpoint}

    chosen_positions = set(source_positions)
    if status == "completed":
        chosen_positions.update(position for position, episode in catalog.items() if _episode_is_released(episode, today))
    elif status == "partial" and endpoint[0] is not None and endpoint[1] is not None:
        chosen_positions.update(
            position for position, episode in catalog.items()
            if position <= endpoint and _episode_is_released(episode, today)
        )
    return source_positions, chosen_positions, cutoff_exclusions


async def _has_completed_event(db: AsyncSession, user_id: int, media_id: int, watched_at: datetime | None) -> bool:
    query = select(WatchEventModel.id).where(
        WatchEventModel.user_id == user_id,
        WatchEventModel.media_id == media_id,
        WatchEventModel.completed.is_(True),
    )
    if watched_at is None:
        query = query.where(WatchEventModel.watched_at.is_(None), WatchEventModel.provisional.is_(True))
    else:
        query = query.where(WatchEventModel.watched_at == watched_at)
    return (await db.execute(query.limit(1))).scalar_one_or_none() is not None


async def _track_imported_root(
    db: AsyncSession,
    user_id: int,
    root: Media,
    outcome: dict,
    accepted_dates: list[date],
    progress_count: int,
    is_movie: bool,
) -> tuple[TrackedEntry, bool]:
    entry = (await db.execute(select(TrackedEntry).where(
        TrackedEntry.user_id == user_id, TrackedEntry.media_id == root.id,
    ).with_for_update())).scalar_one_or_none()
    is_new = entry is None
    if entry is None:
        initial_status = "completed" if is_movie or outcome.get("status") == "completed" else outcome.get("tracking_status") or "watching"
        entry = TrackedEntry(user_id=user_id, media_id=root.id, status=initial_status, progress=0)
        db.add(entry)
    desired_status = "completed" if is_movie or outcome.get("status") == "completed" else outcome.get("tracking_status") or "watching"
    if is_new:
        entry.status = desired_status
        mark_status_change(entry, "netflix-import")
    elif outcome.get("status_overridden") and entry.status != desired_status:
        entry.status = desired_status
        mark_status_change(entry, "netflix-import")
    entry.progress = max(int(entry.progress or 0), progress_count)
    if not is_movie and entry.start_date is None and accepted_dates:
        entry.start_date = min(accepted_dates)
    if (is_movie or outcome.get("status") == "completed") and entry.finish_date is None and accepted_dates:
        entry.finish_date = max(accepted_dates)
    entry.initial_import_completed_at = _now() if is_new and entry.status == "completed" else entry.initial_import_completed_at
    # A final, explicit import can restore an entry the owner previously deleted.
    await db.execute(delete(TrackingDeletion).where(
        TrackingDeletion.user_id == user_id, TrackingDeletion.media_id == root.id,
    ))
    return entry, is_new


async def _apply_staged_rating(db: AsyncSession, user_id: int, root: Media, entry: TrackedEntry, outcome: dict) -> None:
    """Apply an explicitly chosen import rating to the tracked root only."""
    if not outcome.get("manual_score_staged"):
        return
    from core.activity import record_daily_activity
    from core.tracking_rules import effective_score

    old_score = effective_score(entry.rating_mode, entry.manual_score, entry.season_scores)
    score = float(outcome["manual_score"])
    entry.rating_mode = "manual"
    entry.manual_score = score
    row = (await db.execute(select(Rating).where(
        Rating.user_id == user_id, Rating.media_id == root.id,
        Rating.season_number.is_(None), Rating.episode_order.is_(None),
    ))).scalar_one_or_none()
    if row is None:
        db.add(Rating(user_id=user_id, media_id=root.id, rating=score))
    elif row.rating != score:
        row.rating = score
        row.rated_at = _now()
    if old_score != score:
        await record_daily_activity(
            db, user_id=user_id, media_id=root.id, status=entry.status,
            score=score, rating_changed=True, previous_score=old_score,
        )


async def _apply_import(db: AsyncSession, user_id: int, session: NetflixImportSession, progress_callback) -> dict:
    payload = dict(session.payload or {})
    items = deepcopy(payload.get("items", []))
    _resolve_draft_items(items)
    errors = payload.get("errors", [])
    counts = payload.get("counts") or {}
    unresolved = []
    for item in items:
        if item.get("decision", {}).get("action") == "skip":
            continue
        if item.get("match", {}).get("state") in ("review", "unmatched") and not item.get("decision", {}).get("action"):
            unresolved.append(item.get("source_title", "Unknown title"))
    if unresolved:
        raise ValueError(f"Review or skip every uncertain title before importing ({len(unresolved)} remaining).")

    # Lock the account so two imports cannot concurrently overwrite dates or
    # progress. All catalog and personal writes below share this transaction.
    from models.users import User as UserModel
    await db.execute(select(UserModel.id).where(UserModel.id == user_id).with_for_update())

    stats = {
        "movies": 0, "shows": 0, "new_entries": 0, "existing_entries": 0,
        "source_watches": 0, "source_episodes": 0, "duplicates": 0,
        "episodes": 0, "partial_shows": 0,
        "inferred_episodes": 0, "skipped": 0, "unmatched": 0,
        "cutoff_exclusions": 0, "partial_progress": 0, "errors": len(errors),
        "excluded_rows": int(counts.get("excluded_rows", 0) or 0),
        "guessed_episodes": 0, "discarded_episodes": 0, "covered_episodes": 0,
    }
    today = date.today()
    usable_items = [i for i in items if i.get("decision", {}).get("action") != "skip" and i.get("outcome", {}).get("status") != "skip"]
    for item_index, item in enumerate(usable_items):
        if session.id in _cancelled:
            raise ImportCancelled()
        candidate = item.get("match", {}).get("candidate")
        if not candidate:
            stats["skipped"] += 1
            continue
        outcome = item.get("outcome") or {}
        date_values = [d for d in (_to_date(x) for x in item.get("source_dates", [])) if d is not None]
        if item["kind"] == "movie":
            movie = await _get_or_create_movie(db, candidate)
            watched_dates = sorted({_to_date(d) for d in item.get("source_dates", []) if _to_date(d) is not None})
            for watched_date in watched_dates:
                watched_at = datetime.combine(watched_date, datetime.min.time())
                if await _has_completed_event(db, user_id, movie.id, watched_at):
                    stats["duplicates"] += 1
                    continue
                db.add(WatchEvent(user_id=user_id, media_id=movie.id, watched_at=watched_at, completed=True, play_count=1, provisional=False))
                stats["source_watches"] += 1
            entry, is_new = await _track_imported_root(db, user_id, movie, outcome, date_values, 1, True)
            await _apply_staged_rating(db, user_id, movie, entry, outcome)
            stats["movies"] += 1
            stats["new_entries" if is_new else "existing_entries"] += 1
            continue

        show, root = await _get_or_create_show_root(db, candidate, item)
        maps = (await db.execute(select(EpisodeOrderMapping).where(EpisodeOrderMapping.series_tmdb_id == candidate["tmdb_id"]))).scalars().all()
        episode_mappings = {(m.tmdb_season_number, m.tmdb_episode_number): m for m in maps}
        catalog = _all_catalog_episodes(item)
        source_positions: dict[tuple[int, int], list[dict]] = {}
        position_for_source_id: dict[str, tuple[int, int]] = {}
        for ep in item.get("episodes", []):
            action = ep.get("decision", {}).get("action")
            if action == "skip":
                stats["skipped"] += 1
                continue
            position = _position_for_episode(ep)
            if position is None:
                continue
            source_positions.setdefault(position, []).append(ep)
            if ep.get("source_id"):
                position_for_source_id[ep["source_id"]] = position
        try:
            source_positions, chosen_positions, excluded_count = _show_import_positions(item, outcome, today)
        except ValueError as exc:
            raise ValueError(str(exc))
        stats["cutoff_exclusions"] += excluded_count
        stats["episodes"] += len(chosen_positions)
        stats["partial_shows"] += outcome.get("status") == "partial"
        stats["guessed_episodes"] += sum(ep.get("resolution") == "guessed" for episodes in source_positions.values() for ep in episodes)
        stats["discarded_episodes"] += sum(ep.get("resolution") == "discarded" for ep in item.get("episodes", []))
        stats["covered_episodes"] += sum(ep.get("resolution") == "covered" for ep in item.get("episodes", []))
        # A TVDB-canonical existing catalogue must have a proven mapping for
        # every source or inferred TMDB position. Never write TMDB numbers into
        # TVDB-native Media rows.
        if show.canonical_source == "tvdb":
            missing = [pos for pos in chosen_positions if pos not in episode_mappings]
            if missing:
                raise ValueError(f"{item.get('source_title')}: this existing show needs TVDB episode mappings for {len(missing)} episode(s). Skip this title or choose another show.")
        # Preserve a complete canonical catalogue for commits, deriving rows
        # only from metadata fetched during preparation (no network in txn).
        watched_dates_by_media: dict[int, list[date]] = {}
        watched_media: set[int] = set()
        imported_release_media_ids: set[int] = set()
        accepted_dates: list[date] = []
        for position in sorted(chosen_positions):
            episode = catalog.get(position)
            if episode is None:
                # A matched source row can import with its title and provider
                # episode ID if the metadata adapter omitted catalogue detail.
                # A remap must resolve to an actual prepared catalog episode.
                episode = next((
                    e for e in item.get("episodes", [])
                    if _position_for_episode(e) == position
                    and (e.get("decision", {}).get("action") != "remap" or (e.get("season_number"), e.get("episode_number")) == position)
                ), None)
            if episode is None:
                continue
            if not _episode_is_released(episode, today) and position not in source_positions:
                continue
            media = await _get_or_create_episode(db, show, candidate, episode, episode_mappings)
            if media is None:
                continue
            if episode.get("released_for_import"):
                imported_release_media_ids.add(media.id)
            dates_for_position = []
            if position in source_positions:
                for source_ep in source_positions[position]:
                    dates_for_position.extend(_to_date(d) for d in source_ep.get("dates", []) if _to_date(d) is not None)
            # Only source rows through the reviewed endpoint are imported.
            for watched_date in sorted(set(dates_for_position)):
                watched_at = datetime.combine(watched_date, datetime.min.time())
                if await _has_completed_event(db, user_id, media.id, watched_at):
                    stats["duplicates"] += 1
                else:
                    db.add(WatchEvent(user_id=user_id, media_id=media.id, watched_at=watched_at, completed=True, play_count=1, provisional=False))
                    stats["source_watches"] += 1
                watched_media.add(media.id)
                accepted_dates.append(watched_date)
            should_infer = position not in source_positions or not dates_for_position
            if should_infer and outcome.get("status") in ("partial", "completed") and position in chosen_positions:
                any_existing = (await db.execute(select(WatchEventModel.id).where(
                    WatchEventModel.user_id == user_id,
                    WatchEventModel.media_id == media.id,
                    WatchEventModel.completed.is_(True),
                ).limit(1))).scalar_one_or_none()
                if any_existing is None:
                    db.add(WatchEvent(user_id=user_id, media_id=media.id, watched_at=None, completed=True, play_count=1, provisional=True))
                    stats["inferred_episodes"] += 1
                watched_media.add(media.id)
            if position in source_positions:
                stats["source_episodes"] += 1
        for episode in item.get("episodes", []):
            if episode.get("resolution") != "covered" or not episode.get("covered_by"):
                continue
            if tuple(episode["covered_by"]) in chosen_positions:
                accepted_dates.extend(
                    watched_date for value in episode.get("dates", [])
                    if (watched_date := _to_date(value)) is not None
                )
        progress_count = len(watched_media)
        root_data = dict(getattr(root, "tmdb_data", None) or {})
        imported_episode_ids = set(root_data.get("tracking_import_episode_media_ids") or [])
        imported_episode_ids.update(imported_release_media_ids)
        root_data["tracking_import_episode_media_ids"] = sorted(imported_episode_ids)
        root.tmdb_data = root_data
        entry, is_new = await _track_imported_root(db, user_id, root, outcome, accepted_dates, progress_count, False)
        await _apply_staged_rating(db, user_id, root, entry, outcome)
        stats["shows"] += 1
        stats["new_entries" if is_new else "existing_entries"] += 1
        if any(s.get("total_released") is None or s.get("represented", 0) < s["total_released"] for s in item.get("seasons", [])):
            stats["partial_progress"] += 1
        await progress_callback(item_index + 1, len(usable_items), "Saving matched titles")
    stats["skipped"] += sum(1 for i in items if i.get("decision", {}).get("action") == "skip" or i.get("outcome", {}).get("status") == "skip")
    stats["unmatched"] = sum(1 for i in items if i.get("match", {}).get("state") in ("review", "unmatched") and i.get("decision", {}).get("action") == "skip")
    stats["duplicates"] += int(counts.get("duplicate_rows", 0) or 0)
    return stats


async def _commit_session(session_id: str, user_id: int, idempotency_key: str) -> None:
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with maker() as db:
            session = (await db.execute(select(NetflixImportSession).where(
                NetflixImportSession.id == session_id,
                NetflixImportSession.user_id == user_id,
            ).with_for_update())).scalar_one_or_none()
            if session is None or session.status != "committing" or session.idempotency_key != idempotency_key:
                return
            if session_id in _cancelled:
                raise ImportCancelled()
            session.phase = "commit"
            session.progress = {"current": 0, "total": len((session.payload or {}).get("items", [])), "message": "Applying viewing history"}
            await db.flush()

            async def report_progress(current: int, total: int, message: str):
                session.progress = {"current": current, "total": total, "message": message}
                # The progress becomes visible at the final commit, and GET
                # remains cheap while the single atomic transaction is running.

            stats = await _apply_import(db, user_id, session, report_progress)
            if session_id in _cancelled:
                raise ImportCancelled()
            summary = {
                "movies": stats["movies"], "shows": stats["shows"],
                "new_entries": stats["new_entries"], "existing_entries": stats["existing_entries"],
                "source_watches": stats["source_watches"], "source_episodes": stats["source_episodes"],
                "episodes": stats["episodes"], "partial_shows": stats["partial_shows"],
                "duplicates": stats["duplicates"], "inferred_episodes": stats["inferred_episodes"],
                "skipped": stats["skipped"], "unmatched": stats["unmatched"],
                "cutoff_exclusions": stats["cutoff_exclusions"], "partial_progress": stats["partial_progress"],
                "excluded_rows": stats["excluded_rows"],
                "guessed_episodes": stats["guessed_episodes"], "discarded_episodes": stats["discarded_episodes"],
                "covered_episodes": stats["covered_episodes"],
                "errors": stats["errors"],
            }
            session.status = "committed"
            session.phase = "complete"
            session.progress = {"current": len((session.payload or {}).get("items", [])), "total": len((session.payload or {}).get("items", [])), "message": "Import complete"}
            session.result = {"summary": summary, "receipt": idempotency_key, "committed_at": _now().isoformat()}
            session.payload = {}
            session.source_csv = None
            session.revision += 1
            session.error_message = None
            session.expires_at = _now() + timedelta(days=36500)
            await db.commit()
    except ImportCancelled:
        async with maker() as db:
            session = (await db.execute(select(NetflixImportSession).where(
                NetflixImportSession.id == session_id, NetflixImportSession.user_id == user_id,
            ).with_for_update())).scalar_one_or_none()
            if session and session.status == "committing":
                session.status = "cancelled"
                session.phase = "complete"
                session.payload = {}
                session.source_csv = None
                session.progress = {"current": 0, "total": 0, "message": "Import cancelled"}
                session.revision += 1
                await db.commit()
    except ValueError as exc:
        async with maker() as db:
            session = (await db.execute(select(NetflixImportSession).where(
                NetflixImportSession.id == session_id, NetflixImportSession.user_id == user_id,
            ).with_for_update())).scalar_one_or_none()
            if session and session.status == "committing":
                session.status = "review"
                session.phase = "review"
                session.error_message = str(exc)[:500]
                session.progress = {"current": 0, "total": len((session.payload or {}).get("items", [])), "message": "Review required"}
                session.revision += 1
                await db.commit()
    except Exception as exc:
        logger.exception("Netflix import commit failed for session %s", session_id)
        async with maker() as db:
            session = (await db.execute(select(NetflixImportSession).where(
                NetflixImportSession.id == session_id, NetflixImportSession.user_id == user_id,
            ))).scalar_one_or_none()
            if session and session.status == "committing":
                session.status = "failed"
                session.phase = "complete"
                session.error_message = str(exc)[:500]
                session.revision += 1
                await db.commit()
    finally:
        _committing.discard(session_id)
        _cancelled.discard(session_id)


@router.post("/{session_id}/commit", status_code=202)
async def commit_netflix_import(
    session_id: str,
    background_tasks: BackgroundTasks,
    body: dict = Body(...),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    session = await _load_owned(db, session_id, current_user.id, lock=True)
    key = body.get("idempotency_key")
    if not isinstance(key, str) or not key.strip() or len(key) > 128:
        raise HTTPException(status_code=422, detail="An idempotency_key is required.")
    key = key.strip()
    if session.status == "committed":
        if session.idempotency_key != key:
            raise HTTPException(status_code=409, detail="This import was already committed with another idempotency key.")
        return _public_session(session)
    if session.status == "committing":
        if session.idempotency_key != key:
            raise HTTPException(status_code=409, detail="This import is already being committed.")
        _schedule_commit(background_tasks, session_id, current_user.id, key)
        return _public_session(session)
    if session.status != "review":
        raise HTTPException(status_code=409, detail="This import is not ready to commit.")
    if body.get("revision") != session.revision:
        raise HTTPException(status_code=409, detail="This import changed in another request. Reload it and try again.")
    duplicate_receipt = (await db.execute(select(NetflixImportSession).where(
        NetflixImportSession.user_id == current_user.id,
        NetflixImportSession.idempotency_key == key,
        NetflixImportSession.id != session_id,
    ))).scalar_one_or_none()
    if duplicate_receipt:
        raise HTTPException(status_code=409, detail="That idempotency key was used by another import.")
    session.idempotency_key = key
    session.status = "committing"
    session.phase = "commit"
    session.progress = {"current": 0, "total": len((session.payload or {}).get("items", [])), "message": "Starting import"}
    session.revision += 1
    session.error_message = None
    await db.commit()
    await db.refresh(session)
    _schedule_commit(background_tasks, session_id, current_user.id, key)
    return _public_session(session)


async def cleanup_expired_netflix_imports() -> int:
    """Erase private CSV and draft payloads once their seven-day window ends."""
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as db:
        result = await db.execute(select(NetflixImportSession).where(
            NetflixImportSession.expires_at <= _now(),
            NetflixImportSession.status != "committed",
            NetflixImportSession.status != "cancelled",
        ))
        expired = result.scalars().all()
        for session in expired:
            session.source_csv = None
            session.payload = {}
            session.status = "cancelled"
            session.phase = "complete"
            session.progress = {"current": 0, "total": 0, "message": "Import draft expired"}
            session.revision += 1
        if expired:
            await db.commit()
        return len(expired)


async def resume_incomplete_netflix_imports() -> int:
    """Resume persisted jobs after a process restart; review drafts remain idle."""
    maker = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with maker() as db:
        sessions = (await db.execute(select(NetflixImportSession).where(
            NetflixImportSession.status.in_(["preparing", "committing"]),
            NetflixImportSession.expires_at > _now(),
        ))).scalars().all()
        pending = [(s.id, s.user_id, s.status, (s.payload or {}).get("language"), s.idempotency_key) for s in sessions]
    resumed = 0
    for session_id, user_id, status, language, key in pending:
        if status == "preparing" and session_id not in _running:
            _running.add(session_id)
            asyncio.create_task(_prepare_session(session_id, user_id, language))
            resumed += 1
        elif status == "committing" and session_id not in _committing and key:
            _committing.add(session_id)
            asyncio.create_task(_commit_session(session_id, user_id, key))
            resumed += 1
    return resumed
