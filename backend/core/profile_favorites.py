"""Compact Favorites projections, including independent and legacy screen favorites."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.media_images import IMAGE_POLICY_VERSION
from core.tracking_projection import is_anime_data
from models import Media
from models.catalogue import CatalogueEntity
from models.games import GameFavorite
from models.tracking import MediaFavorite, TrackedEntry, TrackingPreferences


async def screen_cards(db: AsyncSession, user_id: int, show_anime: bool) -> list[dict]:
    ids = select(MediaFavorite.media_id).where(MediaFavorite.user_id == user_id).union(
        select(TrackedEntry.media_id).where(TrackedEntry.user_id == user_id, TrackedEntry.favorite.is_(True))
    ).subquery()
    data = Media.tmdb_data
    gallery = data['_anylist_images']
    visibility = ('genres', 'origin_country', 'production_countries', 'original_language')
    projection = [
        Media.id, Media.title, Media.media_type, Media.poster_path,
        gallery['version'].label('poster_version'), gallery['primary_poster'].as_string().label('primary_poster'),
    ]
    if not show_anime:
        projection.extend(data[key].label(key) for key in visibility)
    rows = (await db.execute(select(*projection).join(ids, ids.c.media_id == Media.id))).mappings().all()
    stored_order = await db.scalar(select(TrackingPreferences.favorite_order).where(TrackingPreferences.user_id == user_id))
    positions = {media_id: index for index, media_id in enumerate(stored_order or [])}
    cards = []
    for row in sorted(rows, key=lambda row: (positions.get(row['id'], len(positions)), row['id'])):
        if not show_anime and is_anime_data({key: row[key] for key in visibility}):
            continue
        poster = row['primary_poster'] if row['poster_version'] == IMAGE_POLICY_VERSION else None
        cards.append(dict(id=row['id'], type=row['media_type'].value, title=row['title'], poster=poster or row['poster_path']))
    return cards


async def game_cards(db: AsyncSession, user_id: int, offset: int, limit: int = 50) -> dict:
    total = await db.scalar(select(func.count()).select_from(GameFavorite).where(GameFavorite.user_id == user_id))
    rows = (await db.execute(select(CatalogueEntity.id, CatalogueEntity.name, CatalogueEntity.image_url)
        .join(GameFavorite, GameFavorite.work_id == CatalogueEntity.id)
        .where(GameFavorite.user_id == user_id, CatalogueEntity.kind == 'game')
        .order_by(GameFavorite.position.asc().nullslast(), GameFavorite.work_id)
        .offset(offset).limit(limit))).all() if total else []
    return {
        'total': total,
        'items': [dict(id=row.id, type='game', title=row.name, poster=row.image_url) for row in rows],
    }
