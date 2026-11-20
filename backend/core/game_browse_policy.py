"""Viewer discovery preferences and provider predicates for Games Browse."""
from sqlalchemy import select

from models.users import UserSettings

DEFAULTS = {
    'game_min_ratings': 5,
    'game_min_follows': 2,
    'game_upcoming_min_follows': 5,
    'game_released_min_score': 0,
}
AI_KEYWORDS = {'hide_ai_artwork': 38148, 'hide_ai_voices': 40474, 'hide_ai_music': 43816}


def preferences(values):
    values = values if isinstance(values, dict) else {}
    result = {}
    for key, default in DEFAULTS.items():
        value = values.get(key)
        maximum = 100 if key == 'game_released_min_score' else 10_000_000
        result[key] = value if type(value) is int and 0 <= value <= maximum else default
    return result | {key: values.get(key) is True for key in AI_KEYWORDS}


async def viewer_preferences(db, viewer_id):
    if viewer_id is None:
        return {}
    keys = ('hide_erotic_games', *DEFAULTS, *AI_KEYWORDS)
    row = (await db.execute(select(*(UserSettings.preferences[key] for key in keys))
        .where(UserSettings.user_id == viewer_id))).first()
    return dict(zip(keys, row)) if row else {}


def policy(values, *, upcoming=False, released=False, unfiltered=False):
    saved = preferences(values)
    return {key: value for key, value in {
        'ratings': saved['game_min_ratings'] if not upcoming and not unfiltered else 0,
        'follows': saved['game_upcoming_min_follows' if upcoming else 'game_min_follows'] if not unfiltered else 0,
        'score': saved['game_released_min_score'] if released and not unfiltered else 0,
        'exclude': sorted(identifier for key, identifier in AI_KEYWORDS.items() if saved[key]),
    }.items() if value}


def evidence(game):
    values = {key: game[key] for key in ('rating_count', 'hypes', 'total_rating') if game.get(key)}
    if keywords := set(game.get('keywords') or ()).intersection(AI_KEYWORDS.values()):
        values['ai_keywords'] = sorted(keywords)
    return values


def accepts(row, rule):
    ratings, follows = rule.get('ratings'), rule.get('follows')
    return (
        (not (ratings or follows)
            or ratings and row.get('rating_count', 0) >= ratings
            or follows and row.get('hypes', 0) >= follows)
        and row.get('total_rating', 0) >= rule.get('score', 0)
        and not any(keyword in row.get('ai_keywords', ()) for keyword in rule.get('exclude', ()))
    )


def provider_predicate(rule, prefix):
    engagement = [f'{prefix}{field}>={rule[key]}' for key, field in
        (('ratings', 'rating_count'), ('follows', 'hypes')) if key in rule]
    clauses = ['(' + ' | '.join(engagement) + ')'] if engagement else []
    if score := rule.get('score'):
        clauses.append(f'{prefix}total_rating>={score}')
    if excluded := rule.get('exclude'):
        ids = ','.join(map(str, excluded))
        clauses.append(f'({prefix}keywords=null | {prefix}keywords!=({ids}))')
    if clauses:
        # A port's fields can differ from those of its canonical game.
        return f'({prefix}game_type=11 | (' + ' & '.join(clauses) + '))'
    return None
