"""Job lifecycle primitives shared by provider workers and HTTP routes."""

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from models.sync import SyncJob, SyncStatus


class SyncCancelled(Exception):
    """Raised internally to unwind a background sync loop once its SyncJob has been cancelled."""


async def raise_if_cancelled(db: AsyncSession, job_id: int | None) -> None:
    """Re-read a job's status from the DB and raise SyncCancelled if the user cancelled it.

    Background sync loops run in their own DB session, separate from the one the
    cancel endpoint commits to, so cancellation can only be observed by polling —
    call this at natural checkpoints (per page/batch/item) inside long-running loops.
    """
    if job_id is None:
        return
    result = await db.execute(select(SyncJob.status).where(SyncJob.id == job_id))
    status = result.scalar_one_or_none()
    if status == SyncStatus.cancelled:
        raise SyncCancelled()


async def mark_job_running_unless_cancelled(db: AsyncSession, job_id: int, **values) -> bool:
    """Start only a still-pending job, preserving cancellation while queued.

    Commit the guarded transition and return whether it succeeded. Workers
    must return immediately on False. Extra values reset the worker's phase
    and progress counters as part of the same update.
    """
    result = await db.execute(
        update(SyncJob)
        .where(SyncJob.id == job_id, SyncJob.status == SyncStatus.pending)
        .values(status=SyncStatus.running, **values)
        .returning(SyncJob.id)
    )
    await db.commit()
    return result.scalar_one_or_none() is not None


MAX_ERROR_MESSAGE = 1000


def short_error(exc: BaseException | str) -> str:
    """Fit a failure into the sync_jobs.error_message column."""
    message = str(exc)
    if len(message) <= MAX_ERROR_MESSAGE:
        return message
    return message[: MAX_ERROR_MESSAGE - 1] + "\u2026"
