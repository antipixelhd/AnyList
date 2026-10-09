"""Shared show lookup, identity backfill, and TMDB metadata mapping."""

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from core import tmdb
from core.descriptive_metadata import tmdb_fields as descriptive_fields
from core.identity import coerce_id, link_show_ids, show_tvdb_id_is_free
from models.show import Show


def apply_show_metadata(show: Show, data: dict) -> None:
    """Writes TMDB show-detail fields onto a local Show row. Shared by the
    manual 'Refresh Metadata' action below, the daily metadata sweep
    (main.py's _show_metadata_refresher), and Next Up's on-demand self-heal
    (routers/history.py), so all keep exactly the same field mapping.

    Never call this with TMDB data for a show whose tmdb_data snapshot is
    TVDB-sourced (tmdb_data.source == "tvdb") - its season layout is
    TVDB-shaped (#335) and would be clobbered."""
    show.title = data.get("name") or show.title
    show.original_title = data.get("original_name")
    show.overview = data.get("overview")
    show.poster_path = tmdb.poster_url(data.get("poster_path"))
    show.backdrop_path = tmdb.poster_url(data.get("backdrop_path"), size="w1280")
    show.tmdb_rating = data.get("vote_average")
    show.status = data.get("status")
    show.tagline = data.get("tagline")
    show.first_air_date = data.get("first_air_date")
    show.last_air_date = data.get("last_air_date")
    show.tmdb_data = {
        **descriptive_fields(data, show.tmdb_data),
        "genres": [g["name"] for g in data.get("genres", [])],
        "external_ids": data.get("external_ids", {}),
        "original_language": data.get("original_language"),
        # Kept so capped_season_episode_counts() can exclude unaired episodes
        # from cache-only callers like Next Up, which have no tmdb_extra (#296).
        "last_episode_to_air": data.get("last_episode_to_air"),
        # Kept so Next Up's missing-episode fallback can tell from the DB alone
        # whether a new episode can have aired since this snapshot was written
        # (routers/history.py's _next_up_needs_live_fetch, #332).
        "next_episode_to_air": data.get("next_episode_to_air"),
        # When this snapshot was written - the daily metadata sweep and Next Up
        # use it to bound how stale the snapshot may get before re-fetching.
        "refreshed_at": datetime.now(timezone.utc).isoformat(),
        "seasons": [
            {
                "season_number": s["season_number"],
                "poster_path": tmdb.poster_url(s.get("poster_path")),
                "episode_count": s["episode_count"],
                "name": s["name"],
                "air_date": s.get("air_date"),
                "overview": s.get("overview"),
            }
            for s in data.get("seasons", [])
        ],
        "networks": [
            {
                "id": n.get("id"),
                "name": n.get("name"),
                "logo_path": n.get("logo_path"),
                "origin_country": n.get("origin_country"),
            }
            for n in data.get("networks", [])
        ],
    }


async def find_or_create_show(db: AsyncSession, series_tmdb_id: int, api_key: str = None) -> Show:
    result = await db.execute(select(Show).where(Show.tmdb_id == series_tmdb_id))
    show = result.scalar_one_or_none()
    if show:
        # Backfill the TVDB cross-reference TMDB already told us about, so a
        # TMDB-matched show also carries its tvdb_id (dual identity, step 1).
        ext_tvdb = coerce_id(((show.tmdb_data or {}).get("external_ids") or {}).get("tvdb_id"))
        if ext_tvdb and not show.tvdb_id:
            if await link_show_ids(db, show, tvdb_id=ext_tvdb):
                await db.flush()
        return show
    if not show:
        show_data = await tmdb.get_show(series_tmdb_id, api_key=api_key)
        ext_tvdb = coerce_id((show_data.get("external_ids") or {}).get("tvdb_id"))
        if ext_tvdb and not await show_tvdb_id_is_free(db, ext_tvdb):
            ext_tvdb = None
        show = Show(
            tmdb_id=series_tmdb_id,
            tvdb_id=ext_tvdb,
            title=show_data.get("name", ""),
            original_title=show_data.get("original_name"),
            overview=show_data.get("overview"),
            poster_path=tmdb.poster_url(show_data.get("poster_path")),
            backdrop_path=tmdb.poster_url(show_data.get("backdrop_path"), size="w1280"),
            tmdb_rating=show_data.get("vote_average"),
            status=show_data.get("status"),
            tagline=show_data.get("tagline"),
            first_air_date=show_data.get("first_air_date"),
            last_air_date=show_data.get("last_air_date"),
            tmdb_data={
                **descriptive_fields(show_data),
                "genres": [g["name"] for g in show_data.get("genres", [])],
                "external_ids": show_data.get("external_ids", {}),
                "seasons": [
                    {
                        "season_number": s["season_number"],
                        "poster_path": tmdb.poster_url(s.get("poster_path")),
                        "episode_count": s["episode_count"],
                        "name": s["name"],
                    }
                    for s in show_data.get("seasons", [])
                ],
                "networks": [
                    {
                        "id": n.get("id"),
                        "name": n.get("name"),
                        "logo_path": n.get("logo_path"),
                        "origin_country": n.get("origin_country"),
                    }
                    for n in show_data.get("networks", [])
                ],
            },
        )
        try:
            async with db.begin_nested():
                db.add(show)
                await db.flush()
        except IntegrityError:
            winner = (await db.execute(select(Show).where(Show.tmdb_id == series_tmdb_id))).scalar_one_or_none()
            if winner is None:
                raise
            return winner
    return show
