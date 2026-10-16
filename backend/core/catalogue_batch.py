"""Document-scoped identity reuse and bounded, no-op-aware credit writes."""
import json
import asyncio
from collections import defaultdict

from sqlalchemy import or_, select, tuple_, bindparam, column, func, String, Text, true
from sqlalchemy.dialects.postgresql import insert, JSONB

from models.catalogue import CatalogueCredit, CatalogueEntity, CatalogueIdentity

BATCH_SIZE = 200


async def bounded_scalars(db, statement):
    """Hydrate large ORM collections in yielding chunks, retaining every row."""
    rows = []
    stream = await db.stream_scalars(statement.execution_options(yield_per=BATCH_SIZE))
    try:
        async for chunk in stream.partitions(BATCH_SIZE):
            rows.extend(chunk)
            await asyncio.sleep(0)
    finally:
        await stream.close()
    return rows


class DocumentEntities:
    def __init__(self, db, source):
        self.db, self.source = db, source
        self.known, self.memo = {}, {}

    async def preload(self, records, *, create_missing=True):
        from core import catalogue
        # Existing multi-ID records can be reused only when every identifier
        # already resolves to the same root. New/contradictory bridges still use
        # the established reconciliation path in source order.
        values, singles, multiple = {}, set(), set()
        for record in records:
            for ident in record.get('identities', []):
                key = (ident['namespace'], catalogue.validate_identity(ident['namespace'], ident['external_id'], record['kind']))
                values.setdefault(key, record)
                (singles if len(record['identities']) == 1 else multiple).add(key)
        create_keys = singles - multiple if create_missing else set()
        keys = list(values)
        for start in range(0, len(keys), BATCH_SIZE):
            chunk = keys[start:start+BATCH_SIZE]
            mappings = (await self.db.execute(select(CatalogueIdentity.namespace, CatalogueIdentity.external_id,
                CatalogueEntity).join(CatalogueEntity, CatalogueEntity.id == CatalogueIdentity.entity_id).where(
                    tuple_(CatalogueIdentity.namespace, CatalogueIdentity.external_id).in_(chunk)))).all()
            self.known.update({(ns, native): row for ns, native, row in mappings})
            pending = []
            for key in chunk:
                if key in self.known or key not in create_keys:
                    continue
                record = values[key]
                row = CatalogueEntity(kind=record['kind'], name=record['name'], attributes={},
                    field_sources={} if catalogue.placeholder_name(record) else {'name':self.source}, protected_fields=[])
                catalogue.merge_fields(row, record, self.source)
                self.db.add(row)
                self.known[key] = row
                pending.append((key, row))
            if pending:
                await self.db.flush()
                self.db.add_all([CatalogueIdentity(entity_id=row.id, namespace=key[0], external_id=key[1],
                    source=self.source, evidence={'cross_reference':values[key]['identities'][0]}) for key, row in pending])
                await self.db.flush()

    async def reload(self, keys):
        keys = list(keys)
        self.known.clear()
        self.memo.clear()
        for start in range(0,len(keys),BATCH_SIZE):
            mappings = (await self.db.execute(select(CatalogueIdentity.namespace,CatalogueIdentity.external_id,
                CatalogueEntity).join(CatalogueEntity,CatalogueEntity.id==CatalogueIdentity.entity_id).where(
                    tuple_(CatalogueIdentity.namespace,CatalogueIdentity.external_id).in_(keys[start:start+BATCH_SIZE])))).all()
            self.known.update({(ns,native):row for ns,native,row in mappings})

    def cached(self, values):
        from core import catalogue
        keys = [(i['namespace'], catalogue.validate_identity(i['namespace'], i['external_id'], values['kind']))
                for i in values.get('identities', [])]
        matches = [self.known.get(key) for key in keys]
        return matches[0] if matches and all(match and match is matches[0] for match in matches) else None

    async def existing(self, values):
        from core import catalogue
        row = self.cached(values)
        if row is None:
            from core.catalogue_additive import prepare_values
            keys = set(self.known)
            _, row = await prepare_values(self.db, values, self.source, return_row=True)
            # Preparation can itself reconcile an existing verified bridge.
            await self.reload(keys | {(i['namespace'],catalogue.validate_identity(i['namespace'],i['external_id'],values['kind']))
                                      for i in values.get('identities',[])})
        if row and row.kind != values['kind']:
            raise catalogue.IdentityConflict('Identity kind requires review')
        return row

    async def get(self, values, *, additive=False):
        from core import catalogue
        # Equality is over the complete record, not merely a provider ID: later
        # jobs can carry richer fields and must still merge in document order.
        fingerprint = json.dumps(values, sort_keys=True)
        identity_key = tuple((i['namespace'], catalogue.validate_identity(i['namespace'], i['external_id'], values['kind']))
                             for i in values.get('identities', []))
        previous = self.memo.get(identity_key)
        if previous and previous[0] == fingerprint:
            return previous[1]
        matches = [self.known.get(key) for key in identity_key]
        row = matches[0] if matches and all(match and match is matches[0] for match in matches) else None
        if row and row.kind != values['kind']:
            raise catalogue.IdentityConflict('Provider cross-reference has a different entity kind')
        if additive:
            from core.catalogue_additive import prepare_values
            values = await prepare_values(self.db, values, self.source, row=row)
        if row:
            catalogue.merge_fields(row, values, self.source)
        else:
            cached_keys = set(self.known)
            row = await catalogue.upsert_entity(self.db, values, self.source)
            if len({match.id for match in matches if match}) > 1:
                # Only an actual bridge can redirect unrelated cached IDs.
                await self.reload(cached_keys | set(identity_key))
            self.known.update({key:row for key in identity_key})
        self.memo[identity_key] = (fingerprint, row)
        return row


