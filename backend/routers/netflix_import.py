"""Private, resumable Netflix viewing-history import drafts."""

from __future__ import annotations
from core import netflix_sessions

import csv
import io
import uuid
from copy import deepcopy
from datetime import date, timedelta

from fastapi import APIRouter, BackgroundTasks, Body, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from core import tmdb, settings_store
from db import get_db
from dependencies import get_current_user
from models import Media, NetflixImportSession, User
from models.base import MediaType
from models.tracking import TrackedEntry

router = APIRouter()

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
DRAFT_DAYS = 7
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
    groups = netflix_sessions._normalise_groups(prepared)
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
            source_positions, chosen_positions, excluded = netflix_sessions._show_import_positions(item, outcome, date.today())
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
    if session.expires_at <= netflix_sessions._now() and session.status != "committed":
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
    netflix_sessions._resolve_draft_items(items)
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


def _schedule_commit(background_tasks: BackgroundTasks, session_id: str, user_id: int, idempotency_key: str | None) -> None:
    if idempotency_key and session_id not in netflix_sessions._committing:
        netflix_sessions._committing.add(session_id)
        background_tasks.add_task(netflix_sessions._commit_session, session_id, user_id, idempotency_key)


def _schedule(background_tasks: BackgroundTasks, session_id: str, user_id: int, language: str | None) -> None:
    if session_id not in netflix_sessions._running:
        netflix_sessions._running.add(session_id)
        background_tasks.add_task(netflix_sessions._prepare_session, session_id, user_id, language)


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
    now = netflix_sessions._now()
    await db.execute(delete(NetflixImportSession).where(
        NetflixImportSession.user_id == current_user.id,
        NetflixImportSession.status != "committed",
        NetflixImportSession.expires_at <= now,
    ))
    if not await settings_store.get_user_tmdb_key(db, current_user.id, cached=False):
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
    if session.status == "preparing" and session_id not in netflix_sessions._running:
        _schedule(background_tasks, session_id, current_user.id, (session.payload or {}).get("language"))
    elif session.status == "committing" and session_id not in netflix_sessions._committing:
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
        api_key = await settings_store.get_user_tmdb_key(db, current_user.id, cached=False)
        language = (before.payload or {}).get("language")
        await db.commit()
        if not api_key:
            raise HTTPException(status_code=400, detail="A TMDB API key is required to remap titles.")
        try:
            remapped_item = await _prepare_remapped_item(old_item, media_type, tmdb_id, api_key, language)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
        except Exception:
            netflix_sessions.logger.exception("Could not prepare Netflix remap %s -> %s", item_id, tmdb_id)
            raise HTTPException(status_code=502, detail="The selected title could not be loaded from TMDB. Try again.")

    session = await _load_owned(db, session_id, current_user.id, lock=True)
    if session.status != "review":
        raise HTTPException(status_code=409, detail="This import is no longer editable.")
    if patch.get("revision") != session.revision:
        raise HTTPException(status_code=409, detail="This import changed in another request. Reload it and try again.")
    payload = dict(session.payload or {})
    items = deepcopy(payload.get("items", []))
    netflix_sessions._resolve_draft_items(items)
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
            netflix_sessions._show_import_positions(item, item["outcome"], date.today())
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
    candidate = item.get("match", {}).get("candidate")
    if candidate:
        media_type = MediaType.movie if item["kind"] == "movie" else MediaType.series
        media = (await db.execute(select(Media).where(Media.tmdb_id == candidate["tmdb_id"], Media.media_type == media_type))).scalar_one_or_none()
        tracked = (await db.execute(select(TrackedEntry).where(TrackedEntry.user_id == current_user.id, TrackedEntry.media_id == media.id))).scalar_one_or_none() if media else None
        item["existing"] = netflix_sessions._existing_state(media, tracked)
    payload["items"] = items
    session.payload = payload
    session.error_message = None
    session.revision += 1
    session.updated_at = netflix_sessions._now()
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
        netflix_sessions._cancelled.add(session_id)
    session = await _load_owned(db, session_id, current_user.id, lock=True)
    if session.status == "committed":
        raise HTTPException(status_code=409, detail="An imported history cannot be cancelled after it has been committed.")
    if session.status != "cancelled":
        netflix_sessions._cancelled.add(session_id)
        session.status = "cancelled"
        session.phase = "complete"
        session.source_csv = None
        session.payload = {}
        session.progress = {"current": 0, "total": 0, "message": "Import cancelled"}
        session.revision += 1
        session.updated_at = netflix_sessions._now()
        await db.commit()
        await db.refresh(session)
    return _public_session(session)


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
