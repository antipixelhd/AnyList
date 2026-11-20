"""Run the catalogue schedulers with one owner per PostgreSQL database."""
import asyncio
import logging
import signal

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from core.config import settings

log = logging.getLogger(__name__)
LOCK_KEY = 825128413059316541
CHECK_INTERVAL = 2
DATABASE_TIMEOUT = 5
_CLAIM = text("SELECT pg_try_advisory_lock(:key)")
_PING = text("SELECT 1")


async def run_catalogue_worker():
    from core.scheduler import catalogue_jobs

    # A physical connection owns the lock. A disconnected owner cannot leave
    # an advisory lock behind in the application's pool.
    coordinator = create_async_engine(
        settings.db_url,
        poolclass=NullPool,
        isolation_level="AUTOCOMMIT",
        connect_args={"timeout": DATABASE_TIMEOUT, "server_settings": {"application_name": "anylist-catalogue-lease"}},
    )
    try:
        while True:
            try:
                async with coordinator.connect() as lease:
                    async with asyncio.timeout(DATABASE_TIMEOUT):
                        acquired = await lease.scalar(_CLAIM, {"key": LOCK_KEY})
                    if acquired:
                        log.info("Catalogue worker acquired scheduler ownership")
                        async with catalogue_jobs():
                            while True:
                                await asyncio.sleep(CHECK_INTERVAL)
                                async with asyncio.timeout(DATABASE_TIMEOUT):
                                    await lease.scalar(_PING)
            except (DBAPIError, OSError, TimeoutError):
                log.warning("Catalogue worker lost its PostgreSQL connection; retrying")
            await asyncio.sleep(CHECK_INTERVAL)
    finally:
        await coordinator.dispose()


async def serve():
    """Entry point for the separately supervised catalogue process."""
    from core import game_metadata, tmdb
    from db import engine

    loop = asyncio.get_running_loop()
    owner = asyncio.create_task(run_catalogue_worker())
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, owner.cancel)
        except NotImplementedError:
            signal.signal(sig, lambda *_: loop.call_soon_threadsafe(owner.cancel))
    try:
        await owner
    except asyncio.CancelledError:
        pass
    finally:
        await game_metadata.close(engine)
        await tmdb.close()
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(serve())
