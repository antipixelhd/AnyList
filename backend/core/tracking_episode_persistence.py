"""Batch TMDB episode publication without changing canonical IDs or private facts."""
from sqlalchemy import cast, column, select, values as sql_values
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm.attributes import set_committed_value
from sqlalchemy.orm.util import identity_key

from models import Media
from models.base import MediaType

METADATA_FIELDS = ('title', 'overview', 'poster_path', 'release_date', 'runtime')


async def persist_tmdb_episodes(db, show_id, fetched):
    ids = [data['id'] for _, data in fetched]
    positions = [(number, data['episode_number']) for number, data in fetched]
    if len(set(ids)) != len(ids) or len(set(positions)) != len(positions):
        raise ValueError('The provider returned duplicate episode identities or positions')
    await db.flush()
    fields = (Media.id, Media.tmdb_id, Media.show_id, Media.season_number, Media.episode_number,
        Media.title, Media.overview, Media.poster_path, Media.release_date, Media.runtime)

    async def existing(natives):
        return (await db.execute(select(*fields).where(Media.media_type == MediaType.episode,
            Media.tmdb_id.in_(natives)).order_by(Media.id).with_for_update())).mappings().all()

    def validate(episode, number, data):
        if (episode['show_id'], episode['season_number'], episode['episode_number']) != (show_id, number, data['episode_number']):
            raise ValueError('Episode numbering changed; existing watch history was preserved')

    # Bounded groups also keep large long-running series below parameter limits.
    for offset in range(0, len(fetched), 250):
        batch = fetched[offset:offset + 250]
        by_id = {episode['tmdb_id']: episode for episode in await existing([data['id'] for _, data in batch])}
        for number, data in batch:
            if data['id'] in by_id:
                validate(by_id[data['id']], number, data)
        missing = [(number, data) for number, data in batch if data['id'] not in by_id]
        created = set()
        if missing:
            values = [dict(tmdb_id=data['id'], media_type=MediaType.episode, show_id=show_id,
                season_number=number, episode_number=data['episode_number'],
                title=data.get('name') or f'Episode {data["episode_number"]}',
                overview=data.get('overview') or None, poster_path=data.get('still_path') or None,
                release_date=data.get('air_date'), runtime=data.get('runtime') if type(data.get('runtime')) is int
                    and data['runtime'] > 0 else None) for number, data in missing]
            statement = insert(Media).values(values).on_conflict_do_nothing(
                index_elements=[Media.tmdb_id, Media.media_type], index_where=Media.tmdb_id.is_not(None))
            created = set(await db.scalars(statement.returning(Media.tmdb_id)))
            # An importer/webhook may have won an insert since the first read.
            raced = [data['id'] for _, data in missing if data['id'] not in created]
            if raced:
                by_id.update({episode['tmdb_id']: episode for episode in await existing(raced)})
        updates = []
        for number, data in batch:
            if data['id'] in created:
                continue
            episode = by_id[data['id']]
            validate(episode, number, data)
            values = dict(title=data.get('name') or episode['title'],
                overview=data.get('overview') or episode['overview'],
                poster_path=data.get('still_path') or episode['poster_path'], release_date=data.get('air_date'),
                runtime=data['runtime'] if type(data.get('runtime')) is int and data['runtime'] > 0 else episode['runtime'])
            if any(value != episode[field] for field, value in values.items()):
                updates.append({'episode_row_id': episode['id'], **values})
        if updates:
            table = Media.__table__
            changes = sql_values(column('episode_row_id', table.c.id.type),
                *(column(field, table.c[field].type) for field in METADATA_FIELDS)).data([
                    (row['episode_row_id'], *(row[field] for field in METADATA_FIELDS)) for row in updates
                ]).alias('episode_changes')
            await db.execute(table.update().where(table.c.id == changes.c.episode_row_id)
                .values(**{field: cast(changes.c[field], table.c[field].type) for field in METADATA_FIELDS}))
            # Keep already-held ORM references usable, including unrelated
            # identities/evidence; the focused SQL read never expires those.
            for row in updates:
                held = db.identity_map.get(identity_key(Media, row['episode_row_id']))
                if held is not None:
                    for field in METADATA_FIELDS:
                        set_committed_value(held, field, row[field])
                    db.expire(held, ['updated_at'])
