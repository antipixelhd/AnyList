"""Initial preferences for newly provisioned accounts only."""

from sqlalchemy.ext.asyncio import AsyncSession

from models.base import PrivacyLevel
from models.profile import UserProfileData
from models.tracking import TrackingPreferences
from models.users import User


def initialize_account_defaults(db: AsyncSession, user: User) -> None:
    """Call after flushing a new user, within its creation transaction."""
    user.profile = UserProfileData(user_id=user.id, privacy_level=PrivacyLevel.public)
    db.add(TrackingPreferences(user_id=user.id, default_sort="score"))
