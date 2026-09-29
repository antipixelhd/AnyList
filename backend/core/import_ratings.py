"""Apply imported ratings while preserving precise local provider echoes."""
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from models.media import Media
from models.ratings import Rating, RatingChanges


def imported_rated_at(value: str | None) -> datetime:
    if not value:
        return datetime.utcnow()
    from dateutil import parser as dt_parser

    parsed = dt_parser.isoparse(value)
    if parsed.tzinfo:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def optional_imported_rated_at(value: str | None) -> datetime | None:
    if not value:
        return None
    return imported_rated_at(value)


def apply_imported_rating(
    db: AsyncSession,
    user_id: int,
    media: Media,
    season_number: int | None,
    item: dict,
    existing: dict[tuple[int, int | None], Rating],
    changed: RatingChanges,
) -> bool:
    rating_value = float(item["rating"])
    rated_at = imported_rated_at(item.get("rated_at"))
    key = (media.id, season_number)
    current = existing.get(key)
    from core.rating_projection import is_projected_echo
    if current and (current.rating == rating_value or is_projected_echo(current.rating, rating_value)):
        # A provider echo of a converted half-step must not erase local
        # precision or advance its timestamp over the original edit.
        return False
    if current:
        current.rating = rating_value
        current.rated_at = rated_at
    else:
        current = Rating(
            user_id=user_id,
            media_id=media.id,
            season_number=season_number,
            rating=rating_value,
            rated_at=rated_at,
        )
        db.add(current)
        existing[key] = current
    changed[key] = rating_value
    return True
