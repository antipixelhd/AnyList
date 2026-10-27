"""Project live import drafts into the existing notification inbox."""
from urllib.parse import urlencode

from sqlalchemy import and_, or_, select

from models.connections import MediaServerConnection
from models.netflix_import import NetflixImportSession
from models.tracking import StreamBaseline


async def active_import_events(db, user_id, now):
    draft = NetflixImportSession
    connection_id = draft.payload["connection_id"].as_integer()
    rows = (await db.execute(select(
        draft.id, draft.status, draft.created_at, draft.expires_at,
        draft.payload["source"].as_string().label("source"),
        connection_id.label("connection_id"), MediaServerConnection.name,
    ).outerjoin(MediaServerConnection, MediaServerConnection.id == connection_id)
     .outerjoin(StreamBaseline, StreamBaseline.connection_id == connection_id)
     .where(draft.user_id == user_id, draft.expires_at > now,
            draft.status.in_(("preparing", "review", "committing", "failed")),
            or_(connection_id.is_(None), and_(
                MediaServerConnection.user_id == user_id,
                MediaServerConnection.identity_version == draft.payload["identity_version"].as_integer(),
                StreamBaseline.snapshot["import_session_id"].as_string() == draft.id,
            )))
     .order_by(draft.created_at.desc()))).all()
    results = []
    for row in rows:
        source = row.source or 'netflix'
        name = row.name or {'amazon': 'Prime Video', 'netflix': 'Netflix'}.get(source, source.title())
        params = {'import': row.id, 'source': source}
        if row.connection_id is not None:
            params['connection'] = row.connection_id
        else:
            params['section'] = 'imports'
        message = ('Preparing your history' if row.status == 'preparing' else
                   'Importing your history' if row.status == 'committing' else
                   'Your import needs attention' if row.status == 'failed' else 'Your import is ready to resume')
        results.append({'id': f'import:{row.id}', 'kind': 'import_ready', 'state': 'pending',
                        'priority': 'high', 'dismissible': False, 'provider': source, 'media': None,
                        'message': message, 'created_at': row.created_at,
                        'payload': {'title': f'{name} import', 'import_id': row.id,
                                    'connection_id': row.connection_id,
                                    'expires_at': row.expires_at.isoformat() + 'Z',
                                    'resolve_url': '/connections?' + urlencode(params)}})
    return results
