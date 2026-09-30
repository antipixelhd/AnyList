"""Effective Radarr and Sonarr settings for user and global connections."""

from models.global_settings import GlobalSettings
from models.users import UserSettings


def _effective_radarr(user_settings: UserSettings | None, global_settings: GlobalSettings | None):
    """Return the settings object whose Radarr config is fully configured, user first."""
    for s in (user_settings, global_settings):
        if s and all([s.radarr_url, s.radarr_token, s.radarr_root_folder, s.radarr_quality_profile]):
            return s
    return None


def _effective_sonarr(user_settings: UserSettings | None, global_settings: GlobalSettings | None):
    """Return the settings object whose Sonarr config is fully configured, user first."""
    for s in (user_settings, global_settings):
        if s and all([s.sonarr_url, s.sonarr_token, s.sonarr_root_folder, s.sonarr_quality_profile]):
            return s
    return None
