"""Durable manual matching and ignore handling for cloud-provider imports."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select

from models.media import Media
from models.tracking import ProviderIgnore, ProviderMatch, SyncReview


def provider_identity(kind: str, entry: dict[str, Any]) -> tuple[str, str]:
    singular = {"movies": "movie", "shows": "show", "seasons": "season", "episodes": "episode"}.get(kind, kind)
    data = entry.get(singular)
    data = data if isinstance(data, dict) else entry
    title = str(data.get("title") or data.get("name") or entry.get("title") or "Unknown title")
    ids = data.get("ids") if isinstance(data.get("ids"), dict) else {}
    if kind in {"seasons", "episodes"}:
        show = entry.get("show") or data.get("show") or {}
        show = show if isinstance(show, dict) else {}
        show_ids = show.get("ids") if isinstance(show.get("ids"), dict) else {}
        ids = {**show_ids, **{f"item_{key}": value for key, value in ids.items()}}
        season = data.get("season", entry.get("season", data.get("number") if kind == "seasons" else None))
        if isinstance(season, dict):
            season = season.get("number")
        episode = data.get("number", data.get("episode", entry.get("episode"))) if kind == "episodes" else None
        suffix = f":s{season if season is not None else '?'}" + (f":e{episode if episode is not None else '?'}" if kind == "episodes" else "")
    else:
        suffix = ""
    for namespace in ("trakt", "simkl", "mdblist", "imdb", "tvdb", "tmdb", "slug"):
        value = ids.get(namespace) or data.get(f"{namespace}_id")
        if value not in (None, ""):
            return f"{kind}:{namespace}:{value}{suffix}", title
    stable = json.dumps({"kind": kind, "title": title.casefold(), "year": data.get("year"), "suffix": suffix}, sort_keys=True)
    digest = hashlib.sha256(stable.encode("utf-8")).hexdigest()[:24]
    return f"{kind}:fingerprint:{digest}{suffix}", title


async def provider_override(db, *, user_id: int, provider: str, kind: str, entry: dict[str, Any]) -> tuple[Media | None, bool, str, str]:
    external_key, title = provider_identity(kind, entry)
    ignored = (await db.execute(select(ProviderIgnore.id).where(
        ProviderIgnore.user_id == user_id,
        ProviderIgnore.provider == provider,
        ProviderIgnore.external_key == external_key,
    ))).first() is not None
    if ignored:
        return None, True, external_key, title
    match = (await db.execute(select(ProviderMatch).where(
        ProviderMatch.user_id == user_id,
        ProviderMatch.provider == provider,
        ProviderMatch.external_key == external_key,
    ))).scalar_one_or_none()
    return (await db.get(Media, match.media_id) if match else None), False, external_key, title


async def record_unmatched_import(db, *, user_id: int, provider: str, kind: str, entry: dict[str, Any]) -> bool:
    mapped, ignored, external_key, title = await provider_override(
        db, user_id=user_id, provider=provider, kind=kind, entry=entry
    )
    if mapped or ignored:
        return False
    pending = (await db.execute(select(SyncReview).where(
        SyncReview.user_id == user_id,
        SyncReview.provider == provider,
        SyncReview.kind == "unmatched_import",
        SyncReview.state == "pending",
        SyncReview.payload["external_key"].astext == external_key,
    ).limit(1))).scalar_one_or_none()
    if pending is None:
        label = {"trakt": "Trakt", "simkl": "Simkl", "mdblist": "MDBList"}.get(provider, provider)
        db.add(SyncReview(
            user_id=user_id,
            provider=provider,
            kind="unmatched_import",
            priority="high",
            payload={"external_key": external_key, "title": title, "media_kind": kind},
            message=f'{label} could not match “{title}”. Match it to a catalogue title or ignore it for this provider.',
        ))
    return True
