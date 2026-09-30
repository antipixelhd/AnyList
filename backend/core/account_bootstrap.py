"""Serialize first-account provisioning across password and OIDC signup."""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


async def lock_account_bootstrap(db: AsyncSession) -> None:
    """Hold the shared PostgreSQL advisory lock until account creation commits."""
    get_bind = getattr(db, "get_bind", None)
    if get_bind is None:
        return
    bind = get_bind()
    if getattr(getattr(bind, "dialect", None), "name", None) == "postgresql":
        # "MTRK" namespace plus the bootstrap-account lock id.
        await db.execute(select(func.pg_advisory_xact_lock(1297371723, 1)))
