"""Shared discovery eligibility; tuning never performs provider work."""
from dataclasses import dataclass
import math


def numeric(value):
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else None
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class DiscoveryPolicy:
    min_votes: int = 0
    min_popularity: float = 0

    def _thresholds(self):
        return [(field, minimum) for field, minimum in (
            ('vote_count', self.min_votes), ('popularity', self.min_popularity)) if minimum > 0]

    def accepts(self, item):
        # These are explicitly TMDB counts, never IMDb's different population.
        thresholds = self._thresholds()
        return not thresholds or any((numeric(item.get(field)) or 0) >= minimum
                                     for field, minimum in thresholds)

    def could_accept(self, item):
        """Unknown summary evidence can still qualify after detail enrichment."""
        thresholds = self._thresholds()
        return not thresholds or any(numeric(item.get(field)) is None
                                     or numeric(item.get(field)) >= minimum
                                     for field, minimum in thresholds)

    def sql_clause(self, data):
        from sqlalchemy import Float, func, or_, true
        clauses = [func.coalesce(data[field].astext.cast(Float), 0) >= minimum
                   for field, minimum in self._thresholds()]
        return or_(*clauses) if clauses else true()


def tuning(preferences, media_type, surface, minimum_votes=250):
    """Votes OR popularity qualify on every surface; zero disables that threshold.

    discovery_tuning: {newest: {series: {min_votes: 0}}}
    Missing evidence cannot satisfy an enabled threshold. Both disabled allows all.
    """
    config = preferences.get('discovery_tuning', {}) if isinstance(preferences, dict) else {}
    config = config.get(surface, {}) if isinstance(config, dict) else {}
    config = config.get(media_type, {}) if isinstance(config, dict) else {}
    if not isinstance(config, dict):
        config = {}
    votes = numeric(config.get('min_votes', minimum_votes)) or 0
    popularity = numeric(preferences.get('minimum_popularity', 25) if isinstance(preferences, dict) else 25)
    if popularity is None:
        popularity = 25
    return DiscoveryPolicy(int(min(votes, 10_000_000)), min(popularity, 1_000_000))


async def viewer_policy(db, viewer, media_type, surface, *, unfiltered=False):
    return (await viewer_policies(db, viewer, media_type, (surface,), unfiltered=unfiltered))[surface]


async def viewer_policies(db, viewer, media_type, surfaces, *, unfiltered=False):
    """Read one policy context for all sections, without caching private state."""
    if unfiltered:
        return {surface: DiscoveryPolicy() for surface in surfaces}
    from sqlalchemy import select
    from models.profile import UserProfileData
    from models.users import User, UserSettings
    votes, preferences = 250, {}
    if viewer:
        row = (await db.execute(select(UserProfileData.minimum_votes, UserSettings.preferences)
            .select_from(User).outerjoin(UserProfileData, UserProfileData.user_id == User.id)
            .outerjoin(UserSettings, UserSettings.user_id == User.id).where(User.id == viewer.id))).first()
        if row:
            votes, preferences = row[0] if row[0] is not None else 250, row[1] or {}
    return {surface: tuning(preferences, media_type, surface, votes) for surface in surfaces}
