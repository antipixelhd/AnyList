"""Identity checks and stale-work fencing for Stremio and Nuvio connections."""

from urllib.parse import urlsplit, urlunsplit

from fastapi import HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from core import nuvio
from models.collection import CollectionFile
from models.connections import MediaServerConnection
from models.streaming_library import StreamingLibraryDelivery
from models.sync import SyncJob, SyncStatus
from models.tracking import StreamAction, StreamBaseline, SyncReview
from models.watch_intent import WatchIntent


def canonical_nuvio_url(url: str) -> str:
    """Normalize a Nuvio base URL without changing case-sensitive path data."""
    value = (url or "").strip()
    parsed = urlsplit(value)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Nuvio URL must be an absolute HTTP or HTTPS URL")

    hostname = parsed.hostname.encode("idna").decode("ascii").lower()
    if ":" in hostname and not hostname.startswith("["):
        hostname = f"[{hostname}]"
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Nuvio URL has an invalid port") from exc
    if port is not None and not (scheme == "https" and port == 443) and not (scheme == "http" and port == 80):
        hostname = f"{hostname}:{port}"

    # Preserve userinfo exactly. It is uncommon in configured URLs, but can be
    # meaningful for a reverse proxy and must not be logged or discarded here.
    userinfo = parsed.netloc.rsplit("@", 1)[0] + "@" if "@" in parsed.netloc else ""
    return urlunsplit((scheme, userinfo + hostname, parsed.path.rstrip("/"), parsed.query, parsed.fragment))


async def persist_rotated_session(db: AsyncSession, conn: MediaServerConnection, session) -> None:
    """Persist a one-use refresh-token rotation outside the caller's transaction.

    Token rotation must survive a later validation/route failure. A separate
    transaction does that without committing other pending request changes.
    """
    from db import AsyncSessionLocal

    async with AsyncSessionLocal() as token_db:
        await token_db.execute(
            update(MediaServerConnection)
            .where(MediaServerConnection.id == conn.id)
            .values(token=session.refresh_token)
        )
        await token_db.commit()
    set_committed_value(conn, "token", session.refresh_token)


async def assert_connection_identity_available(
    db: AsyncSession,
    user_id: int,
    provider: str,
    account_id: str | None,
    url: str | None,
    profile_id: str | int | None,
    exclude_connection_id: int | None = None,
) -> None:
    """Resolve legacy identities, then reject another connection for the same account.

    For Nuvio, ``account_id`` must come from its authenticated session. Legacy
    rows without one are refreshed under the shared connection lock; token
    rotation is committed as soon as it arrives, even if later validation fails.
    """
    provider = (provider or "").lower()
    account_id = (account_id or "").strip()
    if provider not in {"stremio", "nuvio"}:
        return
    if not account_id:
        raise HTTPException(status_code=409, detail="The streaming account identity could not be verified")

    canonical_url = None
    if provider == "nuvio":
        try:
            canonical_url = canonical_nuvio_url(url or "")
            profile = nuvio.parse_profile_id(str(profile_id) if profile_id is not None else None)
        except (TypeError, ValueError, nuvio.NuvioAPIError) as exc:
            raise HTTPException(status_code=409, detail="The Nuvio server or profile identity is invalid") from exc

        legacy_rows = (await db.execute(select(MediaServerConnection).where(
            MediaServerConnection.user_id == user_id,
            MediaServerConnection.type == "nuvio",
            MediaServerConnection.provider_account_id.is_(None),
            MediaServerConnection.id != (exclude_connection_id or -1),
        ))).scalars().all()
        for legacy in legacy_rows:
            try:
                legacy_url = canonical_nuvio_url(legacy.url)
            except ValueError:
                continue
            try:
                legacy_profile = nuvio.parse_profile_id(legacy.server_user_id)
            except nuvio.NuvioAPIError:
                continue
            if legacy_url != canonical_url or legacy_profile != profile:
                continue
            async with nuvio.connection_lock(legacy.id):
                await db.refresh(legacy)
                if legacy.provider_account_id is not None:
                    # Another request may have resolved it while this request
                    # waited for the refresh-token lock. Its account identity
                    # is checked by the ordinary duplicate query below.
                    continue
                try:
                    session, _profiles = await nuvio.validate_connection(
                        legacy_url,
                        legacy.token,
                        profile,
                        on_refresh=lambda rotated, row=legacy: persist_rotated_session(db, row, rotated),
                    )
                except Exception as exc:
                    # A transiently unreachable legacy account must not be
                    # treated as a free identity; doing so permits duplicates.
                    raise HTTPException(
                        status_code=503,
                        detail="An existing Nuvio connection could not be verified; retry after it is reachable",
                    ) from exc
                resolved_account = str(getattr(session, "account_id", None) or "").strip()
                if not resolved_account:
                    raise HTTPException(
                        status_code=503,
                        detail="An existing Nuvio connection has no verified account identity",
                    )
                legacy.url = legacy_url
                legacy.provider_account_id = resolved_account
                try:
                    await db.flush()
                except IntegrityError as exc:
                    # A concurrent request may have established this identity;
                    # the partial unique index is the cross-worker authority.
                    await db.rollback()
                    raise HTTPException(status_code=409, detail="This streaming account is already connected") from exc

    query = select(MediaServerConnection.id).where(
        MediaServerConnection.user_id == user_id,
        MediaServerConnection.type == provider,
        MediaServerConnection.provider_account_id == account_id,
        MediaServerConnection.id != (exclude_connection_id or -1),
    )
    if provider == "nuvio":
        query = query.where(
            MediaServerConnection.url == canonical_url,
            MediaServerConnection.server_user_id == str(profile),
        )
    duplicate = (await db.execute(query.limit(1))).scalar_one_or_none()
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="This streaming account is already connected")


