"""Apply Plex watchlist reconciliation and persist the shared pull/push baseline."""

import asyncio
import logging

from sqlalchemy import select, update, func, literal_column
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core import plex, tmdb
from core.enrichment import create_media_safely
from core.watchlist_reconcile import compute_new_baseline, media_key, plan_watchlist_reconcile
from db import engine
from models.base import MediaType
from models.connections import MediaServerConnection
from models.media import Media

logger = logging.getLogger("uvicorn.error")
# Pull and push jobs must share one reconcile lock per connection.
_plex_watchlist_locks: dict[int, asyncio.Lock] = {}
_PLEX_WATCHLIST_SLUG = "__plex_watchlist__"


def _plex_watchlist_lock(connection_id: int) -> asyncio.Lock:
    return _plex_watchlist_locks.setdefault(connection_id, asyncio.Lock())


def _plex_watchlist_remote_map(remote_items: list[dict]) -> dict[str, dict]:
    """Typed key -> raw watchlist item for every remote entry with a TMDB
    guid. Entries without one can't match anything local, so they stay out
    of the reconcile entirely."""
    remote_by_key: dict[str, dict] = {}
    for item in remote_items:
        kind = item.get("type")
        if kind not in ("movie", "show"):
            continue
        tmdb_id: int | None = None
        for guid in item.get("Guid") or []:
            gid = guid.get("id", "")
            if gid.startswith("tmdb://"):
                try:
                    tmdb_id = int(gid[7:])
                except ValueError:
                    pass
        if tmdb_id is None:
            continue
        remote_by_key.setdefault(media_key(kind, tmdb_id), item)
    return remote_by_key


async def _load_local_watchlist_state(db: AsyncSession, user_id: int):
    """The managed list row (or None) plus typed key -> (media_id, title)
    for its TMDB-mapped movies and shows."""
    from models.lists import List as ListModel, ListItem

    wl_result = await db.execute(
        select(ListModel).where(
            ListModel.user_id == user_id,
            ListModel.trakt_slug == _PLEX_WATCHLIST_SLUG,
        )
    )
    watchlist = wl_result.scalar_one_or_none()
    local_by_key: dict[str, tuple[int, str]] = {}
    if watchlist:
        rows = await db.execute(
            select(Media.id, Media.media_type, Media.tmdb_id, Media.title)
            .join(ListItem, ListItem.media_id == Media.id)
            .where(ListItem.list_id == watchlist.id)
        )
        for media_id, media_type, tmdb_id, title in rows:
            if tmdb_id is None:
                continue
            if media_type == MediaType.movie:
                kind = "movie"
            elif media_type == MediaType.series:
                kind = "show"
            else:
                continue
            local_by_key[media_key(kind, tmdb_id)] = (media_id, title)
    return watchlist, local_by_key


