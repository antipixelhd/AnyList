"""Viewer-owned editor snapshots, loaded in batches without provider requests."""

from sqlalchemy import or_, select, tuple_

from core.tracking_rules import effective_score
from models import Collection, CollectionFile, Media, MediaServerConnection
from models.base import CollectionSource
from models.streaming_library import StreamingLibraryIntent
from models.tracking import TrackedEntry


async def attach_editor_context(db, viewer, results, *, media_rows=None):
    if not viewer or not results:
        return
    ids = {row["id"] for row in results if row.get("id")}
    external = {(row["type"], row["tmdb_id"]) for row in results if row.get("tmdb_id")}
    media = media_rows if media_rows is not None else (await db.execute(select(Media).where(or_(
        Media.id.in_(ids), tuple_(Media.media_type, Media.tmdb_id).in_(external),
    )))).scalars().all()
    by_id = {row.id: row for row in media}
    by_external = {(row.media_type.value, row.tmdb_id): row for row in media}
    ids = set(by_id)
    entries = {row.media_id: row for row in (await db.execute(select(TrackedEntry).where(
        TrackedEntry.user_id == viewer.id, TrackedEntry.media_id.in_(ids),
    ))).scalars().all()} if ids else {}
    available = bool((await db.execute(select(MediaServerConnection.id).where(
        MediaServerConnection.user_id == viewer.id,
        MediaServerConnection.type.in_(("stremio", "nuvio")),
    ).limit(1))).first())
    intents, observed = {}, set()
    if available and ids:
        intents = dict((await db.execute(select(
            StreamingLibraryIntent.media_id, StreamingLibraryIntent.desired,
        ).where(StreamingLibraryIntent.user_id == viewer.id,
                StreamingLibraryIntent.media_id.in_(ids)))).all())
        observed = set((await db.execute(select(Collection.media_id).join(
            CollectionFile, CollectionFile.collection_id == Collection.id,
        ).where(Collection.user_id == viewer.id, Collection.media_id.in_(ids),
                CollectionFile.source.in_((CollectionSource.stremio, CollectionSource.nuvio)),
                CollectionFile.connection_id.isnot(None)))).scalars().all())
    for row in results:
        cached = by_id.get(row.get("id")) or by_external.get((row["type"], row.get("tmdb_id")))
        if cached:
            row["id"] = cached.id
            row["backdrop"] = cached.backdrop_path
            row["poster"] = row.get("poster") or cached.poster_path
        entry = entries.get(row.get("id"))
        row["entry"] = {
            "status": entry.status, "progress": entry.progress,
            "manual_score": entry.manual_score, "rating_mode": entry.rating_mode,
            "score": effective_score(entry.rating_mode, entry.manual_score, entry.season_scores),
            "season_scores": entry.season_scores, "favorite": entry.favorite,
            "start_date": entry.start_date, "finish_date": entry.finish_date,
            "rewatch_count": entry.rewatch_count, "notes": entry.notes,
        } if entry else None
        row["list_status"] = entry.status if entry else None
        row["score"] = row["entry"]["score"] if entry else None
        row["rating_mode"] = entry.rating_mode if entry else "manual"
        row["editor_library"] = {
            "available": available,
            "desired": intents.get(row.get("id"), row.get("id") in observed),
        }