async def write_credits(db, rows):
    """Preserve sparse fields and source keys; don't rewrite equal source rows."""
    from core.catalogue import has_value
    keys = ('work_id','provider','source_key')
    # Last source-key occurrence wins, as with sequential upserts; merge sparse
    # duplicates so omitted fields from a later occurrence do not erase values.
    unique = {}
    for raw in rows:
        values = {k:v for k,v in raw.items() if k in keys or has_value(v)}
        if 'attributes' in values:
            values['attributes'] = {k:v for k,v in values['attributes'].items() if has_value(v)}
        identity = tuple(values[k] for k in keys)
        previous = unique.get(identity, {})
        if 'attributes' in previous and 'attributes' in values:
            values['attributes'] = {**previous['attributes'], **values['attributes']}
        unique[identity] = {**previous, **values}
    groups = defaultdict(list)
    changed = False
    for row in unique.values():
        groups[tuple(sorted(row))].append(row)
    for rows in groups.values():
        for start in range(0, len(rows), BATCH_SIZE):
            chunk = rows[start:start+BATCH_SIZE]
            # One JSON parameter keeps compilation/bind construction bounded by
            # the column count, rather than compiling thousands of VALUES
            # expressions on the event loop. PostgreSQL expands <=200 records.
            fields = list(chunk[0])
            records = func.jsonb_to_recordset(bindparam('credit_rows',type_=JSONB)).table_valued(*[
                column(k,Text() if isinstance(CatalogueCredit.__table__.c[k].type,String)
                       else CatalogueCredit.__table__.c[k].type) for k in fields
            ]).render_derived(name='incoming',with_types=True)
            stmt = insert(CatalogueCredit.__table__).from_select(fields,
                select(*(records.c[k] for k in fields)).where(true()),include_defaults=False)
            updates = {k:stmt.excluded[k] for k in chunk[0] if k not in keys}
            if 'attributes' in updates:
                updates['attributes'] = CatalogueCredit.attributes.op('||')(stmt.excluded.attributes)
            # Links remain stable on unchanged refreshes, and are invalidated
            # only when the performer/job/role actually changes.
            role_changed = or_(*(getattr(CatalogueCredit,k).is_distinct_from(stmt.excluded[k])
                                 for k in ('contributor_id','role','character_label') if k in updates))
            from sqlalchemy import case
            updates['screen_character_id'] = case((role_changed, None), else_=CatalogueCredit.screen_character_id)
            stmt = stmt.on_conflict_do_update(index_elements=keys, set_=updates,
                where=or_(*(getattr(CatalogueCredit,k).is_distinct_from(v) for k,v in updates.items())))
            result = await db.scalars(stmt.returning(CatalogueCredit.__table__.c.id),{'credit_rows':chunk})
            changed |= bool(result.all())
    return changed
