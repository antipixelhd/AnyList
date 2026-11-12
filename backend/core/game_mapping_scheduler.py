"""Keep focused public evidence indexed without bulk network work on startup."""
import asyncio
import logging


async def scheduler():
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
