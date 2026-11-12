"""Erotic discovery content is independent of mature age ratings."""
from sqlalchemy import select
from models.users import UserSettings

EROTIC_THEME = 42  # IGDB's documented erotic-game classification.


def erotic_game(raw):
    return any((theme.get('id') if isinstance(theme,dict) else theme)==EROTIC_THEME
        for theme in raw.get('themes') or [])


async def hide_erotic_games(db,viewer_id):
    if viewer_id is None:return True
    value=await db.scalar(select(UserSettings.preferences['hide_erotic_games']).where(UserSettings.user_id==viewer_id))
    return value is not False
