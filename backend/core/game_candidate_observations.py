"""Invalidate accepted evidence for changed facts, not merely another query."""
from sqlalchemy import select,func,literal
from sqlalchemy.dialects.postgresql import array
from models.catalogue import CatalogueIdentity,MetadataSnapshot


def changes_binding(evidence,key,data):
    proof=evidence.get('matching') or {};support=proof.get('support') or {}
    if f'igdb-query:{key}' in evidence.get('dependencies',[]):
        return support.get('candidate_fingerprint')!=data.get('scope_fingerprint') or data.get('complete') is False
    known=support.get('candidate_observations') or {}
    current=data.get('scope_observations') or {}
    title=proof.get('imdb') or {}
    if not known:
        # Upgrade legacy proofs conservatively once, when actual candidate
        # observations overlap. Empty unrelated searches prove no change.
        if not data.get('rows'):return False
        previous={str(evidence.get('igdb_id'))}
        if previous.intersection(current):return True
    elif any(current[native]!=known[native] for native in current.keys()&known.keys()):return True
    from core.game_title_match import decide
    if not title.get('title'):return bool(data.get('rows'))
    return any(str(raw['id']) not in known and decide(title,[raw],complete=True,branches=[])['evidence']['candidates']
        for raw in data.get('rows',[]))


async def invalidate_observed(db,key,data):
    from core.game_mapping_lifecycle import invalidate
    keys=[f'igdb-query:{key}',*[f'igdb-name:{name}' for name in data.get('scope_names',[])]]
    from core.game_mapping_lifecycle import invalidate_deferred
    await invalidate_deferred(db,keys)
    # Only the public proof header is needed; no private entries or full credits.
    evidence=CatalogueIdentity.evidence
    query=select(CatalogueIdentity.id,evidence['igdb_id'].astext.label('igdb_id'),
        evidence['matching']['imdb'].label('imdb'),
        evidence['matching']['support']['candidate_fingerprint'].astext.label('fingerprint'),
        evidence['matching']['support']['candidate_observations'].label('observations'),
        evidence['dependencies'].op('?')(f'igdb-query:{key}').label('own'),literal('binding').label('kind')).where(
        CatalogueIdentity.namespace=='imdb.title',CatalogueIdentity.source=='matched',
        func.coalesce(CatalogueIdentity.evidence['mapping_state'].astext,'active').not_in(('quarantined','stale')),
        CatalogueIdentity.evidence['dependencies'].op('?|')(array(keys)))
    from core.game_credit_parents import KIND
    proof=MetadataSnapshot.payload['evidence']
    parents=select(MetadataSnapshot.id,proof['variant']['child']['id'].astext,
        proof['imdb'],proof['support']['candidate_fingerprint'].astext,
        proof['support']['candidate_observations'],MetadataSnapshot.payload['dependencies'].op('?')(f'igdb-query:{key}'),literal('parent')).where(
        MetadataSnapshot.provider=='imdb',MetadataSnapshot.kind==KIND,
        MetadataSnapshot.coverage['automatic'].astext=='true',MetadataSnapshot.coverage['mapping_state'].astext=='active',
        MetadataSnapshot.payload['dependencies'].op('?|')(array(keys)))
    rows=(await db.execute(query.union_all(parents))).all()
    import asyncio
    def affected():
        return [(row.kind,row.id) for row in rows if changes_binding({'igdb_id':row.igdb_id,
            'dependencies':[f'igdb-query:{key}'] if row.own else [],
            'matching':{'imdb':row.imdb,'support':{'candidate_fingerprint':row.fingerprint,
                'candidate_observations':row.observations}}},key,data)]
    changed=await asyncio.to_thread(affected)
    bindings=[identifier for kind,identifier in changed if kind=='binding']
    parents=[identifier for kind,identifier in changed if kind=='parent']
    if bindings:await invalidate(db,identity_ids=bindings)
    if parents:
        from core.game_mapping_lifecycle import invalidate_parents
        await invalidate_parents(db,snapshot_ids=parents)
