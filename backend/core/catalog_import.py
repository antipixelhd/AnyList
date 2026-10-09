"""Shared catalogue resolution for cloud imports."""

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.descriptive_metadata import tmdb_fields
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
            **tmdb_fields(d),
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


async def get_or_create_episode_media(
    db: AsyncSession,
    show_id: int,
    show_tmdb_id: int,
    season_number: int,
    episode_number: int,
    api_key: str | None,
    season_cache: dict[tuple[int, int], dict] | None = None,
) -> Media | None:
    result = await db.execute(
        select(Media).where(
            Media.show_id == show_id,
            Media.season_number == season_number,
            Media.episode_number == episode_number,
            Media.media_type == MediaType.episode,
        )
    )
    media = result.scalars().first()
    if media:
        return media
    from core import tmdb
    # Fetch episode detail from TMDB
    try:
        cache_key = (show_tmdb_id, season_number)
        if season_cache is not None and cache_key in season_cache:
            season_data = season_cache[cache_key]
        else:
            season_data = await tmdb.get_season(show_tmdb_id, season_number, api_key=api_key)
            if season_cache is not None:
                season_cache[cache_key] = season_data
    except Exception as exc:
        logger.warning("Could not fetch episode s%se%s for show tmdb=%s: %s", season_number, episode_number, show_tmdb_id, exc)
        return None


    ep_map = {ep["episode_number"]: ep for ep in season_data.get("episodes", [])}
    ep = ep_map.get(episode_number)
    if not ep:
        # TMDB has no such episode (provider numbering mismatch, e.g. a Plex/Trakt
        # special counted differently) — don't fabricate a placeholder row for it,
        # since it can never be enriched and would show up as a broken/phantom
        # episode (e.g. in Next Up) with no real metadata behind it.
        logger.warning(
            "Imported episode s%se%s not found on TMDB for show tmdb=%s — skipping",
            season_number, episode_number, show_tmdb_id,
        )
        return None
    media, _created = await create_media_safely(
        db,
        ep["id"],
        MediaType.episode,
        title=ep["name"],
        overview=ep.get("overview"),
        poster_path=tmdb.poster_url(ep.get("still_path"), size="w500"),
        release_date=ep.get("air_date"),
        tmdb_rating=ep.get("vote_average"),
        runtime=ep.get("runtime"),  # see #169
        show_id=show_id,
        season_number=season_number,
        episode_number=episode_number,
        tmdb_data={"runtime": ep.get("runtime"), "cast": []},
    )
    return media
