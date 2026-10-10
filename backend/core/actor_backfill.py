"""Bounded shared cast refresh for listed titles; no calls from statistics reads."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import exists, or_, select

from core import catalogue
from core import tvdb
from core.catalogue_normalize import normalize_tmdb, normalize_tvdb
from models.base import MediaType
from models.catalogue import MetadataSnapshot
from models.media import Media
from models.tracking import TrackedEntry
from models.show import Show

VERSION = 1
BATCH_SIZE = 25


async def backfill_actors(db, providers, *, limit=BATCH_SIZE, force=False):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    attrs = Media.tmdb_data
    query = select(Media).where(Media.media_type.in_([MediaType.movie, MediaType.series]),
        exists(select(TrackedEntry.id).where(TrackedEntry.media_id == Media.id)),
        or_(Media.tmdb_id.is_not(None), Media.tvdb_id.is_not(None)))
    if not force:
        query = query.where(or_(attrs["actor_backfill_attempted_at"].astext.is_(None),
            attrs["actor_metadata_version"].astext != str(VERSION),
            attrs["actor_backfill_attempted_at"].astext < (now - timedelta(days=7)).isoformat()))
    rows = list((await db.scalars(query.order_by(attrs["actor_backfill_attempted_at"].astext.asc().nulls_first(), Media.id).limit(limit))).all())
    result = {"examined": len(rows), "updated": 0, "failed": 0}
    for row in rows:
        success = False
        sources = [("tmdb", row.tmdb_id)] if row.tmdb_id and providers.tmdb_key else []
        tvdb_id = row.tvdb_id
        if row.media_type == MediaType.series and not tvdb_id and row.tmdb_id:
            tvdb_id = await db.scalar(select(Show.tvdb_id).where(Show.tmdb_id == row.tmdb_id).limit(1))
        if row.media_type == MediaType.movie and not tvdb_id and row.imdb_id and providers.tvdb_key:
            try:
                async with providers.http.lane("tvdb"):
                    tvdb_id = await tvdb.find_movie(row.imdb_id, providers.tvdb_key)
            except Exception:
                result["failed"] += 1
        if tvdb_id and providers.tvdb_key:
            sources.append(("tvdb", tvdb_id))
        for provider, identifier in sources:
            try:
                if provider == "tvdb":
                    snapshot = await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.provider == provider,
                        MetadataSnapshot.kind == row.media_type.value, MetadataSnapshot.external_id == str(identifier)))
                    if snapshot and snapshot.fetched_at and "_people" not in (snapshot.payload or {}):
                        snapshot.expires_at = now  # Old snapshots need verified person cross-references once.
                        await db.commit()
                refreshed = await catalogue.refresh_metadata(db, providers, provider, row.media_type.value, str(identifier))
                if refreshed["status"] in ("cached", "updated") and refreshed["entity_id"]:
                    # Existing snapshots predate role artwork; project last-good
                    # payloads through the current normalizer without a new call.
                    if refreshed["status"] == "cached":
                        snapshot = await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.provider == provider,
                            MetadataSnapshot.kind == row.media_type.value, MetadataSnapshot.external_id == str(identifier)))
                        if snapshot and snapshot.payload:
                            doc = normalize_tmdb(snapshot.payload, row.media_type.value) if provider == "tmdb" else normalize_tvdb(snapshot.payload, row.media_type.value)
                            async with db.begin_nested():
                                await catalogue.ingest_document(db, doc, provider)
                    success = True
                    if provider == "tmdb" and row.media_type == MediaType.movie and providers.tvdb_key:
                        imdb_id = row.imdb_id
                        if not imdb_id:
                            snapshot = await db.scalar(select(MetadataSnapshot).where(MetadataSnapshot.provider == "tmdb", MetadataSnapshot.kind == "movie", MetadataSnapshot.external_id == str(identifier)))
                            imdb_id = (snapshot.payload or {}).get("imdb_id") if snapshot else None
                        if tvdb_id is None and imdb_id:
                            async with providers.http.lane("tvdb"):
                                tvdb_id = await tvdb.find_movie(imdb_id, providers.tvdb_key)
                        if tvdb_id and not any(p == "tvdb" for p, _ in sources):
                            sources.append(("tvdb", tvdb_id))
                elif refreshed["status"] not in ("refreshing",):
                    result["failed"] += 1
            except Exception:
                await db.rollback()
                await db.refresh(row)
                result["failed"] += 1
        if sources:
            row.tmdb_data = {**(row.tmdb_data or {}), "actor_metadata_version": VERSION,
                             "actor_backfill_attempted_at": now.isoformat()}
            await db.commit()
        result["updated"] += int(success)
    return result
