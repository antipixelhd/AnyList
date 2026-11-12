"""Public, generation-scoped game references with indexed bidirectional reads."""
import hashlib
import json
import uuid
from datetime import timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert

from core.game_imdb_enrichment import now
from core.game_title_match import spelling
from models.game_mapping import GameMappingGeneration as Generation, GameMappingReference as Reference


async def begin(factory, source, *, observed_at=None):
    native = uuid.uuid4().hex
    async with factory() as db:
        db.add(Generation(id=native, source=source, active=False,
                          observed_at=observed_at or now(), coverage={'staged_at':now().isoformat()}))
        await db.commit()
    return native


async def write(factory, generation, records):
    rows = []
    for record in records:
        data = record['data']
        support = {'id':data.get('id'),'claims':data['claims']} if 'claims' in data else {
            key:data.get(key) for key in ('id','title','year','aliases','canonical_id')}
        fingerprint = hashlib.sha256(json.dumps(support, sort_keys=True, ensure_ascii=True).encode()).hexdigest()
        rows.append(dict(generation=generation, native=record['id'], title=record['title'],
                         year=record.get('year'), names=sorted({spelling(name) for name in
                             [record['title'], *record.get('aliases', [])] if spelling(name)}),
                         imdb_ids=record.get('imdb_ids', []), igdb_ids=record.get('igdb_ids', []),
                         data={'value': data, 'fingerprint': fingerprint}))
    if not rows:
        return
    async with factory() as db:
        staged=await db.get(Generation,generation,with_for_update=True)
        if staged is None or staged.active or staged.published_at is not None:raise ValueError('Reference generations are immutable after publication')
        statement = insert(Reference).values(rows)
        await db.execute(statement.on_conflict_do_update(index_elements=['generation', 'native'], set_={
            key: getattr(statement.excluded, key) for key in rows[0] if key not in ('generation', 'native')}))
        await db.commit()


async def publish(factory, generation, coverage):
    if coverage.get('complete') is not True or not coverage.get('rows'):
        raise ValueError('Only a completed, nonempty reference generation can be published')
    async with factory() as db:
        from core import catalogue
        await catalogue.write_lock(db)
        row = await db.get(Generation, generation, with_for_update=True)
        if row is None or row.active or row.published_at is not None:
            raise ValueError('Invalid staged generation')
        if row.observed_at>now()+timedelta(minutes=5):raise ValueError('Reference observation time is in the future')
        from sqlalchemy import func
        actual=await db.scalar(select(func.count()).select_from(Reference).where(Reference.generation==generation))
        if actual!=coverage['rows']:raise ValueError('Reference row count does not match the completed export')
        current=await db.scalar(select(Generation.observed_at).where(Generation.source==row.source,Generation.active.is_(True)))
        if current and current>row.observed_at:raise ValueError('A newer reference generation is already active')
        await db.execute(update(Generation).where(Generation.source == row.source, Generation.active.is_(True)).values(active=False))
        row.active = True
        row.published_at = now()
        row.coverage = coverage
        # Evidence consumers invalidate only dependencies which actually changed.
        from core.game_mapping_lifecycle import changed_generation
        await changed_generation(db, row)
        keep=select(Generation.id).where(Generation.source==row.source,Generation.published_at.is_not(None))
        keep=keep.order_by(Generation.published_at.desc()).limit(2)
        await db.execute(delete(Generation).where(Generation.source==row.source,Generation.active.is_(False),
            Generation.published_at.is_not(None),Generation.id.not_in(keep)))
        await db.execute(delete(Generation).where(Generation.active.is_(False),Generation.published_at.is_(None),
            Generation.coverage['staged_at'].astext<(now()-timedelta(days=2)).isoformat()))
        await db.commit()


async def lookup(factory, source, *, native=None, natives=None, imdb_id=None, imdb_ids=None, igdb_id=None, names=None, max_age=timedelta(days=2)):
    if factory is None:
        return []
    query = select(Reference.native, Reference.data['value'].label('value'), Generation.id.label('generation'),
                   Generation.observed_at,Generation.coverage).join(Generation, Generation.id == Reference.generation).where(
        Generation.source == source, Generation.active.is_(True), Generation.observed_at >= now() - max_age)
    if native is not None:
        query = query.where(Reference.native == native)
    elif natives:
        query = query.where(Reference.native.in_(natives))
    elif imdb_id is not None:
        query = query.where(Reference.imdb_ids.contains([imdb_id]))
    elif imdb_ids:
        query = query.where(Reference.imdb_ids.overlap(imdb_ids))
    elif igdb_id is not None:
        query = query.where(Reference.igdb_ids.contains([int(igdb_id)]))
    elif names:
        query = query.where(Reference.names.overlap([spelling(name) for name in names]))
    else:
        raise ValueError('A focused reference lookup is required')
    async with factory() as db:
        return [dict(row._mapping) for row in (await db.execute(query.limit(101))).all()]


async def discard(factory, generation):
    async with factory() as db:
        await db.execute(delete(Generation).where(Generation.id == generation, Generation.active.is_(False)))
        await db.commit()


async def wikidata_generation(factory):
    if factory is None:return None
    async with factory() as db:
        return await db.scalar(select(Generation.id).where(Generation.source=='wikidata',Generation.active.is_(True)))
