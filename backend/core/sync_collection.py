"""Stage provider membership until the account reconciliation transaction."""
from sqlalchemy import select

from models import Collection, CollectionFile, MediaServerConnection


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
