"""Connection settings included in personal backups and eligible for restore."""

CONNECTION_EXPORT_FIELDS = (
    "radarr_url", "radarr_token", "radarr_root_folder", "radarr_quality_profile", "radarr_tags",
    "sonarr_url", "sonarr_token", "sonarr_root_folder", "sonarr_quality_profile", "sonarr_tags", "sonarr_season_folder",
    "trakt_client_id", "trakt_client_secret", "trakt_access_token", "trakt_refresh_token", "trakt_token_expires_at",
    "trakt_sync_watched", "trakt_sync_ratings", "trakt_sync_lists", "trakt_watchlist_split",
    "trakt_push_watched", "trakt_push_ratings", "trakt_push_collection", "trakt_push_lists", "trakt_scrobble",
    "trakt_auto_sync_interval", "trakt_auto_push_interval",
    "simkl_client_id", "simkl_access_token",
    "simkl_sync_watched", "simkl_sync_ratings", "simkl_sync_lists",
    "simkl_push_watched", "simkl_push_ratings", "simkl_scrobble",
    "simkl_auto_sync_interval", "simkl_auto_push_interval",
    "mdblist_api_key", "mdblist_sync_watched", "mdblist_sync_ratings", "mdblist_sync_watchlist",
    "mdblist_push_watched", "mdblist_push_ratings", "mdblist_push_watchlist", "mdblist_push_collection", "mdblist_scrobble",
    "mdblist_auto_sync_interval", "mdblist_auto_push_interval",
)

# Restore credentials and sync preferences without activating imported schedules.
CONNECTION_RESTORE_FIELDS = tuple(
    field for field in CONNECTION_EXPORT_FIELDS
    if not field.endswith(("_auto_sync_interval", "_auto_push_interval"))
)
