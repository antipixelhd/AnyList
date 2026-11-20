"""Use existing episode stills as season backgrounds without provider fan-out."""


def collect_backdrops(episodes, release):
    """Keep one released still per canonical season during catalogue publication."""
    backdrops = {}
    for number, episode, day, image in sorted(episodes, key=lambda item: item[:2]):
        if image and str(number) not in backdrops and release.is_released(day):
            backdrops[str(number)] = image
    return backdrops


def stored_backdrop(data, order, number):
    if order == 'tmdb:aired':
        return (data.get('tracking_season_backdrops') or {}).get(str(number))
    return None


def season_backdrop(data, order, number, released):
    # An alternate episode order must use only stills from its own membership.
    return stored_backdrop(data, order, number) or next(
        (ep.poster_path for ep in released if ep.poster_path), None)
