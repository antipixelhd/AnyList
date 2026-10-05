from core import netflix_sessions
from core import scheduler
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import JSONResponse
from dependencies import require_admin
from models.users import User
from contextlib import asynccontextmanager
from sqlalchemy.ext.asyncio import AsyncSession
from db import engine, Base
import models # noqa: F401
from routers import media_discovery, media_playback, webhooks, media, history, ratings, sync, shows, auth, lists, oidc, profile, trakt, simkl, mdblist, bingebase, comments, admin, compat, export, yamtrack, calendar, push, netflix_import

from core.access_log import install as install_access_log_redaction
install_access_log_redaction()

from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from core.limiter import limiter

from sqlalchemy import update, delete
from models.sync import SyncJob, SyncStatus
from models.playback_session import PlaybackSession


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


    await netflix_sessions.cleanup_expired_netflix_imports()

    # Clean up stuck sync jobs and orphaned playback sessions on startup
    from db import async_sessionmaker
    async_session = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with async_session() as db:
        await db.execute(
            update(SyncJob)
            .where(SyncJob.status.in_([SyncStatus.pending, SyncStatus.running]))
            .values(status=SyncStatus.failed, error_message="Aborted due to server restart")
        )
        await db.execute(delete(PlaybackSession))
        await db.commit()

    await netflix_sessions.resume_incomplete_netflix_imports()

    async with scheduler.background_jobs():
        yield

from core.config import settings

# Rate limiter — keyed by client IP, in-memory storage (suitable for single-instance deploy).
# API docs (docs_url/redoc_url/openapi_url) are disabled here and re-added below behind
# require_admin — the schema reveals the full endpoint surface and exact app version,
# which shouldn't be public on a self-hosted instance that may be internet-facing.
app = FastAPI(title="AnyList", version=settings.app_version, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)


@app.get("/openapi.json", include_in_schema=False)
async def get_openapi_schema(_: User = Depends(require_admin)):
    return JSONResponse(app.openapi())


@app.get("/docs", include_in_schema=False)
async def get_docs(_: User = Depends(require_admin)):
    return get_swagger_ui_html(openapi_url="/openapi.json", title=f"{app.title} - Swagger UI")


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

from routers import tracking
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
app.include_router(trakt.router, prefix="/trakt", tags=["trakt"])
app.include_router(simkl.router, prefix="/simkl", tags=["simkl"])
app.include_router(mdblist.router, prefix="/mdblist", tags=["mdblist"])
app.include_router(bingebase.router, prefix="/bingebase", tags=["bingebase"])
app.include_router(comments.router, prefix="/comments", tags=["comments"])
app.include_router(admin.router, prefix="/admin", tags=["admin"])
app.include_router(export.router, prefix="/export", tags=["export"])
app.include_router(yamtrack.router, prefix="/yamtrack", tags=["yamtrack"])
app.include_router(netflix_import.router, prefix="/imports", tags=["imports"])
app.include_router(calendar.router, prefix="/calendar", tags=["calendar"])
app.include_router(compat.router, tags=["compat"])

@app.get("/health")
async def health():
    from sqlalchemy import text
    from fastapi.responses import JSONResponse
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception:
        return JSONResponse(status_code=503, content={"status": "error", "app": "AnyList"})
    return {"status": "ok", "app": "AnyList"}
