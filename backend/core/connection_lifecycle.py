"""Connection removal shared by account settings and import cancellation."""
import logging

from sqlalchemy import select

from models.connections import MediaServerConnection
from models.users import User

logger = logging.getLogger(__name__)


async def delete_connection(db, conn):
    if conn.type in ("stremio", "nuvio"):
        from core import stremio, nuvio
        from core.connection_identity import reset_stream_connection_state
        from core.connection_imports import signal_connection_import_cancellation
        await signal_connection_import_cancellation(db, conn)
        await db.execute(select(User.id).where(User.id == conn.user_id).with_for_update())
        provider = stremio if conn.type == "stremio" else nuvio
        async with provider.connection_lock(conn.id):
            current = await db.get(MediaServerConnection, conn.id, populate_existing=True)
            if current is None:
                # A cancelled commit may have already removed its provisional connection.
                await db.commit()
                return {"status": "deleted"}
            await reset_stream_connection_state(db, current)
            return await _delete_connection_fields(db, current)
    return await _delete_connection_fields(db, conn)


async def _delete_connection_fields(db, conn):
    if conn.type == "stremio":
        from core import stremio
        # A manually supplied auth key may also serve another AnyList user.
        # Removing this connection must not revoke that remaining session.
        shared_session = (await db.execute(select(MediaServerConnection.id).where(
            MediaServerConnection.type == "stremio",
            MediaServerConnection.token == conn.token,
            MediaServerConnection.id != conn.id,
        ).limit(1))).scalar_one_or_none()
        if shared_session is None:
            try:
                await stremio.logout(conn.token)
            except stremio.StremioAPIError:
                logger.warning(
                    "Failed to revoke Stremio session for connection %s",
                    conn.id,
                )
    await db.delete(conn)
    await db.commit()
    return {"status": "deleted"}