async def _apply_local_watchlist_changes(
    db: AsyncSession,
    user_id: int,
    watchlist,
    local_by_key: dict[str, tuple[int, str]],
    plan,
    remote_by_key: dict[str, dict],
    tmdb_api_key: str | None,
) -> set[str]:
    """Apply the plan's local side. Returns the keys actually present
    afterwards: an import that failed stays out, so the baseline never
    records state that doesn't exist."""
    from models.lists import List as ListModel, ListItem

    applied = set(local_by_key)

    if plan.add_local and not tmdb_api_key:
        print("  Warning: skipping Plex watchlist imports - no TMDB API key configured")
    elif plan.add_local:
        if not watchlist:
            watchlist = ListModel(user_id=user_id, name="Plex - Watchlist", trakt_slug=_PLEX_WATCHLIST_SLUG)
            db.add(watchlist)
            await db.flush()
        for key in sorted(plan.add_local):
            item = remote_by_key.get(key, {})
            kind, _, raw_id = key.partition(":")
            tmdb_id_item = int(raw_id)
            try:
                # A savepoint per item - db is the same session the caller
                # keeps using for language backfill and watch-history backfill
                # afterward, so one item's DB-level failure (e.g. an ON
                # CONFLICT mismatch) must not abort the whole shared
                # transaction and take those later steps down with it.
                async with db.begin_nested():
                    if kind == "movie":
                        media_result = await db.execute(
                            select(Media)
                            .where(Media.tmdb_id == tmdb_id_item, Media.media_type == MediaType.movie)
                            .order_by(Media.id)
                        )
                        media = media_result.scalars().first()
                        if not media:
                            d = await tmdb.get_movie(tmdb_id_item, api_key=tmdb_api_key)
                            media, _created = await create_media_safely(
                                db, tmdb_id_item, MediaType.movie,
                                title=d.get("title") or item.get("title", ""),
                                poster_path=tmdb.poster_url(d.get("poster_path")),
                                backdrop_path=tmdb.poster_url(d.get("backdrop_path"), size="w1280"),
                                release_date=d.get("release_date"),
                                tmdb_rating=d.get("vote_average"),
                                overview=d.get("overview"),
                                adult=d.get("adult", False),
                            )
                    else:
                        media_result = await db.execute(
                            select(Media)
                            .where(Media.tmdb_id == tmdb_id_item, Media.media_type == MediaType.series)
                            .order_by(Media.id)
                        )
                        media = media_result.scalars().first()
                        if not media:
                            d = await tmdb.get_show(tmdb_id_item, api_key=tmdb_api_key)
                            media, _created = await create_media_safely(
                                db, tmdb_id_item, MediaType.series,
                                title=d.get("name") or item.get("title", ""),
                                poster_path=tmdb.poster_url(d.get("poster_path")),
                                backdrop_path=tmdb.poster_url(d.get("backdrop_path"), size="w1280"),
                                release_date=d.get("first_air_date"),
                                tmdb_rating=d.get("vote_average"),
                                overview=d.get("overview"),
                                adult=d.get("adult", False),
                            )

                    # Idempotent against a concurrent add from the lists UI, which
                    # doesn't hold this connection's reconcile lock. uq_list_item was
                    # replaced by the uq_list_item_season expression index (#142) -
                    # ON CONFLICT ON CONSTRAINT needs a real constraint, not a bare
                    # index, so the conflict target is given as the matching
                    # expression list instead (verified against the actual index).
                    # The -1 must be a literal, not a bound parameter: once this
                    # statement's plan is executed 5+ times on the same connection,
                    # Postgres switches from a per-execution custom plan to a cached
                    # generic plan, which can no longer prove a coalesce(col, $N)
                    # bind param is the same expression as the index's
                    # coalesce(col, -1) - the arbiter match then silently fails with
                    # "no unique or exclusion constraint matching the ON CONFLICT
                    # specification" for every item after the 5th in a given run.
                    await db.execute(
                        insert(ListItem)
                        .values(list_id=watchlist.id, media_id=media.id)
                        .on_conflict_do_nothing(
                            index_elements=[ListItem.list_id, ListItem.media_id, func.coalesce(ListItem.season_number, literal_column("-1"))]
                        )
                    )
                applied.add(key)
            except Exception as exc:
                print(f"  Warning: failed to import Plex watchlist item {key}: {exc}")

    if plan.remove_local and watchlist:
        remove_ids = [local_by_key[key][0] for key in plan.remove_local if key in local_by_key]
        if remove_ids:
            await db.execute(
                ListItem.__table__.delete().where(
                    ListItem.list_id == watchlist.id,
                    ListItem.media_id.in_(remove_ids),
                )
            )
        applied -= set(plan.remove_local)

    return applied


async def _apply_remote_watchlist_changes(
    conn,
    plan,
    local_by_key: dict[str, tuple[int, str]],
    remote_by_key: dict[str, dict],
) -> tuple[set[str], set[str]]:
    """Push the plan's remote side to Plex. Failed keys are reported back so
    the baseline keeps them pending and the next reconcile retries them."""
    failed_add: set[str] = set()
    failed_remove: set[str] = set()

    for key in sorted(plan.push_add):
        kind, _, raw_id = key.partition(":")
        _, title = local_by_key.get(key, (None, None))
        plex_type = "movie" if kind == "movie" else "show"
        try:
            rating_key = await plex.resolve_tmdb_ratingkey(conn.plex_account_token, int(raw_id), plex_type, title)
            if not rating_key:
                logger.warning(
                    "Could not resolve Plex ratingKey for %s (connection %s); will retry on the next reconcile",
                    key, conn.id,
                )
                failed_add.add(key)
                continue
            if not await plex.add_to_watchlist(conn.plex_account_token, rating_key):
                failed_add.add(key)
        except Exception as exc:
            logger.warning("Plex watchlist add failed for %s (connection %s): %s", key, conn.id, exc)
            failed_add.add(key)

    for key in sorted(plan.push_remove):
        # push_remove keys are on the remote by definition, so the fetched
        # item usually carries its own ratingKey; resolving is the fallback.
        item = remote_by_key.get(key) or {}
        rating_key = item.get("ratingKey")
        try:
            if not rating_key:
                kind, _, raw_id = key.partition(":")
                plex_type = "movie" if kind == "movie" else "show"
                rating_key = await plex.resolve_tmdb_ratingkey(conn.plex_account_token, int(raw_id), plex_type, item.get("title"))
            if not rating_key or not await plex.remove_from_watchlist(conn.plex_account_token, rating_key):
                failed_remove.add(key)
        except Exception as exc:
            logger.warning("Plex watchlist remove failed for %s (connection %s): %s", key, conn.id, exc)
            failed_remove.add(key)

    return failed_add, failed_remove


