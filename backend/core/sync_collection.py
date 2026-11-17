"""Stage provider membership until the account reconciliation transaction."""
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import DateTime, bindparam, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from models import Collection, CollectionFile, MediaServerConnection


def collection_added_at_heal_stmt():
    """Lower add-dates atomically, preserving concurrent earlier observations."""
    table = Collection.__table__
    return update(table).where(table.c.id == bindparam('b_id')).values(
        added_at=func.least(table.c.added_at, bindparam('b_added', type_=DateTime)))


@dataclass
class CollectionMembershipBatch:
    """Own allocations, successful files and dates within one importer batch.

    Record success only after the file's savepoint exits. Flush before the
    importer's batch commit; this object never commits the supplied session.
    """
    db: AsyncSession
    user_id: int
    known: dict[int, int]
    stats: dict[str, int]
    collected: set[int] | None = None
    _allocated: dict[int, int] = field(default_factory=dict, init=False)
    _unclaimed: set[int] = field(default_factory=set, init=False)
    _initial_dates: dict[int, datetime] = field(default_factory=dict, init=False)
    _heals: dict[int, datetime] = field(default_factory=dict, init=False)

    async def prepare(self, media_ids):
        if self._allocated or self._heals:
            raise RuntimeError('Flush membership changes before preparing another batch')
        known, created = await preallocate_memberships(self.db, self.user_id, media_ids)
        self.known.update(known)
        self._allocated.update(created)
        self._unclaimed.update(created)

    def record_file(self, media_id, added_at):
        if media_id not in self._unclaimed:
            return
        self._unclaimed.remove(media_id)
        self.stats['movies'] = self.stats.get('movies', 0) + 1
        if self.collected is not None:
            self.collected.add(media_id)
        if added_at is not None:
            self._initial_dates[self._allocated[media_id]] = added_at

    def heal_date(self, collection_id, added_at):
        if collection_id is not None and added_at is not None:
            self._heals[collection_id] = min(self._heals.get(collection_id, added_at), added_at)

    async def flush(self):
        if self._initial_dates:
            # A first successful observation may legitimately be future dated.
            await self.db.execute(update(Collection.__table__).where(Collection.id == bindparam('b_id'))
                .values(added_at=bindparam('b_added')),
                [{'b_id': key, 'b_added': stamp} for key, stamp in self._initial_dates.items()])
        if self._heals:
            await self.db.execute(collection_added_at_heal_stmt(),
                [{'b_id': key, 'b_added': stamp} for key, stamp in self._heals.items()])
        if self._unclaimed:
            empty = await self.db.scalars(delete(Collection).where(
                Collection.id.in_([self._allocated[media_id] for media_id in self._unclaimed]),
                ~select(CollectionFile.id).where(CollectionFile.collection_id == Collection.id).exists())
                .returning(Collection.media_id))
            for media_id in empty:
                self.known.pop(media_id, None)
        self._allocated.clear()
        self._unclaimed.clear()
        self._initial_dates.clear()
        self._heals.clear()


async def preallocate_memberships(db, user_id, media_ids):
    """Allocate one bounded batch of cached titles without per-file SQL reads.

    The caller retains per-item file savepoints and removes unused allocations
    before committing the batch. A webhook that wins the unique-key race keeps
    its existing membership and add-date.
    """
    if not media_ids:
        return {}, {}
    statement = insert(Collection).values([
        {'user_id': user_id, 'media_id': media_id} for media_id in sorted(set(media_ids))])
    created = {media: identifier for identifier, media in (await db.execute(
        statement.on_conflict_do_nothing(constraint='uq_collection_user_media')
        .returning(Collection.id, Collection.media_id))).all()}
    known = dict(created)
    missing = set(media_ids)-known.keys()
    if missing:
        known.update((await db.execute(select(Collection.media_id, Collection.id).where(
            Collection.user_id == user_id, Collection.media_id.in_(missing)))).all())
    return known, created


async def apply_memberships(db, state):
    if not (state.collection_files or state.collection_removals or state.collection_deleted):
        return
    connections = {row.id: row for row in (await db.execute(select(MediaServerConnection).where(
        MediaServerConnection.user_id == state.user_id))).scalars()}
    def valid(connection_id):
        return connection_id in connections and connections[connection_id].identity_version == state.connection_versions.get(connection_id)
    collections = {row.media_id: row for row in (await db.execute(select(Collection).where(
        Collection.user_id == state.user_id))).scalars()}
    additions = [item for item in state.collection_files.values() if valid(item[0])]
    for _, media_id, _, _, added_at, _ in additions:
        row = collections.get(media_id)
        if row is None:
            row = Collection(user_id=state.user_id, media_id=media_id)
            if added_at:
                row.added_at = added_at
            db.add(row)
            collections[media_id] = row
        elif added_at and (row.added_at is None or added_at < row.added_at):
            row.added_at = added_at
    await db.flush()  # Allocate every new collection in one batch.
    files = list((await db.execute(select(CollectionFile).join(Collection).where(
        Collection.user_id == state.user_id))).scalars())
    by_key = {(row.connection_id, row.collection_id, row.source, row.source_id): row for row in files}
    affected = set()
    deleted = set()
    for row in files:
        if row.id in state.collection_deleted and valid(row.connection_id):
            affected.add(row.collection_id)
            deleted.add(row.id)
            await db.delete(row)
            by_key.pop((row.connection_id, row.collection_id, row.source, row.source_id), None)
    for connection_id, media_id, source_id, source, added_at, quality in additions:
        collection = collections[media_id]
        affected.add(collection.id)
        key = (connection_id, collection.id, source, source_id)
        row = by_key.get(key)
        if row is None:
            row = CollectionFile(collection_id=collection.id, connection_id=connection_id,
                                 source=source, source_id=source_id)
            db.add(row)
            files.append(row)
            by_key[key] = row
        for name in ("file_path", "resolution", "video_codec", "audio_codec", "audio_channels",
                     "audio_languages", "subtitle_languages"):
            if name.endswith("languages") and not quality.get(name):
                continue
            setattr(row, name, quality.get(name))
        if added_at:
            row.added_at = added_at
    await db.flush()
    by_connection = {}
    for row in files:
        if row.id not in deleted:
            by_connection.setdefault((row.connection_id, row.source), []).append(row)
    for connection_id, source, removed_ids, expected in state.collection_removals:
        if not valid(connection_id):
            continue
        for row in by_connection.get((connection_id, source), ()):
            if row.id in deleted:
                continue
            if row.source_id in removed_ids or (expected is not None and row.source_id not in expected):
                affected.add(row.collection_id)
                deleted.add(row.id)
                await db.delete(row)
    await db.flush()
    present = {row.collection_id for row in files if row.id not in deleted}
    for row in collections.values():
        if row.id in affected and row.id not in present:
            await db.delete(row)
    await db.flush()
