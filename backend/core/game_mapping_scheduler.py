"""Keep focused public evidence indexed without bulk network work on startup."""
import asyncio
from datetime import datetime, timedelta, timezone
import logging
import os
from pathlib import Path
import sys


async def imdb_scheduler():
    script = Path(__file__).resolve().parents[2] / 'scripts' / 'import_game_mapping_index.py'
    await asyncio.sleep(60)
    while True:
        process = None
        delay = 3600
        try:
            process = await asyncio.create_subprocess_exec(sys.executable, str(script), '--refresh-imdb')
            async with asyncio.timeout(3600):
                if await process.wait() != 0:
                    logging.getLogger(__name__).warning('IMDb dataset refresh failed; previous snapshot retained')
                else:
                    stamp = datetime.now(timezone.utc)
                    tomorrow = (stamp + timedelta(days=1)).replace(hour=3, minute=0, second=0, microsecond=0)
                    delay = (tomorrow - stamp).total_seconds()
        except asyncio.CancelledError:
            raise
        except (OSError, TimeoutError):
            logging.getLogger(__name__).warning('IMDb dataset refresh could not finish; previous snapshot retained')
        finally:
            if process and process.returncode is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
                await process.wait()
        await asyncio.sleep(delay)


async def scheduler():
    # Development preview databases are cloned from beta. One bulk download
    # per server is enough for the beta slots sharing this host.
    async with asyncio.TaskGroup() as tasks:
        tasks.create_task(refresh_wikidata_index())
        if os.environ.get('PREVIEW_SLOT') == 'beta':
            tasks.create_task(imdb_scheduler())


async def refresh_wikidata_index():
    from db import AsyncSessionLocal
    from core.game_mapping_import import refresh_cached_wikidata
    await asyncio.sleep(60)
    while True:
        try:
            from core.game_mapping_lifecycle import invalidate
            async with AsyncSessionLocal() as db:
                from core import catalogue
                await catalogue.write_lock(db)
                await invalidate(db,outdated=True);await db.commit()
            await refresh_cached_wikidata(AsyncSessionLocal)
        except asyncio.CancelledError:raise
        except Exception:
            logging.getLogger(__name__).warning('Game-reference index refresh failed; previous generation retained')
        await asyncio.sleep(6*3600)
