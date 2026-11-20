"""Games authorization is independent of navigation preferences and shared caches."""
from typing import Annotated

from fastapi import Depends, HTTPException, Response

from dependencies import get_optional_user_or_api_key
from models.users import User


def allowed(viewer) -> bool:
    return bool(viewer and (getattr(viewer, 'is_admin', False) is True
                           or getattr(viewer, 'games_access', False) is True))


def require(viewer) -> None:
    if not allowed(viewer):
        raise HTTPException(403, 'Games access requires approval')


async def approved_games_viewer(viewer: Annotated[User | None, Depends(get_optional_user_or_api_key)], response: Response):
    response.headers['Cache-Control'] = 'private, no-store'
    if viewer is None:
        raise HTTPException(401, 'Sign in to use Games')
    require(viewer)
    return viewer
