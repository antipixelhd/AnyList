"""Compose the API without creating an application when imported by workers."""

from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from sqlalchemy import delete, text, update
from sqlalchemy.ext.asyncio import AsyncSession

import models  # noqa: F401 - register all model tables before startup
from core import netflix_sessions, scheduler
from core.access_log import install as install_access_log_redaction
from core.api_roles import ApiRole, ReaderRoutes
from core.config import settings
from core.game_work import GameWorkMiddleware
from core.limiter import GlobalRateLimitMiddleware, limiter
from core.read_workers import ReadOnlyApp, ReadWorkerMiddleware, read_workers
from db import Base, async_sessionmaker, engine
from dependencies import require_admin
from models.playback_session import PlaybackSession
from models.sync import SyncJob, SyncStatus
from models.users import User
from routers import (
    admin,
    admin_data_health,
    admin_game_runner,
    auth,
    bingebase,
    calendar,
    catalogue,
    comments,
    compat,
    export,
    games,
    history,
    legal,
    lists,
    mdblist,
    media,
    media_discovery,
    media_playback,
    netflix_import,
    oidc,
    profile,
    push,
    ratings,
    season_tracking,
    sharing,
    shows,
    simkl,
    steam,
    sync,
    tracking,
    trakt,
    webhooks,
    yamtrack,
)

install_access_log_redaction()


async def initialize_primary():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await netflix_sessions.cleanup_expired_netflix_imports()

    # Clean up stuck sync jobs and orphaned playback sessions on startup
    async_session = async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )
    async with async_session() as db:
        await db.execute(
            update(SyncJob)
            .where(SyncJob.status.in_([SyncStatus.pending, SyncStatus.running]))
            .values(
                status=SyncStatus.failed, error_message="Aborted due to server restart"
            )
        )
        await db.execute(delete(PlaybackSession))
        await db.commit()

    await netflix_sessions.resume_incomplete_netflix_imports()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Both roles own the same client resources. Only the primary owns recovery,
    # account schedulers and reader supervision.
    try:
        if app.state.role == ApiRole.PRIMARY:
            await initialize_primary()
            async with (
                scheduler.background_jobs(),
                read_workers(app, settings.api_read_workers),
            ):
                yield
        else:
            yield
    finally:
        await close_clients()


async def close_clients():
    from core import (
        chronological_discovery,
        game_metadata,
        tmdb,
        tracking_episode_loading,
    )

    await game_metadata.close(engine)
    await tracking_episode_loading.close()
    await chronological_discovery.close()
    await tmdb.close()

    await engine.dispose()


def create_app(*, role: ApiRole | str = ApiRole.PRIMARY) -> FastAPI:
    # Rate limiter — keyed by client IP, in-memory storage (suitable for single-instance deploy).
    # API docs (docs_url/redoc_url/openapi_url) are disabled here and re-added below behind
    # require_admin — the schema reveals the full endpoint surface and exact app version,
    # which shouldn't be public on a self-hosted instance that may be internet-facing.
    app = FastAPI(
        title="AnyList",
        version=settings.app_version,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.role = ApiRole(role)
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    if app.state.role == ApiRole.PRIMARY:
        app.add_middleware(GlobalRateLimitMiddleware)
    app.add_middleware(GameWorkMiddleware)
    if app.state.role == ApiRole.PRIMARY:
        app.add_middleware(ReadWorkerMiddleware)
    else:
        app.add_middleware(ReadOnlyApp)

    @app.get("/openapi.json", include_in_schema=False)
    async def get_openapi_schema(_: User = Depends(require_admin)):
        return JSONResponse(app.openapi())

    @app.get("/docs", include_in_schema=False)
    async def get_docs(_: User = Depends(require_admin)):
        return get_swagger_ui_html(
            openapi_url="/openapi.json", title=f"{app.title} - Swagger UI"
        )

    @app.get("/redoc", include_in_schema=False)
    async def get_redoc(_: User = Depends(require_admin)):
        return get_redoc_html(openapi_url="/openapi.json", title=f"{app.title} - ReDoc")

    # The backend is internal-only (localhost), but lock CORS to the configured
    # frontend origin as defence-in-depth. The backend uses Bearer token auth only
    # (no cookies), so allow_credentials is not needed.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.server_url],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(season_tracking.router)
    app.include_router(admin_game_runner.router)
    app.include_router(catalogue.router)
    app.include_router(games.router)
    app.include_router(tracking.router, prefix="/tracking", tags=["tracking"])
    app.include_router(auth.router, prefix="/auth", tags=["auth"])
    app.include_router(oidc.router, prefix="/auth/oidc", tags=["oidc"])
    app.include_router(webhooks.router, prefix="/webhooks", tags=["webhooks"])
    app.include_router(media_discovery.router, prefix="/media", tags=["media"])
    app.include_router(media_playback.router, prefix="/media", tags=["media"])
    app.include_router(media.router, prefix="/media", tags=["media"])
    app.include_router(history.router, prefix="/history", tags=["history"])
    app.include_router(ratings.router, prefix="/ratings", tags=["ratings"])
    app.include_router(push.router, prefix="/push", tags=["push"])
    app.include_router(sync.router, prefix="/sync", tags=["sync"])
    app.include_router(shows.router, prefix="/shows", tags=["shows"])
    app.include_router(lists.router, prefix="/lists", tags=["lists"])
    app.include_router(profile.router, prefix="/profile", tags=["profile"])
    app.include_router(sharing.router)
    app.include_router(legal.router)
    app.include_router(steam.router)
    app.include_router(trakt.router, prefix="/trakt", tags=["trakt"])
    app.include_router(simkl.router, prefix="/simkl", tags=["simkl"])
    app.include_router(mdblist.router, prefix="/mdblist", tags=["mdblist"])
    app.include_router(bingebase.router, prefix="/bingebase", tags=["bingebase"])
    app.include_router(comments.router, prefix="/comments", tags=["comments"])
    app.include_router(admin.router, prefix="/admin", tags=["admin"])
    app.include_router(admin_data_health.router)
    app.include_router(export.router, prefix="/export", tags=["export"])
    app.include_router(yamtrack.router, prefix="/yamtrack", tags=["yamtrack"])
    app.include_router(netflix_import.router, prefix="/imports", tags=["imports"])
    app.include_router(calendar.router, prefix="/calendar", tags=["calendar"])
    app.include_router(compat.router, tags=["compat"])

    @app.get("/health")
    async def health():
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        except Exception:
            return JSONResponse(
                status_code=503, content={"status": "error", "app": "AnyList"}
            )
        return {"status": "ok", "app": "AnyList"}

    app.state.reader_routes = ReaderRoutes(app.routes)
    return app
