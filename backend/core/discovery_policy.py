"""Pure discovery policy: tuning never performs provider work or changes budgets."""
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

    def accepts(self, item):
        # These are explicitly TMDB counts, never IMDb's different population.
        return ((not self.min_votes or (numeric(item.get('vote_count')) or 0) >= self.min_votes)
                and (not self.min_popularity or (numeric(item.get('popularity')) or 0) >= self.min_popularity))


def tuning(preferences, media_type, surface, minimum_votes=0):
    """Account popularity floor applies to every surface; internal knobs tune votes.

    discovery_tuning: {newest: {series: {min_popularity: 25, min_votes: 0}}}
    Set a value to zero to disable it. Missing popularity fails an enabled floor.
    """
    config = preferences.get('discovery_tuning', {}) if isinstance(preferences, dict) else {}
    config = config.get(surface, {}) if isinstance(config, dict) else {}
    config = config.get(media_type, {}) if isinstance(config, dict) else {}
    if not isinstance(config, dict):
        config = {}
    votes = numeric(config.get('min_votes', 0 if surface == 'search' else minimum_votes)) or 0
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
    votes, preferences = 0, {}
    if viewer:
        row = (await db.execute(select(UserProfileData.minimum_votes, UserSettings.preferences)
            .select_from(User).outerjoin(UserProfileData, UserProfileData.user_id == User.id)
            .outerjoin(UserSettings, UserSettings.user_id == User.id).where(User.id == viewer.id))).first()
        if row:
            votes, preferences = row[0] or 0, row[1] or {}
    return {surface: tuning(preferences, media_type, surface, votes) for surface in surfaces}