async def reset_stream_connection_state(db: AsyncSession, conn: MediaServerConnection) -> None:
    """Clear connection-scoped stream state before replacing its identity."""
    if conn.type not in {"stremio", "nuvio"}:
        return
    # Lock queued jobs too: a worker must not promote one to running between
    # the active-job check and cancelling the old account's queue.
    jobs = (await db.execute(select(SyncJob.status).where(
        SyncJob.connection_id == conn.id,
        SyncJob.status.in_((SyncStatus.pending, SyncStatus.running)),
    ).with_for_update())).scalars().all()
    if SyncStatus.running in jobs:
        raise HTTPException(status_code=409, detail="Wait for the active sync to finish before changing this account")

    await db.execute(update(SyncJob).where(
        SyncJob.connection_id == conn.id,
        SyncJob.status == SyncStatus.pending,
    ).values(status=SyncStatus.cancelled, error_message="Connection account changed"))
    await db.execute(delete(StreamBaseline).where(StreamBaseline.connection_id == conn.id))
    await db.execute(delete(SyncReview).where(SyncReview.connection_id == conn.id))
    await db.execute(delete(WatchIntent).where(WatchIntent.connection_id == conn.id))
    await db.execute(delete(StreamAction).where(StreamAction.connection_id == conn.id))
    await db.execute(delete(StreamingLibraryDelivery).where(StreamingLibraryDelivery.connection_id == conn.id))
    await db.execute(delete(CollectionFile).where(CollectionFile.connection_id == conn.id))

    conn.stremio_pull_cursor_at = None
    conn.stremio_full_sync_done = False
    conn.stremio_pushed_library_ids = None
    conn.identity_version = int(getattr(conn, "identity_version", 0) or 0) + 1


async def refresh_stream_connection(db: AsyncSession, conn: MediaServerConnection) -> None:
    """Refresh a connection after provider I/O and reject stale identity snapshots."""
    has_version = hasattr(conn, "identity_version")
    captured_version = int(conn.identity_version or 0) if has_version else None
    await db.refresh(conn)
    if has_version and int(conn.identity_version or 0) != captured_version:
        raise HTTPException(status_code=409, detail="Connection account or profile changed; retry with its new snapshot")