async def reconcile_watchlist(user_id: int, connection_id: int, tmdb_api_key: str | None) -> None:
    """Reconcile the Plex account watchlist with the managed Scrob list in
    both directions, against this connection's last-synced baseline (see
    core/watchlist_reconcile.py for the semantics).

    The pull job and the full push job both call this, and it honors both
    direction flags itself, so it doesn't matter which job runs first.
    Never raises: failures log a warning and leave the baseline alone so
    the next run retries.

    Runs in its own DB session, same rationale as _backfill_plex_languages -
    when called from the main collection/watched sync pass, sharing that
    session (which has by then executed tens of thousands of statements)
    was observed to make some of this function's own inserts spuriously
    fail their ON CONFLICT match against a real, verified-correct index,
    for reasons that couldn't be reproduced in isolation. A connection this
    function's own is unaffected."""
    async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with async_session() as db:
        conn = await db.get(MediaServerConnection, connection_id)
        if not conn:
            return

        pull_enabled = bool(conn.plex_sync_watchlist)
        push_enabled = bool(conn.plex_push_watchlist)
        if not (pull_enabled or push_enabled):
            return

        async with _plex_watchlist_lock(conn.id):
            try:
                # A concurrent job may have reconciled while we waited on the
                # lock, and conn was loaded well before it. Re-read the baseline.
                baseline_result = await db.execute(
                    select(MediaServerConnection.plex_watchlist_synced_keys).where(
                        MediaServerConnection.id == conn.id
                    )
                )
                baseline = baseline_result.scalar_one_or_none()

                print("  Fetching Plex watchlist...")
                remote_items = await plex.get_watchlist(conn.plex_account_token)
                print(f"  {len(remote_items)} items in Plex watchlist")
                remote_by_key = _plex_watchlist_remote_map(remote_items)

                watchlist, local_by_key = await _load_local_watchlist_state(db, user_id)
                if watchlist is None and baseline is not None:
                    # The managed list was deleted in the UI. Without this reset,
                    # every baseline key would read as a local deletion and wipe
                    # the user's real Plex watchlist. Start over instead.
                    baseline = None

                plan = plan_watchlist_reconcile(
                    local_by_key.keys(),
                    remote_by_key.keys(),
                    baseline,
                    pull_enabled=pull_enabled,
                    push_enabled=push_enabled,
                )
                if plan.suppressed:
                    logger.warning(
                        "Plex watchlist reconcile suppressed for connection %s: %s. Not acting on a fetch "
                        "that may be truncated. If the removals are real, remove the items from the list "
                        "in Scrob, or toggle watchlist sync off and on to rebuild from a fresh baseline.",
                        conn.id, plan.suppressed_reason,
                    )
                    return

                applied_local = await _apply_local_watchlist_changes(
                    db, user_id, watchlist, local_by_key, plan, remote_by_key, tmdb_api_key
                )
                failed_add, failed_remove = await _apply_remote_watchlist_changes(
                    conn, plan, local_by_key, remote_by_key
                )

                new_baseline = compute_new_baseline(
                    applied_local, failed_push_add=failed_add, failed_push_remove=failed_remove
                )
                await db.execute(
                    update(MediaServerConnection)
                    .where(MediaServerConnection.id == conn.id)
                    .values(plex_watchlist_synced_keys=new_baseline)
                )
                await db.commit()
                print(
                    f"  Plex watchlist reconcile complete: "
                    f"+{len(plan.add_local)}/-{len(plan.remove_local)} local, "
                    f"+{len(plan.push_add) - len(failed_add)}/-{len(plan.push_remove) - len(failed_remove)} on Plex"
                )
            except Exception as exc:
                print(f"  Warning: Plex watchlist reconcile failed: {exc}")
                await db.rollback()
