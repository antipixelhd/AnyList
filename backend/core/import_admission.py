"""Account-wide admission for streaming imports, including initial fetches."""
from urllib.parse import urlencode

from fastapi import HTTPException
from sqlalchemy import select

from core import netflix_sessions
from models import MediaServerConnection, User
from models.tracking import StreamBaseline


async def active_import(db, user_id):
    from models.steam import SteamConnection
    steam = await db.scalar(select(SteamConnection.connection_id).where(
        SteamConnection.user_id == user_id, SteamConnection.imported.is_(False)).limit(1))
    if steam is not None:
        return {'import_id': None, 'connection_id': steam, 'title': 'Steam import',
                'status': 'Select your Steam games', 'resolve_url': '/connections?steam=' + str(steam)}
    from core.import_notifications import active_import_events

    events = await active_import_events(db, user_id, netflix_sessions._now())
    if events:
        event = events[-1]  # Oldest first, including drafts predating admission.
        return {**event['payload'], 'status': event['message']}
    connection = (await db.execute(select(MediaServerConnection)
        .outerjoin(StreamBaseline, StreamBaseline.connection_id == MediaServerConnection.id)
        .where(MediaServerConnection.user_id == user_id,
               MediaServerConnection.type.in_(('nuvio', 'stremio')),
               MediaServerConnection.sync_watched.is_(True),
               StreamBaseline.connection_id.is_(None))
        .order_by(MediaServerConnection.id).limit(1))).scalar_one_or_none()
    if connection is None:
        return None
    return {'import_id': None, 'connection_id': connection.id,
            'title': f'{connection.name} import', 'status': 'Preparing your history',
            'resolve_url': '/connections?' + urlencode({'connection': connection.id, 'source': connection.type})}


async def require_import_slot(db, user_id, *, connection_id=None):
    """Hold the existing user mutex until the caller commits its reservation."""
    await db.execute(select(User.id).where(User.id == user_id).with_for_update())
    current = await active_import(db, user_id)
    if current and (connection_id is None or current['connection_id'] != connection_id):
        raise HTTPException(status_code=409, detail='Resume or cancel your current import before starting another.')
    return current
