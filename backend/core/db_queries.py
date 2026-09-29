"""Database query helpers shared by sync and projection services."""

from sqlalchemy.ext.asyncio import AsyncSession

# Leave room below asyncpg's 32767-parameter limit for other query predicates.
_MAX_IN_PARAMS = 30_000


async def select_in_chunks(db: AsyncSession, stmt_builder, ids: list):
    """Execute a select statement using chunked IN clauses to avoid the 32767-parameter limit.
    stmt_builder(chunk) should return a SQLAlchemy select() statement for that chunk of IDs.
    Returns a flat list of all rows."""
    results = []
    for i in range(0, len(ids), _MAX_IN_PARAMS):
        chunk = ids[i : i + _MAX_IN_PARAMS]
        res = await db.execute(stmt_builder(chunk))
        results.extend(res.scalars().all())
    return results
