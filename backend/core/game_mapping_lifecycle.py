"""Targeted evidence invalidation without rewriting reviewed or private state."""
import uuid

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import array, insert

from core.game_imdb_enrichment import now
from models.catalogue import CatalogueCredit, CatalogueEntity, CatalogueIdentity, MetadataSnapshot
from models.game_mapping import GameMappingGeneration as Generation, GameMappingReference as Reference


async def revision(db):
    from core.game_imdb_bindings import BINDING_REVISION
    value = {'revision': uuid.uuid4().hex}
    statement = insert(MetadataSnapshot).values(provider='imdb', kind=BINDING_REVISION,
        external_id='current', coverage=value, fetched_at=now())
    await db.execute(statement.on_conflict_do_update(constraint='uq_catalogue_snapshot',
        set_={'coverage': value, 'fetched_at': now()}))


async def quarantine(db, binding, reason):
    if binding.source == 'reviewed':
        return
    binding.evidence = {**(binding.evidence or {}), 'mapping_state': 'quarantined', 'reason': reason}
    await db.execute(update(CatalogueCredit).where(CatalogueCredit.provider == 'imdb',
        CatalogueCredit.work_id == binding.entity_id).values(
            attributes=CatalogueCredit.attributes.op('||')({'source_active': False})))
    await revision(db)


async def invalidate(db,keys=(),*,outdated=False,identity_ids=None):
    from core.game_title_match import SUPPORTED_VERSIONS
    if identity_ids is not None:condition=CatalogueIdentity.id.in_(identity_ids)
    elif outdated:
        from core.game_version_scope import legacy_scope_condition
        condition=or_((CatalogueIdentity.source=='matched') & (
            func.coalesce(CatalogueIdentity.evidence['matcher_version'].as_integer(),0).not_in(SUPPORTED_VERSIONS)),
            select(CatalogueEntity.id).where(CatalogueEntity.id==CatalogueIdentity.entity_id,
                legacy_scope_condition(CatalogueIdentity,CatalogueEntity)).exists())
    else:condition=CatalogueIdentity.evidence['dependencies'].op('?|')(array(sorted(set(keys))))
    marker=uuid.uuid4().hex
    work_ids=list(await db.scalars(update(CatalogueIdentity).where(
        CatalogueIdentity.namespace=='imdb.title',CatalogueIdentity.source!='reviewed',condition,
        func.coalesce(CatalogueIdentity.evidence['mapping_state'].astext,'active').not_in(('quarantined','stale'))).values(
            evidence=CatalogueIdentity.evidence.op('||')({'mapping_state':'stale','evidence_revision':marker}))
        .returning(CatalogueIdentity.entity_id)))
    if not work_ids:return
    statement=insert(MetadataSnapshot).values([{'provider':'imdb','kind':'game_credit_job',
        'external_id':str(work),'entity_id':work,'next_attempt_at':now(),'coverage':{'mapping_revalidation':marker}}
        for work in set(work_ids)])
    # Keep the lease/cursor, but preserve a newer request even if a worker is
    # already fetching credits using the old support revision.
    await db.execute(statement.on_conflict_do_update(constraint='uq_catalogue_snapshot',set_={
        'expires_at':None,'next_attempt_at':now(),
        'coverage':MetadataSnapshot.coverage.op('||')({'mapping_revalidation':marker})}))


async def changed_generation(db, generation):
    previous = await db.scalar(select(Generation.id).where(Generation.source == generation.source,
        Generation.id != generation.id, Generation.published_at.is_not(None))
        .order_by(Generation.published_at.desc()).limit(1))
    # Read only identity claims, never the potentially large credit graph.
    claims=func.jsonb_build_object(*[part for prop in ('P345','P5794','P9043')
        for part in (prop,Reference.data['value']['claims'][prop])])
    new = select(Reference.native,Reference.names,Reference.imdb_ids,claims.label('claims'), Reference.data['fingerprint'].astext.label('fingerprint')).where(
        Reference.generation == generation.id).subquery()
    old = select(Reference.native,Reference.names,Reference.imdb_ids, Reference.data['fingerprint'].astext.label('fingerprint')).where(
        Reference.generation == previous).subquery()
    # A bounded cache rebuild is complete only for its retrieved scope. Eviction
    # or expiry is unknown evidence, not a provider deleting an identity claim.
    cached_scope=generation.source=='wikidata' and (generation.coverage or {}).get('scope')=='retrieved_game_evidence'
    stream = await db.stream(select(func.coalesce(new.c.native, old.c.native),new.c.names,old.c.names,
        new.c.imdb_ids,old.c.imdb_ids,new.c.claims,old.c.native).select_from(
        new.join(old, new.c.native == old.c.native, full=True)).where(
            new.c.fingerprint.is_distinct_from(old.c.fingerprint),
            new.c.native.is_not(None) if cached_scope else True))
    async for batch in stream.partitions(500):
        conditions=[]
        for native,new_names,old_names,new_imdb,old_imdb,new_claims,old_native in batch:
            keys=[f'{generation.source}:{native}']
            keys.extend(f'{generation.source}-name:{name}' for name in [*(new_names or []),*(old_names or [])])
            affected=CatalogueIdentity.evidence['dependencies'].op('?|')(array(sorted(set(keys))))
            if generation.source=='wikidata':
                imdb_ids=sorted(set([*(new_imdb or []),*(old_imdb or [])]))
                # Include legacy explicit bindings that predate reverse-ID dependencies.
                affected=or_(affected,CatalogueIdentity.external_id.in_(imdb_ids),
                    CatalogueIdentity.evidence['dependencies'].op('?|')(array([f'wikidata-imdb:{value}' for value in imdb_ids])))
                if cached_scope and old_native is None:
                    from core.wikidata_claims import best_claims
                    proof=CatalogueIdentity.evidence['wikidata']
                    unchanged=and_(CatalogueIdentity.source=='wikidata',proof['wikidata_id'].astext==native,
                        *[proof[prop]==best_claims({'claims':{key:value or [] for key,value in new_claims.items()}},prop)
                            for prop in ('P345','P5794','P9043')])
                    # A returning cache row is already known by its own saved
                    # proof. Other items/changed claims still invalidate it.
                    affected=affected & ~func.coalesce(unchanged,False)
            conditions.append(affected)
        await invalidate(db,identity_ids=select(CatalogueIdentity.id).where(or_(*conditions)))
    # Marked evidence is not available for new verified participation; old public
    # credits are retained until a worker establishes a real contradiction.
