"""One projection for tracking preferences and the authenticated page context."""


def preference_data(row, settings):
    from core.list_categories import resolve
    from core.game_browse_policy import preferences as game_preferences
    return {
        'list_categories': {'game': resolve(preferences=settings.preferences if settings else None)},
        **game_preferences(settings.preferences if settings else None),
        'minimum_popularity': (settings.preferences or {}).get('minimum_popularity', 25) if settings else 25,
        'include_rereleases': bool(settings and (settings.preferences or {}).get('include_rereleases', False)),
        'auto_confirm': bool(row and row.auto_confirm),
        'auto_approve_uncertain_resume': bool(settings and (settings.preferences or {}).get('auto_approve_uncertain_resume') is True),
        'watch_state_sync': (settings.preferences or {}).get('watch_state_sync', 'conservative') if settings else 'conservative',
        'show_list_counts': bool(settings and (settings.preferences or {}).get('show_list_counts') is True),
        'combine_lists': True if row is None else row.combine_lists,
        'show_movies_series': True if row is None else row.show_movies_series,
        'show_games': True if row is None else row.show_games,
        'show_game_achievements': True if row is None else row.show_game_achievements,
        'hide_erotic_games': not settings or (settings.preferences or {}).get('hide_erotic_games',True) is not False,
        'default_sort': 'title' if row is None else row.default_sort,
        'low_priority_notifications': True if row is None else row.low_priority_notifications,
        'low_priority_retention_days': 7 if row is None else row.low_priority_retention_days,
        'show_new_ratings_popup': True if row is None else row.show_new_ratings_popup,
        'show_season_rating_popup': True if row is None else row.show_season_rating_popup,
        'new_season_release_dates': True if row is None else row.new_season_release_dates,
        'new_season_releases': True if row is None else row.new_season_releases,
        'remove_specials_seasons': True if row is None else row.remove_specials_seasons,
    }
