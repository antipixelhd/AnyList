"""Run as the slot user inside its backend venv, never as root."""
import asyncio
import json

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.ext.asyncio import create_async_engine
from core.config import settings


async def main():
    scripts = ScriptDirectory.from_config(Config("alembic.ini"))
    heads = scripts.get_heads()
    known = {revision.revision for revision in scripts.walk_revisions()}
    engine = create_async_engine(settings.db_url)
    try:
        async with engine.connect() as connection:
            current = await connection.run_sync(
                lambda conn: MigrationContext.configure(conn).get_current_heads()
            )
        print(json.dumps({"current": list(current), "heads": heads,
                          "compatible": set(current).issubset(known),
                          "pending": set(current) != set(heads)}))
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
