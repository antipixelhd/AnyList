"""Shared catalogue resolution for cloud imports."""

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.enrichment import create_media_safely, enrich_media
from models.base import MediaType
from models.media import Media
from models.show import Show

logger = logging.getLogger(__name__)


async def get_or_create_show(db: AsyncSession, tmdb_id: int, title: str, api_key: str | None) -> Show | None:
    result = await db.execute(select(Show).where(Show.tmdb_id == tmdb_id))
    show = result.scalars().first()
    if show:
        return show
    from core import tmdb
    try:
        d = await tmdb.get_show(tmdb_id, api_key=api_key)
    except Exception as exc:
        logger.warning("Could not fetch show tmdb=%s: %s", tmdb_id, exc)
        return None
    show = Show(
        tmdb_id=tmdb_id,
        title=d.get("name") or title,
        original_title=d.get("original_name"),
        overview=d.get("overview"),
        poster_path=tmdb.poster_url(d.get("poster_path")),
        backdrop_path=tmdb.poster_url(d.get("backdrop_path"), size="w1280"),
        tmdb_rating=d.get("vote_average"),
        status=d.get("status"),
        tagline=d.get("tagline"),
        first_air_date=d.get("first_air_date"),
        last_air_date=d.get("last_air_date"),
        tmdb_data={
            "genres": [g["name"] for g in d.get("genres", [])],
            "external_ids": d.get("external_ids", {}),
            "original_language": d.get("original_language"),
            "seasons": [
                {
                    "season_number": s["season_number"],
                    "poster_path": tmdb.poster_url(s.get("poster_path")),
                    "episode_count": s["episode_count"],
                    "name": s["name"],
                }
                for s in d.get("seasons", [])
            ],
        },
    )
    try:
        async with db.begin_nested():
            db.add(show)
            await db.flush()
    except IntegrityError:
        result = await db.execute(select(Show).where(Show.tmdb_id == tmdb_id))
        existing = result.scalars().first()
        if existing is None:
            raise
        return existing
    return show


async def get_or_create_movie_media(db: AsyncSession, tmdb_id: int, title: str, api_key: str | None) -> Media | None:
    result = await db.execute(
        select(Media).where(Media.tmdb_id == tmdb_id, Media.media_type == MediaType.movie)
    )
    media = result.scalars().first()
    if media:
        return media
    media, _created = await create_media_safely(db, tmdb_id, MediaType.movie, title=title)
    await enrich_media(media, api_key=api_key)
    return media


async def get_or_create_series_media(
    db: AsyncSession,
    tmdb_id: int,
    title: str,
    api_key: str | None,
) -> Media | None:
    result = await db.execute(
        select(Media).where(
            Media.tmdb_id == tmdb_id,
            Media.media_type == MediaType.series,
        )
    )
    media = result.scalars().first()
    if media:
        return media
    media, _created = await create_media_safely(db, tmdb_id, MediaType.series, title=title)
    await enrich_media(media, api_key=api_key)
    return media
