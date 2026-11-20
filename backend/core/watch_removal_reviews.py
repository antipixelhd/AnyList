"""Present provider watch removals without changing their episode-level identity."""

from collections import defaultdict

from sqlalchemy import or_, select

from models import List, ListItem, Media, Show, TrackedEntry
from models.base import MediaType


async def watch_is_listed(db, user_id, media_id, root_id):
    ids = {media_id, root_id}
    tracked = select(TrackedEntry.id).where(TrackedEntry.user_id == user_id, TrackedEntry.media_id.in_(ids))
    custom = select(ListItem.id).join(List).where(List.user_id == user_id, ListItem.media_id.in_(ids))
    return bool(await db.scalar(select(or_(tracked.exists(), custom.exists()))))


def watch_removal_sources(review):
    """Recognize both new reviews and existing combined snapshot removals."""
    payload = review.payload or {}
    observations = payload.get("observations") or []
    if (review.kind != "watch_conflict" or review.provider != "combined"
            or payload.get("category") != "watched state" or not observations):
        return ()
    sources = set()
    for observation in observations:
        prefix, _, connection = observation.get("source", "").partition(":")
        if (prefix != "connection" or not connection.isascii() or not connection.isdecimal()
                or observation.get("value") is not False):
            return ()
        sources.add(int(connection))
    return tuple(sorted(sources))


def watch_removal_group(review, media):
    sources = watch_removal_sources(review)
    if sources and media is not None and media.media_type == MediaType.episode and media.show_id is not None:
        return media.show_id, sources
    return None


async def project_watch_reviews(db, rows, events, connection_types):
    episode_rows = [(review, media) for review, media in rows
                    if media is not None and media.media_type == MediaType.episode and media.show_id is not None]
    show_ids = {media.show_id for _, media in episode_rows}
    shows = {show.id: show for show in (await db.scalars(
        select(Show).where(Show.id.in_(show_ids)))).all()} if show_ids else {}
    series_by_tmdb, series_by_tvdb = {}, {}
    if shows:
        tmdb_ids = {show.tmdb_id for show in shows.values() if show.tmdb_id}
        tvdb_ids = {show.tvdb_id for show in shows.values() if show.tvdb_id}
        series = await db.scalars(select(Media).where(Media.media_type == MediaType.series,
            or_(Media.tmdb_id.in_(tmdb_ids), Media.tvdb_id.in_(tvdb_ids))).order_by(Media.id))
        for root in series:
            series_by_tmdb.setdefault(root.tmdb_id, root)
            series_by_tvdb.setdefault(root.tvdb_id, root)

    from core.tracking_projection import media_data
    grouped = defaultdict(list)
    for (review, media), event in zip(rows, events, strict=True):
        episode = media is not None and media.media_type == MediaType.episode
        show = shows.get(media.show_id) if episode else None
        payload = dict(event["payload"])
        if episode:
            roots = [root for root in (
                series_by_tmdb.get(show.tmdb_id) if show and show.tmdb_id else None,
                series_by_tvdb.get(show.tvdb_id) if show and show.tvdb_id else None,
            ) if root is not None]
            root = min(roots, key=lambda root: root.id) if roots else None
            href = f"/title/{root.id}?tab=seasons" if root else (
                f"/discover-title?type=series&id={show.tmdb_id}" if show and show.tmdb_id else None)
            payload["episode_context"] = {
                "series_title": show.title if show else None,
                "href": href,
                "poster": media_data(root)["poster"] if root else None,
            }
        sources = watch_removal_sources(review)
        if sources:
            payload["watch_removal"] = {
                "providers": list(dict.fromkeys(connection_types.get(source) or "a connected service" for source in sources)),
                "episodes": [{"title": media.title, "season": media.season_number, "number": media.episode_number}]
                if episode else [],
            }
            group = watch_removal_group(review, media)
            if group and review.state == "pending":
                grouped[group].append(event)
        event["payload"] = payload

    hidden = set()
    for group in grouped.values():
        if len(group) < 2:
            continue
        primary, *others = group
        removal = primary["payload"]["watch_removal"]
        removal["related_review_ids"] = [event["id"] for event in others]
        removal["episodes"] = sorted(
            (episode for event in group for episode in event["payload"]["watch_removal"]["episodes"]),
            key=lambda episode: (episode["season"] or 0, episode["number"] or 0),
        )
        hidden.update(removal["related_review_ids"])
    return [event for event in events if event["id"] not in hidden]
