"""Explicit local-only provider verification; never logs credentials or titles."""
import asyncio
import contextlib
import io
import logging
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
logging.disable(logging.CRITICAL)
from db import AsyncSessionLocal, engine
from models import User, MediaServerConnection, SyncJob
from models.base import CollectionSource
from models.sync import SyncStatus
from models.tracking import TrackedEntry, SyncReview, StreamBaseline
from sqlalchemy import select, func
from routers.sync import _run_stremio_sync


async def main():
    if ':55438/media_tracker' not in os.environ.get('DATABASE_URL', ''):
        raise SystemExit('Disposable local database required')
    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.username == 'provider-test'))).scalar_one()
        conn = (await db.execute(select(MediaServerConnection).where(
            MediaServerConnection.user_id == user.id, MediaServerConnection.type == 'stremio'))).scalar_one()
        if conn.push_enabled or conn.auto_push_interval:
            raise SystemExit('Disable all outbound flags for this verification')
        job = SyncJob(user_id=user.id, source=CollectionSource.stremio,
            status=SyncStatus.pending, connection_id=conn.id)
        db.add(job)
        await db.commit()
        uid, cid, jid = user.id, conn.id, job.id
    with contextlib.redirect_stdout(io.StringIO()), patch('core.stremio.datastore_put',
        new=AsyncMock(side_effect=RuntimeError('Outbound disabled during read-only verification'))) as outbound:
        await _run_stremio_sync(uid, jid, 0, 0, connection_id=cid, full_resync=True)
    async with AsyncSessionLocal() as db:
        job = await db.get(SyncJob, jid)
        print('Import status:', job.status.value, 'job ID:', jid)
        print('Outbound calls:', outbound.await_count)
        for label, model in [('Tracked entries', TrackedEntry), ('Review events', SyncReview)]:
            print(label + ':', await db.scalar(select(func.count()).select_from(model).where(model.user_id == uid)))
        baseline = await db.get(StreamBaseline, cid)
        print('Baseline exists:', bool(baseline), 'approved:', bool(baseline and baseline.approved))
        if job.status == SyncStatus.failed:
            # Error messages can contain account data; inspect locally, never dump.
            raise RuntimeError(f'Import job {jid} failed; inspect its error securely')


async def run():
    try:
        await main()
    finally:
        await engine.dispose()


if __name__ == '__main__':
    asyncio.run(run())
